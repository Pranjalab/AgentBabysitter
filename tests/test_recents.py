"""Recent-launch store (absd/recents.py) + its CLI."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from absd.recents import CAP, Recents, main, resolve_label
from absd.registry import Registry


def _rec(abs_home: Path) -> Recents:
    return Recents(abs_home / "daemon" / "recents.json")


def _home(tmp_path: Path) -> Path:
    h = tmp_path / "abs"
    (h / "daemon").mkdir(parents=True)
    return h


def test_record_list_most_recent_first(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    r = _rec(abs_home)
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    r.record("default", str(a), "a", "normal")
    r.record("default", str(b), "b", "away")
    entries = r.list("default")
    assert [e.label for e in entries] == ["b", "a"]  # most-recent first
    assert entries[0].mode == "away"
    assert entries[0].path == str(b.resolve())


def test_record_dedup_moves_to_top_updates_mode(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    r = _rec(abs_home)
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    r.record("default", str(a), "a", "normal")
    r.record("default", str(b), "b", "normal")
    r.record("default", str(a), "a", "away")  # re-launch a, now away
    entries = r.list("default")
    assert [e.label for e in entries] == ["a", "b"]  # a moved to top
    assert entries[0].mode == "away"  # mode refreshed
    assert len(entries) == 2  # no duplicate row


def test_cap_enforced(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    r = _rec(abs_home)
    dirs = []
    for i in range(CAP + 3):
        d = tmp_path / f"p{i}"; d.mkdir(); dirs.append(d)
        r.record("default", str(d), f"p{i}", "normal")
    entries = r.list("default")
    assert len(entries) == CAP
    # newest kept, oldest dropped
    assert entries[0].label == f"p{CAP + 2}"


def test_per_profile_isolation(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    r = _rec(abs_home)
    a = tmp_path / "a"; a.mkdir()
    r.record("work", str(a), "a", "normal")
    assert r.list("default") == []
    assert len(r.list("work")) == 1


def test_remove(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    r = _rec(abs_home)
    a = tmp_path / "a"; a.mkdir()
    r.record("default", str(a), "a", "normal")
    assert r.remove("default", str(a)) is True
    assert r.list("default") == []
    assert r.remove("default", str(a)) is False  # tolerant no-op


def test_corrupt_file_reads_empty(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    (abs_home / "daemon" / "recents.json").write_text("{ not json ]")
    assert _rec(abs_home).list("default") == []


def test_bad_mode_normalized(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    r = _rec(abs_home)
    a = tmp_path / "a"; a.mkdir()
    r.record("default", str(a), "a", "bananas")  # invalid mode
    assert r.list("default")[0].mode == "normal"


def test_file_is_0600(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    r = _rec(abs_home)
    a = tmp_path / "a"; a.mkdir()
    r.record("default", str(a), "a", "normal")
    mode = stat.S_IMODE((abs_home / "daemon" / "recents.json").stat().st_mode)
    assert mode == 0o600


def test_resolve_label_prefers_registry(tmp_path: Path) -> None:
    abs_home = _home(tmp_path)
    proj = tmp_path / "myrepo"; proj.mkdir()
    Registry(abs_home / "daemon" / "registry.json").add(proj)
    # a registered project keeps its registry label (here == basename), a random
    # dir falls back to basename.
    assert resolve_label(abs_home, str(proj)) == "myrepo"
    other = tmp_path / "scratch"; other.mkdir()
    assert resolve_label(abs_home, str(other)) == "scratch"


# ---- CLI ---------------------------------------------------------------------


def test_cli_add_and_list(tmp_path: Path, capsys) -> None:
    abs_home = _home(tmp_path)
    proj = tmp_path / "web"; proj.mkdir()
    rc = main([
        "--abs-home", str(abs_home), "add",
        "--profile", "default", "--path", str(proj), "--mode", "away",
    ])
    assert rc == 0
    data = json.loads((abs_home / "daemon" / "recents.json").read_text())
    assert data["default"][0]["path"] == str(proj.resolve())
    assert data["default"][0]["mode"] == "away"

    capsys.readouterr()
    rc = main(["--abs-home", str(abs_home), "list", "--profile", "default"])
    assert rc == 0
    assert str(proj.resolve()) in capsys.readouterr().out
