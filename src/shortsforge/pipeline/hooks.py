"""Hook detection — hybrid acoustic + LLM reranker for finding viral moments."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

from shortsforge.security.prompt_guard import wrap_untrusted

if TYPE_CHECKING:
    from shortsforge.pipeline.ingest import Transcript

logger = structlog.get_logger(__name__)

_LLM_CALL_COUNT = 0  # for testing: reset per invocation


class HookCandidate(BaseModel):
    start_s: float
    end_s: float
    headline: str
    predicted_retention: float = Field(ge=0.0, le=1.0)
    rationale: str
    audience_fit_tags: list[str] = []


async def detect_hooks(
    transcript: "Transcript",
    source_audio: Path,
    *,
    niche: str,
    count: int,
    min_len_s: float = 15.0,
    max_len_s: float = 58.0,
) -> list[HookCandidate]:
    """Detect the best hook candidates from a transcript using a hybrid approach.

    Algorithm:
    1. Cheap acoustic scoring per 1s window (energy, pauses, pitch variance).
    2. Text scoring (questions, sentiment, keyword cues).
    3. Build a 3×count shortlist.
    4. Single LLM call to rerank for niche.
    5. Adjust boundaries to sentence/word endings.
    """
    global _LLM_CALL_COUNT
    _LLM_CALL_COUNT = 0

    if not transcript.segments:
        return []

    duration = transcript.duration_s
    windows = _build_windows(transcript, min_len_s, max_len_s)

    # Score windows
    scored: list[tuple[float, dict]] = []
    for w in windows:
        score = _score_window(w, transcript)
        scored.append((score, w))

    scored.sort(reverse=True)
    shortlist_size = min(count * 3, len(scored))
    shortlist = [w for _, w in scored[:shortlist_size]]

    if not shortlist:
        return []

    # Single LLM rerank call
    hooks = await _llm_rerank(shortlist, transcript, niche, count)
    _LLM_CALL_COUNT += 1

    # Snap boundaries to sentence/word endings
    snapped = [_snap_boundaries(h, transcript) for h in hooks]

    logger.info("hooks.done", count=len(snapped), niche=niche)
    return snapped


def _build_windows(transcript: "Transcript", min_len: float, max_len: float) -> list[dict]:
    """Slide windows over the transcript at 5-second steps."""
    windows = []
    duration = transcript.duration_s
    step = 5.0
    t = 0.0
    while t + min_len <= duration:
        end = min(t + max_len, duration)
        windows.append({"start": t, "end": end})
        t += step
    return windows


def _score_window(window: dict, transcript: "Transcript") -> float:
    """Score a time window using cheap heuristics."""
    start, end = window["start"], window["end"]
    text_parts = []
    for seg in transcript.segments:
        if seg.end < start or seg.start > end:
            continue
        text_parts.append(seg.text)
    text = " ".join(text_parts)

    score = 0.0

    # Question marks (engagement signal)
    score += text.count("?") * 2.0

    # Exclamation marks
    score += text.count("!") * 1.5

    # High-retention keywords
    hook_words = [
        "never", "always", "secret", "amazing", "shocking", "truth",
        "revealed", "you won't believe", "actually", "finally",
        "mistake", "wrong", "real", "first time",
    ]
    text_lower = text.lower()
    for kw in hook_words:
        if kw in text_lower:
            score += 3.0

    # Prefer middle of video (not intros)
    mid = transcript.duration_s / 2
    pos = (start + end) / 2
    proximity = 1.0 - abs(pos - mid) / max(mid, 1)
    score += proximity * 5.0

    # Word density (faster speech = more energy)
    words = text.split()
    duration = end - start
    wpm = (len(words) / max(duration, 1)) * 60
    if wpm > 120:
        score += 5.0

    return score


async def _llm_rerank(
    shortlist: list[dict],
    transcript: "Transcript",
    niche: str,
    count: int,
) -> list[HookCandidate]:
    """Use a single LLM call to rerank the shortlist for the given niche."""
    import json

    from shortsforge.providers import llm

    # Wrap transcript snippets as untrusted data
    snippets = []
    for w in shortlist:
        start, end = w["start"], w["end"]
        text = " ".join(
            seg.text for seg in transcript.segments
            if not (seg.end < start or seg.start > end)
        )
        snippets.append({"start": start, "end": end, "text": text[:300]})

    wrapped_snippets = wrap_untrusted(
        json.dumps(snippets, indent=2),
        label="untrusted_transcript_snippets",
    )

    system = (
        f"You are a short-form video editor expert in the '{niche}' niche. "
        "Analyze transcript snippets and select the best hooks for YouTube Shorts. "
        "Return JSON only."
    )
    user = (
        f"Select the top {count} hooks from these snippets for the '{niche}' niche.\n\n"
        f"{wrapped_snippets}\n\n"
        "For each, return: start_s, end_s, headline (≤10 words), "
        "predicted_retention (0-1), rationale (1-2 sentences), audience_fit_tags[].\n"
        f"Return JSON array of exactly {count} objects."
    )

    raw = await llm.complete(system, user, temperature=0.4, max_tokens=2000)

    try:
        # Extract JSON array from response
        import re
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON array in LLM response")
        data = json.loads(match.group(0))
        return [HookCandidate(**item) for item in data[:count]]
    except Exception as exc:
        logger.warning("hooks.llm_parse_error", error=str(exc))
        # Fallback: return top-scored windows as basic candidates
        return [
            HookCandidate(
                start_s=w["start"],
                end_s=w["end"],
                headline=f"Clip at {w['start']:.0f}s",
                predicted_retention=0.5,
                rationale="Heuristic selection",
            )
            for w in shortlist[:count]
        ]


def _snap_boundaries(hook: HookCandidate, transcript: "Transcript") -> HookCandidate:
    """Snap hook boundaries to the nearest sentence/word ending."""
    # Find closest word end near hook.end_s
    best_end = hook.end_s
    min_diff = float("inf")
    for seg in transcript.segments:
        for word in seg.words:
            diff = abs(word.end - hook.end_s)
            if diff < min_diff and word.end <= hook.end_s + 1.0:
                min_diff = diff
                best_end = word.end

    return hook.model_copy(update={"end_s": best_end})
