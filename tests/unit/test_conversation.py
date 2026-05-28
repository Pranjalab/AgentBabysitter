"""conversation — structural extractors for Claude Code pane content.

Tests assert the rule the user articulated:

    "A conversation step starts with a line containing ⏺ and ends with
     the line that has ✻ <verb> for <time>. Everything between is the
     result of the current turn."
"""

from __future__ import annotations

import pytest

from abs.conversation import (
    PendingApproval,
    extract_assistant_step,
    extract_pending_approval,
)


# --- extract_assistant_step ---------------------------------------------


def test_extracts_single_step():
    """Single ⏺ block bracketed by user message + ✻ duration."""
    snap = (
        "❯ Say hi\n"
        "⏺ Hi! How can I help?\n"
        "✻ Sautéed for 1s\n"
        "❯\n"
        "  ? for shortcuts\n"
    )
    out = extract_assistant_step(snap)
    assert out == "⏺ Hi! How can I help?"


def test_extracts_full_multistep_turn_with_continuations():
    """Reproduces the user's weather snapshot — three ⏺ blocks plus
    indented ⎿ continuations and prose. ALL of it must be returned;
    nothing dropped."""
    snap = (
        "❯ Search for Indore and Khandwa\n"
        "⏺ Web Search(\"weather Indore today\")\n"
        "  ⎿  Did 1 search in 4s\n"
        "⏺ Web Search(\"weather Khandwa today\")\n"
        "  ⎿  Did 1 search in 4s\n"
        "⏺ Here's the weather for Indore and Khandwa today:\n"
        "\n"
        "  Indore, Madhya Pradesh:\n"
        "  - Conditions: Sunny, breezy, and very warm\n"
        "  - High: 105°F | Low: 78°F\n"
        "\n"
        "  Khandwa, Madhya Pradesh:\n"
        "  - Conditions: Hazy sunshine\n"
        "✻ Brewed for 10s\n"
        "❯\n"
        "  ? for shortcuts · ← for agents\n"
    )
    out = extract_assistant_step(snap)
    # All three ⏺ headers present.
    assert "Web Search(\"weather Indore today\")" in out
    assert "Web Search(\"weather Khandwa today\")" in out
    assert "Here's the weather for Indore and Khandwa today" in out
    # Indented continuations preserved.
    assert "Did 1 search in 4s" in out
    assert "Indore, Madhya Pradesh" in out
    assert "Sunny, breezy" in out
    assert "Hazy sunshine" in out
    # User's own question is NOT echoed in the result.
    assert "❯ Search for Indore" not in out
    # ✻ duration line is excluded.
    assert "Brewed for 10s" not in out
    # Bottom-of-pane chrome is excluded.
    assert "? for shortcuts" not in out


def test_excludes_earlier_turn_when_multiple_turns_visible():
    """Scrollback has earlier turns; only the latest ⏺...✻ slice wins."""
    snap = (
        "❯ first question\n"
        "⏺ first answer.\n"
        "✻ Worked for 1s\n"
        "❯ second question\n"
        "⏺ second answer.\n"
        "✻ Brewed for 1s\n"
        "❯ third question\n"
        "⏺ third answer.\n"
        "✻ Crunched for 2s\n"
    )
    out = extract_assistant_step(snap)
    assert out == "⏺ third answer."
    assert "first" not in out
    assert "second" not in out


def test_returns_empty_when_no_assistant_block_in_current_turn():
    """User just typed and Claude hasn't started yet — empty result."""
    snap = (
        "❯ first task\n"
        "⏺ done with first.\n"
        "✻ Worked for 1s\n"
        "❯ now do this\n"
        "✻ Pondered for 0s\n"
    )
    out = extract_assistant_step(snap)
    assert out == ""


def test_handles_snapshot_with_no_end_indicator_yet():
    """No ✻ line visible yet — Claude is still running. Return from
    the first ⏺ after the latest user message to end-of-snapshot."""
    snap = (
        "❯ Build a website\n"
        "⏺ Write(index.html)\n"
        "  ⎿  Writing 50 lines...\n"
    )
    out = extract_assistant_step(snap)
    assert "Write(index.html)" in out
    assert "Writing 50 lines" in out


def test_handles_empty_input():
    assert extract_assistant_step("") == ""
    assert extract_assistant_step(None) == ""  # type: ignore[arg-type]


def test_handles_no_user_message_at_all():
    """Banner-only pane / fresh start — should still extract latest
    ⏺...✻ chunk if one exists (degenerate but well-defined)."""
    snap = (
        "▐▛███▜▌  Claude Code v2.1\n"
        "Welcome!\n"
        "⏺ Ready to help.\n"
        "✻ Worked for 0s\n"
    )
    out = extract_assistant_step(snap)
    assert "Ready to help" in out
    assert "Worked" not in out


# --- extract_pending_approval -------------------------------------------


def test_extract_approval_basic():
    """Standard menu shape: question + ❯ 1. Yes + numbered options."""
    snap = (
        "⏺ Bash(rm -rf /tmp/foo)\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. Yes, allow all edits during this session\n"
        "   3. No\n"
    )
    out = extract_pending_approval(snap)
    assert out is not None
    assert isinstance(out, PendingApproval)
    assert out.question == "Do you want to proceed?"
    assert out.options == (
        "1. Yes",
        "2. Yes, allow all edits during this session",
        "3. No",
    )


def test_extract_approval_with_websearch_options():
    """The user's actual WebSearch approval shape — long option text."""
    snap = (
        "⏺ Web Search(\"weather Indore\")\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. Yes, and don't ask again for Web Search commands in /Users/pranjalbhaskare\n"
        "   3. No\n"
        " Esc to cancel · Tab to amend\n"
    )
    out = extract_pending_approval(snap)
    assert out is not None
    assert out.options[0] == "1. Yes"
    assert "Web Search commands" in out.options[1]
    assert out.options[2] == "3. No"


def test_extract_approval_returns_none_when_absent():
    """No live approval — function returns None, never raises."""
    snap = (
        "❯ Hi\n"
        "⏺ Hi! How can I help?\n"
        "✻ Sautéed for 1s\n"
    )
    assert extract_pending_approval(snap) is None
    assert extract_pending_approval("") is None
    assert extract_pending_approval(None) is None  # type: ignore[arg-type]


def test_extract_approval_handles_question_without_first_option():
    """A "Do you want to proceed?" with no matching ❯ 1. Yes below
    isn't a real prompt — return None rather than half-formed data."""
    snap = (
        "Do you want to proceed?\n"
        "(thinking...)\n"
    )
    assert extract_pending_approval(snap) is None


def test_extract_approval_picks_most_recent_when_history_present():
    """An older answered approval may still be visible in scrollback;
    only the LIVE one at the bottom should be returned."""
    snap = (
        "⏺ Bash(mkdir foo)\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. No\n"
        # ↑ answered earlier
        "⏺ Bash(mkdir bar)\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. Yes always\n"
        "   3. No\n"
        # ↑ live
    )
    out = extract_pending_approval(snap)
    assert out is not None
    assert len(out.options) == 3
    assert out.options[1] == "2. Yes always"
