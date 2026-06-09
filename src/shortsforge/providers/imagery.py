"""Imagery provider — AI image generation with moderation gating."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_CACHE_DIR = Path("output") / ".cache" / "images"
_SAFETY_PREPEND = "Safe, family-friendly, non-violent, non-sexual: "


def _cache_path(prompt: str) -> Path:
    key = hashlib.sha256(prompt.encode()).hexdigest()
    return _CACHE_DIR / f"{key}.png"


async def generate_image(
    prompt: str,
    *,
    width: int = 1080,
    height: int = 1920,
    retries: int = 2,
) -> Path:
    """Generate an image for a scene prompt.

    - Caches results by prompt hash.
    - Passes image through moderation; prepends safety prefix and retries once on fail.
    - Falls back to a blank gradient image after max retries.
    """
    from shortsforge.security.moderation import check_image

    cached = _cache_path(prompt)
    if cached.exists():
        logger.debug("imagery.cache_hit")
        return cached

    cached.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(retries + 1):
        safe_prompt = (_SAFETY_PREPEND + prompt) if attempt > 0 else prompt
        try:
            image_bytes = await _generate_via_openai(safe_prompt, width, height)
        except Exception as exc:
            logger.warning("imagery.generation_failed", attempt=attempt, error=str(exc))
            if attempt >= retries:
                return _fallback_image(cached, width, height)
            continue

        if await check_image(image_bytes):
            cached.write_bytes(image_bytes)
            return cached
        else:
            logger.warning("imagery.moderation_failed", attempt=attempt)
            if attempt >= retries:
                return _fallback_image(cached, width, height)

    return _fallback_image(cached, width, height)


async def _generate_via_openai(prompt: str, width: int, height: int) -> bytes:
    import base64
    import httpx
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    # DALL-E 3 supports 1024x1024, 1792x1024, or 1024x1792
    # Use 1024x1792 as closest to 1080x1920
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "dall-e-3",
                "prompt": prompt[:4000],
                "n": 1,
                "size": "1024x1792",
                "response_format": "b64_json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return base64.b64decode(data["data"][0]["b64_json"])


def _fallback_image(path: Path, width: int, height: int) -> Path:
    """Generate a soft gradient fallback image."""
    try:
        from PIL import Image
        img = Image.new("RGB", (width, height), color=(30, 30, 60))
        img.save(str(path), "PNG")
        logger.warning("imagery.using_fallback")
    except Exception:
        path.write_bytes(b"")
    return path
