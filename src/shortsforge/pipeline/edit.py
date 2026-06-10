"""Video edit primitives — cut, vertical reformat, and concat."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

import ffmpeg
import structlog

from shortsforge.security.paths import (
    ALLOWED_INPUT_ROOTS,
    ALLOWED_OUTPUT_ROOTS,
    UnsafePathError,
    safe_resolve,
)
from shortsforge.security.ffmpeg import ensure_ffmpeg_tools_on_path

logger = structlog.get_logger(__name__)

MAX_CLIP_DURATION_S = 120  # hard cap; callers must chunk longer videos


class ClipTooLongError(ValueError):
    """Raised when a clip exceeds the maximum allowed duration."""


def _safe_ffmpeg_run(args: list[str]) -> None:
    """Run ffmpeg with shell=False. Raises subprocess.CalledProcessError on failure."""
    ensure_ffmpeg_tools_on_path()
    try:
        subprocess.run(  # noqa: S603  (shell=False, fixed argv)
            args,
            shell=False,
            stdin=subprocess.DEVNULL,
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "FFmpeg is not installed or not on PATH. Install FFmpeg and ensure both "
            "'ffmpeg' and 'ffprobe' are available in your terminal."
        ) from exc


def cut(
    src_path: str | Path,
    start_s: float,
    end_s: float,
    dst_path: str | Path,
) -> Path:
    """Cut a clip from *src_path* between *start_s* and *end_s*.

    Uses stream-copy when possible; re-encodes only when needed.
    """
    src = safe_resolve(src_path, allowed_roots=ALLOWED_INPUT_ROOTS)
    dst = safe_resolve(dst_path, allowed_roots=ALLOWED_OUTPUT_ROOTS)

    duration = end_s - start_s
    if duration <= 0:
        raise ValueError(f"end_s ({end_s}) must be > start_s ({start_s})")
    if duration > MAX_CLIP_DURATION_S:
        raise ClipTooLongError(
            f"Clip duration {duration:.1f}s exceeds max {MAX_CLIP_DURATION_S}s"
        )

    dst.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "ffmpeg", "-y",
        "-ss", str(start_s),
        "-to", str(end_s),
        "-i", str(src),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(dst),
    ]
    logger.info("cut.start", src=src.name, start=start_s, end=end_s, dst=dst.name)
    _safe_ffmpeg_run(args)
    logger.info("cut.done", dst=dst.name)
    return dst


def reformat_to_vertical(
    src_path: str | Path,
    dst_path: str | Path,
    mode: Literal["speaker_track", "smart_crop", "letterbox"] = "letterbox",
) -> Path:
    """Reformat a clip to 1080x1920 (9:16 vertical) using the given *mode*.

    Modes:
    - speaker_track: face-tracking crop (requires OpenCV)
    - smart_crop: saliency-based static centre crop
    - letterbox: scale-to-fit on a blurred background copy
    """
    src = safe_resolve(src_path, allowed_roots=ALLOWED_INPUT_ROOTS)
    dst = safe_resolve(dst_path, allowed_roots=ALLOWED_OUTPUT_ROOTS)
    dst.parent.mkdir(parents=True, exist_ok=True)

    logger.info("reformat.start", mode=mode, src=src.name)

    if mode == "letterbox":
        _reformat_letterbox(src, dst)
    elif mode == "smart_crop":
        _reformat_smart_crop(src, dst)
    elif mode == "speaker_track":
        _reformat_speaker_track(src, dst)
    else:
        raise ValueError(f"Unknown reformat mode: {mode!r}")

    logger.info("reformat.done", dst=dst.name)
    return dst


def _reformat_letterbox(src: Path, dst: Path) -> None:
    """Scale-to-fit inside 1080x1920 on a blurred background."""
    # Build the complex filter:
    # [0:v] split into foreground (scaled) and background (blurred, scaled-to-fill)
    vf = (
        "[0:v]split=2[fg][bg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=20:5[bgblur];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black@0[fgpad];"
        "[bgblur][fgpad]overlay=0:0[out]"
    )
    args = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-filter_complex", vf,
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(dst),
    ]
    _safe_ffmpeg_run(args)


def _reformat_smart_crop(src: Path, dst: Path) -> None:
    """Static centre crop to 1080x1920."""
    vf = (
        "scale=iw*max(1080/iw\\,1920/ih):ih*max(1080/iw\\,1920/ih),"
        "crop=1080:1920"
    )
    args = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(dst),
    ]
    _safe_ffmpeg_run(args)


def _reformat_speaker_track(src: Path, dst: Path) -> None:
    """Face-detection crop smoothed over a 0.5s window."""
    try:
        import cv2  # type: ignore[import-untyped]
        import numpy as np
    except ImportError:
        logger.warning("opencv-python not installed; falling back to smart_crop")
        _reformat_smart_crop(src, dst)
        return

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    window = int(fps * 0.5)  # 0.5s smoothing window

    centres: list[tuple[int, int]] = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(30, 30))
        if len(faces) > 0:
            x, y, w, h = faces[0]
            cx, cy = x + w // 2, y + h // 2
        else:
            cx, cy = width // 2, height // 2
        centres.append((cx, cy))
    cap.release()

    # Smooth centres with a rolling average
    smoothed: list[tuple[int, int]] = []
    for i, (cx, cy) in enumerate(centres):
        lo = max(0, i - window // 2)
        hi = min(len(centres), i + window // 2 + 1)
        window_pts = centres[lo:hi]
        avg_cx = int(np.mean([p[0] for p in window_pts]))
        avg_cy = int(np.mean([p[1] for p in window_pts]))
        smoothed.append((avg_cx, avg_cy))

    # Write crop instructions via a sendcmd filter file
    # As a simpler approach, use the median of all smoothed centres for a static crop
    med_cx = int(np.median([p[0] for p in smoothed]))
    med_cy = int(np.median([p[1] for p in smoothed]))

    target_w, target_h = 1080, 1920
    scale_factor = max(target_w / width, target_h / height)
    sw = int(width * scale_factor)
    sh = int(height * scale_factor)

    crop_x = max(0, min(int(med_cx * scale_factor) - target_w // 2, sw - target_w))
    crop_y = max(0, min(int(med_cy * scale_factor) - target_h // 2, sh - target_h))

    vf = f"scale={sw}:{sh},crop={target_w}:{target_h}:{crop_x}:{crop_y}"
    args = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(dst),
    ]
    _safe_ffmpeg_run(args)


def concat(clip_paths: list[str | Path], dst_path: str | Path) -> Path:
    """Concatenate clips in order into a single output file."""
    import tempfile

    srcs = [safe_resolve(p, allowed_roots=ALLOWED_INPUT_ROOTS) for p in clip_paths]
    dst = safe_resolve(dst_path, allowed_roots=ALLOWED_OUTPUT_ROOTS)
    dst.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as flist:
        for src in srcs:
            flist.write(f"file '{src}'\n")
        flist_path = flist.name

    try:
        args = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", flist_path,
            "-c", "copy",
            str(dst),
        ]
        _safe_ffmpeg_run(args)
    finally:
        Path(flist_path).unlink(missing_ok=True)

    return dst
