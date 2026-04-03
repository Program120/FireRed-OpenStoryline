"""Speech Rough Cut skill handler — LLM-driven speech cleaning and rough editing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from open_storyline.core.skill import SkillContext, SkillHandler, SkillResult


def _ensure_dict(val: Any) -> dict:
    """Parse JSON string to dict if needed (LLM may pass upstream data as string)."""
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return val if isinstance(val, dict) else {}


def _ensure_list(val: Any) -> list:
    """Parse JSON string to list if needed."""
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []
    return val if isinstance(val, list) else []
from open_storyline.utils.ffmpeg_utils import (
    cut_video_segment_with_ffmpeg,
    resolve_ffmpeg_executable,
)
from open_storyline.utils.parse_json import parse_json_dict
from open_storyline.utils.prompts import get_prompt

CLIP_ID_WIDTH = 4
MS_PER_SEC = 1000.0


class Handler(SkillHandler):
    """Perform LLM-driven speech rough cut based on ASR results."""

    _ffmpeg: str = ""

    def _ensure_ffmpeg(self) -> None:
        if not self._ffmpeg:
            self._ffmpeg = resolve_ffmpeg_executable()

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------

    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        self._ensure_ffmpeg()

        raw_asr = inputs.get("asr")
        await ctx.log("debug", f"[execute] inputs keys: {list(inputs.keys())}, type(asr)={type(raw_asr).__name__}")
        asr_data = _ensure_dict(raw_asr or {})
        asr_infos = asr_data.get("asr_infos", [])
        if isinstance(asr_infos, str):
            asr_infos = _ensure_list(asr_infos)

        # Log whether timestamp arrays survived the data pipeline
        if asr_infos:
            sample = asr_infos[0].get("asr_sentence_info", [])
            if sample:
                has_ts = "timestamp" in sample[0]
                ts_count = len(sample[0].get("timestamp", []))
                await ctx.log("info", f"[execute] ASR data check: {len(sample)} sentences, first has_timestamp={has_ts} (count={ts_count})")
            else:
                await ctx.log("warning", "[execute] ASR data has no asr_sentence_info!")
        history_jsons = _ensure_dict(inputs.get("speech_rough_cut") or {}).get("rough_cut_jsons", [])
        history_jsons = [
            [{"text": item.get("text", "")} for item in sublist]
            for sublist in history_jsons
        ]
        user_request = inputs.get("user_request", "")
        gap_threshold = inputs.get("gap_threshold", 400)

        out_dir = (
            Path(ctx.config.local_mcp_server.server_cache_dir)
            / ctx.session_id
            / ctx.execution_id
        )
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

            # ── Phase 1: batch screening ──
            flagged = await self._phase1_screen(
                ctx, sentences, total, user_request
            )

            # ── Phase 2: precise editing for flagged sentences ──
            rough_cut_json = await self._phase2_edit(
                ctx, sentences, flagged, total,
                asr_info, history_jsons, user_request, system_prompt,
            )

            # ── FFmpeg cutting ──
            await ctx.log("info", "FFmpeg 切割视频中...")

            groups = _group_sentences(rough_cut_json, gap_threshold)
            ranges = [{"start": g[0]["start"], "end": g[-1]["end"]} for g in groups]

            # Extend the last range to cover any trailing content after the
            # last ASR sentence (ASR may not detect short utterances at the end).
            video_end_ms = source_ref.get("duration") or source_ref.get("end", 0)
            if ranges and video_end_ms > 0 and video_end_ms - ranges[-1]["end"] < 2000:
                ranges[-1]["end"] = video_end_ms

            segments = []
            for ci, rng in enumerate(ranges):
                seg = cut_video_segment_with_ffmpeg(
                    video_path=video_path,
                    start=rng["start"] / 1000,
                    end=rng["end"] / 1000,
                    output_path=out_dir / f"speech_rough_cut_{ci:0{CLIP_ID_WIDTH}d}.mp4",
                    ffmpeg_executable=self._ffmpeg,
                )
                segments.append(seg)

            # ── Calibrate timestamps ──
            # Remove _force_break markers before calibration
            rough_cut_json = [s for s in rough_cut_json if not s.get("_force_break")]
            deleted = _compute_deleted_ranges(segments)
            rough_cut_json = _calibrate_times(rough_cut_json, deleted)
            rough_cut_jsons.append(rough_cut_json)

            # ── Build clip metadata ──
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
                        "start": s_ms,
                        "end": e_ms,
                        "duration": dur,
                        "height": source_ref.get("height"),
                        "width": source_ref.get("width"),
                    },
                })
                ci += 1

            await ctx.log("info", f"粗剪完成: {len(clips)} clip(s)")

        preview_urls = [c["path"] for c in clips if c.get("path")]
        return SkillResult(
            success=True,
            data={"clips": clips, "rough_cut_jsons": rough_cut_jsons},
            preview_urls=preview_urls,
        )

    # ------------------------------------------------------------------
    # Phase 1: batch screening — one LLM call to flag problematic sentences
    # ------------------------------------------------------------------

    async def _phase1_screen(
        self,
        ctx: SkillContext,
        sentences: list[dict],
        total: int,
        user_request: str,
    ) -> set[int]:
        lines = [f'[{i}] "{s.get("text", "")}"' for i, s in enumerate(sentences)]
        block = "\n".join(lines)

        prompt = (
            f"以下是一段视频的 ASR 识别结果，共 {total} 句。\n"
            f"用户要求: {user_request}\n\n"
            f"{block}\n\n"
            f"请判断哪些句子需要编辑处理（包含脏话、口水词、无意义语气词、重复内容等）。\n"
            f'只输出需要处理的句子编号列表，JSON格式: {{"flagged": [0, 3, 7, ...]}}\n'
            f'如果所有句子都正常不需要处理，输出: {{"flagged": []}}\n'
            f"只输出JSON，不要其他内容。"
        )

        await ctx.log("info", f"预筛选 {total} 句...", block)

        try:
            raw = await ctx.llm.complete(
                system_prompt="你是一个视频语音内容审核助手。快速判断哪些句子需要清洗处理。",
                user_prompt=prompt,
                media=None,
                temperature=0.1,
                top_p=0.9,
                max_tokens=2048,
                model_preferences=None,
            )
            result = parse_json_dict(raw)
            flagged = set(result.get("flagged", []))
            await ctx.log(
                "info",
                f"预筛选完成: {len(flagged)}/{total} 句需处理",
                f"模型输出: {raw}",
            )
            return flagged

        except Exception as e:
            await ctx.log("warning", f"预筛选失败，降级为全量处理: {e}")
            return set(range(total))

    # ------------------------------------------------------------------
    # Phase 2: precise editing — per-sentence LLM calls for flagged ones
    # ------------------------------------------------------------------

    async def _phase2_edit(
        self,
        ctx: SkillContext,
        sentences: list[dict],
        flagged: set[int],
        total: int,
        asr_info: dict,
        history_jsons: list,
        user_request: str,
        system_prompt: str,
    ) -> list[dict]:
        if not flagged:
            return [
                {"text": s.get("text", ""), "start": s.get("start", 0), "end": s.get("end", 0)}
                for s in sentences
            ]

        # Build results array with placeholders; unflagged sentences filled immediately
        results: list[list[dict] | None] = [None] * total
        for i, sentence in enumerate(sentences):
            if i not in flagged:
                results[i] = [{
                    "text": sentence.get("text", ""),
                    "start": sentence.get("start", 0),
                    "end": sentence.get("end", 0),
                }]

        # Process flagged sentences in parallel (max 5 concurrent LLM calls)
        import asyncio
        sem = asyncio.Semaphore(5)
        flagged_count = len(flagged)
        completed = {"n": 0}

        async def _process_one(i: int, sentence: dict) -> None:
            user_prompt = get_prompt(
                "speech_rough_cut.user",
                lang=ctx.lang,
                curr_asr_sentence_info=json.dumps(sentence, ensure_ascii=False),
                asr_text=asr_info.get("asr_text", ""),
                history_rough_cut_jsons=json.dumps(history_jsons, ensure_ascii=False),
                user_request=user_request,
                pre_ctx=sentences[i - 1]["text"] if i > 0 else "",
                nxt_ctx=sentences[i + 1]["text"] if i < total - 1 else "",
            )

            async with sem:
                try:
                    sent_text = sentence.get("text", "")
                    has_ts = bool(sentence.get("timestamp"))
                    await ctx.log(
                        "debug",
                        f"句 {i} 开始处理: \"{sent_text}\" has_timestamp={has_ts}",
                        f"原始sentence:\n{json.dumps(sentence, ensure_ascii=False)}\n\nuser_prompt:\n{user_prompt}",
                    )

                    raw = await ctx.llm.complete(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        media=None,
                        temperature=0.1,
                        top_p=0.9,
                        max_tokens=8092,
                        model_preferences=None,
                    )
                    parsed = parse_json_dict(raw)
                    res_before = parsed.get("res", [])
                    reason = parsed.get("reason", "")

                    await ctx.log(
                        "debug",
                        f"句 {i} LLM返回 {len(res_before)} 段 (校正前)",
                        f"LLM原始输出:\n{raw}\n\n解析后res:\n{json.dumps(res_before, ensure_ascii=False)}",
                    )

                    # Correct timestamps using original ASR timestamp array
                    res = _correct_timestamps(res_before, sentence)
                    # If LLM split the sentence (multiple segments), mark gaps
                    # between them as intentional deletions so _group_sentences
                    # won't merge them back together.
                    if len(res) > 1:
                        marked: list[dict] = []
                        for seg_idx, seg in enumerate(res):
                            marked.append(seg)
                            if seg_idx < len(res) - 1:
                                # Insert a force-break marker between split segments
                                marked.append({"_force_break": True})
                        results[i] = marked
                    else:
                        results[i] = res

                    action = "删除" if not res else f"保留 ({len(res)} 段)"
                    await ctx.log(
                        "info",
                        f"句 {i} \"{sent_text[:30]}\" → {action} | 原因: {reason}",
                        f"校正前: {json.dumps(res_before, ensure_ascii=False)}\n校正后: {json.dumps(res, ensure_ascii=False)}",
                    )

                except Exception as e:
                    await ctx.log("warning", f"LLM failed for sentence {i}: {type(e).__name__}: {e}")
                    # Fallback: keep original
                    results[i] = [{
                        "text": sentence.get("text", ""),
                        "start": sentence.get("start", 0),
                        "end": sentence.get("end", 0),
                    }]

                completed["n"] += 1
                await ctx.progress(
                    completed["n"] / flagged_count,
                    f"精细处理 {completed['n']}/{flagged_count}",
                )

        tasks = [
            _process_one(i, sentences[i])
            for i in sorted(flagged)
            if i < total
        ]
        await asyncio.gather(*tasks)

        # Flatten results in order
        rough_cut_json: list[dict] = []
        for r in results:
            if r is not None:
                rough_cut_json.extend(r)
        return rough_cut_json

    # ------------------------------------------------------------------
    # Default (skip)
    # ------------------------------------------------------------------

    async def default_execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        return SkillResult(success=True, data={"clips": [], "rough_cut_jsons": []})


# ======================================================================
# Pure functions (no state)
# ======================================================================


def _correct_timestamps(res: list[dict], original_sentence: dict) -> list[dict]:
    """
    Correct LLM-returned timestamps using the original ASR timestamp array.

    LLMs often hallucinate timestamps. Instead of trusting them, we match
    the returned text against the original character-level timestamps to
    compute accurate start/end values.
    """
    if not res:
        return res

    timestamps = original_sentence.get("timestamp", [])
    full_text = original_sentence.get("text", "")
    orig_start = original_sentence.get("start", 0)
    orig_end = original_sentence.get("end", 0)

    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.info(f"[_correct_timestamps] text=\"{full_text}\", timestamps_count={len(timestamps)}, res_count={len(res)}")
    if not timestamps:
        _log.warning(f"[_correct_timestamps] NO TIMESTAMPS for \"{full_text}\" — returning LLM values uncorrected")
    if not timestamps or not full_text:
        return res

    corrected = []
    for seg in res:
        seg_text = seg.get("text", "").strip()
        if not seg_text:
            continue

        # Find where this text segment starts in the full text
        idx = full_text.find(seg_text)
        if idx < 0:
            # Fuzzy: try without punctuation
            import re
            clean_seg = re.sub(r'[，。！？、；：""''（）\s]', '', seg_text)
            clean_full = re.sub(r'[，。！？、；：""''（）\s]', '', full_text)
            idx_clean = clean_full.find(clean_seg)
            if idx_clean >= 0:
                # Map clean index back to original index
                clean_i = 0
                for real_i, ch in enumerate(full_text):
                    if re.match(r'[，。！？、；：""''（）\s]', ch):
                        continue
                    if clean_i == idx_clean:
                        idx = real_i
                        break
                    clean_i += 1

        if idx >= 0 and idx < len(timestamps):
            end_idx = min(idx + len(seg_text) - 1, len(timestamps) - 1)
            new_start = timestamps[idx][0]
            new_end = timestamps[end_idx][1]
            old_start = seg.get("start", -1)
            old_end = seg.get("end", -1)
            _log.info(
                f"[_correct_timestamps] \"{seg_text}\" idx={idx} "
                f"LLM=[{old_start}-{old_end}] -> corrected=[{new_start}-{new_end}]"
            )
            corrected.append({
                "text": seg_text,
                "start": new_start,
                "end": new_end,
            })
        else:
            # Can't match — keep LLM's timestamps but clamp to original range
            corrected.append({
                "text": seg_text,
                "start": max(seg.get("start", orig_start), orig_start),
                "end": min(seg.get("end", orig_end), orig_end),
            })

    return corrected


def _group_sentences(items: list[dict], gap_threshold: int = 400) -> list[list[dict]]:
    """Group sentences into segments by gap threshold (ms).

    Respects ``_force_break`` markers inserted between segments that were
    split by mid-sentence deletion.  A force-break always starts a new group
    regardless of the gap duration, ensuring deleted content is not
    re-included in the ffmpeg cut range.
    """
    # Filter out force-break markers and record break positions
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
    """Compute time ranges that were deleted (gaps between segments)."""
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
    """Adjust ASR timestamps after deleted ranges."""
    if not deleted:
        return items

    prefix = []
    total = 0
    for r in deleted:
        prefix.append((r["start"], r["end"], total))
        total += r["end"] - r["start"]

    def remap(t: int) -> int:
        for start, end, before in prefix:
            if t < start:
                return t - before
            if start <= t <= end:
                return start - before
        return t - total

    out = []
    for item in items:
        ns = remap(item["start"])
        ne = remap(item["end"])
        if ns is not None and ne is not None:
            item["start"] = int(ns)
            item["end"] = int(ne)
            out.append(item)
    return out
