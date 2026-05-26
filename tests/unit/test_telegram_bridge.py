"""Phase 7 — telegram_bridge.py: outbound notify + inbound reply handling."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase7


@pytest.mark.skip(reason="Phase 7 — Telegram bridge not implemented yet")
async def test_notify_approval_needed_sends_summarized_message():
    """notify_approval_needed() sends a Telegram message with a summary <=200 chars."""


@pytest.mark.skip(reason="Phase 7")
async def test_user_reply_yes_routes_to_send_yes():
    """An inbound `y` from Telegram triggers controller.send_yes() (or menu equivalent)."""


@pytest.mark.skip(reason="Phase 7")
async def test_user_reply_text_routes_to_injection():
    """Free-form Telegram replies become controller.send_text(...) injections."""


@pytest.mark.skip(reason="Phase 7")
async def test_only_configured_chat_id_accepted():
    """Messages from other chat IDs must be ignored (auth boundary)."""


@pytest.mark.skip(reason="Phase 7")
async def test_approval_timeout_triggers_default_action():
    """No reply within `approval_timeout_seconds` → fall back to `timeout_action`."""


@pytest.mark.skip(reason="Phase 7")
async def test_bot_reconnects_with_backoff_on_network_error():
    """A dropped connection retries with exponential backoff, doesn't crash the bridge."""


@pytest.mark.skip(reason="Phase 7")
async def test_manual_injection_when_no_prompt_pending():
    """A Telegram message arriving while pending is None still gets injected as text."""
