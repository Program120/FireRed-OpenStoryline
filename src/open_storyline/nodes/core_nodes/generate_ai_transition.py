import base64
import logging
import numpy as np
from typing import Dict, Any, List, Union, Tuple, Optional
from pathlib import Path
import os
from io import BytesIO
from PIL import Image, ImageOps
from moviepy import VideoFileClip  # MoviePy 2.x standard import

from open_storyline.utils.register import NODE_REGISTRY
from open_storyline.utils.logging import get_logger
from open_storyline.utils.prompts import get_prompt
from open_storyline.utils.ai_transition_cancel import is_ai_transition_cancelled
from open_storyline.utils.ai_transition_client import VisionClientFactory
from open_storyline.nodes.core_nodes.base_node import BaseNode, NodeMeta
from open_storyline.nodes.node_state import NodeState
from open_storyline.nodes.node_schema import GenerateAITransitionInput

def encode_image_to_data_url(
    image: Image.Image,
    format: str = "JPEG",
    quality: int = 85,
    max_long_edge: Optional[int] = None,
) -> str:
    """
    Converts a PIL Image object into a Base64-encoded Data URL.
    
    Args:
        image (Image.Image): The PIL Image instance to be encoded.
        format (str): Image format for encoding ('JPEG', 'PNG', 'WEBP'). Defaults to 'JPEG'.
        quality (int): Encoding quality for JPEG/WEBP (1-100). Higher is better quality but larger size.
        max_long_edge (Optional[int]): If provided, downsample the image so its long edge
            does not exceed this value while preserving aspect ratio.
        
    Returns:
        str: A complete Data URL string (e.g., "data:image/jpeg;base64,...").
    """
    # 1. Optionally downsample the image to reduce payload size.
    if max_long_edge and max_long_edge > 0:
        width, height = image.size
        long_edge = max(width, height)
        if long_edge > max_long_edge:
            scale = max_long_edge / float(long_edge)
            new_size = (
                max(1, int(round(width * scale))),
                max(1, int(round(height * scale))),
            )
            image = image.resize(new_size, Image.Resampling.LANCZOS)

    # 2. Handle mode compatibility
    # JPEG format does not support transparency (RGBA) or palette (P) modes.
    # We must convert these to RGB to avoid "OSError: cannot write mode RGBA as JPEG".
    save_format = format.upper()
    if save_format == "JPEG":
        if image.mode in ("RGBA", "P", "LA"):
            image = image.convert("RGB")
        mime_type = "image/jpeg"
    elif save_format == "PNG":
        mime_type = "image/png"
    else:
        mime_type = f"image/{save_format.lower()}"

    # 3. Save image to an in-memory byte buffer
    # This avoids slow disk I/O and temporary file management.
    buffered = BytesIO()
    image.save(
        buffered, 
        format=save_format, 
        quality=quality if save_format in ("JPEG", "WEBP") else None
    )
    
    # 4. Encode binary data to Base64 string
    # getvalue() retrieves the bytes from the buffer, b64encode converts to base64 bytes, 
    # and decode('utf-8') converts it to a standard Python string.
    base64_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    # 5. Format and return the standard Data URL pattern
    return f"data:{mime_type};base64,{base64_str}"


