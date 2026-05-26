"""Session picker: pane parsing and auto-detect heuristics."""

from __future__ import annotations

import pytest

from src.session_picker import (
    Pane,
    SessionPickerError,
    _auto_detect,
    _looks_like_claude,
    pick_session,
)


# --- _looks_like_claude ----------------------------------------------------

@pytest.mark.parametrize("cmd,title,expected", [
    ("claude", "", True),                       # literal command name
    ("node", "", True),                         # alternate command
    ("zsh", "", False),                         # unrelated
    ("2.1.150", "", True),                      # version-string command
    ("3.0.0", "", True),                        # any version
    ("vim", "✳ editing", True),                 # title prefix
    ("vim", "✳ Claude Code", True),             # exact title
    ("zsh", "my session", False),               # nothing matches
    ("python", "claude is here", True),         # "claude" in title
])
def test_looks_like_claude(cmd, title, expected):
    p = Pane(target="0:0.0", current_command=cmd, title=title)
    assert _looks_like_claude(p) is expected


# --- _auto_detect ----------------------------------------------------------

def test_auto_detect_returns_none_for_empty():
    assert _auto_detect([]) is None


def test_auto_detect_skips_unrelated_panes():
    panes = [
        Pane(target="0:0.0", current_command="zsh", title=""),
        Pane(target="0:1.0", current_command="vim", title="editing foo.py"),
    ]
    assert _auto_detect(panes) is None


def test_auto_detect_finds_claude_pane():
    panes = [
        Pane(target="0:0.0", current_command="zsh", title=""),
        Pane(target="work:1.0", current_command="claude", title="✳ Claude Code"),
    ]
    found = _auto_detect(panes)
    assert found is not None
    assert found.target == "work:1.0"


def test_auto_detect_finds_version_command_pane():
    panes = [
        Pane(target="0:0.0", current_command="2.1.150",
             title="✳ Create test file"),
    ]
    found = _auto_detect(panes)
    assert found and found.target == "0:0.0"


def test_auto_detect_prefers_literal_claude_over_other_matches():
    panes = [
        Pane(target="0:0.0", current_command="node", title=""),
        Pane(target="1:0.0", current_command="claude", title=""),
    ]
    found = _auto_detect(panes)
    assert found.target == "1:0.0"


# --- pick_session ----------------------------------------------------------

def test_pick_session_uses_cli_arg_unchanged():
    assert pick_session(cli_arg="manual:0.0") == "manual:0.0"


def test_pick_session_auto_detect_raises_with_panes_listed(monkeypatch):
    monkeypatch.setattr(
        "src.session_picker.list_panes",
        lambda: [Pane(target="0:0.0", current_command="zsh", title="just a shell")],
    )
    with pytest.raises(SessionPickerError) as excinfo:
        pick_session(auto_detect=True)
    assert "no pane running Claude Code" in str(excinfo.value)
    # Failure message should include the panes it saw, so the user can debug.
    assert "0:0.0" in str(excinfo.value)
