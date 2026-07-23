"""Size-based rotation helper (absd/rotate.py)."""

from __future__ import annotations

import stat
from pathlib import Path

from absd import rotate


def test_no_rotate_under_cap(tmp_path: Path) -> None:
    p = tmp_path / "log"
    p.write_text("x" * 100)
    assert rotate.maybe_rotate(p, max_bytes=1000, keep=3) is False
    assert not (tmp_path / "log.1").exists()


def test_rotate_shifts_generations_and_caps_keep(tmp_path: Path) -> None:
    p = tmp_path / "log"
    # roll several times; only keep=2 generations survive
    for gen in ("a", "b", "c", "d"):
        p.write_text(gen * 50)
        assert rotate.maybe_rotate(p, max_bytes=10, keep=2) is True
    assert (tmp_path / "log.1").exists()
    assert (tmp_path / "log.2").exists()
    assert not (tmp_path / "log.3").exists()  # keep=2 → no .3
    # .1 is the most recent roll ('d'), .2 the one before ('c')
    assert (tmp_path / "log.1").read_text() == "d" * 50
    assert (tmp_path / "log.2").read_text() == "c" * 50


def test_rotated_paths_chronological(tmp_path: Path) -> None:
    p = tmp_path / "log"
    p.write_text("live")
    (tmp_path / "log.1").write_text("newer-roll")
    (tmp_path / "log.2").write_text("older-roll")
    paths = rotate.rotated_paths(p, keep=3)
    # oldest generation first, then live
    assert [x.read_text() for x in paths] == ["older-roll", "newer-roll", "live"]


def test_rotate_preserves_0600(tmp_path: Path) -> None:
    p = tmp_path / "log"
    p.write_text("y" * 50)
    rotate.maybe_rotate(p, max_bytes=10, keep=3, mode=0o600)
    assert stat.S_IMODE((tmp_path / "log.1").stat().st_mode) == 0o600
