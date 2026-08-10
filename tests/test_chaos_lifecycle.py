"""Step 2.3's critique gate — randomized lifecycle chaos against the invariants.

The plan asks for "random kills of daemon, session, or fake-telegram over 500
iterations", holding two invariants:

  **I1 — the pool never loses an acked message.** Every message the daemon
  acknowledged is, at every instant, either still pending in the pool or recorded
  as forwarded to a session. Losing one means a user was told "saved" about
  something that then evaporated, which is the worst failure this system has.

  **I2 — the offset never goes backwards.** Telegram's cursor is the only thing
  standing between a restart and re-delivering messages the user already answered.

Plus two structural ones the chaos is well placed to catch:

  **I3 — a marker is never left behind an IDLE poller** once the sweep has had a
  chance to run: a handoff marker with no session is what wedges the next launch.
  **I4 — the state machine always converges.** Whatever the sequence, a quiescent
  world returns the poller to IDLE.

Everything is driven through the real ``Poller`` against fakes — a fake engine
whose sessions can be killed under the daemon, a fake Telegram that can 409 or go
away, and a "daemon restart" modelled the way a restart actually happens: a NEW
``Poller`` over the SAME on-disk state, which is exactly where crash-safety bugs
live. Seeded, so a failure reproduces from the printed seed.
"""

from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from absd.daemon import (
    STATE_IDLE,
    STATE_RECLAIM,
    STATE_SESSION_LIVE,
    HandoffRequest,
    Poller,
)
from absd.pool import Pool
from absd.telegram import Conflict409
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller

#: Iterations per seed. The plan says 500; that is `ABS_CHAOS_ITERS=500 pytest`.
#: The default is smaller so the suite stays fast enough to run on every change —
#: with 5 seeds it is still 500 operations of coverage, just spread across more
#: starting points, which finds more than one long walk does.
ITERS = int(os.environ.get("ABS_CHAOS_ITERS", "100"))
SEEDS = [int(s) for s in os.environ.get("ABS_CHAOS_SEEDS", "1,2,3,4,5").split(",")]


class ChaosEngine(FakeEngine):
    """A FakeEngine whose live session can be killed out from under the daemon."""

    def hard_kill(self) -> None:
        """The session dies without telling anyone — SIGKILL, OOM, closed laptop."""
        for profile in list(self._alive):
            self._alive[profile] = False


def _noop_sleep(sleep_calls: list[float]):
    """A sleep that records instead of waiting — reclaim's grace/backoff for free."""

    async def _sleep(secs: float) -> None:
        sleep_calls.append(secs)

    return _sleep


async def _quiesce(
    poller: Poller, now: list[float], sleep_calls: list[float], limit: int = 40
) -> None:
    """With nothing alive, run the poller until it settles in IDLE.

    The caller must have killed the session first — a *live* session correctly
    never converges, and asserting otherwise would be testing a lie. Time advances
    each turn so a launch still inside its start grace resolves rather than
    spinning, and sleep is a no-op so reclaim's grace/backoff costs no wall clock.

    Asserts convergence (I4) rather than hoping for it.
    """

    for _ in range(limit):
        if poller.session_state == STATE_IDLE:
            return
        now[0] += 60.0
        if poller.session_state == STATE_SESSION_LIVE:
            await poller.watch_once()
        elif poller.session_state == STATE_RECLAIM:
            await poller.reclaim(sleep=_noop_sleep(sleep_calls))
    assert poller.session_state == STATE_IDLE, (
        f"poller failed to converge; stuck in {poller.session_state}"
    )


def _pool_ids(abs_home: Path) -> tuple[set[int], set[int]]:
    """(pending update_ids, forwarded update_ids) straight off disk.

    Read from a FRESH ``Pool`` each time so the assertion is about what actually
    survived to the file, not about an in-memory copy the poller happens to hold.
    """
    pool = Pool(abs_home / "profiles" / "default" / "pool.jsonl")
    records = pool.read_all()
    pending = {m.update_id for m in records if m.forwarded_at is None}
    forwarded = {m.update_id for m in records if m.forwarded_at is not None}
    return pending, forwarded


