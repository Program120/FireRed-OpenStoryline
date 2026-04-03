"""Local ASR skill handler — speech recognition on video clips using FunASR."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any

from open_storyline.core.skill import SkillContext, SkillHandler, SkillResult


def _extract_audio_wav(video_path: str, tmpdir: str) -> str | None:
    """Extract 16kHz mono WAV from video with noise reduction. Returns None if no audio track."""
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        video_path,
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    if not result.stdout.strip():
        return None

    out_wav = os.path.join(tmpdir, "audio.wav")
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-af", "afftdn,agate=threshold=-40dB:ratio=10:attack=20:release=100",
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        out_wav,
    ]
    subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_wav


class Handler(SkillHandler):
    """Perform local ASR on video clips using FunASR paraformer-zh."""

    _asr_model: Any = None

    def _ensure_model(self) -> Any:
        if self._asr_model is not None:
            return self._asr_model
        from funasr import AutoModel

        self._asr_model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            vad_kwargs={"max_single_segment_time": 30000},
        )
        return self._asr_model

    @staticmethod
    def _parse_upstream(val):
        """Parse JSON string to dict if needed (LLM may pass upstream data as string)."""
        if isinstance(val, str):
            import json as _json
            try:
                return _json.loads(val)
            except (ValueError, TypeError):
                return {}
        return val if isinstance(val, dict) else {}

    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        split_data = self._parse_upstream(inputs.get("split_shots") or {})
        clips = split_data.get("clips", [])
        model = self._ensure_model()
        total = len(clips)

        await ctx.progress(0.0, f"开始识别 {total} 个片段...")

        asr_infos: list[dict[str, Any]] = []

        for idx, clip in enumerate(clips):
            video_path = clip["path"]
            kind = clip.get("kind", "video")
            source_ref = clip.get("source_ref", {})
            fps = clip.get("fps", 30)
            clip_id = clip["clip_id"]

            # Skip non-video clips
            if kind != "video":
                asr_infos.append(self._empty_asr(clip_id, video_path, kind, source_ref, fps))
                await ctx.progress((idx + 1) / total, f"已识别 {idx + 1}/{total}")
                continue

            with tempfile.TemporaryDirectory() as tmpdir:
                audio_wav = _extract_audio_wav(video_path, tmpdir)

                if audio_wav is None:
                    asr_infos.append(self._empty_asr(clip_id, video_path, kind, source_ref, fps))
                    await ctx.log("info", f"Clip {clip_id} has no audio track, skipped")
                    await ctx.progress((idx + 1) / total, f"已识别 {idx + 1}/{total}")
                    continue

                res = model.generate(input=audio_wav, sentence_timestamp=True)
                asr_res = res[0] if res else {}

                asr_infos.append({
                    "clip_id": clip_id,
                    "kind": kind,
                    "path": video_path,
                    "asr_text": asr_res.get("text", "") if asr_res else "",
                    "asr_timestamps": asr_res.get("timestamp", []) if asr_res else [],
                    "asr_sentence_info": asr_res.get("sentence_info", []) if asr_res else [],
                    "source_ref": source_ref,
                    "fps": fps,
                })

            await ctx.progress((idx + 1) / total, f"已识别 {idx + 1}/{total}")

        await ctx.log("info", f"ASR completed: {total} clip(s) processed")
        return SkillResult(success=True, data={"asr_infos": asr_infos})

    async def split_into_subtasks(self, inputs: dict) -> list[dict]:
        """Split clips into batches for parallel ASR processing."""
        split_data = self._parse_upstream(inputs.get("split_shots") or {})
        clips = split_data.get("clips", [])
        batch_size = self.meta.concurrency.default_batch_size

        if len(clips) <= batch_size:
            return [inputs]

        batches = []
        for i in range(0, len(clips), batch_size):
            batch_input = dict(inputs)
            batch_input["split_shots"] = {"clips": clips[i : i + batch_size]}
            batches.append(batch_input)
        return batches

    async def merge_subtask_results(self, results: list[SkillResult]) -> SkillResult:
        """Merge ASR results from parallel batches, preserving clip order."""
        merged_infos: list[dict] = []
        for r in results:
            if r.success:
                merged_infos.extend(r.data.get("asr_infos", []))
        return SkillResult(success=True, data={"asr_infos": merged_infos})

    @staticmethod
    def _empty_asr(clip_id: str, path: str, kind: str, source_ref: dict, fps: float) -> dict:
        return {
            "clip_id": clip_id,
            "kind": kind,
            "path": path,
            "asr_text": "",
            "asr_timestamps": [],
            "asr_sentence_info": [],
            "source_ref": source_ref,
            "fps": fps,
        }

    async def default_execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        """Skip ASR — return empty results."""
        return SkillResult(success=True, data={"asr_infos": []})
