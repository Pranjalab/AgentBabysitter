"""Phase 4 — startup banner + session picker."""

from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from cldx.memory import Memory
from cldx.policy_engine import PolicyEngine
from cldx.session_picker import Pane
from cldx.startup import (
    StartupChoice,
    _build_pick_rows,
    _format_ago,
    run_startup,
    show_banner,
    spawn_new_claude_session,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLDX_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def captured_console():
    """Returns (console, get_output) — Rich writing into a StringIO."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=120)
    return console, (lambda: buf.getvalue())


def test_show_banner_contains_agent_and_profile(policy_path, isolated_home,
                                                  captured_console):
    console, get_output = captured_console
    policy = PolicyEngine(policy_path)
    memory = Memory()
    memory.set_agent_name("Aria")

    show_banner(policy, memory, console=console)
    out = get_output()
    assert "Aria" in out
    assert "auto-approve" in out
    assert "telegram" in out.lower()
    assert "cldx" in out.lower()


def test_show_banner_announces_yolo_learned_count(policy_path, isolated_home,
                                                    captured_console):
    console, get_output = captured_console
    policy = PolicyEngine(policy_path, profile_override="yolo")
    memory = Memory()
    memory.learn(approve=True, pattern="A", profile="yolo")
    memory.learn(approve=True, pattern="B", profile="yolo")

    show_banner(policy, memory, console=console)
    assert "2" in get_output() and "remember" in get_output().lower()


def test_pick_rows_lists_live_panes_and_recent_sessions(isolated_home):
    panes = [Pane(target="0:0.0", current_command="2.1.150", title="✳ Claude Code")]
    sessions_dir = isolated_home / "sessions" / "auto-approve"
    sessions_dir.mkdir(parents=True)
    s1 = sessions_dir / "2026-05-26T08-00-00.jsonl"
    s1.write_text('{"t":"2026-05-26T08:00:00+00:00","kind":"note","message":"x"}\n')

    rows = _build_pick_rows(panes, [s1])
    kinds = [r.kind for r in rows]
    assert "resume" in kinds
    assert "connect" in kinds
    assert "start" in kinds


def test_pick_rows_with_no_recent_no_panes_still_offers_start():
    rows = _build_pick_rows([], [])
    assert len(rows) == 1
    assert rows[0].kind == "start"


async def test_run_startup_select_connect_returns_pane(policy_path, isolated_home,
                                                         captured_console):
    console, _ = captured_console
    policy = PolicyEngine(policy_path)
    memory = Memory()

    with patch("cldx.startup.list_panes") as mock_list, \
         patch("cldx.startup.recent_sessions", return_value=[]):
        mock_list.return_value = [
            Pane(target="0:0.0", current_command="claude", title="✳ Claude Code"),
        ]
        choice = await run_startup(policy, memory, console=console,
                                    input_fn=lambda _prompt: "1")
    assert isinstance(choice, StartupChoice)
    assert choice.pane == "0:0.0"
    assert choice.resume_from is None


async def test_run_startup_select_start_spawns_new(policy_path, isolated_home,
                                                    captured_console):
    console, _ = captured_console
    policy = PolicyEngine(policy_path)
    memory = Memory()

    fake_runs: list[list[str]] = []

    def fake_runner(cmd):
        fake_runs.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("cldx.startup.list_panes", return_value=[]), \
         patch("cldx.startup.recent_sessions", return_value=[]), \
         patch("cldx.startup._default_runner", side_effect=fake_runner):
        choice = await run_startup(policy, memory, console=console,
                                    input_fn=lambda _prompt: "1")

    cmds = [" ".join(c) for c in fake_runs]
    assert any("new-session" in c for c in cmds)
    assert any("send-keys" in c and "claude" in c for c in cmds)
    assert choice.pane.endswith(":0.0")


def test_spawn_new_claude_session_uses_unique_name(isolated_home,
                                                     captured_console):
    console, _ = captured_console
    runs = []

    def fake_runner(cmd):
        runs.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    pane = spawn_new_claude_session(console=console, runner=fake_runner)
    assert pane.endswith(":0.0")
    new_session_cmd = runs[0]
    assert "new-session" in new_session_cmd
    name = new_session_cmd[new_session_cmd.index("-s") + 1]
    assert pane.startswith(name + ":")


def test_format_ago_handles_iso_8601():
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert "min ago" in _format_ago(recent)
    assert _format_ago(None) == "unknown"
    assert _format_ago("not a date") == "not a date"


async def test_run_startup_rejects_invalid_input(policy_path, isolated_home,
                                                   captured_console):
    console, _ = captured_console
    policy = PolicyEngine(policy_path)
    memory = Memory()

    inputs = iter(["abc", "99", "", "1"])

    with patch("cldx.startup.list_panes", return_value=[]), \
         patch("cldx.startup.recent_sessions", return_value=[]):
        choice = await run_startup(policy, memory, console=console,
                                    input_fn=lambda _prompt: next(inputs))
    # "1" picks the only row available (start new) — should succeed.
    assert isinstance(choice, StartupChoice)
