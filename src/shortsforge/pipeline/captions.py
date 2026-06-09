"""Animated caption rendering — word-by-word overlays via Pillow + ffmpeg."""

from __future__ import annotations

import hashlib
import math
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog
from PIL import Image, ImageDraw, ImageFont

from shortsforge.pipeline.ingest import Word
from shortsforge.security.paths import ALLOWED_OUTPUT_ROOTS, safe_resolve

logger = structlog.get_logger(__name__)

_ASSETS_FONTS = Path(__file__).parent.parent / "assets" / "fonts"
_CACHE_DIR = Path("output") / ".cache" / "captions"
_FPS = 30


@dataclass
class CaptionStyle:
    font_path: str = ""  # empty → Pillow default
    font_size: int = 64
    fill_rgb: tuple[int, int, int] = (255, 255, 255)
    stroke_rgb: tuple[int, int, int] = (0, 0, 0)
    stroke_w: int = 4
    highlight_rgb: tuple[int, int, int] = (255, 220, 0)
    position: Literal["top", "middle", "bottom"] = "bottom"
    anim: Literal["pop", "fade", "karaoke", "none"] = "pop"
    max_words_per_line: int = 6


def style_preset(name: str) -> CaptionStyle:
    """Return a named CaptionStyle preset."""
    presets: dict[str, CaptionStyle] = {
        "bold-pop": CaptionStyle(
            font_size=72,
            fill_rgb=(255, 255, 255),
            stroke_rgb=(0, 0, 0),
            stroke_w=5,
            highlight_rgb=(255, 50, 50),
            position="bottom",
            anim="pop",
            max_words_per_line=5,
        ),
        "subtle-bottom": CaptionStyle(
            font_size=52,
            fill_rgb=(240, 240, 240),
            stroke_rgb=(20, 20, 20),
            stroke_w=3,
            highlight_rgb=(180, 220, 255),
            position="bottom",
            anim="fade",
            max_words_per_line=7,
        ),
        "glow-center": CaptionStyle(
            font_size=60,
            fill_rgb=(255, 255, 255),
            stroke_rgb=(80, 0, 200),
            stroke_w=6,
            highlight_rgb=(0, 255, 200),
            position="middle",
            anim="karaoke",
            max_words_per_line=5,
        ),
        "meme": CaptionStyle(
            font_size=80,
            fill_rgb=(255, 255, 255),
            stroke_rgb=(0, 0, 0),
            stroke_w=8,
            highlight_rgb=(255, 255, 0),
            position="top",
            anim="none",
            max_words_per_line=4,
        ),
    }
    if name not in presets:
        raise ValueError(f"Unknown preset {name!r}. Available: {list(presets)}")
    return presets[name]


