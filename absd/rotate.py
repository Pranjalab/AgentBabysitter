"""Size-based file rotation, logrotate-style (Step 1.8).

Replaces the crude "one ``.old`` roll" used for ``daemon.log`` and
``events.jsonl`` with proper N-generation rotation: at ``max_bytes`` the live
file becomes ``.1``, ``.1`` → ``.2``, … up to ``keep`` generations; anything
older is dropped. Renames only (``os.replace`` is atomic on the same fs), so a
concurrent reader/writer never sees a half-file.

Reading across rotation: :func:`rotated_paths` returns every generation
**oldest-first then the live file**, so an events reader that concatenates them
sees a continuous chronological stream even after a roll — the dashboard's
timeline reconstruction survives rotation (an obs residual, closed here).
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_KEEP = 3


def _gen(path: Path, n: int) -> Path:
    """The ``.n`` generation path for ``path`` (n >= 1)."""
    return path.with_name(f"{path.name}.{n}")


def maybe_rotate(path: Path, max_bytes: int, keep: int, mode: int = 0o600) -> bool:
    """Rotate ``path`` if it is over ``max_bytes``. Returns True if it rotated.

    ``keep`` is the number of ``.1``…``.keep`` generations retained. Never raises
    (a rotation failure must not disturb the writer); a best-effort no-op on error.
    """
    if keep < 1:
        keep = 1
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return False
        # Drop the oldest, then shift .k-1 → .k, …, .1 → .2, live → .1.
        oldest = _gen(path, keep)
        try:
            oldest.unlink()
        except OSError:
            pass
        for n in range(keep - 1, 0, -1):
            src = _gen(path, n)
            if src.exists():
                os.replace(str(src), str(_gen(path, n + 1)))
        os.replace(str(path), str(_gen(path, 1)))
        try:
            os.chmod(_gen(path, 1), mode)
        except OSError:
            pass
        return True
    except OSError:
        return False


def rotated_paths(path: Path, keep: int = DEFAULT_KEEP) -> list[Path]:
    """Existing files for ``path`` in CHRONOLOGICAL order: oldest generation
    first (``.keep`` … ``.1``), then the live ``path``. Missing generations are
    skipped, so a reader can concatenate the result seamlessly."""
    out: list[Path] = []
    for n in range(keep, 0, -1):
        p = _gen(path, n)
        if p.exists():
            out.append(p)
    if path.exists():
        out.append(path)
    return out
