"""Tests FOR the fake_telegram harness (PLAN.md 0.3 critique gate).

Covers exactly what the plan demands the fake prove: offset semantics (nothing
replayed once confirmed, nothing lost before it), 409 injection, callback_query
queue+consume, plus capture of the outbound methods the daemon uses. All local,
127.0.0.1 only — no real Telegram (PLAN.md section 10).
"""

from __future__ import annotations

import asyncio
import time

import aiohttp
import pytest_asyncio

from tests.harness.fake_telegram import FakeTelegram


@pytest_asyncio.fixture
async def fake():
    server = FakeTelegram()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


async def call(session, url, params):
    """POST params as JSON; return (status, parsed_body)."""
    async with session.post(url, json=params) as resp:
        return resp.status, await resp.json()


# ---- offset semantics: nothing lost, nothing replayed once confirmed --------


async def test_getupdates_returns_queued_updates(fake, session):
    fake.queue_message("one", from_id=1)
    fake.queue_message("two", from_id=1)
    status, body = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    assert status == 200 and body["ok"] is True
    ids = [u["update_id"] for u in body["result"]]
    assert ids == [1, 2]
    assert body["result"][0]["message"]["text"] == "one"


async def test_advancing_offset_confirms_and_prevents_replay(fake, session):
    fake.queue_message("a", from_id=1)
    fake.queue_message("b", from_id=1)
    fake.queue_message("c", from_id=1)  # ids 1,2,3

    _, body = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    ids = [u["update_id"] for u in body["result"]]
    assert ids == [1, 2, 3]

    # Advance offset past the highest received id -> confirms all, returns none.
    highest = max(ids)
    _, body2 = await call(
        session, fake.method_url("getUpdates"), {"offset": highest + 1, "timeout": 0}
    )
    assert body2["result"] == []

    # A new update after confirmation is delivered exactly once.
    fake.queue_message("d", from_id=1)  # id 4
    _, body3 = await call(
        session, fake.method_url("getUpdates"), {"offset": highest + 1, "timeout": 0}
    )
    assert [u["update_id"] for u in body3["result"]] == [4]

    # Invariant: every id delivered, none delivered twice.
    assert fake.delivered_ids == [1, 2, 3, 4]
    assert fake.confirmed_offset == highest + 1


async def test_no_offset_advance_replays_unconfirmed(fake, session):
    fake.queue_message("x", from_id=1)
    fake.queue_message("y", from_id=1)

    _, b1 = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    _, b2 = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    # Same updates come back — real Bot API redelivers until offset advances.
    assert [u["update_id"] for u in b1["result"]] == [1, 2]
    assert [u["update_id"] for u in b2["result"]] == [1, 2]
    assert fake.delivered_ids == [1, 2, 1, 2]


async def test_partial_confirm_keeps_the_rest(fake, session):
    for t in ("a", "b", "c"):
        fake.queue_message(t, from_id=1)  # ids 1,2,3
    # Confirm only up to id 1 (offset=2): 1 dropped, 2 and 3 remain.
    _, body = await call(session, fake.method_url("getUpdates"), {"offset": 2, "timeout": 0})
    assert [u["update_id"] for u in body["result"]] == [2, 3]


# ---- 409 fault injection ----------------------------------------------------


async def test_409_injection_once_then_recovers(fake, session):
    fake.queue_message("hi", from_id=1)
    fake.inject_409(times=1)

    status, body = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    assert status == 409
    assert body["ok"] is False and body["error_code"] == 409
    assert "conflict" in body["description"].lower()

    # Next call succeeds and the queued update is intact (not lost by the 409).
    status2, body2 = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    assert status2 == 200
    assert [u["update_id"] for u in body2["result"]] == [1]


async def test_always_409_persists(fake, session):
    fake.set_always_409(True)
    for _ in range(3):
        status, _ = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
        assert status == 409
    fake.set_always_409(False)
    status, body = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    assert status == 200 and body["ok"] is True


# ---- callback_query queue + consume (the ABS START inline-keyboard flow) -----


async def test_callback_query_can_be_queued_and_consumed(fake, session):
    fake.queue_callback_query(data="start:project:web", from_id=42, message_id=7)
    _, body = await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    upd = body["result"][0]
    assert "callback_query" in upd
    cbq = upd["callback_query"]
    assert cbq["data"] == "start:project:web"
    assert cbq["from"]["id"] == 42
    assert cbq["message"]["message_id"] == 7

    # And the daemon would answer it: answerCallbackQuery is captured.
    await call(
        session,
        fake.method_url("answerCallbackQuery"),
        {"callback_query_id": cbq["id"], "text": "starting…"},
    )
    assert fake.answered_callbacks[-1]["callback_query_id"] == cbq["id"]
    assert fake.answered_callbacks[-1]["text"] == "starting…"


# ---- outbound method capture ------------------------------------------------


async def test_send_message_captured(fake, session):
    _, body = await call(
        session,
        fake.method_url("sendMessage"),
        {"chat_id": 99, "text": "pooled (1)", "reply_markup": {"inline_keyboard": []}},
    )
    assert body["ok"] is True
    rec = fake.sent_messages[-1]
    assert rec["chat_id"] == 99
    assert rec["text"] == "pooled (1)"
    assert rec["reply_markup"] == {"inline_keyboard": []}
    # The returned message_id lets a later editMessageText target it.
    assert body["result"]["message_id"] == rec["message_id"]


async def test_edit_message_text_captured(fake, session):
    await call(
        session,
        fake.method_url("editMessageText"),
        {"chat_id": 99, "message_id": 5, "text": "updated"},
    )
    rec = fake.edited_messages[-1]
    assert rec["chat_id"] == 99 and rec["message_id"] == 5 and rec["text"] == "updated"


async def test_set_my_commands_captured(fake, session):
    cmds = [{"command": "abs_start", "description": "start a session"}]
    await call(session, fake.method_url("setMyCommands"), {"commands": cmds})
    assert fake.commands[-1] == cmds


async def test_unknown_method_404(fake, session):
    status, body = await call(session, fake.method_url("deleteWebhook"), {})
    assert status == 404 and body["ok"] is False


# ---- long-poll semantics ----------------------------------------------------


async def test_long_poll_returns_promptly_when_update_arrives(fake, session):
    # Empty queue; open a long poll, then queue an update from the test.
    poll = asyncio.create_task(
        call(session, fake.method_url("getUpdates"), {"timeout": 5})
    )
    await asyncio.sleep(0.1)
    assert not poll.done(), "poll should be parked while the queue is empty"
    fake.queue_message("late", from_id=1)

    start = time.time()
    status, body = await poll
    assert status == 200
    assert [u["update_id"] for u in body["result"]] == [1]
    assert time.time() - start < 2, "long poll must wake quickly on a new update"


async def test_long_poll_times_out_empty(fake, session):
    start = time.time()
    status, body = await call(session, fake.method_url("getUpdates"), {"timeout": 1})
    elapsed = time.time() - start
    assert status == 200 and body["result"] == []
    assert 0.8 <= elapsed < 3, "empty long poll should return around the timeout"


async def test_configurable_response_delay(fake, session):
    fake.set_delay(0.3)
    start = time.time()
    await call(session, fake.method_url("getUpdates"), {"timeout": 0})
    assert time.time() - start >= 0.3
