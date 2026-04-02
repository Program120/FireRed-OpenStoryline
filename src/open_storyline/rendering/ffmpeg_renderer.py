"""
Standalone FFmpeg-based renderer for OpenStoryline timelines.

This module builds and executes an FFmpeg command that:
  - concatenates video clips with proper scaling/padding
  - mixes source audio, voiceover, and BGM with dynamic ducking
  - burns subtitles via the drawtext filter

It does NOT replace the existing MoviePy renderer; it is an independent
alternative that can be integrated later.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from open_storyline.nodes.node_schema import (
    TimelineTracks,
    ClipTrack,
    SubtitleTrack,
    VoiceoverTrack,
    BgmTrack,
)
from open_storyline.rendering.subtitle_templates import (
    SubtitleStyle,
    SUBTITLE_PRESETS,
)

logger = logging.getLogger(__name__)

MS_PER_S = 1000.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ms_to_s(ms: int | float) -> float:
    return ms / MS_PER_S


def _escape_drawtext(text: str) -> str:
    """Escape special characters for FFmpeg drawtext filter."""
    text = text.replace("\\", "\\\\\\\\")
    text = text.replace("'", "\u2019")  # replace with right single quote
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    return text


# ---------------------------------------------------------------------------
# Clip-source resolver (maps clip_id -> file path)
# ---------------------------------------------------------------------------

class ClipSource(BaseModel):
    """Maps a clip_id to its filesystem path."""

    clip_id: str
    path: str


# ---------------------------------------------------------------------------
# Render configuration
# ---------------------------------------------------------------------------

class RenderConfig(BaseModel):
    """All tunables for a single render pass."""

    width: int = Field(1920, description="Output width in px")
    height: int = Field(1080, description="Output height in px")
    fps: int = Field(25, description="Output frame rate")
    crf: int = Field(23, description="CRF quality (lower = better)")
    preset: str = Field("veryfast", description="x264 encoding preset")
    bg_color: str = Field("black", description="Pad colour name or hex")
    subtitle_style: str = Field(
        "classic_white", description="Name of a subtitle preset"
    )
    include_source_audio: bool = Field(
        False, description="Mix original clip audio into the output"
    )
    source_audio_volume: float = Field(1.0, description="Source audio gain (linear)")
    voiceover_volume: float = Field(1.0, description="Voiceover gain (linear)")
    bgm_volume: float = Field(0.25, description="BGM gain (linear)")
    ducking_enabled: bool = Field(
        True,
        description="Lower BGM when voiceover is present (sidechaincompress)",
    )


# ---------------------------------------------------------------------------
# FFmpegRenderer
# ---------------------------------------------------------------------------

class FFmpegRenderer:
    """Builds and runs an FFmpeg command from a ``TimelineTracks`` structure.

    Usage::

        renderer = FFmpegRenderer(
            timeline=tracks,
            clip_sources=clip_sources,
            voiceover_sources=vo_sources,
            bgm_sources=bgm_sources,
            config=RenderConfig(width=1920, height=1080),
        )
        output_path = renderer.render("/tmp/output.mp4")
    """

    def __init__(
        self,
        timeline: TimelineTracks | Dict[str, Any],
        clip_sources: Dict[str, str],
        voiceover_sources: Dict[str, str] | None = None,
        bgm_sources: Dict[str, str] | None = None,
        config: RenderConfig | None = None,
    ) -> None:
        if isinstance(timeline, dict):
            timeline = TimelineTracks.model_validate(timeline)
        self.timeline = timeline
        self.clip_sources = clip_sources
        self.voiceover_sources = voiceover_sources or {}
        self.bgm_sources = bgm_sources or {}
        self.config = config or RenderConfig()

    # ------------------------------------------------------------------
    # Filter-complex: video
    # ------------------------------------------------------------------

    def build_filter_complex(self) -> Tuple[str, str]:
        """Return ``(filter_complex_str, final_video_pad_name)``.

        Each clip is scaled/padded to the target canvas, trimmed to its
        source window, and then concatenated.
        """
        parts: List[str] = []
        concat_inputs: List[str] = []
        w, h = self.config.width, self.config.height

        for idx, clip in enumerate(self.timeline.video):
            inp_label = f"[{idx}:v]"
            src_start = _ms_to_s(clip.source_window.start)
            src_end = _ms_to_s(clip.source_window.end)
            trimmed = f"v_trim{idx}"
            scaled = f"v_scaled{idx}"

            # trim source window
            parts.append(
                f"{inp_label}trim=start={src_start:.4f}:end={src_end:.4f},"
                f"setpts=PTS-STARTPTS[{trimmed}]"
            )
            # scale + pad to canvas
            parts.append(
                f"[{trimmed}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={self.config.bg_color},"
                f"setsar=1,fps={self.config.fps}[{scaled}]"
            )
            concat_inputs.append(f"[{scaled}]")

        n = len(self.timeline.video)
        if n == 0:
            return "", ""

        concat_label = "v_out"
        concat_str = "".join(concat_inputs) + f"concat=n={n}:v=1:a=0[{concat_label}]"
        parts.append(concat_str)

        return ";\n".join(parts), concat_label

    # ------------------------------------------------------------------
    # Filter-complex: audio mix with ducking
    # ------------------------------------------------------------------

    def build_audio_mix_filter(
        self, *, video_input_count: int
    ) -> Tuple[str, List[str], str]:
        """Return ``(filter_str, extra_input_files, final_audio_pad)``.

        Audio sources (indexed starting after the video inputs):
          - voiceover tracks (concatenated into one stream)
          - BGM tracks (concatenated, looped/trimmed to timeline length)
          - optionally, source audio from each video clip

        When ``ducking_enabled`` the BGM is side-chain compressed against
        the voiceover so it ducks automatically.
        """
        parts: List[str] = []
        extra_inputs: List[str] = []
        next_idx = video_input_count
        total_dur_s = self._total_duration_s()

        # --- voiceover ---------------------------------------------------
        vo_pad: Optional[str] = None
        if self.timeline.voiceover and self.voiceover_sources:
            vo_parts: List[str] = []
            vo_labels: List[str] = []
            for i, vo in enumerate(self.timeline.voiceover):
                src_path = self.voiceover_sources.get(vo.media_id)
                if not src_path:
                    continue
                extra_inputs.append(src_path)
                label = f"vo{i}"
                start_s = _ms_to_s(vo.timeline_window.start)
                # Delay voiceover to its timeline position using adelay
                delay_ms = int(vo.timeline_window.start)
                parts.append(
                    f"[{next_idx}:a]adelay={delay_ms}|{delay_ms},"
                    f"volume={self.config.voiceover_volume:.2f}[{label}]"
                )
                vo_labels.append(f"[{label}]")
                next_idx += 1

            if vo_labels:
                if len(vo_labels) == 1:
                    vo_pad = vo_labels[0].strip("[]")
                else:
                    vo_pad = "vo_mix"
                    parts.append(
                        "".join(vo_labels)
                        + f"amix=inputs={len(vo_labels)}:duration=longest[{vo_pad}]"
                    )

        # --- BGM ---------------------------------------------------------
        bgm_pad: Optional[str] = None
        if self.timeline.bgm and self.bgm_sources:
            bgm_labels: List[str] = []
            for i, bgm in enumerate(self.timeline.bgm):
                src_path = self.bgm_sources.get(bgm.bgm_id)
                if not src_path:
                    continue
                extra_inputs.append(src_path)
                label = f"bgm{i}"
                start_s = _ms_to_s(bgm.timeline_window.start)
                end_s = _ms_to_s(bgm.timeline_window.end)
                dur_s = end_s - start_s
                gain_db = bgm.mix.gain_db
                delay_ms = int(bgm.timeline_window.start)
                parts.append(
                    f"[{next_idx}:a]atrim=0:{dur_s:.4f},asetpts=PTS-STARTPTS,"
                    f"adelay={delay_ms}|{delay_ms},"
                    f"volume={self.config.bgm_volume:.2f}[{label}]"
                )
                bgm_labels.append(f"[{label}]")
                next_idx += 1

            if bgm_labels:
                if len(bgm_labels) == 1:
                    bgm_pad = bgm_labels[0].strip("[]")
                else:
                    bgm_pad = "bgm_mix"
                    parts.append(
                        "".join(bgm_labels)
                        + f"amix=inputs={len(bgm_labels)}:duration=longest[{bgm_pad}]"
                    )

        # --- ducking (sidechain compress BGM against voiceover) ----------
        if (
            self.config.ducking_enabled
            and vo_pad is not None
            and bgm_pad is not None
        ):
            ducked_bgm = "bgm_ducked"
            # sidechaincompress: use voiceover as sidechain to duck BGM
            parts.append(
                f"[{bgm_pad}][{vo_pad}]sidechaincompress="
                f"threshold=0.02:ratio=6:attack=200:release=1000"
                f"[{ducked_bgm}]"
            )
            # After sidechaincompress the voiceover stream is consumed as
            # the side-chain key but is NOT mixed into the output.  We need
            # to re-create a copy.  The simplest portable approach is to
            # split the voiceover *before* feeding it to the sidechain.
            # However, since we already built the graph linearly, we use the
            # split workaround below.

            # Actually, FFmpeg's sidechaincompress outputs only the first
            # (main) input with compression applied; the second input is used
            # as the key signal only.  So vo_pad is still available in the
            # graph as an output we can reference -- but it has been consumed.
            # We need to split it beforehand.  Let's fix this: we'll rebuild
            # the sidechain section with a split.

            # Remove the last appended line and redo with split.
            parts.pop()

            vo_split_a = "vo_sc_a"
            vo_split_b = "vo_sc_b"
            parts.append(f"[{vo_pad}]asplit=2[{vo_split_a}][{vo_split_b}]")
            parts.append(
                f"[{bgm_pad}][{vo_split_a}]sidechaincompress="
                f"threshold=0.02:ratio=6:attack=200:release=1000"
                f"[{ducked_bgm}]"
            )
            # Final mix: ducked BGM + voiceover copy
            final_audio = "a_out"
            parts.append(
                f"[{ducked_bgm}][{vo_split_b}]amix=inputs=2:duration=longest[{final_audio}]"
            )
        elif vo_pad is not None and bgm_pad is not None:
            # No ducking, simple mix
            final_audio = "a_out"
            parts.append(
                f"[{vo_pad}][{bgm_pad}]amix=inputs=2:duration=longest[{final_audio}]"
            )
        elif vo_pad is not None:
            final_audio = vo_pad
        elif bgm_pad is not None:
            final_audio = bgm_pad
        else:
            # Generate silence for the timeline duration
            final_audio = "a_out"
            parts.append(
                f"anullsrc=r=44100:cl=stereo,atrim=0:{total_dur_s:.4f}[{final_audio}]"
            )

        # --- optional: mix in source audio from video clips --------------
        if self.config.include_source_audio and self.timeline.video:
            src_labels: List[str] = []
            for idx, clip in enumerate(self.timeline.video):
                src_start = _ms_to_s(clip.source_window.start)
                src_end = _ms_to_s(clip.source_window.end)
                delay_ms = int(clip.timeline_window.start)
                label = f"src_a{idx}"
                parts.append(
                    f"[{idx}:a]atrim=start={src_start:.4f}:end={src_end:.4f},"
                    f"asetpts=PTS-STARTPTS,"
                    f"adelay={delay_ms}|{delay_ms},"
                    f"volume={self.config.source_audio_volume:.2f}[{label}]"
                )
                src_labels.append(f"[{label}]")

            if src_labels:
                prev_final = final_audio
                final_audio = "a_final"
                all_audio = "".join(src_labels)
                n_src = len(src_labels)
                # Mix all source audios together first
                if n_src > 1:
                    parts.append(
                        f"{all_audio}amix=inputs={n_src}:duration=longest[src_amix]"
                    )
                    src_mix_label = "[src_amix]"
                else:
                    src_mix_label = src_labels[0]
                # Then mix with the rest
                parts.append(
                    f"{src_mix_label}[{prev_final}]amix=inputs=2:duration=longest[{final_audio}]"
                )

        return ";\n".join(parts), extra_inputs, final_audio

    # ------------------------------------------------------------------
    # Subtitle drawtext chain
    # ------------------------------------------------------------------

    def build_subtitle_filters(self, video_pad: str) -> Tuple[str, str]:
        """Return ``(filter_str, final_video_pad)`` with subtitle drawtext
        filters chained onto the video stream.
        """
        if not self.timeline.subtitles:
            return "", video_pad

        style = SUBTITLE_PRESETS.get(
            self.config.subtitle_style,
            SUBTITLE_PRESETS["classic_white"],
        )

        parts: List[str] = []
        current_pad = video_pad

        for idx, sub in enumerate(self.timeline.subtitles):
            out_pad = f"v_sub{idx}"
            start_s = _ms_to_s(sub.timeline_window.start)
            end_s = _ms_to_s(sub.timeline_window.end)
            escaped_text = _escape_drawtext(sub.text)

            base_params = style.to_drawtext_params(
                video_width=self.config.width, video_height=self.config.height
            )
            drawtext = (
                f"[{current_pad}]drawtext="
                f"text='{escaped_text}':"
                f"enable='between(t,{start_s:.4f},{end_s:.4f})':"
                f"{base_params}"
                f"[{out_pad}]"
            )
            parts.append(drawtext)
            current_pad = out_pad

        return ";\n".join(parts), current_pad

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render(self, output_path: str | Path) -> Path:
        """Execute FFmpeg and produce the final MP4.

        Parameters
        ----------
        output_path:
            Destination file path (will be overwritten if it exists).

        Returns
        -------
        Path
            The resolved output path on success.

        Raises
        ------
        FileNotFoundError
            If ``ffmpeg`` is not found on ``$PATH``.
        subprocess.CalledProcessError
            If FFmpeg exits with a non-zero code.
        """
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise FileNotFoundError(
                "ffmpeg not found on PATH. Install FFmpeg to use FFmpegRenderer."
            )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # --- build filter-complex ----------------------------------------
        video_fc, video_pad = self.build_filter_complex()
        if not video_pad:
            raise ValueError("Timeline has no video tracks; nothing to render.")

        n_video_inputs = len(self.timeline.video)
        audio_fc, extra_audio_inputs, audio_pad = self.build_audio_mix_filter(
            video_input_count=n_video_inputs
        )
        subtitle_fc, final_video_pad = self.build_subtitle_filters(video_pad)

        # Combine all filter sections
        filter_sections = [s for s in (video_fc, audio_fc, subtitle_fc) if s]
        filter_complex = ";\n".join(filter_sections)

        # --- build command -----------------------------------------------
        cmd: List[str] = [ffmpeg, "-y"]

        # Video inputs
        for clip in self.timeline.video:
            src_path = self.clip_sources.get(clip.clip_id, "")
            cmd.extend(["-i", src_path])

        # Audio inputs (voiceover + bgm)
        for extra in extra_audio_inputs:
            cmd.extend(["-i", extra])

        cmd.extend(["-filter_complex", filter_complex])
        cmd.extend([
            "-map", f"[{final_video_pad}]",
            "-map", f"[{audio_pad}]",
            "-c:v", "libx264",
            "-preset", self.config.preset,
            "-crf", str(self.config.crf),
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path),
        ])

        logger.info("FFmpegRenderer: executing command with %d inputs", n_video_inputs)
        logger.debug("FFmpeg command: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            logger.error("FFmpeg stderr:\n%s", result.stderr[-4000:] if result.stderr else "(empty)")
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

        logger.info("FFmpegRenderer: output written to %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _total_duration_s(self) -> float:
        """Compute total timeline duration in seconds."""
        end = 0
        for clip in self.timeline.video:
            if clip.timeline_window.end > end:
                end = clip.timeline_window.end
        for sub in self.timeline.subtitles:
            if sub.timeline_window.end > end:
                end = sub.timeline_window.end
        for vo in self.timeline.voiceover:
            if vo.timeline_window.end > end:
                end = vo.timeline_window.end
        for bgm in self.timeline.bgm:
            if bgm.timeline_window.end > end:
                end = bgm.timeline_window.end
        return _ms_to_s(end)
