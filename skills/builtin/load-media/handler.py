"""Load Media skill handler — indexes input media and extracts metadata."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from open_storyline.core.skill import SkillContext, SkillHandler, SkillResult

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _image_metadata(path: Path) -> dict[str, Any]:
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        try:
            img2 = ImageOps.exif_transpose(img)
            w, h = img2.size
        except Exception:
            w, h = img.size

    return {"width": int(w), "height": int(h)}


def _video_metadata(path: Path) -> dict[str, Any]:
    import av

    from open_storyline.utils.util import get_video_rotation

    container = av.open(str(path))

    video_stream = next(
        (s for s in container.streams if s.type == "video"), None
    )
    if video_stream is None:
        container.close()
        raise ValueError(f"No video stream found: {path}")

    # duration (microseconds → seconds)
    duration_sec = 0.0
    if container.duration is not None:
        duration_sec = container.duration / 1_000_000
    elif video_stream.duration is not None and video_stream.time_base is not None:
        duration_sec = float(video_stream.duration * video_stream.time_base)
    duration_sec = round(duration_sec, 3)

    # resolution with rotation correction
    w = int(video_stream.codec_context.width or 0)
    h = int(video_stream.codec_context.height or 0)
    rotation = get_video_rotation(path)
    if abs(rotation) in (90, 270):
        w, h = h, w

    # frame rate
    fps = 0.0
    if video_stream.average_rate:
        fps = float(video_stream.average_rate)
    elif video_stream.base_rate:
        fps = float(video_stream.base_rate)

    # audio
    audio_stream = next(
        (s for s in container.streams if s.type == "audio"), None
    )
    has_audio = audio_stream is not None
    audio_sample_rate_hz = (
        int(audio_stream.rate) if audio_stream and audio_stream.rate else 0
    )

    container.close()

    return {
        "duration": int(duration_sec * 1000),  # ms
        "width": w,
        "height": h,
        "fps": fps,
        "has_audio": has_audio,
        "audio_sample_rate_hz": audio_sample_rate_hz,
    }


class Handler(SkillHandler):
    """Load and index input media files, extracting metadata for downstream skills."""

    async def execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        input_media = inputs.get("inputs", [])

        media_idx = 1
        media: list[dict[str, Any]] = []

        for item in input_media:
            path = Path(item["path"])
            suffix = path.suffix.lower()

            if suffix in VIDEO_EXTS:
                metadata = _video_metadata(path)
                media_type = "video"
            elif suffix in IMAGE_EXTS:
                metadata = _image_metadata(path)
                media_type = "image"
            else:
                await ctx.log("warning", f"Skipping unsupported file: {path.name}")
                continue

            media.append(
                {
                    "media_id": f"media_{media_idx:04d}",
                    "path": str(path),
                    "media_type": media_type,
                    "metadata": metadata,
                }
            )
            await ctx.log("info", f"Added media_{media_idx:04d}: ({media_type})")
            media_idx += 1

        c = Counter(m["media_type"] for m in media)
        await ctx.log(
            "info",
            f"Media indexing completed: {c.get('video', 0)} video(s), "
            f"{c.get('image', 0)} image(s)",
        )

        return SkillResult(success=True, data={"media": media})

    async def default_execute(self, ctx: SkillContext, inputs: dict) -> SkillResult:
        """load-media has no meaningful skip behavior — always run full logic."""
        return await self.execute(ctx, inputs)
