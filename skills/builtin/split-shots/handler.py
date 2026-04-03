"""Split Shots skill handler — segment video by shot boundary detection."""

from __future__ import annotations

import functools
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from open_storyline.core.skill import SkillContext, SkillHandler, SkillResult
from open_storyline.utils.ffmpeg_utils import (
    VideoSegment,
    read_video_frames_as_rgb24,
    resolve_ffmpeg_executable,
    segment_video_stream_copy_with_ffmpeg,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_CACHE_MAXSIZE = 4

TRANSNETV2_INPUT_HEIGHT = 27
TRANSNETV2_INPUT_WIDTH = 48

DEFAULT_SCENE_FPS = 25
DEFAULT_SCENE_THRESHOLD = 0.5
DEFAULT_SPLIT_GAP_SECONDS = 1e-3

DEFAULT_MIN_SHOT_MS = 1000
DEFAULT_MAX_SHOT_MS = 30000

CLIP_ID_WIDTH = 4
MS_PER_SEC = 1000.0

COPY_VIDEO_WHEN_NO_SPLIT = False


# ---------------------------------------------------------------------------
# TransNetV2 model loading (LRU-cached for service mode)
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=MODEL_CACHE_MAXSIZE)
def _load_transnetv2(weight_path: str, device: str = "auto"):
    import torch
    from transnetv2_pytorch import TransNetV2

    model = TransNetV2(device=device)
    model.eval()
    state_dict = torch.load(weight_path, map_location=model.device)
    model.load_state_dict(state_dict)
    return model


# ---------------------------------------------------------------------------
# Scene detection
# ---------------------------------------------------------------------------

def _detect_scenes(
    model: Any,
    video_path: Path,
    ffmpeg_exe: str,
    *,
    fps: int = DEFAULT_SCENE_FPS,
    threshold: float = DEFAULT_SCENE_THRESHOLD,
) -> List[Dict[str, Any]]:
    """Run TransNetV2 on video frames and return scene dicts."""
    import torch

    frames = read_video_frames_as_rgb24(
        video_path,
        ffmpeg_exe,
        frames_per_second=fps,
        target_width=TRANSNETV2_INPUT_WIDTH,
        target_height=TRANSNETV2_INPUT_HEIGHT,
    )
    if frames.size == 0 or frames.shape[0] == 0:
        return []

    tensor = torch.from_numpy(frames).unsqueeze(0).contiguous()
    dev = getattr(model, "device", None)
    if dev is not None:
        tensor = tensor.to(dev, non_blocking=True)

    with torch.inference_mode():
        pred, _ = model.predict_raw(tensor)

    pred = np.squeeze(pred.detach().cpu().numpy())
    if pred.ndim != 1:
        pred = pred.reshape(-1)

    return model.predictions_to_scenes_with_data(
        pred, fps=float(fps), threshold=float(threshold)
    )


def _scenes_to_split_points(
    scenes: List[Dict[str, Any]],
    *,
    min_gap: float = DEFAULT_SPLIT_GAP_SECONDS,
) -> List[float]:
    """Convert TransNetV2 scenes to split points (seconds)."""
    ends: List[float] = []
    last = 0.0
    for s in scenes:
        try:
            t = float(s.get("end_time", 0.0))
        except Exception:
            continue
        if t > last + min_gap:
            ends.append(t)
            last = t
    if len(ends) <= 1:
        return []
    return ends[:-1]


# ---------------------------------------------------------------------------
# Duration constraints
# ---------------------------------------------------------------------------

def _enforce_duration_constraints(
    split_pts: List[float],
    *,
    total_ms: int,
    min_ms: Optional[int],
    max_ms: Optional[int],
) -> List[float]:
    """Merge short segments and split long segments."""

    def _norm(v: Optional[int]) -> Optional[int]:
        if v is None or v == 0:
            return None
        return int(v)

    min_ms = _norm(min_ms)
    max_ms = _norm(max_ms)

    if min_ms and max_ms and min_ms > max_ms:
        raise ValueError(f"min ({min_ms}) > max ({max_ms})")

    cuts = sorted(
        {int(round(p * MS_PER_SEC)) for p in split_pts}
        - {c for c in [0] if True}  # ensure 0 not in set
    )
    cuts = [c for c in cuts if 0 < c < total_ms]

    # Merge short
    if min_ms and cuts:
        merged: List[int] = []
        start = 0
        for c in cuts:
            if c - start >= min_ms:
                merged.append(c)
                start = c
        if merged and total_ms - merged[-1] < min_ms:
            merged.pop()
        cuts = merged

    # Split long
    if max_ms and max_ms > 0:
        extras: set[int] = set(cuts)
        bounds = [0] + cuts + [total_ms]
        for a, b in zip(bounds[:-1], bounds[1:]):
            seg = b - a
            if seg <= max_ms:
                continue
            pieces = math.ceil(seg / max_ms)
            if pieces <= 1:
                continue
            base = seg // pieces
            rem = seg % pieces
            cur = a
            for i in range(pieces - 1):
                cur += base + (1 if i < rem else 0)
                if a < cur < b:
                    extras.add(cur)
        cuts = sorted(c for c in extras if 0 < c < total_ms)

    return [c / MS_PER_SEC for c in cuts]


# ---------------------------------------------------------------------------
# Clip builders
# ---------------------------------------------------------------------------

def _clip_id(idx: int) -> str:
    return f"clip_{idx:0{CLIP_ID_WIDTH}d}"


