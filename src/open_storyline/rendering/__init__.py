"""
FireRed-OpenStoryline rendering package.

Provides FFmpeg-based rendering, subtitle templates, and timeline overview
utilities as standalone modules that can complement the existing MoviePy renderer.
"""

from open_storyline.rendering.timeline_overview import (
    TimelineOverview,
    build_timeline_overview,
)
from open_storyline.rendering.subtitle_templates import (
    SubtitleStyle,
    SUBTITLE_PRESETS,
    get_subtitle_style,
)
from open_storyline.rendering.ffmpeg_renderer import FFmpegRenderer

__all__ = [
    "TimelineOverview",
    "build_timeline_overview",
    "SubtitleStyle",
    "SUBTITLE_PRESETS",
    "get_subtitle_style",
    "FFmpegRenderer",
]
