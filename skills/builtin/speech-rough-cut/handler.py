"""Speech Rough Cut skill handler — LLM-driven speech cleaning and rough editing."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from open_storyline.core.skill import SkillContext, SkillHandler, SkillResult
from open_storyline.utils.ffmpeg_utils import (
    cut_video_segment_with_ffmpeg,
    resolve_ffmpeg_executable,
)
from open_storyline.utils.parse_json import parse_json_dict
from open_storyline.utils.prompts import get_prompt

logger = logging.getLogger(__name__)

CLIP_ID_WIDTH = 4
MS_PER_SEC = 1000.0


def _ensure_dict(val: Any) -> dict:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return val if isinstance(val, dict) else {}


def _ensure_list(val: Any) -> list:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []
    return val if isinstance(val, list) else []


class Handler(SkillHandler):
    """Perform LLM-driven speech rough cut based on ASR results."""

    _ffmpeg: str = ""

    def _ensure_ffmpeg(self) -> None:
        if not self._ffmpeg:
            self._ffmpeg = resolve_ffmpeg_executable()

    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        self._ensure_ffmpeg()

        asr_data = _ensure_dict(inputs.get("asr") or {})
        asr_infos = asr_data.get("asr_infos", [])
        if isinstance(asr_infos, str):
            asr_infos = _ensure_list(asr_infos)

        history_jsons = _ensure_dict(inputs.get("speech_rough_cut") or {}).get("rough_cut_jsons", [])
        history_jsons = [[{"text": item.get("text", "")} for item in sub] for sub in history_jsons]
        user_request = inputs.get("user_request", "")
        gap_threshold = inputs.get("gap_threshold", 400)

        out_dir = Path(ctx.config.local_mcp_server.server_cache_dir) / ctx.session_id / ctx.execution_id
        out_dir.mkdir(parents=True, exist_ok=True)

        rough_cut_jsons: list[list] = []
        clips: list[dict] = []
        system_prompt = get_prompt("speech_rough_cut.system", lang=ctx.lang)

        for asr_info in asr_infos:
            video_path = asr_info.get("path")
            source_ref = asr_info.get("source_ref", {})
            fps = asr_info.get("fps", 30)
            sentences = asr_info.get("asr_sentence_info", [])
            total = len(sentences)

            if total == 0:
                rough_cut_jsons.append([])
                continue

            # Phase 1: batch screening
            flagged = await self._phase1_screen(ctx, sentences, total, user_request)

            # Phase 2: precise editing (returns sentences + mute_ranges)
            rough_cut_json, mute_ranges = await self._phase2_edit(
                ctx, sentences, flagged, total,
                asr_info, history_jsons, user_request, system_prompt,
            )

            # FFmpeg cutting + muting
            await ctx.log("info", "FFmpeg 切割视频中...")

            groups = _group_sentences(rough_cut_json, gap_threshold)
            ranges = [{"start": g[0]["start"], "end": g[-1]["end"]} for g in groups]

            # Extend last range to cover trailing content
            video_end_ms = source_ref.get("duration") or source_ref.get("end", 0)
            if ranges and video_end_ms > 0 and video_end_ms - ranges[-1]["end"] < 2000:
                ranges[-1]["end"] = video_end_ms

            segments = []
            for ci, rng in enumerate(ranges):
                # Find mute ranges that fall within this cut range
                local_mutes = [
                    m for m in mute_ranges
                    if m["start"] >= rng["start"] and m["end"] <= rng["end"]
                ]
                out_path = out_dir / f"speech_rough_cut_{ci:0{CLIP_ID_WIDTH}d}.mp4"

                if local_mutes:
                    # Cut with audio muting
                    seg = _cut_with_mute(
                        video_path=video_path,
                        start_ms=rng["start"],
                        end_ms=rng["end"],
                        mute_ranges=local_mutes,
                        output_path=out_path,
                        ffmpeg_executable=self._ffmpeg,
                    )
                    await ctx.log("info", f"Clip {ci}: cut [{rng['start']}-{rng['end']}] + mute {len(local_mutes)} range(s)")
                else:
                    seg = cut_video_segment_with_ffmpeg(
                        video_path=video_path,
                        start=rng["start"] / 1000,
                        end=rng["end"] / 1000,
                        output_path=out_path,
                        ffmpeg_executable=self._ffmpeg,
                    )
                segments.append(seg)

            # Calibrate timestamps
            rough_cut_json = [s for s in rough_cut_json if not s.get("_force_break")]
            deleted = _compute_deleted_ranges(segments)
            rough_cut_json = _calibrate_times(rough_cut_json, deleted)
            rough_cut_jsons.append(rough_cut_json)

            # Build clip metadata
            ci = 0
            for seg in segments:
                clip_id = f"clip_{ci:0{CLIP_ID_WIDTH}d}"
                s_ms = max(0, int(round(seg.start_seconds * MS_PER_SEC)))
                e_ms = max(s_ms, int(round(seg.end_seconds * MS_PER_SEC)))
                dur = e_ms - s_ms
                if dur <= 0:
                    continue
                clips.append({
                    "clip_id": clip_id,
                    "kind": "video",
                    "path": str(Path(seg.path).resolve()),
                    "fps": fps,
                    "source_ref": {
                        "media_id": source_ref.get("media_id"),
                        "start": s_ms, "end": e_ms, "duration": dur,
                        "height": source_ref.get("height"),
                        "width": source_ref.get("width"),
                    },
                })
                ci += 1

            await ctx.log("info", f"粗剪完成: {len(clips)} clip(s), {len(mute_ranges)} mute range(s)")

        preview_urls = [c["path"] for c in clips if c.get("path")]
        return SkillResult(
            success=True,
            data={"clips": clips, "rough_cut_jsons": rough_cut_jsons},
            preview_urls=preview_urls,
        )

    # ------------------------------------------------------------------
    # Phase 1: batch screening
    # ------------------------------------------------------------------

    async def _phase1_screen(self, ctx, sentences, total, user_request) -> set[int]:
        lines = [f'[{i}] "{s.get("text", "")}"' for i, s in enumerate(sentences)]
        block = "\n".join(lines)
        prompt = (
            f"以下是一段视频的 ASR 识别结果，共 {total} 句。\n"
            f"用户要求: {user_request}\n\n{block}\n\n"
            f"请判断哪些句子需要编辑处理（包含脏话、口水词、无意义语气词、重复内容等）。\n"
            f'只输出JSON: {{"flagged": [0, 3, 7, ...]}}\n'
            f'如果都正常: {{"flagged": []}}'
        )
        await ctx.log("info", f"预筛选 {total} 句...", block)
        try:
            raw = await ctx.llm.complete(
                system_prompt="你是一个视频语音内容审核助手。快速判断哪些句子需要清洗处理。",
                user_prompt=prompt, media=None, temperature=0.1, top_p=0.9,
                max_tokens=2048, model_preferences=None,
            )
            result = parse_json_dict(raw)
            flagged = set(result.get("flagged", []))
            await ctx.log("info", f"预筛选完成: {len(flagged)}/{total} 句需处理", f"模型输出: {raw}")
            return flagged
        except Exception as e:
            await ctx.log("warning", f"预筛选失败，降级为全量处理: {e}")
            return set(range(total))

    # ------------------------------------------------------------------
    # Phase 2: precise editing with cut/mute/keep decisions
    # ------------------------------------------------------------------

    async def _phase2_edit(self, ctx, sentences, flagged, total,
                           asr_info, history_jsons, user_request, system_prompt):
        """Returns (rough_cut_json, mute_ranges)."""
        mute_ranges: list[dict] = []

        if not flagged:
            return (
                [{"text": s.get("text", ""), "start": s.get("start", 0), "end": s.get("end", 0)}
                 for s in sentences],
                mute_ranges,
            )

        results: list[list[dict] | None] = [None] * total
        for i, sentence in enumerate(sentences):
            if i not in flagged:
                results[i] = [{"text": sentence.get("text", ""),
                               "start": sentence.get("start", 0),
                               "end": sentence.get("end", 0)}]

        sem = asyncio.Semaphore(5)
        flagged_count = len(flagged)
        completed = {"n": 0}

        async def _process_one(i: int, sentence: dict) -> None:
            user_prompt = get_prompt(
                "speech_rough_cut.user", lang=ctx.lang,
                curr_asr_sentence_info=json.dumps(sentence, ensure_ascii=False),
                asr_text=asr_info.get("asr_text", ""),
                history_rough_cut_jsons=json.dumps(history_jsons, ensure_ascii=False),
                user_request=user_request,
                pre_ctx=sentences[i - 1]["text"] if i > 0 else "",
                nxt_ctx=sentences[i + 1]["text"] if i < total - 1 else "",
            )

            async with sem:
                try:
                    raw = await ctx.llm.complete(
                        system_prompt=system_prompt, user_prompt=user_prompt,
                        media=None, temperature=0.1, top_p=0.9,
                        max_tokens=8092, model_preferences=None,
                    )
                    parsed = parse_json_dict(raw)
                    action = parsed.get("action", "keep")
                    reason = parsed.get("reason", "")
                    mute_chars = parsed.get("mute_chars", [])
                    sent_text = sentence.get("text", "")

                    if action == "cut":
                        # Entire sentence deleted
                        results[i] = []
                        await ctx.log("info", f"句 {i} \"{sent_text[:30]}\" → 切除 | {reason}")

                    elif action == "mute":
                        # Keep sentence but mute specific chars
                        ranges = _find_mute_ranges(sentence, mute_chars)
                        mute_ranges.extend(ranges)
                        # Sentence stays with original timestamps
                        results[i] = [{"text": sent_text, "start": sentence.get("start", 0),
                                       "end": sentence.get("end", 0)}]
                        await ctx.log("info",
                            f"句 {i} \"{sent_text[:30]}\" → 静音 {mute_chars} | {reason}",
                            f"mute_ranges: {json.dumps(ranges, ensure_ascii=False)}")

                    else:
                        # keep
                        results[i] = [{"text": sent_text, "start": sentence.get("start", 0),
                                       "end": sentence.get("end", 0)}]
                        await ctx.log("info", f"句 {i} \"{sent_text[:30]}\" → 保留 | {reason}")

                except Exception as e:
                    await ctx.log("warning", f"LLM failed for sentence {i}: {type(e).__name__}: {e}")
                    results[i] = [{"text": sentence.get("text", ""),
                                   "start": sentence.get("start", 0),
                                   "end": sentence.get("end", 0)}]

                completed["n"] += 1
                await ctx.progress(completed["n"] / flagged_count, f"精细处理 {completed['n']}/{flagged_count}")

        tasks = [_process_one(i, sentences[i]) for i in sorted(flagged) if i < total]
        await asyncio.gather(*tasks)

        rough_cut_json: list[dict] = []
        for r in results:
            if r is not None:
                rough_cut_json.extend(r)

        return rough_cut_json, mute_ranges

    async def default_execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        return SkillResult(success=True, data={"clips": [], "rough_cut_jsons": []})


# ======================================================================
# Pure functions
# ======================================================================


def _find_mute_ranges(sentence: dict, mute_chars: list[str]) -> list[dict]:
    """Find precise time ranges for characters to mute using ASR timestamp array."""
    timestamps = sentence.get("timestamp", [])
    full_text = sentence.get("text", "")

    if not timestamps or not full_text or not mute_chars:
        return []

    # Build char→timestamp mapping (skip punctuation)
    char_ts: list[tuple[str, int, int]] = []  # (char, start_ms, end_ms)
    ts_idx = 0
    for ch in full_text:
        is_punct = bool(re.match(r'[，。！？、；：""''（）\s]', ch))
        if not is_punct and ts_idx < len(timestamps):
            char_ts.append((ch, timestamps[ts_idx][0], timestamps[ts_idx][1]))
            ts_idx += 1
        elif is_punct:
            char_ts.append((ch, -1, -1))  # punctuation placeholder

    ranges = []
    BUFFER_MS = 30  # small buffer to ensure complete muting

    for dirty_word in mute_chars:
        clean_dirty = re.sub(r'[，。！？、；：""''（）\s]', '', dirty_word)
        if not clean_dirty:
            continue

        # Find the dirty word in char_ts sequence
        clean_chars = [(i, ct) for i, ct in enumerate(char_ts) if ct[1] >= 0]
        clean_text = "".join(ct[0] for _, ct in clean_chars)

        pos = clean_text.find(clean_dirty)
        if pos < 0:
            continue

        # Get time range from first to last char of dirty word
        first_idx = clean_chars[pos][0]
        last_idx = clean_chars[pos + len(clean_dirty) - 1][0]
        start_ms = char_ts[first_idx][1] - BUFFER_MS
        end_ms = char_ts[last_idx][2] + BUFFER_MS

        ranges.append({"start": max(0, start_ms), "end": end_ms, "word": dirty_word})
        logger.info(f"[_find_mute_ranges] \"{dirty_word}\" -> [{start_ms}-{end_ms}]")

    return ranges


def _cut_with_mute(
    video_path: str,
    start_ms: int,
    end_ms: int,
    mute_ranges: list[dict],
    output_path: Path,
    ffmpeg_executable: str = "ffmpeg",
):
    """Cut a video segment and mute specific time ranges in the audio.

    Uses ffmpeg's volume filter to silence audio at precise timestamps
    while keeping the video intact.
    """
    from open_storyline.utils.ffmpeg_utils import VideoSegment

    output_path.parent.mkdir(parents=True, exist_ok=True)
    start_s = start_ms / 1000
    end_s = end_ms / 1000

    # Build volume filter: mute each dirty word range
    # volume=enable='between(t,start,end)':volume=0
    # Chain multiple mute ranges together
    filter_parts = []
    for mr in mute_ranges:
        # Convert absolute ms to seconds relative to the CUT start
        ms = max(0, mr["start"] - start_ms) / 1000
        me = (mr["end"] - start_ms) / 1000
        filter_parts.append(f"volume=enable='between(t,{ms:.3f},{me:.3f})':volume=0")

    audio_filter = ",".join(filter_parts) if filter_parts else "anull"

    command = [
        ffmpeg_executable,
        "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start_s:.3f}",
        "-to", f"{end_s:.3f}",
        "-i", str(video_path),
        "-c:v", "libx264",
        "-af", audio_filter,
        "-movflags", "+faststart",
        str(output_path),
    ]

    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mute failed:\n{completed.stderr.decode('utf-8', errors='replace')}"
        )

    logger.info(f"[_cut_with_mute] [{start_ms}-{end_ms}] muted {len(mute_ranges)} ranges -> {output_path.name}")

    return VideoSegment(
        path=output_path,
        start_seconds=start_s,
        end_seconds=end_s,
    )


def _group_sentences(items: list[dict], gap_threshold: int = 400) -> list[list[dict]]:
    """Group sentences into segments by gap threshold (ms).

    Respects ``_force_break`` markers for intentional gaps.
    """
    clean: list[dict] = []
    break_after: set[int] = set()
    for item in items:
        if item.get("_force_break"):
            if clean:
                break_after.add(len(clean) - 1)
        else:
            clean.append(item)

    if not clean:
        return []

    groups: list[list[dict]] = [[clean[0]]]
    for i in range(len(clean) - 1):
        gap = clean[i + 1]["start"] - clean[i]["end"]
        if gap > gap_threshold or i in break_after:
            groups.append([clean[i + 1]])
        else:
            groups[-1].append(clean[i + 1])
    return groups


def _compute_deleted_ranges(segments: list) -> list[dict]:
    deleted = []
    prev_end = 0
    for seg in segments:
        s_ms = int(seg.start_seconds * 1000)
        e_ms = int(seg.end_seconds * 1000)
        if s_ms > prev_end:
            deleted.append({"start": prev_end, "end": s_ms})
        prev_end = e_ms
    return deleted


def _calibrate_times(items: list[dict], deleted: list[dict]) -> list[dict]:
    if not deleted:
        return items
    prefix = []
    total = 0
    for r in deleted:
        prefix.append((r["start"], r["end"], total))
        total += r["end"] - r["start"]

    def remap(t):
        for start, end, before in prefix:
            if t < start:
                return t - before
            if start <= t <= end:
                return start - before
        return t - total

    out = []
    for item in items:
        ns, ne = remap(item["start"]), remap(item["end"])
        if ns is not None and ne is not None:
            item["start"], item["end"] = int(ns), int(ne)
            out.append(item)
    return out
