"""
Eight preset subtitle styles for use with the FFmpeg ``drawtext`` filter.

Each style is a :class:`SubtitleStyle` Pydantic model that maps directly
to ``drawtext`` parameters.
"""

from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, Field


class SubtitleStyle(BaseModel):
    """Configuration for one subtitle visual preset."""

    name: str = Field(..., description="Human-readable preset name")
    font_size: int = Field(40, description="Font size in pixels")
    font_color: str = Field(
        "FFFFFF", description="Font colour as 6-digit hex (no '#')"
    )
    stroke_width: int = Field(2, description="Outline / border width (px)")
    stroke_color: str = Field(
        "000000", description="Outline colour as 6-digit hex"
    )
    margin_bottom: int = Field(
        60, description="Distance from the bottom of the frame (px)"
    )
    background_box: bool = Field(
        False, description="Whether to render a semi-transparent box behind text"
    )
    background_color: str = Field(
        "000000@0.5",
        description=(
            "FFmpeg box colour string (hex + optional alpha), "
            "only used when background_box is True"
        ),
    )
    font_family: Optional[str] = Field(
        None,
        description=(
            "Optional font family name (e.g. 'Arial', 'Georgia'). "
            "If None the system default sans-serif is used."
        ),
    )

    def to_drawtext_params(self, *, video_width: int, video_height: int) -> str:
        """Return a drawtext filter value string for FFmpeg.

        The caller is responsible for supplying ``text`` and ``enable``
        time-range expressions.
        """
        parts = [
            f"fontsize={self.font_size}",
            f"fontcolor=0x{self.font_color}",
            f"borderw={self.stroke_width}",
            f"bordercolor=0x{self.stroke_color}",
            f"x=(w-text_w)/2",
            f"y=h-{self.margin_bottom}-text_h",
        ]
        if self.font_family:
            parts.append(f"fontfile=''")
            parts.append(f"font='{self.font_family}'")
        if self.background_box:
            parts.append("box=1")
            parts.append(f"boxcolor=0x{self.background_color}")
            parts.append("boxborderw=8")
        return ":".join(parts)


# ---------------------------------------------------------------------------
# 8 preset styles
# ---------------------------------------------------------------------------

SUBTITLE_PRESETS: Dict[str, SubtitleStyle] = {
    "classic_white": SubtitleStyle(
        name="Classic White",
        font_size=42,
        font_color="FFFFFF",
        stroke_width=2,
        stroke_color="000000",
        margin_bottom=60,
    ),
    "cinematic_yellow": SubtitleStyle(
        name="Cinematic Yellow",
        font_size=44,
        font_color="F5D442",
        stroke_width=2,
        stroke_color="1A1A1A",
        margin_bottom=70,
    ),
    "minimal_gray": SubtitleStyle(
        name="Minimal Gray",
        font_size=36,
        font_color="CCCCCC",
        stroke_width=0,
        stroke_color="000000",
        margin_bottom=50,
    ),
    "bold_outline": SubtitleStyle(
        name="Bold Outline",
        font_size=48,
        font_color="FFFFFF",
        stroke_width=4,
        stroke_color="000000",
        margin_bottom=65,
    ),
    "soft_bg": SubtitleStyle(
        name="Soft Background",
        font_size=40,
        font_color="FFFFFF",
        stroke_width=0,
        stroke_color="000000",
        margin_bottom=55,
        background_box=True,
        background_color="000000@0.50",
    ),
    "vlog_pop": SubtitleStyle(
        name="Vlog Pop",
        font_size=46,
        font_color="FF6B6B",
        stroke_width=3,
        stroke_color="FFFFFF",
        margin_bottom=60,
    ),
    "dark_mode": SubtitleStyle(
        name="Dark Mode",
        font_size=40,
        font_color="E0E0E0",
        stroke_width=1,
        stroke_color="333333",
        margin_bottom=55,
        background_box=True,
        background_color="1A1A1A@0.70",
    ),
    "elegant_serif": SubtitleStyle(
        name="Elegant Serif",
        font_size=38,
        font_color="F0EAD6",
        stroke_width=1,
        stroke_color="3B3B3B",
        margin_bottom=65,
        font_family="Georgia",
    ),
}


def get_subtitle_style(name: str) -> SubtitleStyle:
    """Look up a preset by name.

    Raises ``KeyError`` if *name* is not one of the eight presets.
    """
    if name not in SUBTITLE_PRESETS:
        available = ", ".join(sorted(SUBTITLE_PRESETS))
        raise KeyError(
            f"Unknown subtitle preset '{name}'. Available: {available}"
        )
    return SUBTITLE_PRESETS[name].model_copy()
