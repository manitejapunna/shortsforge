"""One-shot repurpose orchestrator — ingest → transcribe → hooks → cut → render."""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class ClipResult(BaseModel):
    clip_id: str
    path: Path
    title: str
    hook: dict
    predicted_retention: float
    citations: list[str] = []


async def repurpose(
    video: Path,
    *,
    niche: str,
    count: int,
    caption_preset: str = "bold-pop",
    add_broll: bool = True,
    kb_id: str | None = None,
) -> list[ClipResult]:
    """Full repurpose pipeline — ingest → transcribe → detect_hooks → cut → render.

    Runs up to 3 clips concurrently.
    """
    from shortsforge.pipeline.edit import reformat_to_vertical
    from shortsforge.pipeline.hooks import detect_hooks
    from shortsforge.pipeline.ingest import ingest
    from shortsforge.pipeline.render import ClipRef, Timeline, render_short
    from shortsforge.security.paths import safe_output_path
    from shortsforge.security.rate_limit import LLM_BUCKET

    # Step 1: Ingest + transcribe
    logger.info("repurpose.ingest", video=str(video))
    transcript = ingest(video)

    # Step 2: Detect hooks
    logger.info("repurpose.detect_hooks", niche=niche, count=count)
    hooks = await detect_hooks(transcript, video, niche=niche, count=count)

    if not hooks:
        logger.warning("repurpose.no_hooks_found")
        return []

    # Step 3: Process hooks in parallel (max 3)
    semaphore = asyncio.Semaphore(3)

    async def process_hook(hook: object, idx: int) -> ClipResult | None:
        async with semaphore:
            try:
                from shortsforge.pipeline.captions import render_captions_over, style_preset
                from shortsforge.pipeline.edit import cut
                from ulid import ULID

                clip_id = str(ULID())
                # Cut
                cut_dst = safe_output_path(f"{clip_id}_cut.mp4")
                cut_path = cut(str(video), hook.start_s, hook.end_s, cut_dst)

                # Reformat vertical
                vert_dst = safe_output_path(f"{clip_id}_vertical.mp4")
                vert_path = reformat_to_vertical(cut_path, vert_dst, "speaker_track")

                # Captions
                style = style_preset(caption_preset)
                cap_dst = safe_output_path(f"{clip_id}_captioned.mp4")
                cap_path = render_captions_over(
                    vert_path,
                    transcript.all_words,
                    style,
                    cap_dst,
                )

                # Render final short
                timeline = Timeline(clips=[ClipRef(path=str(cap_path))])
                final_dst = safe_output_path(f"{clip_id}_short.mp4")
                final_path = render_short(timeline, final_dst)

                citations: list[str] = []
                if kb_id:
                    from shortsforge.providers.foundry_iq import FoundryIQ
                    fiq = FoundryIQ.from_env()
                    result = await fiq.kb_query(kb_id, hook.headline, top_k=3)
                    citations = [c.source for c in result.citations]
                    await fiq.close()

                return ClipResult(
                    clip_id=clip_id,
                    path=final_path,
                    title=hook.headline,
                    hook=hook.model_dump(),
                    predicted_retention=hook.predicted_retention,
                    citations=citations,
                )
            except Exception:
                logger.exception("repurpose.clip_failed", idx=idx)
                return None

    tasks = [process_hook(h, i) for i, h in enumerate(hooks)]
    results_raw = await asyncio.gather(*tasks)
    results = [r for r in results_raw if r is not None]

    logger.info("repurpose.done", clips=len(results))
    return results
