"""Story generator — structured short-form stories grounded via Foundry IQ."""

from __future__ import annotations

import json
from typing import Literal

import structlog
from pydantic import BaseModel, ValidationError

from shortsforge.providers import llm
from shortsforge.security.prompt_guard import sanitize, wrap_untrusted

logger = structlog.get_logger(__name__)


class Scene(BaseModel):
    beat: str
    voiceover_text: str
    image_prompt: str
    duration_s: float
    mood_tag: str
    caption_text: str = ""
    citations: list[str] = []


class Story(BaseModel):
    title: str
    logline: str
    scenes: list[Scene]
    total_duration_s: float
    citations: list[str] = []


_SYSTEM = """You are an expert short-form video director specializing in vertical video (9:16).
Create stories structured for YouTube Shorts — hook in the first 3 seconds, fast pacing,
strong visual beats, voiceover that works without audio.

Respond ONLY with valid JSON matching the schema provided.
"""


async def generate_story(
    prompt: str,
    *,
    audience: str,
    length_seconds: int,
    tone: Literal["soothing", "punchy", "mysterious", "uplifting", "educational"],
    kb_id: str | None = None,
    seed: int | None = None,
) -> Story:
    """Generate a structured short-form story.

    If *kb_id* is provided, grounds the story with Foundry IQ retrieval.
    """
    # Sanitize user prompt to prevent injection
    safe_prompt = sanitize(prompt)

    grounding_context = ""
    grounding_citations: list[str] = []

    if kb_id:
        from shortsforge.providers.foundry_iq import FoundryIQ
        fiq = FoundryIQ.from_env()
        result = await fiq.kb_query(kb_id, safe_prompt, top_k=8)
        grounding_context = result.grounded_text  # already wrapped with guard
        grounding_citations = [c.source for c in result.citations]
        await fiq.close()

    schema = Story.model_json_schema()
    user_msg = f"""Create a {length_seconds}-second {tone} story for {audience} audience.

Prompt: {safe_prompt}

{grounding_context}

Requirements:
- Total duration MUST be within 10% of {length_seconds}s
- Each scene has a strong visual beat suitable for vertical video
- image_prompt is detailed enough for an AI image generator
- caption_text is ≤7 words per on-screen line
- Include at least one strong hook beat in the first scene

Respond with JSON matching this schema:
{json.dumps(schema, indent=2)}"""

    temperature = {
        "soothing": 0.5,
        "punchy": 0.9,
        "mysterious": 0.8,
        "uplifting": 0.7,
        "educational": 0.4,
    }[tone]

    raw = await llm.complete(
        _SYSTEM,
        user_msg,
        temperature=temperature,
        max_tokens=3000,
        response_format="json_object",
    )

    # Parse and validate — retry once on failure
    story = _parse_story(raw, grounding_citations)
    if story is None:
        logger.warning("story.parse_failed.retrying")
        retry_msg = (
            f"The previous response failed Pydantic validation. Fix and return valid JSON.\n"
            f"Error context: parsing failed.\nOriginal:\n{raw}"
        )
        raw2 = await llm.complete(
            _SYSTEM, retry_msg, temperature=0.2, max_tokens=3000, response_format="json_object"
        )
        story = _parse_story(raw2, grounding_citations)
        if story is None:
            raise RuntimeError("Story generation failed validation after retry")

    logger.info("story.done", title=story.title, scenes=len(story.scenes))
    return story


def _parse_story(raw: str, extra_citations: list[str]) -> Story | None:
    try:
        data = json.loads(raw)
        story = Story.model_validate(data)
        # Merge grounding citations
        all_cites = list(set(story.citations + extra_citations))
        return story.model_copy(update={"citations": all_cites})
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("story.parse_error", error=str(exc))
        return None
