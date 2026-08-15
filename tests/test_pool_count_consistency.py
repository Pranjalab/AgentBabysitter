"""One question, one answer: how many messages are waiting?

Forwarding stamps ``forwarded_at`` rather than deleting, so a pool file keeps
every message it has ever held. `ABS START` correctly reads ``unforwarded()``.
Everything else read ``count()`` — the size of the FILE — so the moment anything
was delivered the two diverged and never converged again.

Found on Pranjal's own machine during the 3.0.0 release test: `abs daemon
status` advertised `pool=4` for a profile whose four messages had been delivered
on 5 August. `ABS POOL` would have listed all four under "Send ABS START to act
on them", and `ABS START` would then have offered nothing. He was midway through
section F — the pool multi-select checklist — and this would have read as the
pool being broken.

The invariant worth pinning is not "the number is right" but **the numbers
agree**: what `ABS POOL` claims is waiting must equal what `ABS START` offers.
A count that can disagree with the screen it points you at is worse than no
count, because it survives a glance.
"""

from __future__ import annotations

import json
from pathlib import Path

from absd.daemon import Poller, render_pool, render_status
from absd.pool import Pool, PooledMessage, utc_now_iso
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller


def _seed(poller: Poller, texts, forwarded: int = 0):
    """Pool `texts`, marking the first `forwarded` of them as delivered."""
    for i, t in enumerate(texts, start=1):
        poller.pool.append(PooledMessage(i, 42, t, utc_now_iso()))
    if forwarded:
        poller.pool.mark_forwarded(list(range(1, forwarded + 1)))


# ---- the storage layer -------------------------------------------------------


def test_pending_count_ignores_delivered_messages(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "pool.jsonl")
    for i, t in enumerate(["a", "b", "c", "d"], start=1):
        pool.append(PooledMessage(i, 42, t, utc_now_iso()))
    pool.mark_forwarded([1, 2, 3])
    assert pool.pending_count() == 1
    assert pool.count() == 4          # the file still holds all four, by design


def test_pending_count_is_zero_when_everything_was_delivered(tmp_path: Path) -> None:
    """Pranjal's exact state: four old messages, all forwarded, nothing waiting."""
    pool = Pool(tmp_path / "pool.jsonl")
    for i in range(1, 5):
        pool.append(PooledMessage(i, 42, "hi", utc_now_iso()))
    pool.mark_forwarded([1, 2, 3, 4])
    assert pool.pending_count() == 0
    assert pool.unforwarded() == []


def test_pending_count_matches_count_when_nothing_was_delivered(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "pool.jsonl")
    for i in range(1, 4):
        pool.append(PooledMessage(i, 42, "x", utc_now_iso()))
    assert pool.pending_count() == pool.count() == 3


# ---- every place the operator reads a number ---------------------------------


async def test_abs_pool_lists_only_what_is_waiting(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    _seed(poller, ["old one", "old two", "still waiting"], forwarded=2)

    fake.queue_message("ABS POOL", from_id=42)
    await poller.poll_once()

    body = "\n".join(m["text"] for m in fake.sent_messages)
    assert "Pool (1)" in body
    assert "still waiting" in body
    assert "old one" not in body and "old two" not in body


async def test_abs_pool_says_empty_when_all_were_delivered(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """The bug's headline case. It used to list four delivered messages and tell
    you to send ABS START to act on them — which would have offered none."""
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    _seed(poller, ["hi", "hi", "hi", "hi"], forwarded=4)

    fake.queue_message("ABS POOL", from_id=42)
    await poller.poll_once()

    body = "\n".join(m["text"] for m in fake.sent_messages)
    assert "Pool is empty" in body
    assert "Send ABS START to act on them" not in body


async def test_abs_status_counts_only_what_is_waiting(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    _seed(poller, ["a", "b", "c"], forwarded=2)

    fake.queue_message("ABS STATUS", from_id=42)
    await poller.poll_once()

    body = "\n".join(m["text"] for m in fake.sent_messages)
    assert "Pool: 1 message(s)" in body


async def test_the_dashboard_status_file_counts_only_what_is_waiting(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """`abs daemon status` reads this file. It said pool=4 on a drained pool."""
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    _seed(poller, ["a", "b", "c", "d"], forwarded=4)

    poller.write_status()
    rec = json.loads((abs_home / "daemon" / "status-default.json").read_text())
    assert rec["pool_count"] == 0


async def test_clear_pool_reports_what_the_operator_thought_they_had(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """"Cleared 4" after discarding one pending message reads as data loss."""
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    _seed(poller, ["a", "b", "c", "d"], forwarded=3)

    fake.queue_message("ABS CLEAR POOL", from_id=42)
    await poller.poll_once()

    body = "\n".join(m["text"] for m in fake.sent_messages)
    assert "1 message(s) removed" in body
    assert poller.pool.count() == 0          # the file really is emptied


# ---- the invariant that ties them together -----------------------------------


async def test_what_abs_pool_claims_equals_what_abs_start_offers(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """The property, not the number. Any future change that makes one of these
    read a different collection than the other breaks here rather than in front
    of an operator halfway through a checklist."""
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    _seed(poller, ["gone 1", "gone 2", "gone 3", "here 1", "here 2"], forwarded=3)

    fake.queue_message("ABS POOL", from_id=42)
    await poller.poll_once()
    claimed = "\n".join(m["text"] for m in fake.sent_messages)

    offered = poller.pool.unforwarded()
    assert f"Pool ({len(offered)})" in claimed
    assert len(offered) == 2
    for m in offered:
        assert m.text in claimed


def test_render_pool_is_never_handed_delivered_records(tmp_path: Path) -> None:
    """Belt and braces at the renderer: it is a pure function over whatever it is
    given, so the guarantee has to live at the call site — this pins that the
    call site passes `unforwarded`, by showing what the alternative produced."""
    pool = Pool(tmp_path / "pool.jsonl")
    for i in range(1, 5):
        pool.append(PooledMessage(i, 42, "hi", utc_now_iso()))
    pool.mark_forwarded([1, 2, 3, 4])
    assert render_pool(pool.unforwarded()) == "🗂 Pool is empty."
    assert "Pool (4)" in render_pool(pool.read_all())   # the old, wrong argument


def test_render_status_shows_the_pending_number(tmp_path: Path) -> None:
    out = render_status("default", None, 0)
    assert "Pool: 0 message(s)" in out
