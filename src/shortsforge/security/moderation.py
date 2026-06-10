"""Content moderation — fail-closed wrapper around OpenAI omni-moderation."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2


async def check_text(text: str) -> bool:
    """Return True if *text* passes moderation, False if flagged.

    Fails CLOSED on network errors (returns False after retries).
    Never logs the moderated content itself.
    """
    import httpx

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; skipping text moderation (allow)")
        return True

    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/moderations",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": "omni-moderation-latest", "input": text},
                )
                resp.raise_for_status()
                data = resp.json()
                flagged: bool = data["results"][0]["flagged"]
                if flagged:
                    logger.warning(
                        "Text moderation: content flagged [attempt=%d]", attempt
                    )
                return not flagged
        except Exception:
            logger.exception("Text moderation request failed [attempt=%d]", attempt)
            if attempt >= _MAX_RETRIES:
                logger.error(
                    "Moderation failed after %d retries; failing closed", _MAX_RETRIES
                )
                return False
    return False


async def check_image(image_bytes: bytes) -> bool:
    """Return True if *image_bytes* passes moderation, False if flagged.

    Falls back to False (fail closed) on any error.
    """
    import base64

    import httpx

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; skipping image moderation (allow)")
        return True

    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": "omni-moderation-latest",
        "input": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        ],
    }

    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/moderations",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                flagged: bool = data["results"][0]["flagged"]
                if flagged:
                    logger.warning(
                        "Image moderation: content flagged [attempt=%d]", attempt
                    )
                return not flagged
        except Exception:
            logger.exception("Image moderation request failed [attempt=%d]", attempt)
            if attempt >= _MAX_RETRIES:
                logger.error("Image moderation failed; failing closed")
                return False
    return False
