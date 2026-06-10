"""Path safety utilities — reject traversal, UNC, symlinks, and out-of-roots paths."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

# Resolve relative to the project root so the path is stable regardless of CWD.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # .../shortsforge/
_DEFAULT_RUNTIME_ROOT = _PROJECT_ROOT / "output"
_RUNTIME_ROOT = Path(os.getenv("SHORTSFORGE_RUNTIME_DIR", str(_DEFAULT_RUNTIME_ROOT))).resolve()
_OUTPUT_DIR = _RUNTIME_ROOT if _RUNTIME_ROOT.name.lower() == "output" else (_RUNTIME_ROOT / "output").resolve()
_IMPORTS_DIR = (_RUNTIME_ROOT / "imports").resolve()
_WORKSPACE_FILE = (_RUNTIME_ROOT / "workspace.json").resolve()
_STUDIO_DIRS: dict[str, Path] = {
    "story": (_OUTPUT_DIR / "story").resolve(),
    "script": (_OUTPUT_DIR / "script").resolve(),
    "repurpose": (_OUTPUT_DIR / "repurpose").resolve(),
}


def runtime_root() -> Path:
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    return _RUNTIME_ROOT


def runtime_output_dir() -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for d in _STUDIO_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
    return _OUTPUT_DIR


def runtime_imports_dir() -> Path:
    _IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return _IMPORTS_DIR


def runtime_workspace_file() -> Path:
    root = runtime_root()
    return (root / "workspace.json").resolve()

ALLOWED_INPUT_ROOTS: list[Path] = [
    Path("samples").resolve(),
    _OUTPUT_DIR,
    _IMPORTS_DIR,
    _RUNTIME_ROOT,
]

ALLOWED_OUTPUT_ROOTS: list[Path] = [
    _OUTPUT_DIR,
    _RUNTIME_ROOT,
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
    return runtime_output_dir()


def safe_output_path(
    filename: str,
    *,
    studio: Literal["story", "script", "repurpose"] = "repurpose",
) -> Path:
    """Return a validated output path for *filename* in a studio subdirectory."""
    ensure_output_dir()
    out = _STUDIO_DIRS[studio]
    dest = (out / filename).resolve()
    if not _is_under(dest, out):
        raise UnsafePathError(f"Output filename {filename!r} would escape output dir")
    return dest
