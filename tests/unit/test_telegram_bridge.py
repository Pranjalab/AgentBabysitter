"""Phase 7 — Telegram bridge: parsing, auth, outbound, inbound routing.

All tests use a fake bot factory; no real Telegram traffic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from abs.agent import Agent
from abs.policy_engine import DecisionResult, PolicyDecision
from abs.prompt_classifier import ClassifiedPrompt, PromptType
from abs.telegram_bridge import (
    ParsedReply,
    TelegramBridge,
    TelegramConfig,
    parse_reply,
)


# --- parse_reply ----------------------------------------------------------

@pytest.mark.parametrize("text,expected_kind,expected_value", [
    ("y", "yes", ""),
    ("Y", "yes", ""),
    ("yes", "yes", ""),
    ("ok", "yes", ""),
    ("👍", "yes", ""),
    ("n", "no", ""),
    ("no", "no", ""),
    ("👎", "no", ""),
    ("1", "digit", "1"),
    ("3", "digit", "3"),
    ("type some text", "text", "type some text"),
    ("hello world", "text", "hello world"),
    ("", "ignore", ""),
    ("   ", "ignore", ""),
    (None, "ignore", ""),
])
def test_parse_reply(text, expected_kind, expected_value):
    result = parse_reply(text)
    assert result.kind == expected_kind
    if expected_value:
        assert result.value == expected_value


# --- TelegramConfig.from_env_file -----------------------------------------

def test_config_from_env_file(tmp_path):
    p = tmp_path / "telegram.env"
    p.write_text(
        "TELEGRAM_BOT_TOKEN=tkn-abc\n"
        "TELEGRAM_CHAT_ID=12345\n"
        "APPROVAL_TIMEOUT_SECONDS=300\n"
        "TIMEOUT_ACTION=auto_no\n"
        "# this is a comment\n"
        "  \n"
    )
    cfg = TelegramConfig.from_env_file(p)
    assert cfg is not None
    assert cfg.bot_token == "tkn-abc"
    assert cfg.chat_id == "12345"
    assert cfg.approval_timeout_seconds == 300
    assert cfg.timeout_action == "auto_no"


def test_config_returns_none_when_file_missing(tmp_path):
    assert TelegramConfig.from_env_file(tmp_path / "nope.env") is None


def test_config_returns_none_when_required_keys_missing(tmp_path):
    p = tmp_path / "incomplete.env"
    p.write_text("TELEGRAM_BOT_TOKEN=abc\n")  # no CHAT_ID
    assert TelegramConfig.from_env_file(p) is None


# --- TelegramBridge: fake bot wiring --------------------------------------

@pytest.fixture
def fake_app():
    """A MagicMock that mimics the python-telegram-bot Application."""
    app = MagicMock()
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    app.updater = MagicMock()
    app.updater.start_polling = AsyncMock()
    app.updater.stop = AsyncMock()
    app.add_handler = MagicMock()
    app.bot = MagicMock()
    app.bot.send_message = AsyncMock()
    return app


@pytest.fixture
def config():
    return TelegramConfig(
        bot_token="tkn", chat_id="100",
        approval_timeout_seconds=5, timeout_action="auto_no",
    )


@pytest.fixture
def agent():
    return Agent.default()


def _factory(app):
    return lambda token: app


# Pytest needs to know the imports below succeed at collection time. Since
# python-telegram-bot exposes telegram.ext as part of its install, that's
# fine in CI. If the package isn't installed, the start() tests skip.

def _ptb_installed() -> bool:
    try:
        import telegram.ext  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ptb_installed(), reason="python-telegram-bot not installed")
async def test_bridge_start_initializes_app(fake_app, config, agent):
    handler = AsyncMock()
    bridge = TelegramBridge(config, agent, handler, bot_factory=_factory(fake_app))
    await bridge.start()
    fake_app.initialize.assert_awaited_once()
    fake_app.start.assert_awaited_once()
    fake_app.updater.start_polling.assert_awaited_once()
    await bridge.stop()
    fake_app.shutdown.assert_awaited_once()


@pytest.mark.skipif(not _ptb_installed(), reason="python-telegram-bot not installed")
async def test_notify_approval_needed_sends_summarized_message(
    fake_app, config, agent, monkeypatch
):
    handler = AsyncMock()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # force fallback summary
    bridge = TelegramBridge(config, agent, handler, bot_factory=_factory(fake_app))
    await bridge.start()

    prompt = ClassifiedPrompt(
        type=PromptType.APPROVAL_MENU,
        extracted_command="Bash(npm install)",
        menu_options=("1. Yes", "2. No"),
        context="Bash(npm install)",
    )
    decision = DecisionResult(
        decision=PolicyDecision.ESCALATE_TELEGRAM,
        profile="restricted",
        wait_interval_seconds=0.0,
        is_destructive=False,
    )
    await bridge.notify_approval_needed(prompt, decision)
    fake_app.bot.send_message.assert_awaited()
    call_kwargs = fake_app.bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == config.chat_id
    text = call_kwargs["text"]
    assert "Reply" in text
    # menu_options were 2, so 1/2 should appear as digit hints
    assert "1/2" in text or "1" in text
    assert bridge.pending_prompt is prompt
    await bridge.stop()


@pytest.mark.skipif(not _ptb_installed(), reason="python-telegram-bot not installed")
async def test_only_configured_chat_id_accepted(fake_app, config, agent):
    handler = AsyncMock()
    bridge = TelegramBridge(config, agent, handler, bot_factory=_factory(fake_app))
    await bridge.start()

    update = MagicMock()
    update.effective_chat.id = 99999      # NOT the configured 100
    update.message.text = "y"

    await bridge._on_telegram_message(update, None)
    handler.assert_not_awaited()
    await bridge.stop()


@pytest.mark.skipif(not _ptb_installed(), reason="python-telegram-bot not installed")
async def test_authorized_yes_routes_through_handler(fake_app, config, agent):
    handler = AsyncMock()
    bridge = TelegramBridge(config, agent, handler, bot_factory=_factory(fake_app))
    await bridge.start()

    # Set up a pending prompt so handler sees it.
    bridge._pending_prompt = ClassifiedPrompt(
        type=PromptType.APPROVAL_YN, extracted_command="Bash(ls)", context="Bash(ls)",
    )
    update = MagicMock()
    update.effective_chat.id = 100
    update.message.text = "y"

    await bridge._on_telegram_message(update, None)
    handler.assert_awaited_once()
    reply_arg = handler.await_args.args[0]
    assert reply_arg.kind == "yes"
    # pending should clear after the reply.
    assert bridge.pending_prompt is None
    await bridge.stop()


@pytest.mark.skipif(not _ptb_installed(), reason="python-telegram-bot not installed")
async def test_authorized_text_does_not_clear_pending(fake_app, config, agent):
    """Free-form text injection shouldn't consume a pending approval prompt."""
    handler = AsyncMock()
    bridge = TelegramBridge(config, agent, handler, bot_factory=_factory(fake_app))
    await bridge.start()

    pending = ClassifiedPrompt(type=PromptType.APPROVAL_YN, extracted_command="X")
    bridge._pending_prompt = pending

    update = MagicMock()
    update.effective_chat.id = 100
    update.message.text = "wait, can you explain why?"

    await bridge._on_telegram_message(update, None)
    handler.assert_awaited_once()
    assert handler.await_args.args[0].kind == "text"
    assert bridge.pending_prompt is pending  # text reply doesn't consume it
    await bridge.stop()


@pytest.mark.skipif(not _ptb_installed(), reason="python-telegram-bot not installed")
async def test_wait_for_reply_or_timeout_returns_false_on_timeout(
    fake_app, config, agent
):
    handler = AsyncMock()
    fast_config = TelegramConfig(
        bot_token=config.bot_token, chat_id=config.chat_id,
        approval_timeout_seconds=1, timeout_action="auto_no",
    )
    bridge = TelegramBridge(fast_config, agent, handler, bot_factory=_factory(fake_app))

    event = asyncio.Event()
    arrived = await bridge.wait_for_reply_or_timeout(event)
    assert arrived is False


@pytest.mark.skipif(not _ptb_installed(), reason="python-telegram-bot not installed")
async def test_wait_for_reply_returns_true_when_event_fires(
    fake_app, config, agent
):
    handler = AsyncMock()
    bridge = TelegramBridge(config, agent, handler, bot_factory=_factory(fake_app))

    event = asyncio.Event()

    async def fire_later():
        await asyncio.sleep(0.05)
        event.set()

    asyncio.create_task(fire_later())
    arrived = await bridge.wait_for_reply_or_timeout(event)
    assert arrived is True
