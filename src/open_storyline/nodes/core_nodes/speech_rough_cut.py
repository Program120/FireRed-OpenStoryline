from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
import asyncio
import json
from open_storyline.nodes.core_nodes.base_node import BaseNode, NodeMeta
from open_storyline.nodes.node_state import NodeState
from open_storyline.nodes.node_schema import SpeechRoughCutInput
from open_storyline.utils.prompts import get_prompt
from open_storyline.utils.parse_json import parse_json_dict
from open_storyline.utils.ffmpeg_utils import (
    resolve_ffmpeg_executable,
    cut_video_segment_with_ffmpeg,
    VideoSegment,
)
from open_storyline.utils.register import NODE_REGISTRY


async def _emit_log(mcp_ctx, level: str, message: str, detail: str = ""):
    """
    Send a structured log entry through MCP report_progress channel.
    Uses a JSON-encoded message with a __log__ marker so the frontend
    can distinguish log entries from regular progress updates.
    """
    try:
        log_payload = json.dumps({
            "__log__": True,
            "level": level,
            "message": message,
            "detail": detail[:6000] if detail else "",
        }, ensure_ascii=False)
        await mcp_ctx.report_progress(0, 0, log_payload)
    except Exception:
        pass

CLIP_ID_NUMBER_WIDTH = 4
MILLISECONDS_PER_SECOND = 1000.0
DEFAULT_BUFFER_MS = 0  # buffer in milliseconds for safe cut

