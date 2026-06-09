"""Disk usage guard — rotate oldest files when a directory exceeds a cap."""

from __future__ import annotations

import os
from pathlib import Path


def get_dir_size(directory: Path) -> int:
    """Return total byte size of all files under *directory*."""
    total = 0
    for root, _, files in os.walk(directory):
        for fname in files:
            try:
                total += os.path.getsize(os.path.join(root, fname))
            except OSError:
                pass
    return total


def ensure_under_cap(directory: Path, max_bytes: int) -> list[Path]:
    """Delete oldest files in *directory* until total size <= *max_bytes*.

    Returns list of deleted paths.
    """
    directory = directory.resolve()
    deleted: list[Path] = []

    current = get_dir_size(directory)
    if current <= max_bytes:
        return deleted

    # Collect all files sorted oldest-first
    all_files: list[tuple[float, Path]] = []
    for root, _, files in os.walk(directory):
        for fname in files:
            p = Path(root) / fname
            try:
                mtime = p.stat().st_mtime
                all_files.append((mtime, p))
            except OSError:
                pass

    all_files.sort()  # oldest first

    for _, file_path in all_files:
        if current <= max_bytes:
            break
        try:
            size = file_path.stat().st_size
            file_path.unlink()
            current -= size
            deleted.append(file_path)
        except OSError:
            pass

    return deleted
