"""A sandbox orphan must not outlive the daemon that was supposed to reap it.

`engine.kill()` only closes the host-side `docker exec` client; the claude inside
the container and its Telegram plugin survive it. That orphan keeps polling the
profile's bot, Telegram hands each update to whichever consumer asks first, and
the next session sees a random half of the operator's messages. Nothing errors.

`_kill_engine_session` reaps the in-box half through `self._session_sandbox`,
which is set at launch and restored from the handoff marker on the branch where
a session SURVIVED a restart. The branch where it did not survive — reclaim —
never restored it, so the reaper looked at `None` and returned. The fix worked
for the lifetime of one daemon process and was undone by every restart.

Found on Pranjal's machine during the 3.0.0 release test, ten days after the
orphan-poller fix shipped: a `claude` from 5 August was still running inside
`absd-sbx-v4box`, still polling `abs_test_001_bot`, while the host daemon
reported that profile as idle.

`test_sandbox_session.py` already covers the reaper — but it assigns
`poller._session_sandbox` by hand, so it proves the mechanism and never the path
that has to reach it. That is the shape of this whole bug, and the reason a
green suite said nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from absd.daemon import STATE_IDLE, STATE_SESSION_LIVE
from absd.pool import utc_now_iso
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller
from tests.test_sandbox_session import FakeSandbox

_DEAD_PID = 999_999_999


def _marker(
    abs_home: Path,
    *,
    sandbox: str | None,
    pane_id: str | None = "default:w1:p1",
    profile: str = "default",
) -> None:
    (abs_home / "profiles" / profile / "daemon-handoff.json").write_text(
        json.dumps(
            {
                "timestamp": utc_now_iso(),
                "project": "/p/llm",
                "mode": "normal",
                "chat_id": 42,
                "pane_id": pane_id,
                "launcher_pid": _DEAD_PID,
                "sandbox": sandbox,
            }
        )
    )


def _dead_poller(abs_home: Path, client_factory, sbx: FakeSandbox):
    """A daemon booting onto a recorded session that is entirely dead: the pane
    is gone and the launcher pid is not a live process."""
    engine = FakeEngine()
    engine._alive["default"] = False
    return make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)


# ---- the bug -----------------------------------------------------------------


async def test_boot_reclaim_reaps_the_in_box_half(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """The regression. A dead sandbox session found at boot must have its
    container half killed, not just its host-side client."""
    write_profile(abs_home, allow_ids=[42])
    _marker(abs_home, sandbox="v4box")
    sbx = FakeSandbox()
    poller = _dead_poller(abs_home, client_factory, sbx)

    outcome = await poller.boot_recover_and_notify()

    assert outcome == "reclaimed"
    assert sbx.killed_sessions == [("v4box", "default")]


async def test_a_dead_non_sandbox_session_never_touches_a_box(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """The other half of the contract: a plain host session has no container to
    reap, and reclaim must not invent one."""
    write_profile(abs_home, allow_ids=[42])
    _marker(abs_home, sandbox=None)
    sbx = FakeSandbox()
    poller = _dead_poller(abs_home, client_factory, sbx)

    outcome = await poller.boot_recover_and_notify()

    assert outcome == "reclaimed"
    assert sbx.killed_sessions == []


async def test_a_surviving_sandbox_session_is_left_alone(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """A box whose session came through the restart intact must NOT be reaped —
    that would kill a working session on every daemon restart, which is a worse
    bug than the one being fixed."""
    write_profile(abs_home, allow_ids=[42])
    _marker(abs_home, sandbox="v4box")
    engine = FakeEngine()
    engine._alive["default"] = True          # pane survived
    sbx = FakeSandbox()
    poller = make_poller(abs_home, client_factory, engine=engine, sandbox_mgr=sbx)

    outcome = await poller.boot_recover_and_notify()

    assert outcome == "session-live"
    assert poller.session_state == STATE_SESSION_LIVE
    assert sbx.killed_sessions == []
    assert poller._session_sandbox == "v4box"   # restored, ready for a later reclaim


# ---- what the orphan actually costs ------------------------------------------


async def test_after_reclaim_the_profile_is_idle_and_polling_again(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """The daemon resumes polling this token after reclaim. That is precisely why
    the orphan matters: two consumers, and Telegram gives each update to whoever
    asks first."""
    write_profile(abs_home, allow_ids=[42])
    _marker(abs_home, sandbox="v4box")
    sbx = FakeSandbox()
    poller = _dead_poller(abs_home, client_factory, sbx)

    await poller.boot_recover_and_notify()
    assert poller.session_state == STATE_IDLE

    before = fake.getupdates_calls
    await poller.poll_once()
    assert fake.getupdates_calls > before     # the daemon is now a consumer


async def test_a_failed_in_box_reap_at_boot_is_not_silent(
    abs_home: Path, fake: FakeTelegram, client_factory, caplog
) -> None:
    """If the kill fails, the orphan is still out there splitting updates. The
    operator's messages go missing with nothing else to explain it, so the log
    line is the only thread back to the cause."""
    import logging

    write_profile(abs_home, allow_ids=[42])
    _marker(abs_home, sandbox="v4box")
    sbx = FakeSandbox()
    sbx.kill_result = False
    poller = _dead_poller(abs_home, client_factory, sbx)

    with caplog.at_level(logging.WARNING):
        await poller.boot_recover_and_notify()

    assert "may have survived" in caplog.text


async def test_reclaim_reaps_the_box_named_in_the_marker_not_the_profile_default(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    """A profile can run sessions in different ad-hoc boxes over time. The one to
    kill is the one this session was launched into, which only the marker knows —
    `rc.json`'s `sandbox` field is the dedicated box of a *restricted* profile and
    is absent here."""
    write_profile(abs_home, allow_ids=[42])
    _marker(abs_home, sandbox="box1")
    sbx = FakeSandbox()
    poller = _dead_poller(abs_home, client_factory, sbx)

    await poller.boot_recover_and_notify()

    assert sbx.killed_sessions == [("box1", "default")]
    rc = json.loads((abs_home / "profiles" / "default" / "rc.json").read_text())
    assert "sandbox" not in rc          # the name really did come from the marker
