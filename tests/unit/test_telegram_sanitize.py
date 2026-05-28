"""telegram_sanitize — strip pane chrome before sending to Telegram."""

from __future__ import annotations

import pytest

from abs.telegram_sanitize import (
    clean_for_telegram,
    extract_assistant_reply,
)


# --- clean_for_telegram ---------------------------------------------------


def test_clean_strips_ansi_escapes():
    raw = "\x1b[31mred text\x1b[0m"
    assert clean_for_telegram(raw) == "red text"


def test_clean_drops_banner_lines():
    raw = (
        "▐▛███▜▌   Claude Code v2.1.152\n"
        "▝▜█████▛▘  Opus 4.7\n"
        "Hello, world.\n"
    )
    out = clean_for_telegram(raw)
    assert "Hello, world." in out
    # The banner lines should be gone.
    assert "▐" not in out
    assert "Claude Code v2.1.152" in out  # accompanying text survives


def test_clean_drops_separator_runs():
    raw = "Section header\n────────────────\nBody\n━━━━━━━━━━\n"
    out = clean_for_telegram(raw)
    assert "Section header" in out
    assert "Body" in out
    assert "─" not in out
    assert "━" not in out


def test_clean_removes_inline_box_chars():
    raw = "│  hello world  │"
    assert clean_for_telegram(raw).strip() == "hello world"


def test_clean_drops_known_chrome_lines():
    raw = (
        "real content here\n"
        "  ? for shortcuts · ← for agents\n"
        "esc to cancel\n"
        "more content\n"
    )
    out = clean_for_telegram(raw)
    assert "real content here" in out
    assert "more content" in out
    assert "shortcuts" not in out
    assert "esc to cancel" not in out


def test_clean_collapses_blank_runs():
    raw = "first\n\n\n\nsecond\n\n\n\n\nthird"
    out = clean_for_telegram(raw)
    # At most one blank line between paragraphs.
    assert "\n\n\n" not in out
    assert "first" in out and "second" in out and "third" in out


def test_clean_trims_leading_trailing_blanks():
    raw = "\n\n\nhello\n\n\n"
    assert clean_for_telegram(raw) == "hello"


def test_clean_truncates_to_max_chars():
    raw = "x" * 5000
    out = clean_for_telegram(raw, max_chars=100)
    assert len(out) == 100
    assert out.endswith("…")


def test_clean_handles_empty_input():
    assert clean_for_telegram("") == ""
    assert clean_for_telegram(None) == ""


def test_clean_strips_unsummarized_marker():
    raw = "[unsummarized: LLM disabled] real content"
    out = clean_for_telegram(raw)
    assert out == "real content"


# --- extract_assistant_reply ----------------------------------------------


def test_extract_assistant_reply_basic():
    snap = (
        "❯ hi\n"
        "⏺ Hi! What would you like to work on?\n"
        "✻ Crunched for 1s\n"
        "❯\n"
        "  ? for shortcuts · ← for agents\n"
    )
    out = extract_assistant_reply(snap)
    assert "Hi! What would you like to work on?" in out
    # ⏺ marker stripped.
    assert "⏺" not in out
    # ✻ thinking line not included.
    assert "Crunched" not in out


def test_extract_assistant_reply_multiple_blocks_keeps_recent():
    snap = (
        "⏺ First reply.\n"
        "❯ next question\n"
        "⏺ Second reply.\n"
        "⏺ Also part of second response.\n"
    )
    out = extract_assistant_reply(snap)
    assert "Second reply." in out
    assert "Also part of second response." in out


def test_extract_assistant_reply_handles_indented_continuation():
    snap = (
        "⏺ Bash(ls)\n"
        "  ⎿ file1.txt\n"
        "    file2.txt\n"
    )
    out = extract_assistant_reply(snap)
    assert "Bash(ls)" in out
    assert "file1.txt" in out
    assert "file2.txt" in out


def test_extract_assistant_reply_returns_empty_when_no_marker():
    assert extract_assistant_reply("just banner text\nno marker here") == ""
    assert extract_assistant_reply("") == ""


# --- "Don't include history from earlier turns" --------------------------


def test_extract_anchors_on_latest_user_message():
    """A chatty session has many ⏺ replies in the scrollback. The
    extractor must take ONLY the reply to the most recent user
    message — not every ⏺ line ever printed."""
    snap = (
        "❯ first question\n"
        "⏺ first answer.\n"
        "✻ Worked for 1s\n"
        "❯ second question\n"
        "⏺ second answer.\n"
        "✻ Brewed for 1s\n"
        "❯ third question\n"
        "⏺ third answer.\n"
        "✻ Crunched for 1s\n"
        "❯\n"
        "  ? for shortcuts\n"
    )
    out = extract_assistant_reply(snap)
    assert out == "third answer."
    assert "first" not in out
    assert "second" not in out


def test_extract_includes_indented_continuation_of_latest_reply():
    """The current turn's reply may have several ⏺ lines + indented
    continuations. All belong to "the latest reply" — earlier turns'
    blocks must still be excluded."""
    snap = (
        "❯ earlier prompt\n"
        "⏺ earlier reply that should NOT appear.\n"
        "✻ Cooked for 1s\n"
        "❯ run the task\n"
        "⏺ Bash(make test)\n"
        "  ⎿ 5 passed\n"
        "⏺ Done.\n"
    )
    out = extract_assistant_reply(snap)
    assert "Bash(make test)" in out
    assert "5 passed" in out
    assert "Done." in out
    assert "earlier reply" not in out


def test_extract_handles_user_message_with_no_reply_yet():
    """If the most recent user message has no ⏺ line below it (Claude
    is still thinking), return empty. Earlier turn replies must NOT
    leak in as a substitute."""
    snap = (
        "❯ old question\n"
        "⏺ old answer.\n"
        "✻ Sautéed for 1s\n"
        "❯ new question\n"
    )
    out = extract_assistant_reply(snap)
    assert "old" not in out
    assert out == ""
