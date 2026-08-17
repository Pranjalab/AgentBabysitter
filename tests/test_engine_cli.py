"""absd/engines/cli.py — config-aware engine resolution + cross-engine sessions/
attach (live-demo bug 2). Pure logic + monkeypatched fakes; no real engine."""

from __future__ import annotations

import json
from pathlib import Path

from absd import config as config_mod
from absd.config import DaemonConfig
from absd.engines import cli
from absd.engines.base import SessionInfo


class _FakeEng:
    def __init__(self, name: str, sessions: list[SessionInfo]) -> None:
        self.name = name
        self._sessions = sessions

    def list_sessions(self) -> list[SessionInfo]:
        return self._sessions


# ---- default engine follows config.json --------------------------------------


def test_resolve_default_follows_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ABS_ENGINE", raising=False)
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    config_mod.save(abs_home / "daemon" / "config.json", DaemonConfig(engine="herdr"))
    monkeypatch.setenv("ABS_HOME", str(abs_home))
    assert cli.resolve_default_engine_name() == "herdr"


def test_resolve_default_missing_config_is_auto(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ABS_ENGINE", raising=False)
    monkeypatch.setenv("ABS_HOME", str(tmp_path / "empty"))
    assert cli.resolve_default_engine_name() == "auto"


def test_resolve_default_env_overrides_config(monkeypatch, tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    config_mod.save(abs_home / "daemon" / "config.json", DaemonConfig(engine="herdr"))
    monkeypatch.setenv("ABS_HOME", str(abs_home))
    monkeypatch.setenv("ABS_ENGINE", "tmux")
    assert cli.resolve_default_engine_name() == "tmux"


# ---- sessions lists across engines -------------------------------------------


def test_sessions_lists_across_engines(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ABS_ENGINE", raising=False)
    e_tmux = _FakeEng("tmux", [SessionInfo("a", "abs-a", True, "/a", 1)])
    e_herdr = _FakeEng("herdr", [SessionInfo("b", "abs-b", True, "/b", 2)])
    monkeypatch.setattr(
        cli, "available_engines", lambda socket=cli.DEFAULT_SOCKET: [e_tmux, e_herdr]
    )
    rc = cli.main(["sessions", "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["engine"] for r in rows} == {"tmux", "herdr"}
    assert {r["profile"] for r in rows} == {"a", "b"}


def test_sessions_table_has_engine_column() -> None:
    rows = [
        ("tmux", SessionInfo("a", "abs-a", True, "/a", 1)),
        ("herdr", SessionInfo("b", "abs-b", True, "/b", 2)),
    ]
    table = cli.format_engine_sessions_table(rows)
    assert "ENGINE" in table and "tmux" in table and "herdr" in table
    assert "a" in table and "b" in table


# ---- attach resolves the owning engine ---------------------------------------


def test_attach_resolves_single_owner() -> None:
    # profile lives only in herdr → attach herdr (the demo's exact case).
    assert cli.resolve_attach_target("web", [("herdr", "web")]) == ("herdr", "web")


def test_attach_ambiguous_when_both_have_it() -> None:
    eng, msg = cli.resolve_attach_target("web", [("tmux", "web"), ("herdr", "web")])
    assert eng is None and "multiple engines" in msg


def test_attach_none_when_no_session() -> None:
    eng, msg = cli.resolve_attach_target("web", [])
    assert eng is None and "No live session" in msg


def test_attach_no_profile_single_live() -> None:
    assert cli.resolve_attach_target(None, [("herdr", "web")]) == ("herdr", "web")


def test_attach_no_profile_multiple_live() -> None:
    eng, msg = cli.resolve_attach_target(None, [("tmux", "a"), ("herdr", "b")])
    assert eng is None and "Multiple live sessions" in msg
