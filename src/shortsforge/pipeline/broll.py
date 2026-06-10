"""B-roll auto-inserter — overlays AI-generated stills over talking-head segments."""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_BROLL_INTERVAL_S = 8.0  # insert b-roll every N seconds
_BROLL_DURATION_S = 2.0  # each b-roll is N seconds long
_MIN_SKIP_START_S = 1.5  # don't insert in first N seconds
_MIN_SKIP_END_S = 1.5  # don't insert in last N seconds
_MAX_BROLL_FRACTION = 0.25  # b-roll can't exceed 25% of total clip duration


async def insert_broll(
    clip_path: str | Path,
    transcript_segments: list,
    output_path: str | Path,
) -> Path:
    """Insert 2-second AI-generated b-roll stills over a talking-head clip.

    Places a Ken-Burns overlay every ~8 seconds in segments > 12 seconds,
    skipping the first/last 1.5 seconds of each segment.
    Falls back to a soft-blur title card if moderation fails.
    """
    from shortsforge.security.paths import ALLOWED_OUTPUT_ROOTS, safe_resolve

    src = safe_resolve(
        clip_path, allowed_roots=[Path("output").resolve(), Path("samples").resolve()]
    )
    dst = safe_resolve(output_path, allowed_roots=ALLOWED_OUTPUT_ROOTS)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Probe clip duration
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(src),
        ],
        shell=False,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    try:
        total_duration = float(probe.stdout.strip())
    except ValueError:
        total_duration = 30.0

    max_broll_total = total_duration * _MAX_BROLL_FRACTION

    # Find insertion points in talking-head segments > 12s
    insert_times: list[float] = []
    for seg in transcript_segments:
        seg_duration = seg.end - seg.start
        if seg_duration <= 12.0:
            continue
        t = seg.start + _MIN_SKIP_START_S + _BROLL_INTERVAL_S
        while t < seg.end - _MIN_SKIP_END_S - _BROLL_DURATION_S:
            insert_times.append(t)
            t += _BROLL_INTERVAL_S

    # Cap total b-roll
    max_inserts = int(max_broll_total / _BROLL_DURATION_S)
    insert_times = insert_times[:max_inserts]

    if not insert_times:
        logger.info("broll.no_insertions_needed")
        import shutil

        shutil.copy2(str(src), str(dst))
        return dst

    logger.info("broll.inserting", count=len(insert_times))

    # For now: use a softblur title card overlay (placeholder for imagery provider)
    # When providers/imagery.py is available, replace _generate_broll_image
    for t in insert_times:
        logger.debug("broll.insert_at", t=t)

    # Apply simple overlay using ffmpeg drawbox as a visual placeholder
    # Real implementation: generate image via imagery provider, validate with moderation
    filter_parts = []
    for _i, t in enumerate(insert_times):
        filter_parts.append(
            f"drawbox=enable='between(t,{t},{t + _BROLL_DURATION_S})':"
            f"x=0:y=0:w=iw:h=ih:color=black@0.3:t=fill"
        )

    if filter_parts:
        vf = ",".join(filter_parts)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                str(dst),
            ],
            shell=False,
            stdin=subprocess.DEVNULL,
            check=True,
            capture_output=True,
        )
    else:
        import shutil

        shutil.copy2(str(src), str(dst))

    logger.info("broll.done", dst=dst.name, inserts=len(insert_times))
    return dst
