"""
Lightweight timeline overview: extracts a structured JSON-serialisable
summary from a TimelineTracks object without performing any rendering.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from open_storyline.nodes.node_schema import (
    TimelineTracks,
    ClipTrack,
    SubtitleTrack,
    VoiceoverTrack,
    BgmTrack,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class SegmentOverview(BaseModel):
    """One segment in the timeline overview."""

    clip_id: str = Field(..., description="Clip identifier")
    timeline_start_ms: int = Field(..., description="Start on the global timeline (ms)")
    timeline_end_ms: int = Field(..., description="End on the global timeline (ms)")
    duration_ms: int = Field(..., description="Segment duration (ms)")
    source_start_ms: int = Field(..., description="Source-media start (ms)")
    source_end_ms: int = Field(..., description="Source-media end (ms)")
    subtitle_text: Optional[str] = Field(
        None, description="Subtitle text overlapping this segment, if any"
    )
    voiceover_id: Optional[str] = Field(
        None, description="Voiceover media-id overlapping this segment, if any"
    )
    bgm_id: Optional[str] = Field(
        None, description="BGM id active during this segment, if any"
    )
    bgm_gain_db: Optional[float] = Field(
        None, description="BGM gain (dB) during this segment"
    )


class TimelineOverview(BaseModel):
    """High-level structured summary of a timeline."""

    total_duration_ms: int = Field(..., description="Total timeline duration (ms)")
    segment_count: int = Field(..., description="Number of video segments")
    subtitle_count: int = Field(..., description="Number of subtitle entries")
    voiceover_count: int = Field(..., description="Number of voiceover entries")
    bgm_count: int = Field(..., description="Number of BGM entries")
    segments: List[SegmentOverview] = Field(
        default_factory=list, description="Per-segment details"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_overlapping_subtitle(
    track: ClipTrack, subtitles: List[SubtitleTrack]
) -> Optional[str]:
    """Return the text of the first subtitle that overlaps this clip window."""
    for sub in subtitles:
        # overlap if sub.start < clip.end AND sub.end > clip.start
        if (
            sub.timeline_window.start < track.timeline_window.end
            and sub.timeline_window.end > track.timeline_window.start
        ):
            return sub.text
    return None


def _find_overlapping_voiceover(
    track: ClipTrack, voiceovers: List[VoiceoverTrack]
) -> Optional[str]:
    for vo in voiceovers:
        if (
            vo.timeline_window.start < track.timeline_window.end
            and vo.timeline_window.end > track.timeline_window.start
        ):
            return vo.media_id
    return None


def _find_overlapping_bgm(
    track: ClipTrack, bgms: List[BgmTrack]
) -> tuple[Optional[str], Optional[float]]:
    for bgm in bgms:
        if (
            bgm.timeline_window.start < track.timeline_window.end
            and bgm.timeline_window.end > track.timeline_window.start
        ):
            return bgm.bgm_id, bgm.mix.gain_db
    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_timeline_overview(
    timeline_data: TimelineTracks | Dict[str, Any],
) -> TimelineOverview:
    """Build a :class:`TimelineOverview` from *timeline_data*.

    Parameters
    ----------
    timeline_data:
        Either a ``TimelineTracks`` instance or a raw dict that can be parsed
        into one.

    Returns
    -------
    TimelineOverview
        A JSON-serialisable structured summary of the timeline.
    """
    if isinstance(timeline_data, dict):
        timeline_data = TimelineTracks.model_validate(timeline_data)

    segments: List[SegmentOverview] = []
    total_end = 0

    for clip_track in timeline_data.video:
        tw = clip_track.timeline_window
        sw = clip_track.source_window
        duration_ms = tw.end - tw.start

        subtitle_text = _find_overlapping_subtitle(
            clip_track, timeline_data.subtitles
        )
        voiceover_id = _find_overlapping_voiceover(
            clip_track, timeline_data.voiceover
        )
        bgm_id, bgm_gain_db = _find_overlapping_bgm(clip_track, timeline_data.bgm)

        segments.append(
            SegmentOverview(
                clip_id=clip_track.clip_id,
                timeline_start_ms=tw.start,
                timeline_end_ms=tw.end,
                duration_ms=duration_ms,
                source_start_ms=sw.start,
                source_end_ms=sw.end,
                subtitle_text=subtitle_text,
                voiceover_id=voiceover_id,
                bgm_id=bgm_id,
                bgm_gain_db=bgm_gain_db,
            )
        )
        if tw.end > total_end:
            total_end = tw.end

    return TimelineOverview(
        total_duration_ms=total_end,
        segment_count=len(segments),
        subtitle_count=len(timeline_data.subtitles),
        voiceover_count=len(timeline_data.voiceover),
        bgm_count=len(timeline_data.bgm),
        segments=segments,
    )
