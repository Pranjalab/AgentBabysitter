"""`BridgeUI._extract_current_task` — show only the most recent task in
the green completion panel, not the entire pane scrollback."""

from __future__ import annotations

import pytest

from abs.cli import BridgeUI


def test_extract_takes_from_last_submitted_user_input():
    """Latest `❯ <text>` block is the current task; earlier ones aren't."""
    snap = (
        "Banner stuff here\n"
        "❯ hi\n"
        "⏺ Hi! What's up?\n"
        "✻ Crunched for 1s\n"
        "❯ create /tmp/foo.py\n"
        "⏺ Bash(mkdir -p)\n"
        "⏺ Write(/tmp/foo.py)\n"
        "⏺ Done.\n"
        "✻ Cooked for 4s\n"
        "❯ \n"                                 # input area (suggestion)
        "  ? for shortcuts · ← for agents\n"
    )
    out = BridgeUI._extract_current_task(snap)
    # The latest SUBMITTED user message is "create /tmp/foo.py".
    assert "create /tmp/foo.py" in out
    # And the assistant's response to it should be there.
    assert "Write(/tmp/foo.py)" in out
    # But the previous task's content should NOT be.
    assert "Hi! What's up?" not in out
    assert "Banner stuff here" not in out


def test_extract_strips_trailing_ui_chrome():
    """UI chrome (`?` hint, `─` separators, esc-to-cancel) is removed."""
    snap = (
        "❯ delete the file\n"
        "⏺ Bash(rm /tmp/foo)\n"
        "⏺ Deleted.\n"
        "─────────────────\n"
        "❯ next thing\n"
        "─────────────────\n"
        "  ? for shortcuts · ← for agents\n"
    )
    out = BridgeUI._extract_current_task(snap)
    assert "Deleted." in out
    # All chrome stripped:
    assert "? for shortcuts" not in out
    assert "next thing" not in out  # this was the suggestion line
    # Trailing separators gone too:
    assert not out.endswith("─")


def test_extract_single_user_input_works():
    """When only one `❯` line exists, take from it to end of snapshot."""
    snap = (
        "❯ hi\n"
        "⏺ Hi! What's up?\n"
        "✻ Crunched for 1s\n"
    )
    out = BridgeUI._extract_current_task(snap)
    assert "hi" in out
    assert "Hi! What's up?" in out


def test_extract_no_user_input_falls_back_to_tail():
    """No `❯` lines at all → return the last 20 lines (banner case)."""
    snap = "\n".join(f"line {i}" for i in range(50))
    out = BridgeUI._extract_current_task(snap)
    assert "line 49" in out
    # First 30 lines should be gone (tail is last 20).
    assert "line 0\n" not in out


def test_extract_ignores_menu_option_caret():
    """`❯ 1. Yes` is a menu option, not a user input — must not anchor here."""
    snap = (
        "Banner content\n"
        "❯ do something\n"
        "⏺ Bash(rm /x)\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"                   # menu option — NOT a user input
        "   2. No\n"
    )
    out = BridgeUI._extract_current_task(snap)
    # Anchor should be on "do something", not on the menu option.
    assert "do something" in out
    assert "Bash(rm /x)" in out
    assert "1. Yes" in out  # menu IS included; it's part of this task


def test_extract_does_not_include_banner_duplicates():
    """Real Claude Code panes sometimes show the banner twice — the
    extractor must not include any of it once a user input has occurred."""
    snap = (
        "▐▛███▜▌   Claude Code v2.1.152\n"
        "▝▜█████▛▘  Opus 4.7\n"
        "▐▛███▜▌   Claude Code v2.1.152\n"   # duplicate
        "▝▜█████▛▘  Opus 4.7\n"
        "❯ hi\n"
        "⏺ Hi! What's up?\n"
    )
    out = BridgeUI._extract_current_task(snap)
    assert "▐▛███▜▌" not in out
    assert "Claude Code v2.1.152" not in out
    assert "Hi! What's up?" in out


# --- Suggestion extraction ----------------------------------------------

def test_extract_suggestion_finds_bottom_pane_hint():
    """The dim suggestion below the last ⏺ block (between ─ separators)."""
    snap = (
        "❯ create the file again\n"
        "⏺ Write(file.py)\n"
        "⏺ Recreated.\n"
        "✻ Baked for 5s\n"
        "─────────────\n"
        "❯ delete it\n"
        "─────────────\n"
        "  ? for shortcuts · ← for agents\n"
    )
    assert BridgeUI._extract_suggestion(snap) == "delete it"


def test_extract_suggestion_skips_submitted_user_messages():
    """A ❯ line that has a ⏺ AFTER it is a submitted message, not a hint."""
    snap = (
        "❯ create the file\n"            # submitted (has ⏺ below)
        "⏺ Bash(touch file)\n"
        "⏺ Done.\n"
    )
    # No suggestion line at the bottom → empty result.
    assert BridgeUI._extract_suggestion(snap) == ""


def test_extract_suggestion_skips_menu_options():
    """A '❯ 1. Yes' menu option must NOT be picked up as a suggestion."""
    snap = (
        "❯ delete it\n"             # submitted
        "⏺ Bash(rm /x)\n"
        "Do you want to proceed?\n"
        " ❯ 1. Yes\n"                # menu option — NOT a suggestion
        "   2. No\n"
    )
    assert BridgeUI._extract_suggestion(snap) == ""


def test_extract_suggestion_returns_empty_when_pane_is_empty():
    assert BridgeUI._extract_suggestion("") == ""
    assert BridgeUI._extract_suggestion("   \n\n") == ""