def _load_font(style: CaptionStyle, size: int | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    sz = size or style.font_size
    if style.font_path:
        font_path = Path(style.font_path)
        # Reject fonts outside assets/fonts/
        if not font_path.is_relative_to(_ASSETS_FONTS):
            resolved = font_path.resolve()
            if not any(resolved.is_relative_to(r) for r in [_ASSETS_FONTS.resolve()]):
                raise ValueError(
                    f"Font path {font_path!r} is outside allowed directory {_ASSETS_FONTS}"
                )
        return ImageFont.truetype(str(font_path), sz)
    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def _cache_key(text: str, style: CaptionStyle, dims: tuple[int, int]) -> str:
    payload = f"{text}|{style}|{dims}"
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _render_caption_frame(
    text: str,
    highlight_word: str | None,
    style: CaptionStyle,
    width: int,
    height: int,
    alpha: float = 1.0,
) -> Image.Image:
    """Render a single transparent PNG caption frame."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(style)

    # Wrap text
    wrapped = textwrap.fill(text, width=style.max_words_per_line * 8)
    lines = wrapped.split("\n")

    # Compute vertical position
    line_h = style.font_size + 8
    total_h = line_h * len(lines)
    if style.position == "top":
        y_start = 40
    elif style.position == "middle":
        y_start = (height - total_h) // 2
    else:  # bottom
        y_start = height - total_h - 60

    a = int(255 * alpha)

    for line in lines:
        # Centre-align
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(line) * (style.font_size // 2)

        x = (width - text_w) // 2

        # Draw stroke
        stroke_color = style.stroke_rgb + (a,)
        for dx in range(-style.stroke_w, style.stroke_w + 1):
            for dy in range(-style.stroke_w, style.stroke_w + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y_start + dy), line, font=font, fill=stroke_color)

        # Draw fill — highlight current word if karaoke mode
        if highlight_word and highlight_word in line and style.anim == "karaoke":
            # Simple: render whole line in highlight color
            fill_color = style.highlight_rgb + (a,)
        else:
            fill_color = style.fill_rgb + (a,)

        draw.text((x, y_start), line, font=font, fill=fill_color)
        y_start += line_h

    return img


def render_captions_over(
    input_mp4: str | Path,
    words: list[Word],
    style: CaptionStyle,
    output_mp4: str | Path,
) -> Path:
    """Overlay animated word-by-word captions on *input_mp4*, writing to *output_mp4*.

    Uses a PNG sequence overlay approach via ffmpeg.
    """
    import subprocess
    import tempfile

    src = safe_resolve(input_mp4, allowed_roots=[Path("output").resolve(), Path("samples").resolve()])
    dst = safe_resolve(output_mp4, allowed_roots=ALLOWED_OUTPUT_ROOTS)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Probe dimensions
    import json
    probe_result = subprocess.run(  # noqa: S603
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(src)],
        shell=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    probe_data = json.loads(probe_result.stdout)
    width, height = 1080, 1920
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = stream.get("width", 1080)
            height = stream.get("height", 1920)
            break

    # Group words into caption chunks
    chunk_size = style.max_words_per_line
    chunks: list[tuple[float, float, list[Word]]] = []
    for i in range(0, len(words), chunk_size):
        group = words[i : i + chunk_size]
        if not group:
            continue
        chunks.append((group[0].start, group[-1].end, group))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Get total frame count from video duration
        dur_result = subprocess.run(  # noqa: S603
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            shell=False, capture_output=True, stdin=subprocess.DEVNULL,
        )
        try:
            total_duration = float(dur_result.stdout.strip())
        except ValueError:
            total_duration = 60.0

        total_frames = int(math.ceil(total_duration * _FPS))

        # Render one frame per caption chunk covering its time range
        for frame_idx in range(total_frames):
            t = frame_idx / _FPS
            active_chunk = None
            for start, end, chunk_words in chunks:
                if start <= t <= end:
                    active_chunk = (start, end, chunk_words)
                    break

            if active_chunk is None:
                frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            else:
                start, end, chunk_words = active_chunk
                text = " ".join(w.text.strip() for w in chunk_words)
                # Find current word for karaoke highlight
                current_word = None
                for w in chunk_words:
                    if w.start <= t <= w.end:
                        current_word = w.text.strip()
                        break

                alpha = 1.0
                if style.anim == "fade":
                    chunk_dur = end - start
                    elapsed = t - start
                    if elapsed < 0.1:
                        alpha = elapsed / 0.1
                    elif elapsed > chunk_dur - 0.1:
                        alpha = (chunk_dur - elapsed) / 0.1

                frame = _render_caption_frame(text, current_word, style, width, height, alpha)

            frame.save(tmp / f"frame{frame_idx:06d}.png")

        # Overlay via ffmpeg
        args = [
            "ffmpeg", "-y",
            "-i", str(src),
            "-framerate", str(_FPS),
            "-i", str(tmp / "frame%06d.png"),
            "-filter_complex",
            "[0:v][1:v]overlay=0:0:format=auto[out]",
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(dst),
        ]
        subprocess.run(  # noqa: S603
            args, shell=False, stdin=subprocess.DEVNULL, check=True, capture_output=True
        )

    logger.info("captions.done", dst=dst.name, words=len(words))
    return dst
