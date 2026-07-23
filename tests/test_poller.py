"""The IDLE_POLLING poller (PLAN.md 4.1 + 5.1-5.3 + D9/D14) against fakes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from absd.config import DaemonConfig
from absd.daemon import (
    POOL_ACK,
    START_ACK,
    Poller,
    is_status,
    normalize_command,
    render_pool,
    render_status,
)
from absd.pool import PooledMessage, utc_now_iso
from absd.profiles import discover
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram


def build_poller(abs_home: Path, client_factory, cfg: DaemonConfig | None = None) -> Poller:
    prof = discover(abs_home, home=abs_home)[0]
    client = client_factory(prof.load_token())
    cfg = cfg or DaemonConfig(poll_timeout_s=0, reclaim_backoff_max_s=10.0)
    return Poller(prof, client, cfg, state_dir=abs_home / "daemon")


# ---- allowlist (5.1 / D10) ---------------------------------------------------


async def test_allowlisted_message_pooled_and_acked(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    uid = fake.queue_message("please do a thing", from_id=42)
    poller = build_poller(abs_home, client_factory)

    n = await poller.poll_once()

    assert n == 1
    recs = poller.pool.read_all()
    assert len(recs) == 1
    assert recs[0].text == "please do a thing"
    assert len(fake.sent_messages) == 1
    assert fake.sent_messages[0]["text"] == POOL_ACK.format(n=1)
    assert poller.offset == uid + 1  # offset advanced


async def test_stranger_silence_offset_advances_nothing_pooled(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    uid = fake.queue_message("let me in", from_id=999)  # not allowlisted
    poller = build_poller(abs_home, client_factory)

    await poller.poll_once()

    assert fake.sent_messages == []  # no reply — don't leak liveness (5.1)
    assert poller.pool.read_all() == []  # not pooled
    assert poller.offset == uid + 1  # but offset still advances


# ---- read commands (D9) ------------------------------------------------------


async def test_abs_status_reply(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.queue_message("ABS STATUS", from_id=42)
    poller = build_poller(abs_home, client_factory)

    await poller.poll_once()

    assert poller.pool.read_all() == []  # a command is not pooled
    text = fake.sent_messages[0]["text"]
    assert "ABS STATUS — default" in text
    assert "Pool: 0 message(s)" in text
    assert "Daemon: absd" in text
    assert "idle" in text  # no live session


async def test_abs_status_case_insensitive(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.queue_message("abs status", from_id=42)
    poller = build_poller(abs_home, client_factory)
    await poller.poll_once()
    assert "ABS STATUS — default" in fake.sent_messages[0]["text"]
    assert poller.pool.read_all() == []


async def test_abs_pool_reply(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.queue_message("first pooled thing", from_id=42)
    poller = build_poller(abs_home, client_factory)
    await poller.poll_once()  # pools one
    fake.queue_message("ABS POOL", from_id=42)
    await poller.poll_once()  # asks for the preview

    preview = fake.sent_messages[-1]["text"]
    assert "Pool (1)" in preview
    assert "1. first pooled thing" in preview


async def test_abs_pool_empty(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.queue_message("ABS POOL", from_id=42)
    poller = build_poller(abs_home, client_factory)
    await poller.poll_once()
    assert "empty" in fake.sent_messages[-1]["text"].lower()


# ---- near misses pool, not execute (D9) --------------------------------------


@pytest.mark.parametrize("text", ["ABS  STATUS", "abs status extra words", "ABSSTATUS"])
async def test_near_miss_pools_not_executes(
    abs_home: Path, fake: FakeTelegram, client_factory, text: str
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.queue_message(text, from_id=42)
    poller = build_poller(abs_home, client_factory)
    await poller.poll_once()

    # It was pooled (D9: everything non-exact pools) and got the generic ack.
    assert len(poller.pool.read_all()) == 1
    assert fake.sent_messages[0]["text"] == POOL_ACK.format(n=1)


async def test_abs_start_distinct_ack_and_pools(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.queue_message("ABS START", from_id=42)
    poller = build_poller(abs_home, client_factory)
    await poller.poll_once()

    assert len(poller.pool.read_all()) == 1  # still pooled (1.5 wires the flow)
    ack = fake.sent_messages[0]["text"]
    assert ack == START_ACK.format(n=1)
    assert "isn't wired up yet" in ack


async def test_callback_query_pools_and_answers(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.queue_callback_query("proj:foo", from_id=42, chat_id=42)
    poller = build_poller(abs_home, client_factory)
    await poller.poll_once()

    # callback_query is not a command — it pools + acks + answers the callback.
    assert len(poller.pool.read_all()) == 1
    assert fake.answered_callbacks  # spinner cleared
    assert fake.sent_messages[0]["text"] == POOL_ACK.format(n=1)


# ---- dedupe / D14 ------------------------------------------------------------


async def test_dedupe_on_redelivered_update_id(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = build_poller(abs_home, client_factory)
    # Pre-seed the pool as if this update was already stored before a crash.
    poller.pool.append(PooledMessage(5, 42, "already here", utc_now_iso()))
    fake.queue_message("already here", from_id=42, update_id=5)

    await poller.poll_once()

    assert len(poller.pool.read_all()) == 1  # not duplicated
    assert fake.sent_messages == []  # redelivery stays silent (already acked)


# ---- crash safety: seam between pool-persist and offset-advance --------------


async def test_crash_between_pool_and_offset_no_loss_no_dup(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.queue_message("must survive a crash", from_id=42, update_id=5)

    poller = build_poller(abs_home, client_factory)

    async def boom() -> None:
        raise RuntimeError("simulated crash after pool, before offset commit")

    poller.on_batch_persisted = boom
    with pytest.raises(RuntimeError):
        await poller.poll_once()

    # Pool persisted; offset NOT committed (state file absent).
    assert [m.text for m in poller.pool.read_all()] == ["must survive a crash"]
    assert not (abs_home / "daemon" / "poller-default.json").exists()
    assert len(fake.sent_messages) == 1  # ack was sent before the crash

    # ---- restart: fresh poller, no seam, message is redelivered --------------
    poller2 = build_poller(abs_home, client_factory)
    assert poller2.offset is None  # nothing committed to resume from
    await poller2.poll_once()

    assert len(poller2.pool.read_all()) == 1  # NO duplicate (D14 + dedupe)
    assert len(fake.sent_messages) == 1  # NO second ack
    assert poller2.offset == 6  # now the offset commits
    assert (abs_home / "daemon" / "poller-default.json").exists()


# ---- 409 backoff -------------------------------------------------------------


async def test_409_exponential_backoff_capped(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    fake.set_always_409()
    cfg = DaemonConfig(poll_timeout_s=0, reclaim_backoff_max_s=10.0)
    poller = build_poller(abs_home, client_factory, cfg=cfg)

    delays: list[float] = []

    async def rec(d: float) -> None:
        delays.append(d)  # no real sleeping

    await poller.run(max_cycles=4, sleep=rec)

    assert delays == [2.0, 4.0, 8.0, 10.0]  # 2,4,8 then capped at the max


# ---- live session yield (PLAN.md 4.1) ----------------------------------------


async def test_live_session_no_getupdates_then_resumes(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], session_pid=os.getpid())
    poller = build_poller(abs_home, client_factory)

    naps: list[float] = []

    async def rec(d: float) -> None:
        naps.append(d)

    await poller.run(max_cycles=3, sleep=rec)
    assert fake.getupdates_calls == 0  # never polled while a session is live
    assert len(naps) == 3  # yielded each cycle

    # Session ends (abs exit removes session.pid) -> polling resumes.
    (abs_home / "profiles" / "default" / "session.pid").unlink()
    fake.queue_message("now you can hear me", from_id=42)
    await poller.poll_once()
    assert fake.getupdates_calls >= 1
    assert len(poller.pool.read_all()) == 1


# ---- blocked / off yield (D11) -----------------------------------------------


async def test_blocked_does_not_poll(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], blocked=True)
    fake.queue_message("hi", from_id=42)
    poller = build_poller(abs_home, client_factory)
    assert await poller.poll_once() == -1
    assert fake.getupdates_calls == 0
    assert poller.pool.read_all() == []


async def test_off_does_not_poll(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42], dm_policy="disabled")
    fake.queue_message("hi", from_id=42)
    poller = build_poller(abs_home, client_factory)
    assert await poller.poll_once() == -1
    assert fake.getupdates_calls == 0
    assert poller.pool.read_all() == []


# ---- pure helpers ------------------------------------------------------------


def test_normalize_and_status_matcher() -> None:
    assert normalize_command("  abs status ") == "ABS STATUS"
    assert is_status("ABS STATUS")
    assert is_status("abs status")
    assert not is_status("ABS  STATUS")  # double space is a near miss
    assert not is_status("ABS STATUS now")


def test_render_status_live() -> None:
    out = render_status("work", 1234, 3)
    assert "live (pid 1234)" in out
    assert "Pool: 3" in out


def test_render_pool_truncates_and_caps() -> None:
    msgs = [PooledMessage(i, 42, "x" * 200, utc_now_iso()) for i in range(15)]
    out = render_pool(msgs)
    assert "Pool (15)" in out
    # capped preview
    assert "showing 10 of 15" in out
    # each shown line truncated with an ellipsis
    assert "…" in out
