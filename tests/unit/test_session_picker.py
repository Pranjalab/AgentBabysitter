"""Session picker: pane parsing and auto-detect heuristics."""

from __future__ import annotations

import pytest

from abs.session_picker import (
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


def test_find_claude_panes_prefers_literal_claude_first():
    """find_claude_panes sorts literal `claude` ahead of node/version matches."""
    from abs.session_picker import find_claude_panes
    panes = [
        Pane(target="0:0.0", current_command="node", title=""),
        Pane(target="1:0.0", current_command="claude", title=""),
    ]
    found = find_claude_panes(panes)
    assert found[0].target == "1:0.0"


# --- pick_session ----------------------------------------------------------

def test_pick_session_uses_cli_arg_unchanged():
    assert pick_session(cli_arg="manual:0.0") == "manual:0.0"


def test_pick_session_auto_detect_raises_with_panes_listed(monkeypatch):
    monkeypatch.setattr(
        "abs.session_picker.list_panes",
        lambda: [Pane(target="0:0.0", current_command="zsh", title="just a shell")],
    )
    with pytest.raises(SessionPickerError) as excinfo:
        pick_session(auto_detect=True)
    assert "no pane running Claude Code" in str(excinfo.value)
    # Failure message should include the panes it saw, so the user can debug.
    assert "0:0.0" in str(excinfo.value)


# --- multi-candidate auto-detect ------------------------------------------


def test_find_claude_panes_returns_all_candidates(monkeypatch):
    """find_claude_panes returns the whole list of Claude-looking panes."""
    monkeypatch.setattr(
        "abs.session_picker.list_panes",
        lambda: [
            Pane(target="0:0.0", current_command="zsh"),
            Pane(target="work:0.0", current_command="claude", title="✳"),
            Pane(target="play:0.0", current_command="2.1.150", title="✳ Code"),
        ],
    )
    from abs.session_picker import find_claude_panes
    found = find_claude_panes()
    assert len(found) == 2
    # "claude" command should sort first.
    assert found[0].target == "work:0.0"


def test_auto_detect_returns_none_when_multiple_candidates():
    """With more than one candidate, _auto_detect must defer to the picker."""
    from abs.session_picker import _auto_detect
    panes = [
        Pane(target="a:0.0", current_command="claude", title=""),
        Pane(target="b:0.0", current_command="claude", title=""),
    ]
    assert _auto_detect(panes) is None


def test_pick_session_picks_via_interactive_when_multiple_claude_panes(monkeypatch):
    """Two Claude panes + --auto-detect → numbered picker scoped to them."""
    from abs.session_picker import pick_session
    monkeypatch.setattr(
        "abs.session_picker.list_panes",
        lambda: [
            Pane(target="zsh:0.0", current_command="zsh"),
            Pane(target="claude-a:0.0", current_command="claude", title="✳"),
            Pane(target="claude-b:0.0", current_command="claude", title="✳"),
        ],
    )
    # Simulate the user typing "2" at the picker.
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: "2",
    )
    target = pick_session(auto_detect=True)
    # Must pick from the Claude-scoped list, not from all panes.
    assert target in ("claude-a:0.0", "claude-b:0.0")
    # And must NOT be the zsh pane (it was filtered out).
    assert target != "zsh:0.0"


def test_pick_session_auto_detect_single_candidate_skips_picker(monkeypatch):
    """When there's exactly one Claude pane, --auto-detect picks it silently."""
    from abs.session_picker import pick_session
    monkeypatch.setattr(
        "abs.session_picker.list_panes",
        lambda: [
            Pane(target="zsh:0.0", current_command="zsh"),
            Pane(target="claude:0.0", current_command="claude", title="✳"),
        ],
    )
    # input() would block forever if the picker erroneously fired.
    def _explode(prompt):
        raise AssertionError(f"picker should not have prompted: {prompt!r}")
    monkeypatch.setattr("builtins.input", _explode)

    assert pick_session(auto_detect=True) == "claude:0.0"
