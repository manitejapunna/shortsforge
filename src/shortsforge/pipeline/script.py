"""Script generator — grounded screenplay/dialogue/voiceover scripts via Foundry IQ."""

from __future__ import annotations

import json
from typing import Literal

import structlog
from pydantic import BaseModel, ValidationError

from shortsforge.providers import llm
from shortsforge.security.prompt_guard import sanitize

logger = structlog.get_logger(__name__)


class ScriptLine(BaseModel):
    type: Literal["slugline", "action", "character", "dialogue",
                  "parenthetical", "voiceover", "transition"]
    text: str
    speaker: str | None = None
    citations: list[str] = []


class Script(BaseModel):
    title: str
    genre: str
    format: Literal["screenplay", "dialogue", "voiceover"]
    characters: list[str]
    lines: list[ScriptLine]
    citations: list[str] = []

    def to_fountain(self) -> str:
        """Render as Fountain-compatible plain text (screenplay format)."""
        parts: list[str] = [f"Title: {self.title}\n"]
        for line in self.lines:
            if line.type == "slugline":
                parts.append(f"\n{line.text.upper()}\n")
            elif line.type == "action":
                parts.append(f"\n{line.text}\n")
            elif line.type == "character":
                parts.append(f"\n{line.text.upper()}\n")
            elif line.type == "parenthetical":
                parts.append(f"({line.text})\n")
            elif line.type == "dialogue":
                parts.append(f"{line.text}\n")
            elif line.type == "voiceover":
                parts.append(f"V.O.\n{line.text}\n")
            elif line.type == "transition":
                parts.append(f"\n{line.text.upper()}:\n")
        return "".join(parts)


_SYSTEM = """You are an expert short-form video scriptwriter.
Write concise, engaging scripts optimized for vertical video (9:16, ≤60 seconds).
Use vivid language and strong hooks. Follow the requested format precisely.
Respond ONLY with valid JSON matching the schema provided.
"""


async def generate_script(
    logline: str,
    *,
    genre: str,
    characters: list[str],
    format: Literal["screenplay", "dialogue", "voiceover"],
    kb_id: str | None = None,
) -> Script:
    """Generate a short-form script, optionally grounded via Foundry IQ."""
    safe_logline = sanitize(logline)
    safe_genre = sanitize(genre)
    safe_chars = [sanitize(c) for c in characters]

    grounding_context = ""
    grounding_citations: list[str] = []

    if kb_id:
        from shortsforge.providers.foundry_iq import FoundryIQ
        fiq = FoundryIQ.from_env()
        result = await fiq.kb_query(kb_id, safe_logline, top_k=8)
        grounding_context = result.grounded_text
        grounding_citations = [c.source for c in result.citations]
        await fiq.close()

    schema = Script.model_json_schema()
    user_msg = f"""Write a {format} script.

Logline: {safe_logline}
Genre: {safe_genre}
Characters: {', '.join(safe_chars)}
Format: {format}

{grounding_context}

Requirements:
- Short-form: fits within 60 seconds when performed
- Strong hook in first line
- Specific, vivid dialogue grounded in the knowledge base (if provided)
- Screenplay format follows Fountain conventions

Respond with JSON matching this schema:
{json.dumps(schema, indent=2)}"""

    raw = await llm.complete(
        _SYSTEM, user_msg, temperature=0.7, max_tokens=3000, response_format="json_object"
    )

    script = _parse_script(raw, grounding_citations)
    if script is None:
        logger.warning("script.parse_failed.retrying")
        raw2 = await llm.complete(
            _SYSTEM,
            f"Validation failed. Fix and return valid JSON.\nOriginal:\n{raw}",
            temperature=0.2,
            max_tokens=3000,
            response_format="json_object",
        )
        script = _parse_script(raw2, grounding_citations)
        if script is None:
            raise RuntimeError("Script generation failed validation after retry")

    logger.info("script.done", title=script.title, lines=len(script.lines))
    return script


def _parse_script(raw: str, extra_citations: list[str]) -> Script | None:
    try:
        data = json.loads(raw)
        script = Script.model_validate(data)
        all_cites = list(set(script.citations + extra_citations))
        return script.model_copy(update={"citations": all_cites})
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("script.parse_error", error=str(exc))
        return None
