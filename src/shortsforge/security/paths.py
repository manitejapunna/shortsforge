"""Path safety utilities — reject traversal, UNC, symlinks, and out-of-roots paths."""

from __future__ import annotations

import os
from pathlib import Path

_HOME = Path.home()

ALLOWED_INPUT_ROOTS: list[Path] = [
    Path("samples").resolve(),
    Path("output").resolve(),
    _HOME / ".shortsforge",
]

ALLOWED_OUTPUT_ROOTS: list[Path] = [
    Path("output").resolve(),
    _HOME / ".shortsforge",
]


class UnsafePathError(ValueError):
    """Raised when a path fails safety checks."""


def safe_resolve(p: str | Path, *, allowed_roots: list[Path] | None = None) -> Path:
    """Resolve *p* to an absolute path and validate it is safe.

    Raises UnsafePathError if:
    - The path is or contains a symlink
    - The resolved path is not under any of *allowed_roots*
    - The path is a UNC path (\\\\server\\share)
    - The path contains null bytes
    """
    raw = str(p)
    if "\x00" in raw:
        raise UnsafePathError("Path contains null byte")

    # Reject UNC paths on Windows
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise UnsafePathError(f"UNC paths are not allowed: {raw!r}")

    path = Path(raw)

    # Reject if any component is a symlink
    try:
        resolved = path.resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise UnsafePathError(f"Cannot resolve path {raw!r}: {exc}") from exc

    # Check for symlink at any component
    check = resolved
    while check != check.parent:
        if check.exists() and check.is_symlink():
            raise UnsafePathError(f"Symlink detected in path: {check}")
        check = check.parent

    roots = allowed_roots if allowed_roots is not None else ALLOWED_INPUT_ROOTS

    if not any(_is_under(resolved, root) for root in roots):
        raise UnsafePathError(
            f"Path {resolved!r} is outside allowed roots: "
            + ", ".join(str(r) for r in roots)
        )

    return resolved


def _is_under(path: Path, root: Path) -> bool:
    """Return True if *path* is a descendant of *root*."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def ensure_output_dir() -> Path:
    """Return the output directory, creating it if necessary."""
    out = Path("output").resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def safe_output_path(filename: str) -> Path:
    """Return a validated output path for *filename*."""
    out = ensure_output_dir()
    dest = (out / filename).resolve()
    if not _is_under(dest, out):
        raise UnsafePathError(f"Output filename {filename!r} would escape output dir")
    return dest
