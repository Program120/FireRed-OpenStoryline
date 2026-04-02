"""
V1 User Profile models.

Stores per-user preferences for subtitle styling, audio settings,
and reusable style templates.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SubtitlePreference(BaseModel):
    """User preferences for subtitle rendering."""
    font_name: Optional[str] = None
    font_size: int = 36
    font_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: int = 2
    position: str = "bottom"  # "top", "center", "bottom"
    template: str = "classic_white"


class AudioPreference(BaseModel):
    """User preferences for audio/voiceover."""
    tts_provider: str = "302"
    voice_id: Optional[str] = None
    speed: float = 1.0
    bgm_volume: float = 0.3
    voiceover_volume: float = 1.0


class StyleTemplate(BaseModel):
    """A reusable combination of subtitle + audio + visual settings."""
    name: str
    description: str = ""
    subtitle: SubtitlePreference = Field(default_factory=SubtitlePreference)
    audio: AudioPreference = Field(default_factory=AudioPreference)
    extra: Dict[str, Any] = Field(default_factory=dict)


class UserProfile(BaseModel):
    """
    Aggregate user profile.

    Stores subtitle/audio preferences and a library of saved style templates
    that the user can switch between.
    """
    user_id: str
    display_name: str = ""
    subtitle: SubtitlePreference = Field(default_factory=SubtitlePreference)
    audio: AudioPreference = Field(default_factory=AudioPreference)
    templates: List[StyleTemplate] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def get_template(self, name: str) -> Optional[StyleTemplate]:
        """Look up a saved style template by name."""
        for t in self.templates:
            if t.name == name:
                return t
        return None

    def upsert_template(self, template: StyleTemplate) -> None:
        """Add or update a style template (matched by name)."""
        for i, t in enumerate(self.templates):
            if t.name == template.name:
                self.templates[i] = template
                return
        self.templates.append(template)

    def remove_template(self, name: str) -> bool:
        """Remove a template by name. Returns True if found and removed."""
        for i, t in enumerate(self.templates):
            if t.name == name:
                self.templates.pop(i)
                return True
        return False
