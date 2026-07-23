"""Kill ladder while idle (Step 1.7 / D11): ABS OFF / BLOCK / CLEAR POOL."""

from __future__ import annotations

import json
from pathlib import Path

from absd.daemon import BLOCK_ACK, CLEAR_POOL_ACK, OFF_ACK
from absd.events import EventLog, iter_events
from absd.pool import PooledMessage, utc_now_iso
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller


def _events(abs_home: Path):
    return EventLog(abs_home / "daemon" / "events.jsonl")


def _cmd_names(abs_home: Path):
    return [
        e["name"]
        for e in iter_events(abs_home / "daemon" / "events.jsonl", event="command")
    ]


async def test_abs_off_disables_and_stops_polling(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine(), events=_events(abs_home))
    assert poller.profile.should_poll() is True

    fake.queue_message("ABS OFF", from_id=42)
    await poller.poll_once()

    # access.json dmPolicy -> disabled (the SAME field abs.sh writes)
    access = json.loads((poller.profile.access_path).read_text())
    assert access["dmPolicy"] == "disabled"
    # other keys preserved (allowFrom etc.)
    assert "allowFrom" in access
    assert fake.sent_messages[-1]["text"] == OFF_ACK
    assert poller.profile.should_poll() is False  # poller now yields
    assert "ABS OFF" in _cmd_names(abs_home)


async def test_abs_block_sets_blocked_and_stops(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine(), events=_events(abs_home))

    fake.queue_message("ABS BLOCK", from_id=42)
    await poller.poll_once()

    rc = json.loads((poller.profile.rc_path).read_text())
    assert rc["blocked"] is True
    assert "tg_dir" in rc  # other rc.json keys preserved
    assert fake.sent_messages[-1]["text"] == BLOCK_ACK
    assert poller.profile.is_blocked() is True
    assert poller.profile.should_poll() is False
    assert "ABS BLOCK" in _cmd_names(abs_home)


async def test_abs_clear_pool_empties_and_acks(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine(), events=_events(abs_home))
    poller.pool.append(PooledMessage(1, 42, "one", utc_now_iso()))
    poller.pool.append(PooledMessage(2, 42, "two", utc_now_iso()))

    fake.queue_message("ABS CLEAR POOL", from_id=42)
    await poller.poll_once()

    assert poller.pool.read_all() == []
    assert fake.sent_messages[-1]["text"] == CLEAR_POOL_ACK.format(n=2)
    assert "ABS CLEAR POOL" in _cmd_names(abs_home)


async def test_kill_ladder_case_insensitive(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    fake.queue_message("abs off", from_id=42)  # lowercase
    await poller.poll_once()
    assert poller.profile.is_off() is True


async def test_kill_ladder_gated_by_allowlist(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # A stranger's "ABS OFF" is dropped (allowlist first) — no state change, no ack.
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    fake.queue_message("ABS OFF", from_id=999)  # not allowlisted
    await poller.poll_once()

    assert poller.profile.is_off() is False  # unchanged
    assert fake.sent_messages == []  # silent for strangers
