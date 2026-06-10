"""FFmpeg tool discovery helpers.

Resolves ffmpeg/ffprobe from PATH or local bundled tools directory.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def ensure_ffmpeg_tools_on_path() -> None:
    """Ensure both ffmpeg and ffprobe are discoverable in PATH.

    Search order:
    1. Existing PATH (no-op if both found)
    2. SHORTSFORGE_FFMPEG_BIN env var
    3. <repo>/tools/ffmpeg/*/bin
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return

    candidates: list[Path] = []

    env_bin = os.getenv("SHORTSFORGE_FFMPEG_BIN")
    if env_bin:
        candidates.append(Path(env_bin))

    repo_root = Path(__file__).resolve().parents[3]
    tools_root = repo_root / "tools" / "ffmpeg"
    if tools_root.exists():
        for child in tools_root.iterdir():
            if child.is_dir():
                candidates.append(child / "bin")

    for cand in candidates:
        ffmpeg_bin = cand / "ffmpeg.exe"
        ffprobe_bin = cand / "ffprobe.exe"
        if ffmpeg_bin.exists() and ffprobe_bin.exists():
            current = os.environ.get("PATH", "")
            os.environ["PATH"] = (
                f"{cand}{os.pathsep}{current}" if current else str(cand)
            )
            return
