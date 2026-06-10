"""Storyboard normalizer — converts Story or Script into a unified Scene list."""

from __future__ import annotations

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class Scene(BaseModel):
    beat: str
    voiceover_text: str
    image_prompt: str
    duration_s: float
    mood_tag: str
    caption_text: str = ""
    citations: list[str] = []

    def model_post_init(self, __context: object) -> None:
        # Enforce minimum duration for readability
        if self.duration_s < 1.5:
            object.__setattr__(self, "duration_s", 1.5)


def storyboard(source: object) -> list[Scene]:
    """Normalize a Story or Script into a list of Scenes.

    Enriches image_prompt with visual style descriptors for coherence.
    Enforces caption_text ≤ 7 words per line.
    """
    scenes: list[Scene] = []

    # Handle Story objects
    if hasattr(source, "scenes") and hasattr(source, "logline"):
        for raw in source.scenes:
            caption = _truncate_caption(
                getattr(raw, "caption_text", None) or raw.voiceover_text
            )
            image_prompt = _enrich_image_prompt(raw.image_prompt, raw.mood_tag)
            scenes.append(
                Scene(
                    beat=raw.beat,
                    voiceover_text=raw.voiceover_text,
                    image_prompt=image_prompt,
                    duration_s=max(raw.duration_s, 1.5),
                    mood_tag=raw.mood_tag,
                    caption_text=caption,
                    citations=getattr(raw, "citations", []),
                )
            )

    # Handle Script objects
    elif hasattr(source, "lines") and hasattr(source, "format"):
        voiceover_lines = [
            ln for ln in source.lines if ln.type in ("voiceover", "dialogue", "action")
        ]
        chunk_dur = 5.0  # default seconds per spoken line
        for ln in voiceover_lines:
            scenes.append(
                Scene(
                    beat=ln.type,
                    voiceover_text=ln.text,
                    image_prompt=_enrich_image_prompt(
                        f"Cinematic still for: {ln.text[:80]}", "neutral"
                    ),
                    duration_s=chunk_dur,
                    mood_tag="neutral",
                    caption_text=_truncate_caption(ln.text),
                    citations=getattr(ln, "citations", []),
                )
            )
    else:
        raise TypeError(f"Cannot storyboard object of type {type(source).__name__}")

    logger.info("storyboard.done", scenes=len(scenes))
    return scenes


def _truncate_caption(text: str, max_words_per_line: int = 7) -> str:
    """Truncate caption text to ≤ max_words_per_line words."""
    words = text.split()
    return " ".join(words[:max_words_per_line])


def _enrich_image_prompt(base_prompt: str, mood: str) -> str:
    """Add cinematic style descriptors based on mood."""
    mood_styles = {
        "soothing": "soft golden hour lighting, shallow depth of field, warm tones",
        "punchy": "high contrast, dramatic lighting, bold composition, vivid colors",
        "mysterious": "foggy atmosphere, dark shadows, cinematic blue tones, moody",
        "uplifting": "bright natural light, vibrant colors, dynamic angle, inspiring",
        "educational": "clean modern design, well-lit, clear and informative visual",
        "neutral": "professional cinematography, clean lighting, sharp focus",
    }
    style = mood_styles.get(mood.lower(), mood_styles["neutral"])
    return f"{base_prompt}. Style: {style}. Vertical 9:16 composition. Ultra-realistic."
