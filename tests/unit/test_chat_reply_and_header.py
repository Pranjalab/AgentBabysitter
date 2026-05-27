"""BridgeUI: chat-only replies surface to terminal + Telegram, and the
dynamic input-box header reflects what's connected (TMUX / Telegram /
session-limit reset)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cldx.prompt_classifier import PromptType
from cldx.session_limit import SessionLimit
from datetime import datetime, timezone


# Reuse the _make_bridge_ui fixture from the eager-classification suite.
from tests.unit.test_eager_classification import _make_bridge_ui


_CHAT_ONLY_SNAPSHOT = (
    "❯ hi\n"
    "\n"
    "⏺ Hi! What would you like to work on?\n"
    "\n"
    "✻ Crunched for 1s\n"
    "\n"
    "❯\n"
    "  ? for shortcuts · ← for agents\n"
)


# --- chat-only reply surface ---------------------------------------------


async def test_chat_only_reply_logs_to_claude_out(tmp_path, monkeypatch):
    """Claude's chat reply must be written into the interaction log so the
    user can see it in their session file."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    await ui.on_stable(_CHAT_ONLY_SNAPSHOT)

    log_contents = ui.interaction_log.path.read_text()
    assert "Hi! What would you like to work on?" in log_contents
    # The "claude-out" channel tag should appear on that line.
    assert "claude-out" in log_contents


async def test_chat_only_reply_attempts_telegram_when_enabled(tmp_path, monkeypatch):
    """If telegram is connected and enabled, chat replies get forwarded."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.args.no_telegram = False   # tests bridge gating, not the CLI flag
    ui.telegram = MagicMock()
    ui.telegram._send = AsyncMock()
    ui.telegram_enabled = True

    await ui.on_stable(_CHAT_ONLY_SNAPSHOT)
    ui.telegram._send.assert_awaited()
    body = ui.telegram._send.call_args[0][0]
    assert "Claude" in body
    assert "Hi! What would you like to work on?" in body


async def test_chat_only_reply_skips_telegram_when_disabled(tmp_path, monkeypatch):
    """The /telegram off gate must suppress chat-reply forwarding."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.telegram = MagicMock()
    ui.telegram._send = AsyncMock()
    ui.telegram_enabled = False

    await ui.on_stable(_CHAT_ONLY_SNAPSHOT)
    ui.telegram._send.assert_not_awaited()


async def test_telegram_text_inject_clears_completion_lock(tmp_path, monkeypatch):
    """Regression — Telegram text injection must clear ``_completion_locked``.

    Without this, the FIRST chat reply runs ``_handle_completion`` which
    locks the panel. The user then sends another ``Hi`` over Telegram; the
    next chat reply arrives but on_stable's ``COMPLETE + locked`` branch
    short-circuits and the reply never surfaces. Subsequent Telegram
    messages stay silent indefinitely.

    Reproduces the user's transcript:
      21:17:43  Telegram Hi  → silent (lock held from earlier turn)
      21:18:27  TERMINAL hi  → reply (terminal path clears the lock)
      21:18:44  Telegram Hi  → silent (lock held again)
    """
    from cldx.telegram_bridge import ParsedReply

    ui = _make_bridge_ui(tmp_path, monkeypatch)
    # Lock the panel as if a previous task just completed.
    ui._completion_locked = True
    ui.pending_signature = "menu|1. Yes|2. No"

    # User now sends "Hi" via Telegram.
    reply = ParsedReply(kind="text", value="Hi")
    await ui._telegram_reply_handler(reply, None)

    # Both flags must reset so the next on_stable can fire the
    # chat-reply card for Claude's response.
    assert ui._completion_locked is False, (
        "Telegram text injection must clear _completion_locked so the next "
        "reply can surface"
    )
    assert ui.pending_signature is None, (
        "Telegram text injection must clear pending_signature to avoid "
        "stale dispatch dedup"
    )


