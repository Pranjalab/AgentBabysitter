"""Telegram slash-command dispatch — pure unit tests, no real bot."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cldx.telegram_commands import (
    COMMANDS,
    dispatch,
    is_command,
    parse_command,
)


# --- parse / detect -------------------------------------------------------


def test_is_command_recognises_registered_names():
    assert is_command("/help")
    assert is_command("/status hello")
    assert is_command("/profile yolo")
    # Telegram group-chat suffix.
    assert is_command("/help@MyBot")


def test_is_command_rejects_unknown_and_plain_text():
    assert not is_command("/unknown_cmd")
    assert not is_command("hello world")
    assert not is_command("")
    assert not is_command("/")


def test_parse_command_extracts_name_and_args():
    assert parse_command("/profile yolo") == ("profile", "yolo")
    assert parse_command("/help") == ("help", "")
    assert parse_command("/profile@MyBot strict") == ("profile", "strict")
    assert parse_command("not a command") is None
    assert parse_command("/nope") is None


def test_command_registry_completeness():
    """Every command we document in the help text must be registered."""
    expected = {
        "help", "start", "status", "panes", "snapshot",
        "stop", "cancel", "yes", "no",
        "pause", "resume", "profile",
    }
    assert expected <= set(COMMANDS.keys())


# --- dispatch primitives --------------------------------------------------


def _bridge(**overrides):
    """Minimal fake BridgeUI for command handlers."""
    base = SimpleNamespace(
        pane_target="0:0.0",
        pending=None,
        controller=MagicMock(),
        policy=SimpleNamespace(
            active_profile_name="auto-approve",
            profiles={"auto-approve": {}, "yolo": {}, "restricted": {}},
            set_active_profile=lambda name: None,
        ),
        monitor=SimpleNamespace(last_snapshot="hello pane"),
        paused=False,
        set_paused=lambda v: setattr(base, "paused", v),
        _update_prompt_label=lambda: None,
        _telegram_reply_handler=AsyncMock(),
    )
    base.controller.send_escape = AsyncMock()
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


@pytest.mark.asyncio
async def test_dispatch_returns_none_for_unknown_command():
    bridge = _bridge()
    assert await dispatch(bridge, "not a command") is None
    assert await dispatch(bridge, "/zzznope") is None


@pytest.mark.asyncio
async def test_help_renders_help_message():
    bridge = _bridge()
    reply = await dispatch(bridge, "/help")
    assert reply and "/help" in reply
    assert "auto-approve" in reply  # profile shown


@pytest.mark.asyncio
async def test_status_shows_pane_and_profile():
    bridge = _bridge()
    reply = await dispatch(bridge, "/status")
    assert "0:0.0" in reply
    assert "auto-approve" in reply
    assert "Paused: no" in reply


@pytest.mark.asyncio
async def test_status_shows_pending_when_set():
    pending = SimpleNamespace(extracted_command="Bash(ls)", type="approval_yn")
    bridge = _bridge(pending=pending)
    reply = await dispatch(bridge, "/status")
    assert "Bash(ls)" in reply


@pytest.mark.asyncio
async def test_stop_sends_escape_and_clears_pending():
    pending = SimpleNamespace(extracted_command="Bash(ls)")
    bridge = _bridge(pending=pending)
    reply = await dispatch(bridge, "/stop")
    bridge.controller.send_escape.assert_called_once()
    assert bridge.pending is None
    assert "interrupted" in reply.lower()


@pytest.mark.asyncio
async def test_cancel_clears_pending_only():
    pending = SimpleNamespace(extracted_command="x")
    bridge = _bridge(pending=pending)
    reply = await dispatch(bridge, "/cancel")
    assert bridge.pending is None
    # Did NOT send escape (cancel just clears local state).
    bridge.controller.send_escape.assert_not_called()
    assert "cleared" in reply.lower()


@pytest.mark.asyncio
async def test_cancel_with_nothing_pending_is_safe():
    bridge = _bridge()
    reply = await dispatch(bridge, "/cancel")
    assert "Nothing pending" in reply


@pytest.mark.asyncio
async def test_pause_then_resume_round_trip():
    bridge = _bridge()
    r1 = await dispatch(bridge, "/pause")
    assert "Paused" in r1
    assert bridge.paused is True

    # Pausing again is a no-op with a message.
    r2 = await dispatch(bridge, "/pause")
    assert "Already paused" in r2

    r3 = await dispatch(bridge, "/resume")
    assert "Resumed" in r3
    assert bridge.paused is False


@pytest.mark.asyncio
async def test_profile_list_and_switch():
    bridge = _bridge()
    reply = await dispatch(bridge, "/profile")
    assert "yolo" in reply and "auto-approve" in reply

    bridge.policy.set_active_profile = MagicMock()
    reply2 = await dispatch(bridge, "/profile yolo")
    bridge.policy.set_active_profile.assert_called_once_with("yolo")
    assert "yolo" in reply2.lower()


@pytest.mark.asyncio
async def test_profile_rejects_unknown_name():
    bridge = _bridge()
    reply = await dispatch(bridge, "/profile bogus")
    assert "Unknown profile" in reply
    assert "bogus" in reply


@pytest.mark.asyncio
async def test_yes_no_route_through_reply_handler():
    pending = SimpleNamespace(extracted_command="x")
    bridge = _bridge(pending=pending)
    bridge._telegram_reply_handler = AsyncMock()

    await dispatch(bridge, "/yes")
    bridge._telegram_reply_handler.assert_called_once()
    call_arg = bridge._telegram_reply_handler.call_args[0][0]
    assert call_arg.kind == "yes"

    bridge._telegram_reply_handler.reset_mock()
    await dispatch(bridge, "/no")
    assert bridge._telegram_reply_handler.call_args[0][0].kind == "no"


@pytest.mark.asyncio
async def test_yes_with_nothing_pending_is_noop():
    bridge = _bridge()  # pending=None
    reply = await dispatch(bridge, "/yes")
    assert "Nothing pending" in reply
    bridge._telegram_reply_handler.assert_not_called()


@pytest.mark.asyncio
async def test_handler_exceptions_become_user_visible_reply():
    """A bug in a handler must NEVER crash the bot — it must surface as
    a one-line error message."""
    bridge = _bridge()

    async def boom(b, args):
        raise RuntimeError("kaboom")

    COMMANDS["help"] = boom
    try:
        reply = await dispatch(bridge, "/help")
        assert "/help failed" in reply
        assert "kaboom" in reply
    finally:
        # Restore the real help handler.
        from cldx.telegram_commands import cmd_help
        COMMANDS["help"] = cmd_help


@pytest.mark.asyncio
async def test_snapshot_returns_pane_content():
    bridge = _bridge()
    bridge.monitor.last_snapshot = "line a\nline b\nline c"
    reply = await dispatch(bridge, "/snapshot")
    assert "line c" in reply
    assert "snapshot" in reply.lower()