@pytest.mark.parametrize("seed", SEEDS)
async def test_lifecycle_chaos_holds_the_invariants(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path, seed: int
) -> None:
    rng = random.Random(seed)
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "proj"
    proj.mkdir()

    engine = ChaosEngine()
    now = [1_000.0]
    poller = make_poller(
        abs_home,
        client_factory,
        engine=engine,
        clock=lambda: now[0],
        stale_handoff_check_s=1.0,
        stale_handoff_after_s=60.0,
        session_start_grace_s=30.0,
    )

    sleeps: list[float] = []
    #: Every update_id handed to the fake server. Derived from the SERVER side on
    #: purpose: an invariant that reads the pool to decide what should be in the
    #: pool is circular and passes through a pool that silently drops writes
    #: (verified — the first version of this test did exactly that and survived a
    #: mutant that dropped one message in five).
    queued: list[int] = []
    last_offset = -1
    marker_path = abs_home / "profiles" / "default" / "daemon-handoff.json"

    for step in range(ITERS):
        action = rng.choice(
            [
                "message",
                "message",  # weighted: traffic is the common case
                "start",
                "hard_kill",
                "restart_daemon",
                "409",
                "advance_clock",
                "tick",
                "tick",
            ]
        )

        if action == "message":
            # Plain text, never a command word — so every one of these that the
            # daemon consumes MUST end up in the pool. That is the whole invariant.
            queued.append(fake.queue_message(f"chaos {step}", from_id=42))
            try:
                await poller.poll_once()
            except Conflict409:
                # A leftover 409 pulse. In production run() catches this and backs
                # off; nothing was consumed, so the message is still on the server.
                pass
            except Exception as exc:  # noqa: BLE001 - a crash IS the finding
                pytest.fail(f"seed {seed} step {step}: poll_once raised {exc!r}")

        elif action == "start":
            if poller.session_state == STATE_IDLE:
                poller._handoff_request = HandoffRequest(
                    chat_id=42, project_path=str(proj), label="proj", away=False
                )
                await poller._do_handoff()

        elif action == "hard_kill":
            engine.hard_kill()

        elif action == "restart_daemon":
            # A restart is a NEW Poller over the SAME disk state — the only honest
            # way to model it, and where crash-safety bugs actually live. The old
            # poller is simply dropped, exactly as a killed daemon process is; the
            # new one must re-derive everything from what is on disk.
            poller = make_poller(
                abs_home,
                client_factory,
                engine=engine,
                clock=lambda: now[0],
                stale_handoff_check_s=1.0,
                stale_handoff_after_s=60.0,
                session_start_grace_s=30.0,
            )
            await poller.boot_recover_and_notify()

        elif action == "409":
            fake.inject_409(times=rng.randint(1, 2))
            try:
                await poller.poll_once()
            except Conflict409:
                pass  # expected: run() backs off, no state change

        elif action == "advance_clock":
            now[0] += rng.choice([1.0, 5.0, 90.0])

        elif action == "tick":
            # One turn of run()'s dispatch. It must cover RECLAIM too: without it
            # the walk parks in RECLAIM forever, poll_once stops consuming, and the
            # message invariant goes quietly vacuous — which is how the first
            # version of this test passed while proving nothing.
            if poller.session_state == STATE_SESSION_LIVE:
                await poller.watch_once()
            elif poller.session_state == STATE_RECLAIM:
                await poller.reclaim(sleep=_noop_sleep(sleeps))
            else:
                await poller.sweep_stale_handoff()

        # ---- invariants, checked after EVERY operation ----------------------

        pending, forwarded = _pool_ids(abs_home)
        # Consumed = the daemon advanced its cursor past it, i.e. it will never be
        # offered by Telegram again. From that instant the pool is the ONLY copy.
        consumed = (
            {u for u in queued if u < poller.offset} if poller.offset is not None
            else set()
        )
        lost = consumed - pending - forwarded
        assert not lost, (
            f"seed {seed} step {step} ({action}): messages {sorted(lost)} were "
            "consumed from Telegram but exist nowhere in the pool"
        )

        if poller.offset is not None:
            assert poller.offset >= last_offset, (
                f"seed {seed} step {step} ({action}): offset went backwards "
                f"({poller.offset} < {last_offset})"
            )
            last_offset = poller.offset

    # ---- final convergence (I4) + no marker left behind (I3) ----------------

    engine.hard_kill()
    await _quiesce(poller, now, sleeps)
    assert poller.session_state == STATE_IDLE

    # I3 deliberately, not incidentally: put the system in the exact state the
    # sweep exists for — a session started, then killed so hard the daemon never
    # observed it (a restart drops the in-memory state, the marker stays on disk) —
    # and require the sweep to clear it. Asserting "no marker" at the end of a
    # random walk would pass vacuously on the runs that never started a session.
    poller._handoff_request = HandoffRequest(
        chat_id=42, project_path=str(proj), label="proj", away=False
    )
    await poller._do_handoff()
    assert marker_path.exists()
    engine.hard_kill()
    poller = make_poller(
        abs_home,
        client_factory,
        engine=engine,
        clock=lambda: now[0],
        stale_handoff_check_s=1.0,
        stale_handoff_after_s=60.0,
        session_start_grace_s=30.0,
    )
    poller._marker_age_s = lambda m: 9_999.0  # type: ignore[method-assign]
    now[0] += 10_000.0
    await poller.sweep_stale_handoff()  # arms the timer
    now[0] += 10.0
    assert await poller.sweep_stale_handoff() is True
    assert not marker_path.exists(), (
        f"seed {seed}: a handoff marker survived a quiesced, session-less poller — "
        "the next ABS START would collide with it"
    )

    # And every message the daemon ever consumed is still accounted for.
    pending, forwarded = _pool_ids(abs_home)
    consumed = (
        {u for u in queued if u < poller.offset} if poller.offset is not None else set()
    )
    assert not (consumed - pending - forwarded)
    # Non-vacuity: a run that never consumed a message proves nothing about I1.
    assert consumed, f"seed {seed}: no message was ever consumed — I1 was vacuous"