def _image_clip(item: dict, idx: int) -> dict:
    return {
        "clip_id": _clip_id(idx),
        "kind": "image",
        "path": item.get("path", ""),
        "source_ref": {
            "media_id": item["media_id"],
            "height": item.get("metadata", {}).get("height"),
            "width": item.get("metadata", {}).get("width"),
        },
    }


def _whole_video_clip(item: dict, idx: int, dur_ms: int) -> dict:
    meta = item.get("metadata", {})
    return {
        "clip_id": _clip_id(idx),
        "kind": "video",
        "path": item["path"],
        "fps": meta.get("fps"),
        "source_ref": {
            "media_id": item["media_id"],
            "start": 0,
            "end": dur_ms,
            "duration": dur_ms,
            "height": meta.get("height"),
            "width": meta.get("width"),
        },
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class Handler(SkillHandler):
    """Segment video using TransNetV2 shot boundary detection + ffmpeg stream copy."""

    _model: Any = None
    _ffmpeg: str = ""

    def _ensure_model(self, ctx: SkillContext) -> None:
        """Lazy-load model on first call, reuse across invocations."""
        if self._model is not None:
            return
        cfg = ctx.config
        self._model = _load_transnetv2(
            str(cfg.split_shots.transnet_weights),
            device=cfg.split_shots.transnet_device,
        )
        self._ffmpeg = resolve_ffmpeg_executable()

    @staticmethod
    def _parse_upstream(val):
        if isinstance(val, str):
            import json as _json
            try:
                return _json.loads(val)
            except (ValueError, TypeError):
                return {}
        return val if isinstance(val, dict) else {}

    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        self._ensure_model(ctx)

        load_data = self._parse_upstream(inputs.get("load_media") or {})
        media = load_data.get("media", []) or []
        min_dur = inputs.get("min_shot_duration", DEFAULT_MIN_SHOT_MS)
        max_dur = inputs.get("max_shot_duration", DEFAULT_MAX_SHOT_MS)

        # Clamp to safe range
        if min_dur > max_dur:
            min_dur, max_dur = DEFAULT_MIN_SHOT_MS, DEFAULT_MAX_SHOT_MS
        min_dur = max(min_dur, DEFAULT_MIN_SHOT_MS)
        max_dur = min(max_dur, DEFAULT_MAX_SHOT_MS)

        # Output directory for segmented clips
        out_dir = (
            Path(ctx.config.local_mcp_server.server_cache_dir)
            / ctx.session_id
            / ctx.execution_id
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        clips: list[dict] = []
        ci = 1  # clip index

        for item in media:
            mtype = item.get("media_type", "")
            mid = item.get("media_id", "")

            # --- image: pass through ---
            if mtype == "image":
                clips.append(_image_clip(item, ci))
                ci += 1
                continue

            if mtype != "video":
                await ctx.log("warning", f"Skipping unsupported type: {mtype}")
                continue

            meta = item.get("metadata", {})
            dur_ms = int(meta.get("duration", 0))
            vpath = Path(item["path"]).expanduser()

            # --- too short to split ---
            if dur_ms < min_dur:
                clips.append(_whole_video_clip(item, ci, dur_ms))
                ci += 1
                continue

            # --- scene detection ---
            scenes = _detect_scenes(self._model, vpath, self._ffmpeg)
            pts = _scenes_to_split_points(scenes)
            pts = _enforce_duration_constraints(
                pts, total_ms=dur_ms, min_ms=min_dur, max_ms=max_dur
            )

            # --- no split needed ---
            if not pts and not COPY_VIDEO_WHEN_NO_SPLIT:
                clips.append(_whole_video_clip(item, ci, dur_ms))
                ci += 1
                continue

            # --- ffmpeg segment ---
            segments = segment_video_stream_copy_with_ffmpeg(
                input_video=vpath,
                ffmpeg_executable=self._ffmpeg,
                split_points_seconds=pts,
                output_directory=out_dir,
                filename_prefix="clip",
                start_index=ci,
            )

            for seg in segments:
                cid = _clip_id(ci)

                if seg.end_seconds < 0:
                    s_ms, e_ms = 0, dur_ms
                else:
                    s_ms = max(0, int(round(seg.start_seconds * MS_PER_SEC)))
                    e_ms = max(s_ms, int(round(seg.end_seconds * MS_PER_SEC)))

                seg_dur = e_ms - s_ms
                if seg_dur <= 0:
                    continue

                clips.append(
                    {
                        "clip_id": cid,
                        "kind": "video",
                        "path": str(Path(seg.path).resolve()),
                        "fps": meta.get("fps"),
                        "source_ref": {
                            "media_id": mid,
                            "start": s_ms,
                            "end": e_ms,
                            "duration": seg_dur,
                            "height": meta.get("height"),
                            "width": meta.get("width"),
                        },
                    }
                )
                ci += 1

        preview_urls = [c["path"] for c in clips if c.get("path")]
        await ctx.log("info", f"Split completed: {len(clips)} clip(s)")
        return SkillResult(success=True, data={"clips": clips}, preview_urls=preview_urls)

    async def default_execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        """Skip splitting — pass through media as single clips."""
        load_data = self._parse_upstream(inputs.get("load_media") or {})
        media = load_data.get("media", []) or []
        clips: list[dict] = []
        ci = 1

        for item in media:
            mtype = item.get("media_type", "")
            if mtype == "image":
                clips.append(_image_clip(item, ci))
            elif mtype == "video":
                dur_ms = int(item.get("metadata", {}).get("duration", 0))
                clips.append(_whole_video_clip(item, ci, dur_ms))
            ci += 1

        await ctx.log("info", "Shot splitting skipped")
        return SkillResult(success=True, data={"clips": clips})