@NODE_REGISTRY.register()
class SpeechRoughCutNode(BaseNode):

    meta = NodeMeta(
        name="speech_rough_cut",
        description="Perform rough cut on speech clips based on ASR results",
        node_id="speech_rough_cut",
        node_kind="speech_rough_cut",
        require_prior_kind=['asr', 'speech_rough_cut'],
        default_require_prior_kind=['asr'],
        next_available_node=[],
    )

    input_schema = SpeechRoughCutInput

    def __init__(self, server_cfg):
        super().__init__(server_cfg)
        self.ffmpeg_executable = resolve_ffmpeg_executable()

    async def default_process(self, node_state, inputs: Dict[str, Any]) -> Any:
        return {}

    async def process(self, node_state: NodeState, inputs: Dict[str, Any]) -> Any:
        """
        Main processing function:
        - Calls LLM to get rough cut suggestions
        - Groups sentences by gap threshold
        - Adds buffer and computes cut points
        - Splits video with ffmpeg
        - Calibrates ASR timestamps after deleted segments
        - Returns final clip metadata and updated ASR json
        """
        asr_infos = inputs["asr"].get('asr_infos', [])
        history_rough_cut_jsons = inputs.get('speech_rough_cut', {}).get('rough_cut_jsons', [])
        history_rough_cut_jsons = [[{'text': item.get('text', '')} for item in sublist] for sublist in history_rough_cut_jsons]
        user_request = inputs.get('user_request', {})
        gap_threshold = inputs.get('gap_threshold', 400)
        output_directory = self._prepare_output_directory(node_state, inputs)
        llm = node_state.llm
        rough_cut_jsons, clips = [], []

        # Load system prompt for rough cut
        system_prompt = get_prompt("speech_rough_cut.system", lang=node_state.lang)

        for asr_info in asr_infos:
            video_path = asr_info.get('path')
            source_ref = asr_info.get('source_ref', {})
            fps = asr_info.get('fps', 30)

            rough_cut_json = []
            asr_sentence_info = asr_info.get("asr_sentence_info", [])
            total_sentences = len(asr_sentence_info)

            # ── V1 optimization: two-phase approach ──
            # Phase 1: One LLM call to batch-screen all sentences (which need editing?)
            # Phase 2: Only send flagged sentences for precise timestamp editing
            #
            # This reduces 59 LLM calls to ~1 + N (where N << 59)

            try:
                await node_state.mcp_ctx.report_progress(0, 3, f"预筛选 {total_sentences} 句...")
            except Exception:
                pass

            # Phase 1: Batch screening
            sentences_summary = []
            for i, s in enumerate(asr_sentence_info):
                sentences_summary.append(f"[{i}] \"{s.get('text', '')}\"")
            sentences_block = "\n".join(sentences_summary)

            screen_prompt = (
                f"以下是一段视频的 ASR 识别结果，共 {total_sentences} 句。\n"
                f"用户要求: {user_request}\n\n"
                f"{sentences_block}\n\n"
                f"请判断哪些句子需要编辑处理（包含脏话、口水词、无意义语气词、重复内容等）。\n"
                f"只输出需要处理的句子编号列表，JSON格式: {{\"flagged\": [0, 3, 7, ...]}}\n"
                f"如果所有句子都正常不需要处理，输出: {{\"flagged\": []}}\n"
                f"只输出JSON，不要其他内容。"
            )

            flagged_indices = set()
            screen_system = "你是一个视频语音内容审核助手。快速判断哪些句子需要清洗处理。"

            # Log: show all sentences to user
            await _emit_log(node_state.mcp_ctx, "info", f"📝 全部 {total_sentences} 句 ASR 识别结果", sentences_block)
            await _emit_log(node_state.mcp_ctx, "info", f"🤖 发送给模型进行预筛选",
                          f"【System Prompt】\n{screen_system}\n\n【User Prompt】\n{screen_prompt}")
            try:
                raw_screen = await llm.complete(
                    system_prompt=screen_system,
                    user_prompt=screen_prompt,
                    media=None,
                    temperature=0.1,
                    top_p=0.9,
                    max_tokens=2048,
                    model_preferences=None,
                )
                screen_result = parse_json_dict(raw_screen)
                flagged_indices = set(screen_result.get("flagged", []))

                # Log: show model response
                flagged_texts = [f"  [{i}] \"{asr_sentence_info[i].get('text', '')}\"" for i in sorted(flagged_indices) if i < total_sentences]
                await _emit_log(node_state.mcp_ctx, "info",
                    f"✅ 预筛选完成: {len(flagged_indices)}/{total_sentences} 句需处理",
                    f"【模型原始输出】\n{raw_screen}\n\n【需处理的句子】\n" + "\n".join(flagged_texts))
            except Exception as e:
                node_state.node_summary.add_warning(f"Batch screening failed: {e}, processing all sentences")
                flagged_indices = set(range(total_sentences))
                await _emit_log(node_state.mcp_ctx, "warn", f"⚠️ 预筛选失败，降级为全量处理", str(e))

            try:
                await node_state.mcp_ctx.report_progress(1, 3, f"预筛选完成: {len(flagged_indices)}/{total_sentences} 句需处理")
            except Exception:
                pass

            # Phase 2: Precise editing only for flagged sentences
            if flagged_indices:
                flagged_count = len(flagged_indices)
                processed = 0
                for i, sentence in enumerate(asr_sentence_info):
                    if i in flagged_indices:
                        sent_text = sentence.get("text", "")

                        user_prompt = get_prompt(
                            "speech_rough_cut.user",
                            lang=node_state.lang,
                            curr_asr_sentence_info=json.dumps(sentence),
                            asr_text=asr_info.get("asr_text", ''),
                            history_rough_cut_jsons=json.dumps(history_rough_cut_jsons),
                            user_request=user_request,
                            pre_ctx=asr_sentence_info[i-1]["text"] if i > 0 else '',
                            nxt_ctx=asr_sentence_info[i+1]["text"] if i < total_sentences - 1 else '',
                        )
                        # Log: show input to model
                        await _emit_log(node_state.mcp_ctx, "info",
                            f"🔍 精细处理 句 {i}: \"{sent_text}\"",
                            f"【发送给模型】\nSystem: {system_prompt[:200]}...\n\nUser:\n{user_prompt}")

                        try:
                            raw = await llm.complete(
                                system_prompt=system_prompt,
                                user_prompt=user_prompt,
                                media=None,
                                temperature=0.1,
                                top_p=0.9,
                                max_tokens=8092,
                                model_preferences=None,
                            )
                            parsed_json = parse_json_dict(raw)
                            res = parsed_json.get('res', [])
                            rough_cut_json += res
                            action = "✂️ 删除" if not res else f"✅ 保留 ({len(res)} 段)"
                            await _emit_log(node_state.mcp_ctx, "info",
                                f"句 {i} → {action}",
                                f"【模型原始输出】\n{raw}\n\n【解析结果】\n原因: {parsed_json.get('reason', '无')}\n结果: {json.dumps(res, ensure_ascii=False)}")
                        except Exception as e:
                            node_state.node_summary.add_warning(f"LLM rough cut failed for sentence {i}: {e}")
                            rough_cut_json.append({"text": sentence.get("text", ""), "start": sentence.get("start", 0), "end": sentence.get("end", 0)})
                            await _emit_log(node_state.mcp_ctx, "error", f"❌ 句 {i} LLM 调用失败，保留原句", str(e))
                        processed += 1
                        try:
                            await node_state.mcp_ctx.report_progress(
                                1 + processed, flagged_count + 2,
                                f"精细处理 {processed}/{flagged_count} 句"
                            )
                        except Exception:
                            pass
                    else:
                        # Not flagged → keep original sentence as-is
                        rough_cut_json.append({
                            "text": sentence.get("text", ""),
                            "start": sentence.get("start", 0),
                            "end": sentence.get("end", 0),
                        })

            else:
                # Nothing flagged → keep all sentences as-is
                for sentence in asr_sentence_info:
                    rough_cut_json.append({
                        "text": sentence.get("text", ""),
                        "start": sentence.get("start", 0),
                        "end": sentence.get("end", 0),
                    })

            # Report FFmpeg cutting phase
            try:
                await node_state.mcp_ctx.report_progress(2, 3, "FFmpeg 切割视频中...")
            except Exception:
                pass

            # Group sentences based on gap threshold
            segments_groups = self.group_sentences(rough_cut_json, gap_threshold=gap_threshold)

            # Convert grouped sentences into ranges
            ranges = self.segments_to_ranges(segments_groups)

            # Group sentences into segments and compute cut points
            filtered_segments = []
            for clip_index, item in enumerate(ranges):
                segment = cut_video_segment_with_ffmpeg(
                    video_path=video_path,
                    start=item["start"] / 1000,
                    end=item["end"] / 1000,
                    output_path=output_directory / f"speech_rough_cut_{clip_index:0{CLIP_ID_NUMBER_WIDTH}d}.mp4",
                    ffmpeg_executable=self.ffmpeg_executable
                )
                filtered_segments.append(segment)

            # Compute deleted ranges and recalibrate ASR timestamps
            deleted_ranges = self.compute_deleted_ranges(filtered_segments)
            rough_cut_json = self.calibrate_asr_times(rough_cut_json, deleted_ranges)
            rough_cut_jsons.append(rough_cut_json)

            # Report completion
            try:
                await node_state.mcp_ctx.report_progress(
                    total_sentences + 2, total_sentences + 2, "粗剪完成"
                )
            except Exception:
                pass

            # Generate final clip metadata
            clip_index = 0
            for segment in filtered_segments:
                clip_id = self._format_clip_id(clip_index)
                start_ms = max(0, int(round(segment.start_seconds * MILLISECONDS_PER_SECOND)))
                end_ms = max(start_ms, int(round(segment.end_seconds * MILLISECONDS_PER_SECOND)))
                duration_ms = max(0, end_ms - start_ms)
                if duration_ms <= 0:
                    continue

                clips.append({
                    "clip_id": clip_id,
                    "kind": "video",
                    "path": str(segment.path),
                    "fps": fps,
                    "source_ref": {
                        "media_id": source_ref.get("media_id"),
                        "start": start_ms,
                        "end": end_ms,
                        "duration": duration_ms,
                        "height": source_ref.get("height"),
                        "width": source_ref.get("width"),
                    },
                })
                node_state.node_summary.info_for_user(f"{clip_id} split successfully", preview_urls=[str(segment.path)])
                clip_index += 1

        return {"clips": clips, "rough_cut_jsons": rough_cut_jsons}

    # --------------------- Sentence Grouping ---------------------
    def group_sentences(self, items, gap_threshold: int = 400):
        """Group sentences into segments by gap threshold (ms)."""
        segments = []
        if not items:
            return segments
        current = [items[0]]
        for i in range(len(items) - 1):
            cur = items[i]
            nxt = items[i + 1]
            gap = nxt["start"] - cur["end"]
            if gap > gap_threshold:
                segments.append(current)
                current = [nxt]
            else:
                current.append(nxt)
        if current:
            segments.append(current)
        return segments

    def segments_to_ranges(self, segments):
        """Convert grouped sentence segments to start/end ranges."""
        return [{"start": seg[0]["start"], "end": seg[-1]["end"]} for seg in segments]

    def ranges_to_cut_points(self, ranges, buffer_ms=100):
        """
        Convert ranges to ffmpeg cut points.
        Adds buffer for safe cuts and prevents overlap.
        """
        cuts = []
        for i in range(len(ranges) - 1):
            end_cut = ranges[i]["end"] + buffer_ms
            start_cut = ranges[i + 1]["start"] - buffer_ms
            # Prevent overlap
            if start_cut < end_cut:
                mid = (start_cut + end_cut) // 2
                end_cut = mid
                start_cut = mid
            cuts.append(end_cut)
            cuts.append(start_cut)
        cuts = [max(ranges[0]["start"] - buffer_ms, 0)] + cuts + [ranges[-1]["end"] + buffer_ms]
        return cuts

    # --------------------- Time Calibration ---------------------
    def compute_deleted_ranges(self, segments):
        """Compute time ranges that were deleted (gaps between segments)."""
        deleted = []
        prev_end = 0
        for seg in segments:
            start_ms = int(seg.start_seconds * 1000)
            end_ms = int(seg.end_seconds * 1000)
            if start_ms > prev_end:
                deleted.append({"start": prev_end, "end": start_ms})
            prev_end = end_ms
        return deleted

    def calibrate_asr_times(self, rough_cut_json, deleted_ranges):
        """
        Adjust ASR timestamps after deleted ranges.
        New time = original time - total deleted duration before it.
        """
        if not deleted_ranges:
            return rough_cut_json

        # Build prefix sum of deleted durations
        prefix = []
        total = 0
        for r in deleted_ranges:
            prefix.append((r["start"], r["end"], total))
            total += r["end"] - r["start"]
        
        def remap_time(t):
            for start, end, deleted_before in prefix:
                if t < start:
                    return t - deleted_before
                if start <= t <= end:
                    return start - deleted_before  # timestamp falls in deleted segment
            return t - prefix[-1][2]

        new_json = []
        for item in rough_cut_json:
            new_start = remap_time(item["start"])
            new_end = remap_time(item["end"])
            if new_start is None or new_end is None:
                continue
            item["start"] = int(new_start)
            item["end"] = int(new_end)
            new_json.append(item)
        return new_json

    # --------------------- Helpers ---------------------
    def _prepare_output_directory(self, node_state: NodeState, inputs: Dict[str, Any]) -> Path:
        """Create output directory for clips."""
        artifact_id = node_state.artifact_id
        session_id = node_state.session_id
        output_directory = self.server_cache_dir / session_id / artifact_id
        output_directory.mkdir(parents=True, exist_ok=True)
        return output_directory

    def _format_clip_id(self, clip_index: int) -> str:
        """Generate zero-padded clip ID."""
        return f"clip_{clip_index:0{CLIP_ID_NUMBER_WIDTH}d}"