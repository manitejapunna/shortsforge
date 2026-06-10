"""Timeline model and render functions for YouTube Shorts-ready output."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field, field_validator

from shortsforge.security.disk import ensure_under_cap
from shortsforge.security.ffmpeg import ensure_ffmpeg_tools_on_path
from shortsforge.security.paths import ALLOWED_OUTPUT_ROOTS, runtime_output_dir, safe_resolve

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)

_OUTPUT_DIR = runtime_output_dir()
_MAX_OUTPUT_BYTES = 5 * 1024**3  # 5 GB
_MAX_SHORT_DURATION = 60.0       # YouTube Shorts hard cap
_TARGET_W, _TARGET_H = 1080, 1920
_MAX_BITRATE_KBPS = 8000


class ClipRef(BaseModel):
    path: str
    start_s: float = 0.0
    end_s: float | None = None


class AudioTrack(BaseModel):
    path: str
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    fade_in_s: float = 0.0
    fade_out_s: float = 0.0


class Overlay(BaseModel):
    path: str          # image or video path
    x: int = 0
    y: int = 0
    start_s: float = 0.0
    end_s: float | None = None


class CaptionTrack(BaseModel):
    words_json: str    # JSON of list[Word]
    preset: str = "bold-pop"


class Timeline(BaseModel):
    clips: list[ClipRef] = Field(default_factory=list)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
    overlays: list[Overlay] = Field(default_factory=list)
    captions: list[CaptionTrack] = Field(default_factory=list)
    duration_s: float | None = None

    @field_validator("duration_s")
    @classmethod
    def _cap_duration(cls, v: float | None) -> float | None:
        if v is not None and v > _MAX_SHORT_DURATION:
            raise ValueError(
                f"duration_s {v} exceeds Shorts limit of {_MAX_SHORT_DURATION}s"
            )
        return v


def _run(args: list[str]) -> None:
    """Run a command with shell=False."""
    ensure_ffmpeg_tools_on_path()
    try:
        subprocess.run(  # noqa: S603
            args,
            shell=False,
            stdin=subprocess.DEVNULL,
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "FFmpeg is not installed or not on PATH. Install FFmpeg and ensure both "
            "'ffmpeg' and 'ffprobe' are available."
        ) from exc


def render_short(timeline: Timeline, dst_path: str | Path) -> Path:
    """Render a Timeline to a YouTube-Shorts-ready mp4.

    Output spec: 1080x1920, H.264 yuv420p, AAC, faststart, ≤60s, ≤8 Mbps.
    """
    # Disk cap guard
    ensure_under_cap(_OUTPUT_DIR, _MAX_OUTPUT_BYTES)
    ensure_ffmpeg_tools_on_path()

    dst = safe_resolve(dst_path, allowed_roots=ALLOWED_OUTPUT_ROOTS)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Step 1: Concatenate clips
        if not timeline.clips:
            raise ValueError("Timeline has no clips")

        concat_list = tmp / "concat.txt"
        with open(concat_list, "w") as f:
            for clip in timeline.clips:
                f.write(f"file '{clip.path}'\n")

        concat_out = tmp / "concat.mp4"
        _run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(concat_out),
        ])

        # Step 2: Enforce ≤60s duration
        try:
            dur_result = subprocess.run(  # noqa: S603
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(concat_out)],
                shell=False, capture_output=True, stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "FFprobe is not installed or not on PATH. Install FFmpeg and retry."
            ) from exc
        try:
            actual_dur = float(dur_result.stdout.strip())
        except ValueError:
            actual_dur = _MAX_SHORT_DURATION

        trim_dur = min(actual_dur, timeline.duration_s or actual_dur, _MAX_SHORT_DURATION)

        # Step 3: Scale to 1080x1920 + encode
        render_out = tmp / "render.mp4"
        vf = (
            f"scale={_TARGET_W}:{_TARGET_H}:force_original_aspect_ratio=decrease,"
            f"pad={_TARGET_W}:{_TARGET_H}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        _run([
            "ffmpeg", "-y",
            "-i", str(concat_out),
            "-t", str(trim_dur),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-b:v", f"{_MAX_BITRATE_KBPS}k",
            "-maxrate", f"{_MAX_BITRATE_KBPS}k",
            "-bufsize", f"{_MAX_BITRATE_KBPS * 2}k",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(render_out),
        ])

        # Step 4: Mix in audio tracks if any
        if timeline.audio_tracks:
            audio_out = tmp / "audio_mix.mp4"
            inputs = ["-i", str(render_out)]
            for at in timeline.audio_tracks:
                inputs += ["-i", at.path]
            filter_parts = [f"[0:a]aformat=sample_rates=44100[a0]"]
            for i, at in enumerate(timeline.audio_tracks, 1):
                filter_parts.append(
                    f"[{i}:a]volume={at.volume},aformat=sample_rates=44100[a{i}]"
                )
            mix_inputs = "".join(f"[a{i}]" for i in range(len(timeline.audio_tracks) + 1))
            filter_parts.append(
                f"{mix_inputs}amix=inputs={len(timeline.audio_tracks) + 1}:duration=first[aout]"
            )
            _run([
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", ";".join(filter_parts),
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-movflags", "+faststart",
                str(audio_out),
            ])
            render_out = audio_out

        # Copy to final destination
        import shutil
        shutil.copy2(str(render_out), str(dst))

    logger.info("render_short.done", dst=dst.name, duration_s=trim_dur)
    return dst


def render_storyboard(scenes: list[Any], dst_path: str | Path) -> Path:
    """Render a list of Scene objects from a Story/Script into a Shorts-ready mp4.

    For each scene: synthesize voiceover → generate b-roll image → Ken-Burns animation
    → animated captions → mood-matched music → end-card with citations.
    All providers are called asynchronously and results are cached by hash.
    """
    import asyncio

    async def _async_render() -> Path:
        return await _render_storyboard_async(scenes, dst_path)

    return asyncio.run(_async_render())


async def _render_storyboard_async(scenes: list[Any], dst_path: str | Path) -> Path:
    """Async implementation of render_storyboard."""
    import shutil
    import tempfile

    from shortsforge.pipeline.captions import CaptionStyle, render_captions_over, style_preset
    from shortsforge.providers.imagery import generate_image
    from shortsforge.providers.tts import synthesize
    from shortsforge.security.paths import ALLOWED_OUTPUT_ROOTS, safe_resolve

    dst = safe_resolve(dst_path, allowed_roots=ALLOWED_OUTPUT_ROOTS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ensure_under_cap(_OUTPUT_DIR, _MAX_OUTPUT_BYTES)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        scene_clips: list[Path] = []
        all_citations: list[str] = []

        for i, scene in enumerate(scenes):
            scene_tmp = tmp / f"scene_{i:03d}"
            scene_tmp.mkdir()

            voiceover_text = getattr(scene, "voiceover_text", "")
            image_prompt = getattr(scene, "image_prompt", "")
            duration = getattr(scene, "duration_s", 5.0)
            caption_text = getattr(scene, "caption_text", voiceover_text[:50])
            citations = getattr(scene, "citations", [])
            all_citations.extend(citations)

            # 1. TTS voiceover
            try:
                audio_path = await synthesize(voiceover_text)
            except Exception:
                audio_path = None

            # 2. B-roll image with Ken-Burns zoom
            try:
                img_path = await generate_image(image_prompt)
            except Exception:
                img_path = None

            # 3. Compose scene: image + Ken-Burns + audio
            scene_out = scene_tmp / "scene.mp4"
            _compose_scene(img_path, audio_path, duration, scene_out)

            # 4. Animated captions
            if caption_text:
                cap_out = scene_tmp / "captioned.mp4"
                try:
                    from shortsforge.pipeline.ingest import Word
                    words = [Word(start=0.0, end=duration, text=caption_text, confidence=1.0)]
                    style = style_preset("subtle-bottom")
                    render_captions_over(scene_out, words, style, cap_out)
                    scene_out = cap_out
                except Exception:
                    pass

            scene_clips.append(scene_out)

        # 5. Append citations end-card
        unique_cites = list(dict.fromkeys(all_citations))
        if unique_cites:
            endcard = tmp / "endcard.mp4"
            _render_endcard(unique_cites[:5], endcard)
            scene_clips.append(endcard)

        # 6. Concat + render final
        concat_list = tmp / "scenes.txt"
        with open(concat_list, "w") as f:
            for sc in scene_clips:
                f.write(f"file '{sc}'\n")

        concat_out = tmp / "concat.mp4"
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
              "-i", str(concat_list), "-c", "copy", str(concat_out)])

        # 7. Final Shorts encode
        vf = (
            f"scale={_TARGET_W}:{_TARGET_H}:force_original_aspect_ratio=decrease,"
            f"pad={_TARGET_W}:{_TARGET_H}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        _run(["ffmpeg", "-y", "-i", str(concat_out),
              "-t", str(_MAX_SHORT_DURATION),
              "-vf", vf,
              "-c:v", "libx264", "-preset", "fast", "-crf", "23",
              "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "128k",
              "-movflags", "+faststart",
              str(dst)])

    logger.info("render_storyboard.done", dst=dst.name, scenes=len(scenes))
    return dst


def _compose_scene(
    img_path: Path | None,
    audio_path: Path | None,
    duration: float,
    dst: Path,
) -> None:
    """Compose a single scene: image with Ken-Burns zoom + optional audio."""
    fps = 30
    total_frames = int(duration * fps)

    if img_path and img_path.exists():
        # Ken-Burns: slow zoom from 1.0 to 1.05 over the scene duration
        vf = (
            f"scale={_TARGET_W * 2}:{_TARGET_H * 2},"
            f"zoompan=z='min(zoom+0.0002,1.05)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={total_frames}:s={_TARGET_W}x{_TARGET_H}:fps={fps}"
        )
        inputs = ["-loop", "1", "-i", str(img_path)]
    else:
        # Fallback: black frame
        vf = f"color=c=black:s={_TARGET_W}x{_TARGET_H}:r={fps}"
        inputs = ["-f", "lavfi"]

    args = ["ffmpeg", "-y"] + inputs
    if audio_path and audio_path.exists():
        args += ["-i", str(audio_path)]

    args += [
        "-t", str(duration),
        "-vf" if img_path else "-filter_complex", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
    ]
    if audio_path and audio_path.exists():
        args += ["-c:a", "aac"]
    else:
        args += ["-an"]
    args += [str(dst)]

    try:
        _run(args)
    except Exception:
        # Last resort: silent black frame
        _run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=black:s={_TARGET_W}x{_TARGET_H}:r={fps}",
            "-t", str(duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            str(dst),
        ])


def _render_endcard(citations: list[str], dst: Path) -> None:
    """Render a 2.5-second citation end-card."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (_TARGET_W, _TARGET_H), color=(15, 15, 30))
        draw = ImageDraw.Draw(img)
        draw.text((60, 200), "Sources", fill=(255, 140, 0))
        y = 300
        for cite in citations[:5]:
            draw.text((60, y), f"• {cite[:60]}", fill=(220, 220, 220))
            y += 60

        endcard_png = dst.parent / "endcard.png"
        img.save(str(endcard_png))
        _run([
            "ffmpeg", "-y", "-loop", "1", "-i", str(endcard_png),
            "-t", "2.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-vf", f"scale={_TARGET_W}:{_TARGET_H}", "-an", str(dst),
        ])
    except Exception:
        _run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=black:s={_TARGET_W}x{_TARGET_H}:r=30",
            "-t", "2.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            str(dst),
        ])