@NODE_REGISTRY.register()
class GenerateAITransitionNode(BaseNode):
    meta = NodeMeta(
        name="generate_ai_transition",
        description="Generate transition videos: Create transition videos for grouped video clips, generating an appropriate transition from the last frame of the previous clip to the first frame of the next clip based on user requirements.",
        node_id="generate_ai_transition",
        node_kind="generate_ai_transition",
        require_prior_kind=["split_shots", "group_clips"],
        default_require_prior_kind=['group_clips'],
        next_available_node=["generate_script"],
    )
    input_schema = GenerateAITransitionInput
    VIDEO_EXTS = {
        ".mp4", ".mov", ".mkv", ".avi"
    }
    IMAGE_EXTS = {
        ".jpg", ".jpeg", ".png", ".webp", ".bmp"
    }

    DEFAULT_TRANSITION_DURATION = 5
    SECOND_TO_MILLISECOND = 1000
    MAX_ASPECT_RATIO_FACTOR = 1.1
    SCENE_THRESHOLD = 0.4
    MIN_OLD_SCENE_SIM = 0.5
    MAX_NEW_SCENE_SIM = 0.3
    FRAME_PAIR_MAX_SIM = 0.7
    GRADUAL_DROP_MIN = 0.15
    _dinov2_processor = None
    _dinov2_model = None
    _logger = get_logger("generate_ai_transition")

    @classmethod
    def _get_dinov2(cls):
        """Lazy-load DINOv2-small for semantic scene similarity."""
        if cls._dinov2_model is None:
            import torch
            from transformers import AutoImageProcessor, AutoModel
            cls._dinov2_processor = AutoImageProcessor.from_pretrained(
                "facebook/dinov2-small", use_fast=True,
            )
            cls._dinov2_model = AutoModel.from_pretrained("facebook/dinov2-small")
            cls._dinov2_model.eval()
        return cls._dinov2_processor, cls._dinov2_model

    @classmethod
    def _frame_embedding(cls, frame) -> "torch.Tensor":
        """Get DINOv2 CLS embedding for a PIL Image or numpy frame."""
        import torch
        from PIL import Image as _Img
        processor, model = cls._get_dinov2()
        if not isinstance(frame, _Img.Image):
            frame = _Img.fromarray(frame if isinstance(frame, np.ndarray) else np.array(frame))
        inputs = processor(images=frame, return_tensors="pt")
        with torch.no_grad():
            return model(**inputs).last_hidden_state[:, 0]

    def _coarse_scan(
        self,
        boundary_frames: List[Image.Image],
        ref_emb: "torch.Tensor",
    ) -> Tuple[Optional[Tuple[int, int]], List[Tuple[int, float]]]:
        """
        Phase 1: Coarse scan boundary clip with ~15 sample points.

        Returns:
            (region, sample_points) where region is (start_idx, end_idx) of
            the interval containing the scene change, or None if not found.
        """
        import torch
        log = self._logger
        n = len(boundary_frames)
        coarse_step = max(1, n // 15)
        threshold = self.SCENE_THRESHOLD

        sample_points: List[Tuple[int, float]] = []
        for idx in range(0, n, coarse_step):
            emb = self._frame_embedding(boundary_frames[idx])
            sim = torch.nn.functional.cosine_similarity(ref_emb, emb).item()
            sample_points.append((idx, sim))
            log.debug("[coarse] boundary_frame[%d/%d] sim=%.4f", idx, n, sim)

        # Always include last frame
        if sample_points[-1][0] != n - 1:
            emb = self._frame_embedding(boundary_frames[-1])
            sim = torch.nn.functional.cosine_similarity(ref_emb, emb).item()
            sample_points.append((n - 1, sim))
            log.debug("[coarse] boundary_frame[%d/%d] sim=%.4f (last)", n - 1, n, sim)

        # Find threshold crossing: old scene → new scene
        for i in range(len(sample_points) - 1):
            idx_before, sim_before = sample_points[i]
            idx_after, sim_after = sample_points[i + 1]
            if sim_before >= threshold and sim_after < threshold:
                log.info("[coarse] Change region found: [%d, %d] (sim %.4f→%.4f)",
                         idx_before, idx_after, sim_before, sim_after)
                return (idx_before, idx_after), sample_points

        # All frames below threshold → change is at or before frame 0
        if sample_points[0][1] < threshold:
            log.info("[coarse] First frame already new scene (sim=%.4f)", sample_points[0][1])
            return (0, 0), sample_points

        # No clean crossing → find largest single-step drop as fallback
        max_drop = 0.0
        max_drop_i = -1
        for i in range(len(sample_points) - 1):
            drop = sample_points[i][1] - sample_points[i + 1][1]
            if drop > max_drop:
                max_drop = drop
                max_drop_i = i

        if max_drop > self.GRADUAL_DROP_MIN and max_drop_i >= 0:
            region = (sample_points[max_drop_i][0], sample_points[max_drop_i + 1][0])
            log.info("[coarse] No threshold crossing; using largest drop=%.4f at region [%d, %d]",
                     max_drop, region[0], region[1])
            return region, sample_points

        log.warning("[coarse] No scene change detected. samples=%s",
                    [(i, f"{s:.3f}") for i, s in sample_points])
        return None, sample_points

    def _fine_scan(
        self,
        boundary_frames: List[Image.Image],
        ref_emb: "torch.Tensor",
        region_start: int,
        region_end: int,
    ) -> Tuple[int, List[Tuple[int, float]]]:
        """
        Phase 2: Fine scan within [region_start, region_end] to find precise change frame.

        Returns:
            (change_idx, fine_points) where change_idx is the last "old scene" frame index.
        """
        import torch
        log = self._logger
        threshold = self.SCENE_THRESHOLD
        span = region_end - region_start
        fine_step = max(1, span // 8)

        fine_points: List[Tuple[int, float]] = []
        for idx in range(region_start, region_end + 1, fine_step):
            emb = self._frame_embedding(boundary_frames[idx])
            sim = torch.nn.functional.cosine_similarity(ref_emb, emb).item()
            fine_points.append((idx, sim))
            log.debug("[fine] boundary_frame[%d] sim=%.4f", idx, sim)

        # Ensure last point included
        if fine_points[-1][0] != region_end:
            emb = self._frame_embedding(boundary_frames[region_end])
            sim = torch.nn.functional.cosine_similarity(ref_emb, emb).item()
            fine_points.append((region_end, sim))
            log.debug("[fine] boundary_frame[%d] sim=%.4f (end)", region_end, sim)

        # Find precise crossing
        for i in range(len(fine_points) - 1):
            idx_before, sim_before = fine_points[i]
            idx_after, sim_after = fine_points[i + 1]
            if sim_before >= threshold and sim_after < threshold:
                log.info("[fine] Precise change at idx=%d (sim %.4f→%.4f at idx %d)",
                         idx_before, sim_before, sim_after, idx_after)
                return idx_before, fine_points

        # Fallback: steepest drop
        max_drop = 0.0
        best_idx = region_start
        for i in range(len(fine_points) - 1):
            drop = fine_points[i][1] - fine_points[i + 1][1]
            if drop > max_drop:
                max_drop = drop
                best_idx = fine_points[i][0]

        log.info("[fine] No clean crossing; steepest drop=%.4f at idx=%d", max_drop, best_idx)
        return best_idx, fine_points

    def _select_transition_frames(
        self,
        boundary_frames: List[Image.Image],
        from_frames: List[Image.Image],
        after_frames: Optional[List[Image.Image]],
        change_idx: int,
        ref_emb: "torch.Tensor",
    ) -> Tuple[Image.Image, Image.Image]:
        """
        Phase 3: Select visually distinct from_frame (old scene) and to_frame (new scene).

        Walks backward/forward from change_idx to find stable scene frames.
        """
        import torch
        log = self._logger
        n = len(boundary_frames)
        search_range = 30  # ~1 second at 30fps

        # --- from_frame: pick the best "old scene" representative ---
        # Strategy: search backward in boundary clip AND check from_clip[-1],
        # then pick whichever has higher similarity to reference (= more clearly old scene).
        boundary_candidate = None
        boundary_candidate_sim = 0.0
        search_start = max(0, change_idx - search_range)
        step_back = max(1, (change_idx - search_start) // 5)

        for idx in range(change_idx, search_start - 1, -step_back):
            emb = self._frame_embedding(boundary_frames[idx])
            sim = torch.nn.functional.cosine_similarity(ref_emb, emb).item()
            log.debug("[select] backward idx=%d sim=%.4f", idx, sim)
            if sim > boundary_candidate_sim:
                boundary_candidate = boundary_frames[idx]
                boundary_candidate_sim = sim
            if sim >= self.MIN_OLD_SCENE_SIM:
                break

        # Always evaluate from_clip[-1] as a candidate
        from_clip_candidate_sim = 0.0
        if from_frames:
            from_clip_emb = self._frame_embedding(from_frames[-1])
            from_clip_candidate_sim = torch.nn.functional.cosine_similarity(ref_emb, from_clip_emb).item()
            log.debug("[select] from_clip[-1] sim=%.4f", from_clip_candidate_sim)

        # Pick the better candidate
        if from_frames and from_clip_candidate_sim > boundary_candidate_sim:
            from_frame = from_frames[-1]
            from_frame_sim = from_clip_candidate_sim
            log.info("[select] from_frame: using from_clip[-1] (sim=%.4f > boundary best=%.4f)",
                     from_clip_candidate_sim, boundary_candidate_sim)
        elif boundary_candidate is not None:
            from_frame = boundary_candidate
            from_frame_sim = boundary_candidate_sim
            log.info("[select] from_frame: using boundary candidate (sim=%.4f)", boundary_candidate_sim)
        else:
            from_frame = from_frames[-1] if from_frames else boundary_frames[0]
            from_frame_sim = from_clip_candidate_sim
            log.info("[select] from_frame: fallback to from_clip[-1]")

        # --- to_frame: walk forward for stable new scene ---
        to_frame = None
        to_frame_sim = 1.0
        search_end = min(n - 1, change_idx + search_range)
        step_fwd = max(1, (search_end - change_idx) // 5)

        for idx in range(change_idx + 1, search_end + 1, step_fwd):
            if idx >= n:
                break
            emb = self._frame_embedding(boundary_frames[idx])
            sim = torch.nn.functional.cosine_similarity(ref_emb, emb).item()
            log.debug("[select] forward idx=%d sim=%.4f", idx, sim)
            if sim < to_frame_sim:
                to_frame = boundary_frames[idx]
                to_frame_sim = sim
            if sim <= self.MAX_NEW_SCENE_SIM:
                break

        if to_frame_sim > self.MAX_NEW_SCENE_SIM and after_frames:
            log.info("[select] to_frame: boundary doesn't reach new scene (best sim=%.4f), "
                     "using after_clip first frame", to_frame_sim)
            to_frame = after_frames[0]

        # Final fallback
        if from_frame is None:
            from_frame = from_frames[-1] if from_frames else boundary_frames[0]
        if to_frame is None:
            to_frame = after_frames[0] if after_frames else boundary_frames[-1]

        # Validate: from and to must be visually distinct
        from_emb = self._frame_embedding(from_frame)
        to_emb = self._frame_embedding(to_frame)
        pair_sim = torch.nn.functional.cosine_similarity(from_emb, to_emb).item()
        log.info("[select] from_frame sim_to_ref=%.4f, to_frame sim_to_ref=%.4f, "
                 "from↔to sim=%.4f (want < %.1f)",
                 from_frame_sim, to_frame_sim, pair_sim, self.FRAME_PAIR_MAX_SIM)

        if pair_sim > self.FRAME_PAIR_MAX_SIM:
            log.warning("[select] from↔to too similar (%.4f > %.1f)! "
                        "Falling back to from_clip[-1] and after_clip[0]",
                        pair_sim, self.FRAME_PAIR_MAX_SIM)
            from_frame = from_frames[-1] if from_frames else from_frame
            to_frame = after_frames[0] if after_frames else boundary_frames[-1]

        return from_frame, to_frame

    @staticmethod
    def _extract_prompt(raw: str) -> str:
        """Extract the English prompt from LLM output that contains analysis + prompt."""
        if not raw:
            return ""
        # Look for "PROMPT:" marker
        for marker in ("PROMPT:", "PROMPT：", "Prompt:"):
            if marker in raw:
                return raw.split(marker, 1)[1].strip()
        # Fallback: return the whole response (old format without analysis)
        return raw.strip()

    @staticmethod
    def _extract_duration(raw: str) -> Optional[int]:
        """Extract the duration (seconds) suggested by LLM from its response."""
        if not raw:
            return None
        import re
        for marker in ("DURATION:", "DURATION：", "Duration:"):
            if marker in raw:
                after = raw.split(marker, 1)[1].strip()
                m = re.match(r"(\d+)", after)
                if m:
                    return int(m.group(1))
        return None

    def _raise_if_cancelled(self, node_state: NodeState) -> None:
        if is_ai_transition_cancelled(self.server_cache_dir, node_state.session_id):
            raise RuntimeError("generate_ai_transition cancelled by user")

    async def process(self, node_state: NodeState, inputs: Dict[str, Any]) -> Any:
        log = self._logger
        group_clips = inputs.get("group_clips", {})
        split_shots = inputs.get("split_shots", {})
        speech_rough_cut = inputs.get("speech_rough_cut", {})
        groups = group_clips.get("groups", [])

        runtime_cfg = self._resolve_ai_transition_runtime_cfg(inputs)
        provider = runtime_cfg["provider"]
        api_key = runtime_cfg["api_key"]
        model_name = runtime_cfg["model_name"]
        transition_duration = inputs.get("duration")
        resolution = inputs.get("resolution")
        user_request = inputs.get("user_request", "以一镜到底的方式拍摄，场景丝滑过渡")
        max_transitions = inputs.get("max_transitions")  # None = unlimited

        log.info("[process] Starting: %d groups, provider=%s, model=%s, max_transitions=%s",
                 len(groups), provider, model_name, max_transitions)

        # Build clip_map from both sources so group_clips can reference either set of IDs.
        # Rough-cut clips take precedence (same ID overrides split_shots version).
        clip_map = {clip['clip_id']: clip for clip in split_shots.get('clips', [])}
        for clip in speech_rough_cut.get('clips', []):
            clip_map[clip['clip_id']] = clip
        log.debug("[process] clip_map built: %d clips from split_shots, %d from rough_cut, total unique=%d",
                  len(split_shots.get('clips', [])), len(speech_rough_cut.get('clips', [])), len(clip_map))
        for cid, cinfo in clip_map.items():
            log.debug("[process] clip_map[%s] path=%s source_ref=%s",
                      cid, cinfo.get("path"), cinfo.get("source_ref"))

        # Transitions only at group boundaries (not within groups)
        total_transitions = max(len(groups) - 1, 0)
        if total_transitions == 0:
            log.warning("[process] Only %d group(s) → 0 group boundaries → NO transitions will be generated. "
                        "Ensure group_clips produces multiple groups for transitions to work.",
                        len(groups))
        if max_transitions is not None:
            total_transitions = min(total_transitions, max_transitions)

        await node_state.mcp_ctx.report_progress(
            0,
            total_transitions,
            "AI transition generation starting...",
        )

        node_cache_dir = self._prepare_output_directory(node_state)

        transition_info = {}
        transition_index = 1
        transition_context = {
            "clip_map": clip_map,
            "node_state": node_state,
            "node_cache_dir": node_cache_dir,
            "provider": provider,
            "api_key": api_key,
            "model_name": model_name,
            "transition_duration": transition_duration,
            "resolution": resolution,
            "user_request": user_request,
            "total_transitions": total_transitions,
        }

        for i, group in enumerate(groups):
            group_clip_ids = group.get("clip_ids", [])
            valid_group_clip_ids = [clip_id for clip_id in group_clip_ids if clip_id in clip_map]
            log.debug("[process] Group %d/%d: id=%s, clip_ids=%s, valid=%d/%d",
                      i, len(groups) - 1, group.get("group_id"), group_clip_ids,
                      len(valid_group_clip_ids), len(group_clip_ids))
            if len(valid_group_clip_ids) != len(group_clip_ids):
                missing_clip_ids = [clip_id for clip_id in group_clip_ids if clip_id not in clip_map]
                node_state.node_summary.add_warning(
                    f"Clips <{missing_clip_ids}> not found in split_shots; they will be skipped in generate_ai_transition."
                )

            def _reached_limit():
                return max_transitions is not None and len(transition_info) >= max_transitions

            new_group_clip_ids = list(valid_group_clip_ids)
            transition_total_duration_sec = 0.0

            # Only generate transitions at GROUP BOUNDARIES (scene changes),
            # not between every clip within a group (same scene).
            if i < len(groups) - 1 and valid_group_clip_ids and not _reached_limit():
                next_group = groups[i + 1]
                next_group_clip_ids = [clip_id for clip_id in next_group.get("clip_ids", []) if clip_id in clip_map]
                log.info("[process] Group boundary %d→%d: last_clip=%s, next_group_clips=%s",
                         i, i + 1, valid_group_clip_ids[-1] if valid_group_clip_ids else "none",
                         next_group_clip_ids)
                if next_group_clip_ids:
                    # Identify the boundary clip (first clip of next group) and the
                    # clip after it. The boundary clip spans the scene change — we
                    # REPLACE it with the AI transition instead of inserting alongside it.
                    boundary_clip_id = next_group_clip_ids[0]
                    after_boundary_id = next_group_clip_ids[1] if len(next_group_clip_ids) > 1 else None

                    smart_result = await self._build_transition_clip_smart(
                        from_clip_id=valid_group_clip_ids[-1],
                        boundary_clip_id=boundary_clip_id,
                        after_boundary_clip_id=after_boundary_id,
                        transition_index=transition_index,
                        **transition_context,
                    )
                    transition_result, should_replace, change_ratio = smart_result
                    if transition_result:
                        transition_clip_id, transition_payload = transition_result
                        transition_info[transition_clip_id] = transition_payload
                        transition_index += 1

                        # If boundary clip contains old-scene content before the
                        # scene change, preserve it as a "pre-change" segment so
                        # the transition starts at the actual cut point, not at
                        # the beginning of the boundary clip.
                        if should_replace and change_ratio and change_ratio > 0.05:
                            boundary_clip = clip_map.get(boundary_clip_id)
                            if boundary_clip:
                                orig_dur_ms = boundary_clip.get("source_ref", {}).get("duration", 0)
                                pre_dur_ms = int(orig_dur_ms * change_ratio)
                                pre_clip_id = f"transition_pre_{transition_index - 1:04d}"
                                transition_info[pre_clip_id] = {
                                    "kind": "video",
                                    "path": boundary_clip.get("path", ""),
                                    "fps": boundary_clip.get("fps", 0),
                                    "source_ref": {
                                        "media_id": pre_clip_id,
                                        "start": 0,
                                        "end": pre_dur_ms,
                                        "duration_ms": pre_dur_ms,
                                        "height": boundary_clip.get("source_ref", {}).get("height", 0),
                                        "width": boundary_clip.get("source_ref", {}).get("width", 0),
                                    },
                                }
                                new_group_clip_ids.append(pre_clip_id)
                                transition_total_duration_sec += pre_dur_ms / self.SECOND_TO_MILLISECOND
                                log.info("[process] Added pre-change clip %s (%.0fms of %s "
                                         "before scene change at ratio=%.2f)",
                                         pre_clip_id, pre_dur_ms, boundary_clip_id, change_ratio)

                        new_group_clip_ids.append(transition_clip_id)
                        transition_total_duration_sec += self._transition_payload_duration_seconds(transition_payload)
                        if should_replace:
                            # Boundary clip spans scene change → remove it (replaced by pre-change + transition)
                            next_group["clip_ids"] = [cid for cid in next_group_clip_ids if cid != boundary_clip_id]

            base_duration_sec = self._parse_duration_seconds(group.get("duration", 0.0))
            group["clip_ids"] = new_group_clip_ids
            group["duration"] = f"{base_duration_sec + transition_total_duration_sec:.1f}s"

        group_clips["groups"] = groups
        group_clips["transition_info"] = transition_info
        if total_transitions > 0:
            await node_state.mcp_ctx.report_progress(
                total_transitions,
                total_transitions,
                "AI transition generation finished",
            )
        return group_clips
    
    async def default_process(self, node_state: NodeState, inputs: Dict[str, Any]) -> Any:
        return inputs.get("group_clips", {})

    def _is_complete_provider_cfg(self, cfg: Dict[str, Any], required_keys: list[str]) -> bool:
        return all(cfg.get(k) not in (None, "") for k in required_keys)
    
    def _get_provider_cfg(self, provider_name: str) -> Dict[str, Any]:
        providers = getattr(self.server_cfg.generate_ai_transition, "providers", None) or {}
        cfg = providers.get(provider_name)
        if not isinstance(cfg, dict):
            raise ValueError(f"provider={provider_name} not configured in config.toml")
         
        return cfg

    def _resolve_ai_transition_runtime_cfg(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        provider = str(inputs.get("provider") or "").strip().lower() or "minimax"
        config_cfg = self._get_provider_cfg(provider)

        required_keys = list(config_cfg.keys())

        frontend_cfg = {k: inputs.get(k) for k in required_keys}

        if self._is_complete_provider_cfg(frontend_cfg, required_keys):
            final_cfg = frontend_cfg
        elif self._is_complete_provider_cfg(config_cfg, required_keys):
            final_cfg = config_cfg
        else:
            missing = [k for k in required_keys if config_cfg.get(k) in (None, "")]
            raise ValueError(
                f"provider={provider} missing required fields: {missing}. "
                f"Please configure in sidebar or config.toml."
            )

        return {"provider": provider, **final_cfg}

    async def _build_transition_clip_smart(
        self,
        *,
        from_clip_id: str,
        boundary_clip_id: str,
        after_boundary_clip_id: Optional[str],
        clip_map: Dict[str, Any],
        **kwargs,
    ):
        """
        DINOv2-based precise scene change detection + transition generation.

        Uses semantic embeddings to find the EXACT frame where the scene
        changes, then extracts from/to frames around that point.
        Falls back to simple boundary-based approach if DINOv2 fails.

        Returns (transition_result, should_replace_boundary_clip).
        """
        from PIL import Image as _Img
        from moviepy import VideoFileClip
        import torch

        log = self._logger
        from_clip = clip_map.get(from_clip_id)
        boundary_clip = clip_map.get(boundary_clip_id)
        if not from_clip or not boundary_clip:
            log.warning("[smart] from_clip=%s or boundary_clip=%s not found in clip_map, skipping",
                        from_clip_id, boundary_clip_id)
            return None, False, None

        log.debug("[smart] ===== Transition: from_clip=%s → boundary_clip=%s (after=%s) =====",
                  from_clip_id, boundary_clip_id, after_boundary_clip_id)

        # Get the original video path (clips share the same source video)
        from_src = from_clip.get("source_ref", {})
        boundary_src = boundary_clip.get("source_ref", {})
        log.debug("[smart] from_clip path=%s, source_ref=%s", from_clip.get("path"), from_src)
        log.debug("[smart] boundary_clip path=%s, source_ref=%s", boundary_clip.get("path"), boundary_src)

        # Try DINOv2-based scene change detection on the original video
        try:
            # Use the original clip paths to find the source video
            # Search region: from_clip's last 2s through end of boundary clip (+ after if exists)
            from_start = from_src.get("start", 0) / 1000.0
            from_end = from_src.get("end", 0) / 1000.0
            boundary_end = boundary_src.get("end", 0) / 1000.0

            search_start = max(0, from_end - 2.0)
            search_end = boundary_end
            if after_boundary_clip_id:
                after_clip = clip_map.get(after_boundary_clip_id)
                if after_clip:
                    search_end = after_clip.get("source_ref", {}).get("end", boundary_end) / 1000.0

            # Reference: middle of from_clip (solidly in old scene)
            ref_timestamp = (from_start + from_end) / 2.0

            # Find scene change using the ORIGINAL video path
            # All clips come from the same source video
            source_video = from_clip.get("path")
            # But clips might be split files; we need the original
            # Use the source media path from load_media
            # For now, use from_clip's path and adjust timestamps
            # Actually, source_ref has start/end relative to the ORIGINAL video
            # But clip path points to the CUT clip file, not the original
            # We need to work with the cut clip files directly

            # Load from_clip frames and boundary clip frames
            from_frames = self._load_clip(from_clip.get("path"))
            boundary_frames = self._load_clip(boundary_clip.get("path"))
            log.debug("[smart] Loaded frames: from_clip=%d frames, boundary_clip=%d frames",
                      len(from_frames), len(boundary_frames))

            if from_frames and boundary_frames:
                # Reference: middle frame of from_clip (solidly in old scene)
                ref_idx = len(from_frames) // 2
                ref_frame = from_frames[ref_idx]
                ref_emb = self._frame_embedding(ref_frame)
                log.debug("[smart] Reference frame: from_clip[%d/%d] (middle of from_clip)",
                          ref_idx, len(from_frames))

                # Check boundary clip's first frame against reference
                boundary_first_emb = self._frame_embedding(boundary_frames[0])
                first_sim = torch.nn.functional.cosine_similarity(ref_emb, boundary_first_emb).item()

                # Check boundary clip's last frame against reference
                boundary_last_emb = self._frame_embedding(boundary_frames[-1])
                last_sim = torch.nn.functional.cosine_similarity(ref_emb, boundary_last_emb).item()

                log.info("[smart] boundary first_frame sim=%.4f, last_frame sim=%.4f (threshold=0.4)",
                         first_sim, last_sim)

                should_replace = first_sim > self.SCENE_THRESHOLD
                log.info("[smart] Decision: should_replace=%s (first_sim %.4f %s %.1f)",
                         should_replace, first_sim,
                         ">" if should_replace else "<=", self.SCENE_THRESHOLD)

                if should_replace:
                    # Boundary clip starts in old scene → two-phase detection
                    # Phase 1: Coarse scan
                    coarse_result, _ = self._coarse_scan(boundary_frames, ref_emb)

                    # Load after_boundary frames for frame selection
                    after_frames = None
                    if after_boundary_clip_id:
                        after_clip = clip_map.get(after_boundary_clip_id)
                        if after_clip:
                            after_frames = self._load_clip(after_clip.get("path"))

                    change_ratio = None
                    if coarse_result:
                        region_start, region_end = coarse_result
                        # Phase 2: Fine scan
                        change_idx, _ = self._fine_scan(
                            boundary_frames, ref_emb, region_start, region_end)
                        # Phase 3: Select visually distinct frames
                        from_frame, to_frame = self._select_transition_frames(
                            boundary_frames, from_frames, after_frames,
                            change_idx, ref_emb)
                        change_ratio = change_idx / len(boundary_frames)
                        log.info("[smart] change_ratio=%.4f (change_idx=%d/%d)",
                                 change_ratio, change_idx, len(boundary_frames))
                    else:
                        log.warning("[smart] No scene change in boundary clip, "
                                    "using from_clip[-1] and after/boundary frame")
                        from_frame = from_frames[-1]
                        to_frame = (after_frames[0] if after_frames
                                    else boundary_frames[-1])

                    result = await self._build_transition_clip_with_frames(
                        from_clip_id=from_clip_id,
                        to_clip_id=after_boundary_clip_id or boundary_clip_id,
                        from_frame_override=from_frame,
                        to_frame_override=to_frame,
                        clip_map=clip_map,
                        **kwargs,
                    )
                    return result, True, change_ratio
                else:
                    # Boundary clip is already in new scene → just INSERT
                    log.info("[smart] INSERT mode: using from_clip last frame → boundary first frame")
                    result = await self._build_transition_clip_with_frames(
                        from_clip_id=from_clip_id,
                        to_clip_id=boundary_clip_id,
                        from_frame_override=from_frames[-1],
                        to_frame_override=boundary_frames[0],
                        clip_map=clip_map,
                        **kwargs,
                    )
                    return result, False, None

        except Exception as e:
            log.error("[smart] DINOv2 scene detection failed: %s, falling back to simple approach",
                      e, exc_info=True)

        # Fallback: simple boundary-based approach
        log.info("[smart] FALLBACK: using simple boundary-based approach (from_clip last → to_clip first)")
        from_frames = self._load_clip(from_clip.get("path"))
        boundary_frames = self._load_clip(boundary_clip.get("path"))
        if from_frames and boundary_frames:
            to_clip_id = after_boundary_clip_id or boundary_clip_id
            to_clip = clip_map.get(to_clip_id)
            to_frames = self._load_clip(to_clip.get("path")) if to_clip else None
            to_frame = to_frames[0] if to_frames else boundary_frames[-1]
            log.debug("[smart] Fallback frames: from_clip[-1], to_clip(%s)[0], "
                      "from_frames=%d, boundary_frames=%d",
                      to_clip_id, len(from_frames), len(boundary_frames))

            result = await self._build_transition_clip_with_frames(
                from_clip_id=from_clip_id,
                to_clip_id=to_clip_id,
                from_frame_override=from_frames[-1],
                to_frame_override=to_frame,
                clip_map=clip_map,
                **kwargs,
            )
            return result, False, None
        log.warning("[smart] Could not load frames for fallback, returning None")
        return None, False

    async def _build_transition_clip_with_frames(
        self,
        *,
        from_clip_id: str,
        to_clip_id: str,
        from_frame_override,
        to_frame_override,
        clip_map: Dict[str, Any],
        node_state: NodeState,
        node_cache_dir: Path,
        provider: str,
        api_key: str,
        model_name: str,
        transition_duration,
        resolution,
        transition_index: int,
        total_transitions: int,
        user_request: str,
    ):
        """Build transition clip using explicitly provided frames."""
        self._raise_if_cancelled(node_state)

        prev_clip = clip_map.get(from_clip_id)
        next_clip = clip_map.get(to_clip_id)
        if not prev_clip or not next_clip:
            return None

        prev_clip_size = self._extract_clip_size(prev_clip)
        next_clip_size = self._extract_clip_size(next_clip)
        if not self._is_aspect_ratio_compatible(prev_clip_size, next_clip_size):
            node_state.node_summary.info_for_user(
                f"Skipped AI transition for clips <{from_clip_id}, {to_clip_id}> "
                f"because aspect ratios differ too much."
            )
            return None

        aligned_first_frame, aligned_last_frame, _, _ = self._preprocess_first_last_frame(
            from_frame_override,
            to_frame_override,
        )

        log = self._logger
        llm = node_state.llm
        meta_system_prompt = get_prompt("generate_ai_transition.system", lang=node_state.lang)
        meta_user_prompt = get_prompt("generate_ai_transition.user", lang=node_state.lang, user_request=user_request)

        raw_response = await llm.complete(
            system_prompt=meta_system_prompt,
            user_prompt=meta_user_prompt,
            media=[
                {"url": encode_image_to_data_url(aligned_first_frame, quality=80, max_long_edge=768)},
                {"url": encode_image_to_data_url(aligned_last_frame, quality=80, max_long_edge=768)},
            ],
            temperature=0.3,
            top_p=0.9,
            max_tokens=1024,
            model_preferences=None
        )
        prompt = self._extract_prompt(raw_response)
        llm_duration = self._extract_duration(raw_response)
        log.info("[transition] LLM raw response:\n%s", raw_response)
        log.info("[transition] Extracted prompt (first 200 chars): %s",
                 prompt[:200] if prompt else "(empty)")
        log.info("[transition] LLM suggested duration=%s, user requested=%s",
                 llm_duration, transition_duration)

        # Duration priority: user request > LLM suggestion > provider default
        effective_transition_duration = transition_duration or llm_duration
        log.info("[transition] Final duration sent to video model: %s",
                 effective_transition_duration)

        self._raise_if_cancelled(node_state)

        gen_video_path, _, effective_duration = self._generate_video(
            provider=provider,
            api_key=api_key,
            model_name=model_name,
            prompt=prompt,
            first_frame_data_url=encode_image_to_data_url(aligned_first_frame, max_long_edge=1280),
            last_frame_data_url=encode_image_to_data_url(aligned_last_frame, max_long_edge=1280),
            duration=effective_transition_duration,
            resolution=resolution,
            output_dir=node_cache_dir,
            cancel_checker=lambda: is_ai_transition_cancelled(self.server_cache_dir, node_state.session_id),
        )

        with VideoFileClip(str(gen_video_path)) as generated_clip:
            clip_w, clip_h = generated_clip.size
            actual_dur_s = generated_clip.duration

        transition_clip_id = f"transition_{transition_index:04d}"
        dur_ms = int(round(actual_dur_s * self.SECOND_TO_MILLISECOND))

        payload = {
            "kind": "video",
            "path": str(gen_video_path),
            "fps": 30,
            "source_ref": {
                "media_id": transition_clip_id,
                "start": 0,
                "end": dur_ms,
                "duration_ms": dur_ms,
                "height": clip_h,
                "width": clip_w,
            },
        }

        await node_state.mcp_ctx.report_progress(
            transition_index, total_transitions,
            f"AI transition for clips <{from_clip_id}, {to_clip_id}> succeeded",
        )
        node_state.node_summary.info_for_user(
            f"AI transition for clips <{from_clip_id}, {to_clip_id}> succeeded",
            preview_urls=[str(gen_video_path)],
        )
        return transition_clip_id, payload

    async def _build_transition_clip(
        self,
        *,
        from_clip_id: str,
        to_clip_id: str,
        clip_map: Dict[str, Any],
        node_state: NodeState,
        node_cache_dir: Path,
        provider: str,
        api_key: str,
        model_name: str,
        transition_duration: Optional[int],
        resolution: Optional[str],
        transition_index: int,
        total_transitions: int,
        user_request: str,
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        self._raise_if_cancelled(node_state)
        prev_clip = clip_map.get(from_clip_id)
        next_clip = clip_map.get(to_clip_id)
        if not prev_clip or not next_clip:
            node_state.node_summary.add_warning(
                f"Clips <{from_clip_id}, {to_clip_id}> not found in split_shots; skipping transition generation."
            )
            return None

        prev_clip_size = self._extract_clip_size(prev_clip)
        next_clip_size = self._extract_clip_size(next_clip)
        if not self._is_aspect_ratio_compatible(prev_clip_size, next_clip_size):
            node_state.node_summary.info_for_user(
                f"Skipped AI transition for clips <{from_clip_id}, {to_clip_id}> "
                f"because aspect ratios differ too much: "
                f"{self._format_size(prev_clip_size)} -> {self._format_size(next_clip_size)}."
            )
            return None

        prev_frames = self._load_clip(prev_clip.get("path"))
        next_frames = self._load_clip(next_clip.get("path"))

        first_frame = prev_frames[-1]
        last_frame = next_frames[min(len(next_frames) * 3 // 4, len(next_frames) - 1)]

        aligned_first_frame, aligned_last_frame, _, _ = self._preprocess_first_last_frame(
            first_frame,
            last_frame,
        )

        llm = node_state.llm

        meta_system_prompt = get_prompt("generate_ai_transition.system", lang=node_state.lang)
        meta_user_prompt = get_prompt("generate_ai_transition.user", lang=node_state.lang, user_request=user_request)
    
        raw_response = await llm.complete(
            system_prompt=meta_system_prompt,
            user_prompt=meta_user_prompt,
            media=[
                {"url": encode_image_to_data_url(aligned_first_frame, quality=80, max_long_edge=768)},
                {"url": encode_image_to_data_url(aligned_last_frame, quality=80, max_long_edge=768)},
            ],
            temperature=0.3,
            top_p=0.9,
            max_tokens=1024,
            model_preferences=None
        )
        prompt = self._extract_prompt(raw_response)
        self._raise_if_cancelled(node_state)

        gen_video_path, _, effective_duration = self._generate_video(
            provider=provider,
            api_key=api_key,
            model_name=model_name,
            prompt=prompt,
            first_frame_data_url=encode_image_to_data_url(aligned_first_frame, max_long_edge=1280),
            last_frame_data_url=encode_image_to_data_url(aligned_last_frame, max_long_edge=1280),
            duration=transition_duration,
            resolution=resolution,
            output_dir=node_cache_dir,
            cancel_checker=lambda: is_ai_transition_cancelled(self.server_cache_dir, node_state.session_id),
        )

        with VideoFileClip(str(gen_video_path)) as generated_clip:
            fps = float(generated_clip.fps or 0)
            width, height = map(int, generated_clip.size)
            duration_ms = int(round((generated_clip.duration or effective_duration or self.DEFAULT_TRANSITION_DURATION) * self.SECOND_TO_MILLISECOND))
            await node_state.mcp_ctx.report_progress(
                transition_index,
                total_transitions or transition_index,
                f"AI transition {transition_index}/{total_transitions or transition_index} generated",
            )
            node_state.node_summary.info_for_user(
                f"AI transition for clips <{from_clip_id}, {to_clip_id}> succeeded",
                preview_urls=[gen_video_path]
            )

        transition_clip_id = f"transition_{transition_index:04d}"
        return transition_clip_id, {
            "fps": fps,
            "path": gen_video_path,
            "source_ref": {
                "duration_ms": duration_ms,
                "width": width,
                "height": height,
            }
        }

    def _extract_clip_size(self, clip: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        source_ref = clip.get("source_ref") or {}
        size = clip.get("size")

        width = source_ref.get("width")
        height = source_ref.get("height")
        if width and height:
            return int(width), int(height)

        if isinstance(size, (list, tuple)) and len(size) >= 2 and size[0] and size[1]:
            return int(size[0]), int(size[1])

        return None

    def _is_aspect_ratio_compatible(
        self,
        first_size: Optional[Tuple[int, int]],
        second_size: Optional[Tuple[int, int]],
    ) -> bool:
        if not first_size or not second_size:
            return False

        first_ratio = self._aspect_ratio(first_size)
        second_ratio = self._aspect_ratio(second_size)
        if first_ratio is None or second_ratio is None:
            return False

        ratio_factor = max(first_ratio, second_ratio) / min(first_ratio, second_ratio)
        return ratio_factor <= self.MAX_ASPECT_RATIO_FACTOR

    def _aspect_ratio(self, size: Tuple[int, int]) -> Optional[float]:
        width, height = size
        if width <= 0 or height <= 0:
            return None
        return width / height

    def _format_size(self, size: Optional[Tuple[int, int]]) -> str:
        if not size:
            return "unknown"
        return f"{size[0]}x{size[1]}"
    
    def _parse_duration_seconds(self, duration: Any) -> float:
        if isinstance(duration, (int, float)):
            return float(duration)
        if isinstance(duration, str):
            normalized = duration.strip().lower()
            if normalized.endswith("s"):
                normalized = normalized[:-1]
            try:
                return float(normalized)
            except ValueError:
                return 0.0
        return 0.0

    def _transition_payload_duration_seconds(self, transition_payload: Dict[str, Any]) -> float:
        source_ref = transition_payload.get("source_ref") or {}
        duration_ms = source_ref.get("duration_ms", 0)
        try:
            return max(0.0, float(duration_ms) / self.SECOND_TO_MILLISECOND)
        except (TypeError, ValueError):
            return 0.0
    
    def _load_clip(
        self,
        image_or_video_path: Union[str, Path]
    ) -> List[Image.Image]:
        """
        Loads media frames using PIL for images and MoviePy 2.2.1 for videos.
        
        Args:
            image_or_video_path: Path to the media file.
            
        Returns:
            List[Image.Image]: A list of frames as PIL Image objects in RGB mode.
        """
        path = Path(image_or_video_path)
        if not path.exists():
            raise FileNotFoundError(f"Media file not found: {path}")

        ext = path.suffix.lower()
        frames: List[Image.Image] = []

        # --- Process Images ---
        if ext in self.IMAGE_EXTS:
            try:
                # Open with PIL and force RGB mode
                with Image.open(path) as img:
                    frames.append(ImageOps.exif_transpose(img).convert("RGB"))
            except Exception as e:
                raise RuntimeError(f"Failed to load image via PIL: {path}. Error: {e}")

        # --- Process Videos ---
        elif ext in self.VIDEO_EXTS:
            try:
                # VideoFileClip in v2.x works best within a context manager
                with VideoFileClip(str(path)) as clip:
                    # iter_frames yields RGB numpy arrays by default
                    for frame_array in clip.iter_frames():
                        # Image.fromarray converts the numpy array (RGB) to a PIL object
                        frames.append(Image.fromarray(frame_array))
            except Exception as e:
                raise RuntimeError(f"MoviePy failed to decode video: {path}. Error: {e}")

            if not frames:
                raise RuntimeError(f"Extraction resulted in an empty frame list for: {path}")

        else:
            raise ValueError(f"File extension {ext} is not supported by this processor.")

        return frames

    def _preprocess_first_last_frame(
        self,
        first_frame: Image.Image,
        last_frame: Image.Image,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None
    ) -> Tuple[Image.Image, Image.Image, Dict[str, Any], Dict[str, Any]]:
        """
        Normalizes color modes and aligns both frames to a target resolution.
        
        Args:
            first_frame (Image.Image): The starting frame.
            last_frame (Image.Image): The ending frame.
            target_width (Optional[int]): Desired output width. Defaults to first_frame width.
            target_height (Optional[int]): Desired output height. Defaults to first_frame height.
            
        Returns:
            Tuple[Image.Image, Image.Image, Dict, Dict]: 
                (Aligned First Frame, Aligned Last Frame, First Frame Meta, Last Frame Meta)
        """

        # 1. Determine Target Resolution
        # Use provided dimensions or fallback to the first frame's original size
        if target_width and target_height:
            target_size = (target_width, target_height)
        else:
            target_size = first_frame.size

        # 2. Color Mode Normalization (RGB)
        # Required for API compatibility (removes Alpha/transparency channels)
        def normalize_img(img: Image.Image) -> Image.Image:
            return img.convert("RGB") if img.mode != "RGB" else img

        first_frame = normalize_img(first_frame)
        last_frame = normalize_img(last_frame)

        # 3. Helper for Resizing and Metadata Logging
        def process_frame(img: Image.Image, role: str) -> Tuple[Image.Image, Dict[str, Any]]:
            meta = {
                "role": role,
                "original_size": img.size,
                "target_size": target_size,
                "transformations": []
            }
            
            if img.size != target_size:
                src_w, src_h = img.size
                dst_w, dst_h = target_size
                scale = max(dst_w / src_w, dst_h / src_h)
                resized_size = (
                    max(1, int(round(src_w * scale))),
                    max(1, int(round(src_h * scale))),
                )

                resized_img = img.resize(resized_size, Image.Resampling.LANCZOS)
                crop_box = (
                    max(0, (resized_size[0] - dst_w) // 2),
                    max(0, (resized_size[1] - dst_h) // 2),
                    max(0, (resized_size[0] - dst_w) // 2) + dst_w,
                    max(0, (resized_size[1] - dst_h) // 2) + dst_h,
                )
                img = resized_img.crop(crop_box)

                meta["transformations"].append({
                    "type": "resize_with_center_crop",
                    "method": "LANCZOS",
                    "resized_size": resized_size,
                    "target": target_size,
                    "crop_box": crop_box,
                })
            else:
                meta["transformations"].append({"type": "none", "reason": "already_correct_size"})
                
            return img, meta

        # 4. Execute Processing for both frames
        aligned_first_frame, first_frame_meta = process_frame(first_frame, "first")
        aligned_last_frame, last_frame_meta = process_frame(last_frame, "last")

        # Final pixel-perfect validation
        assert aligned_first_frame.size == aligned_last_frame.size == target_size

        return aligned_first_frame, aligned_last_frame, first_frame_meta, last_frame_meta

    def _generate_video(
        self,
        provider,
        api_key,
        model_name,
        prompt,
        first_frame_data_url,
        last_frame_data_url,
        output_dir,
        duration=None,
        resolution=None,
        cancel_checker=None,
    ) -> Tuple[str, Dict[str, Any], int]:
        client = VisionClientFactory.create(
            provider=provider,
            api_key=api_key,
            cancel_checker=cancel_checker,
        )
        effective_duration = int(duration) if duration is not None else int(client.duration)
        
        gen_video_path, response = client.generate(
            task_type="video_generation",
            model=model_name,
            prompt=prompt,
            first_frame=first_frame_data_url,
            last_frame=last_frame_data_url,
            resolution=resolution,
            duration=duration,
            prompt_optimizer=True,
            output_dir=output_dir,
        )
        
        return gen_video_path, response, effective_duration
