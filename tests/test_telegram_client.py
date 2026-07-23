"""TelegramClient against FakeTelegram: methods, 409, token hygiene."""

from __future__ import annotations

import pytest

from absd.telegram import Conflict409, TelegramClient, TelegramError
from tests.harness.fake_telegram import FakeTelegram


async def test_get_updates_returns_queued(fake: FakeTelegram, client_factory) -> None:
    fake.queue_message("hello", from_id=42)
    client = client_factory()
    updates = await client.get_updates(timeout=0)
    assert len(updates) == 1
    assert updates[0]["message"]["text"] == "hello"


async def test_get_updates_offset_confirms(fake: FakeTelegram, client_factory) -> None:
    uid = fake.queue_message("one", from_id=42)
    client = client_factory()
    await client.get_updates(offset=None, timeout=0)
    # Advance the offset past it — should be confirmed and not redelivered.
    again = await client.get_updates(offset=uid + 1, timeout=0)
    assert again == []


async def test_send_message_captured(fake: FakeTelegram, client_factory) -> None:
    client = client_factory()
    await client.send_message(chat_id=42, text="ack")
    assert fake.sent_messages[-1]["chat_id"] == 42
    assert fake.sent_messages[-1]["text"] == "ack"


async def test_edit_and_answer_and_commands(fake: FakeTelegram, client_factory) -> None:
    client = client_factory()
    await client.edit_message_text(chat_id=42, message_id=5, text="new")
    await client.answer_callback_query("cbq-1", text="ok")
    await client.set_my_commands([{"command": "abs", "description": "x"}])
    assert fake.edited_messages[-1]["text"] == "new"
    assert fake.answered_callbacks[-1]["callback_query_id"] == "cbq-1"
    assert fake.commands


async def test_409_surfaces_as_conflict(fake: FakeTelegram, client_factory) -> None:
    fake.set_always_409()
    client = client_factory()
    with pytest.raises(Conflict409):
        await client.get_updates(timeout=0)


async def test_409_not_subclass_confusion(fake: FakeTelegram, client_factory) -> None:
    fake.set_always_409()
    client = client_factory()
    try:
        await client.get_updates(timeout=0)
    except Conflict409 as exc:
        assert isinstance(exc, TelegramError)  # a 409 IS a TelegramError subtype
        assert "409" in str(exc)


async def test_token_never_in_repr() -> None:
    client = TelegramClient("SUPERSECRET:TOKEN", base_url="http://x")
    assert "SUPERSECRET" not in repr(client)
    assert "redacted" in repr(client)


async def test_unknown_method_raises_telegram_error(fake: FakeTelegram) -> None:
    # Drive a method the fake 404s to prove non-200 becomes TelegramError, not a
    # silent None. get_updates path is fine; use a direct _request.
    client = TelegramClient("TEST", base_url=fake.base_url)
    try:
        with pytest.raises(TelegramError):
            await client._request("getMe", {}, timeout_s=5)
    finally:
        await client.close()