async def test_telegram_digit_reply_forwarded_when_nothing_pending(
    tmp_path, monkeypatch
):
    """Regression — when Claude asks a CHAT question with numbered
    options (prose, not an approval menu), the user's "1" reply via
    Telegram must reach Claude as text injection, NOT be silently
    dropped as "nothing pending".

    User transcript example: Claude asked "Which file to delete?
    1. foo  2. bar  3. Both". User replied "1" via Telegram. The
    old code parsed it as kind=digit, saw self.pending is None, and
    logged "ignored". Fix: fall through to text-inject."""
    from cldx.telegram_bridge import ParsedReply

    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.pending = None  # No active approval

    reply = ParsedReply(kind="digit", value="1", raw_text="1")
    await ui._telegram_reply_handler(reply, None)

    # The controller's send_text must have been called with "1".
    ui.controller.send_text.assert_called_once_with("1")


async def test_telegram_yes_reply_forwarded_when_nothing_pending(
    tmp_path, monkeypatch
):
    """Same rule for 'y'/'yes'/'ok' — when nothing's pending these are
    just words. Forward them to Claude as text."""
    from cldx.telegram_bridge import ParsedReply

    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.pending = None

    reply = ParsedReply(kind="yes", raw_text="yes please")
    await ui._telegram_reply_handler(reply, None)

    ui.controller.send_text.assert_called_once_with("yes please")


async def test_telegram_no_reply_forwarded_when_nothing_pending(
    tmp_path, monkeypatch
):
    from cldx.telegram_bridge import ParsedReply

    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.pending = None

    reply = ParsedReply(kind="no", raw_text="no thanks")
    await ui._telegram_reply_handler(reply, None)

    ui.controller.send_text.assert_called_once_with("no thanks")


async def test_telegram_digit_still_acts_on_pending_menu(tmp_path, monkeypatch):
    """The fall-through must NOT regress the original behaviour — when
    a real approval menu IS pending, a digit reply must pick that
    menu option (not text-inject the digit)."""
    from cldx.telegram_bridge import ParsedReply
    from cldx.prompt_classifier import ClassifiedPrompt, PromptType

    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.pending = ClassifiedPrompt(
        type=PromptType.APPROVAL_MENU,
        extracted_command="Bash(ls)",
        menu_options=("1. Yes", "2. No"),
    )

    reply = ParsedReply(kind="digit", value="1", raw_text="1")
    await ui._telegram_reply_handler(reply, None)

    ui.controller.send_digit.assert_called_once_with(1)
    ui.controller.send_text.assert_not_called()


async def test_terminal_and_telegram_text_inject_clear_lock_symmetrically(
    tmp_path, monkeypatch
):
    """The fix above must apply equally to terminal and Telegram input —
    otherwise the user's experience differs by channel (silent on
    Telegram, working in terminal), which is exactly the intermittent
    behaviour the user reported."""
    from cldx.telegram_bridge import ParsedReply

    # Round 1: terminal path.
    ui_term = _make_bridge_ui(tmp_path, monkeypatch)
    ui_term._completion_locked = True
    await ui_term._handle_input("Hi")
    assert ui_term._completion_locked is False

    # Round 2: Telegram path (must behave identically).
    ui_tg = _make_bridge_ui(tmp_path, monkeypatch)
    ui_tg._completion_locked = True
    await ui_tg._telegram_reply_handler(ParsedReply(kind="text", value="Hi"), None)
    assert ui_tg._completion_locked is False


# --- dynamic header ------------------------------------------------------


