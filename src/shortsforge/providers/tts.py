"""TTS provider — ElevenLabs with hash-based caching."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_CACHE_DIR = Path("output") / ".cache" / "tts"


def _cache_path(text: str, voice_id: str) -> Path:
    key = hashlib.sha256(f"{voice_id}:{text}".encode()).hexdigest()
    return _CACHE_DIR / f"{key}.mp3"


async def synthesize(text: str, *, voice_id: str = "default") -> Path:
    """Synthesize speech and return path to the audio file.

    Results are cached by (voice_id, text) hash for 24 hours.
    Falls back to gTTS if ElevenLabs key is not configured.
    """
    cached = _cache_path(text, voice_id)
    if cached.exists():
        logger.debug("tts.cache_hit")
        return cached

    cached.parent.mkdir(parents=True, exist_ok=True)
    api_key = os.getenv("ELEVENLABS_API_KEY")

    if api_key:
        await _elevenlabs_synthesize(text, voice_id, api_key, cached)
    else:
        logger.warning("ELEVENLABS_API_KEY not set; using gTTS fallback")
        await _gtts_synthesize(text, cached)

    return cached


async def _elevenlabs_synthesize(
    text: str, voice_id: str, api_key: str, dst: Path
) -> None:
    import httpx
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_multilingual_v2"},
        )
        resp.raise_for_status()
        dst.write_bytes(resp.content)


async def _gtts_synthesize(text: str, dst: Path) -> None:
    """Simple fallback using gTTS (no API key required)."""
    try:
        from gtts import gTTS  # type: ignore[import-untyped]
        tts = gTTS(text=text, lang="en", slow=False)
        tts.save(str(dst))
    except ImportError:
        # Last resort: write a silent 1s WAV
        import struct
        import wave
        with wave.open(str(dst.with_suffix(".wav")), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("<" + "h" * 16000, *([0] * 16000)))
        dst.unlink(missing_ok=True)
        dst.with_suffix(".wav").rename(dst)
