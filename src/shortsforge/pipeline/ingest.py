"""Video ingest and transcription using faster-whisper."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional

import ffmpeg
import structlog
from pydantic import BaseModel, Field

from shortsforge.security.paths import (
    ALLOWED_INPUT_ROOTS,
    UnsafePathError,
    runtime_imports_dir,
    safe_resolve,
)
from shortsforge.security.ffmpeg import ensure_ffmpeg_tools_on_path

logger = structlog.get_logger(__name__)

# Configurable limits
_MAX_SIZE_BYTES = int(os.getenv("SHORTSFORGE_MAX_INPUT_SIZE_GB", "2")) * 1024**3
_MAX_DURATION_S = int(os.getenv("SHORTSFORGE_MAX_INPUT_DURATION_HOURS", "4")) * 3600
_WHISPER_MODEL = os.getenv("SHORTSFORGE_WHISPER_MODEL", "base.en")
_ALLOW_YOUTUBE_URL_INGEST = os.getenv("SHORTSFORGE_ALLOW_YOUTUBE_URL_INGEST", "1") == "1"
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
_REMOTE_IMPORT_DIR = runtime_imports_dir()


class InputTooLargeError(ValueError):
    """Input file exceeds size or duration limits."""


class UnsupportedMediaError(ValueError):
    """Input file cannot be probed or decoded by ffmpeg."""


class Word(BaseModel):
    start: float
    end: float
    text: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    speaker: Optional[str] = None


class Transcript(BaseModel):
    source_path: str
    duration_s: float
    language: str
    segments: list[Segment]

    @property
    def full_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    @property
    def all_words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]


def _probe_media(path: Path) -> dict:
    """Probe media file with ffmpeg. Raises UnsupportedMediaError on failure."""
    ensure_ffmpeg_tools_on_path()
    try:
        return ffmpeg.probe(str(path))
    except FileNotFoundError as exc:
        raise UnsupportedMediaError(
            "FFprobe is not installed or not on PATH. Install FFmpeg and ensure "
            "'ffprobe' is available in your terminal."
        ) from exc
    except ffmpeg.Error as exc:
        raise UnsupportedMediaError(
            f"ffmpeg cannot probe {path.name!r}: {exc.stderr.decode(errors='replace')}"
        ) from exc


def _get_duration(probe_data: dict) -> float:
    """Extract duration in seconds from ffprobe output."""
    try:
        return float(probe_data["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        # Try first video stream
        for stream in probe_data.get("streams", []):
            if "duration" in stream:
                return float(stream["duration"])
        return 0.0


def _is_youtube_url(raw: str) -> bool:
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    return host in _YOUTUBE_HOSTS


def _download_youtube_video(url: str) -> Path:
    """Download a YouTube URL into the runtime imports directory and return local path."""
    if not _ALLOW_YOUTUBE_URL_INGEST:
        raise ValueError("YouTube URL ingest is disabled by policy")

    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-untyped]
    except Exception as exc:
        raise RuntimeError(
            "YouTube URL support requires 'yt-dlp'. Install dependencies and retry."
        ) from exc

    from ulid import ULID

    _REMOTE_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    clip_id = str(ULID())
    outtmpl = str(_REMOTE_IMPORT_DIR / f"{clip_id}.%(ext)s")

    ydl_opts = {
        "format": "mp4/bestvideo+bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = Path(ydl.prepare_filename(info)).resolve()

    # Keep downloaded files under controlled directory.
    resolved = safe_resolve(downloaded, allowed_roots=ALLOWED_INPUT_ROOTS)
    if not resolved.exists():
        raise FileNotFoundError("yt-dlp completed but output file was not found")
    return resolved


def ingest(
    source: str | Path,
    *,
    request_id: str = "unknown",
) -> Transcript:
    """Ingest a media file and return a full Transcript.

    Args:
        source: Path to the media file (mp4, mov, m4a, wav).
        request_id: Correlation ID for structured logging.

    Raises:
        UnsafePathError: If the path is outside allowed roots.
        InputTooLargeError: If the file is too large or too long.
        UnsupportedMediaError: If ffmpeg cannot probe the file.
    """
    log = logger.bind(request_id=request_id)

    source_str = str(source)

    # --- Source safety ---
    if _is_youtube_url(source_str):
        resolved = _download_youtube_video(source_str)
    else:
        resolved = safe_resolve(source, allowed_roots=ALLOWED_INPUT_ROOTS)
    log.info("ingest.start", path=str(resolved))

    # --- File size guard ---
    size = resolved.stat().st_size
    if size > _MAX_SIZE_BYTES:
        raise InputTooLargeError(
            f"File size {size / 1024**3:.2f} GB exceeds limit of "
            f"{_MAX_SIZE_BYTES / 1024**3:.1f} GB"
        )

    # --- ffmpeg probe ---
    probe = _probe_media(resolved)
    duration = _get_duration(probe)

    if duration > _MAX_DURATION_S:
        raise InputTooLargeError(
            f"Duration {duration / 3600:.2f}h exceeds limit of "
            f"{_MAX_DURATION_S / 3600:.1f}h"
        )

    log.info("ingest.probed", duration_s=duration)

    # --- Transcription ---
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    model = WhisperModel(_WHISPER_MODEL, device="cpu", compute_type="int8")
    raw_segments, info = model.transcribe(
        str(resolved),
        word_timestamps=True,
    )

    segments: list[Segment] = []
    for seg in raw_segments:
        words = []
        if seg.words:
            for w in seg.words:
                words.append(
                    Word(
                        start=w.start,
                        end=w.end,
                        text=w.word,
                        confidence=getattr(w, "probability", 1.0),
                    )
                )
        segments.append(
            Segment(start=seg.start, end=seg.end, text=seg.text.strip(), words=words)
        )

    transcript = Transcript(
        source_path=str(resolved),
        duration_s=duration,
        language=info.language,
        segments=segments,
    )
    log.info("ingest.done", segments=len(segments), language=info.language)
    return transcript