def test_header_base_is_claude_plus_tmux(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    title = ui._prompt_title()
    assert "Claude + TMUX" in title
    assert "Telegram" not in title
    assert "Resets at" not in title


def test_header_includes_telegram_when_enabled(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.telegram = MagicMock()
    ui.telegram_enabled = True
    title = ui._prompt_title()
    assert "Claude + TMUX + Telegram" in title


def test_header_drops_telegram_when_disabled(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.telegram = MagicMock()
    ui.telegram_enabled = False
    title = ui._prompt_title()
    assert "Claude + TMUX" in title
    assert "Telegram" not in title


def test_header_shows_reset_tag_when_limit_set(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.session_limit = SessionLimit(
        reset_at=datetime(2026, 5, 27, 14, 20, 0, tzinfo=timezone.utc),
        label="7:50 pm",
        timezone_str="Asia/Calcutta",
    )
    title = ui._prompt_title()
    assert "Resets at 7:50 pm" in title


def test_header_combines_telegram_and_reset(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.telegram = MagicMock()
    ui.telegram_enabled = True
    ui.session_limit = SessionLimit(
        reset_at=datetime(2026, 5, 27, 14, 20, 0, tzinfo=timezone.utc),
        label="9:30 pm",
    )
    title = ui._prompt_title()
    assert "Claude + TMUX + Telegram (Resets at 9:30 pm)" in title


def test_header_preserves_pending_suffix(tmp_path, monkeypatch):
    from cldx.prompt_classifier import ClassifiedPrompt
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.pending = ClassifiedPrompt(
        type=PromptType.APPROVAL_YN, extracted_command="Bash(ls)",
    )
    title = ui._prompt_title()
    assert "Claude + TMUX" in title
    assert "y / n" in title


# --- /telegram on|off toggle ---------------------------------------------


async def test_telegram_toggle_off(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.telegram = MagicMock()
    ui.telegram_enabled = True

    await ui._handle_telegram_toggle("off")
    assert ui.telegram_enabled is False
    assert "Telegram" not in ui._prompt_title()


async def test_telegram_toggle_on_no_op_when_already_on(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.telegram = MagicMock()
    ui.telegram_enabled = True
    await ui._handle_telegram_toggle("on")
    assert ui.telegram_enabled is True


async def test_telegram_toggle_off_then_on(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.telegram = MagicMock()
    ui.telegram_enabled = True

    await ui._handle_telegram_toggle("off")
    assert ui.telegram_enabled is False

    await ui._handle_telegram_toggle("on")
    assert ui.telegram_enabled is True


async def test_telegram_toggle_invalid_arg(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    # Should not crash; logs usage hint.
    await ui._handle_telegram_toggle("garbage")
    # No state change.
    assert ui.telegram_enabled is True


# --- session-limit detection --------------------------------------------


async def test_session_limit_detected_in_snapshot(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    snapshot = (
        "⏺ doing work\n"
        "You've hit your session limit · resets 7:50pm (Asia/Calcutta)\n"
    )
    await ui.on_change("", snapshot)
    assert ui.session_limit is not None
    assert ui.session_limit.label == "7:50 pm"
    # Header now carries the reset tag.
    assert "Resets at 7:50 pm" in ui._prompt_title()
    # And the watcher task is running.
    assert ui._reset_task is not None
    ui._reset_task.cancel()


async def test_session_limit_does_not_double_fire(tmp_path, monkeypatch):
    """Same banner across many frames must not re-trigger notifications."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.args.no_telegram = False
    ui.telegram = MagicMock()
    ui.telegram._send = AsyncMock()
    ui.telegram_enabled = True

    snap = "session limit · resets 7:50pm (Asia/Calcutta)"
    await ui.on_change("", snap)
    await ui.on_change("", snap)
    await ui.on_change("", snap)

    # The first detection schedules a Telegram send as a task; let it run.
    import asyncio as _asyncio
    await _asyncio.sleep(0)

    # Telegram send fires once per UNIQUE banner.
    assert ui.telegram._send.await_count == 1
    if ui._reset_task is not None:
        ui._reset_task.cancel()


async def test_session_limit_sends_to_telegram_when_enabled(tmp_path, monkeypatch):
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    ui.args.no_telegram = False
    ui.telegram = MagicMock()
    ui.telegram._send = AsyncMock()
    ui.telegram_enabled = True

    await ui.on_change(
        "", "session limit · resets 7:50pm (Asia/Calcutta)",
    )
    # The telegram send is scheduled as a task — yield once so it runs.
    import asyncio as _asyncio
    await _asyncio.sleep(0)
    ui.telegram._send.assert_awaited()
    body = ui.telegram._send.call_args[0][0]
    assert "Session limit" in body
    assert "7:50 pm" in body
    if ui._reset_task is not None:
        ui._reset_task.cancel()
