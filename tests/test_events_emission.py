"""Event emission per daemon code path — the JSONL trail a dashboard/human reads.

Drives the poller through the flow → handoff → session_start → death → session_end
→ reclaim_done against fakes, asserting the event sequence, field presence, and —
critically — that message_pooled carries NO message content.
"""

from __future__ import annotations

from pathlib import Path

from absd.events import EventLog, iter_events
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller, _register


def _events(abs_home: Path) -> EventLog:
    return EventLog(abs_home / "daemon" / "events.jsonl")


def _names(abs_home: Path, **kw):
    return [e["event"] for e in iter_events(abs_home / "daemon" / "events.jsonl", **kw)]


async def test_pooling_emits_metadata_only(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    log = _events(abs_home)
    poller = make_poller(abs_home, client_factory, engine=FakeEngine(), events=log)

    fake.queue_message("my secret plan text", from_id=42)
    await poller.poll_once()

    path = abs_home / "daemon" / "events.jsonl"
    pooled = list(iter_events(path, event="message_pooled"))
    assert len(pooled) == 1
    assert pooled[0]["update_id"] >= 1
    # NO message content anywhere in the event line
    assert "text" not in pooled[0]
    assert "secret" not in path.read_text()


async def test_command_events(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    log = _events(abs_home)
    poller = make_poller(abs_home, client_factory, engine=FakeEngine(), events=log)
    fake.queue_message("ABS STATUS", from_id=42)
    await poller.poll_once()
    fake.queue_message("ABS POOL", from_id=42)
    await poller.poll_once()
    cmds = [
        e["name"]
        for e in iter_events(abs_home / "daemon" / "events.jsonl", event="command")
    ]
    assert cmds == ["ABS STATUS", "ABS POOL"]


async def test_handoff_session_reclaim_sequence(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"
    proj.mkdir()
    _register(abs_home, proj)
    log = _events(abs_home)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine, events=log)

    # flow → handoff
    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()

    path = abs_home / "daemon" / "events.jsonl"
    seq = [e["event"] for e in iter_events(path)]
    assert "command" in seq
    assert seq.index("handoff") < seq.index("session_start")
    # poller_state idle→session-live emitted
    ps = [e for e in iter_events(path, event="poller_state")]
    assert any(e["state"] == "session-live" and e["from_state"] == "idle" for e in ps)
    # handoff fields
    ho = next(iter_events(path, event="handoff"))
    assert ho["project"] == str(proj.resolve())
    assert ho["mode"] == "normal"
    assert ho["engine"] == "fake"
    assert ho["resume"] is False
    # session_start fields
    ss = next(iter_events(path, event="session_start"))
    assert "pane_id" in ss and "pid" in ss

    # kill → death → reclaim
    assert await poller.watch_once() is True
    engine.kill("default")
    assert await poller.watch_once() is False

    async def rec(_d: float) -> None:
        pass

    await poller.reclaim(sleep=rec)

    seq2 = [e["event"] for e in iter_events(path)]
    assert "engine_kill" in seq2
    assert "session_end" in seq2 and "reclaim_done" in seq2
    end = next(iter_events(path, event="session_end"))
    assert end["reason"] == "exited"
    assert isinstance(end["lived_s"], int)
    done = next(iter_events(path, event="reclaim_done"))
    assert done["backoff_409s"] == 0
    # menu_set events for both kinds
    kinds = {e["kind"] for e in iter_events(path, event="menu_set")}
    assert {"session", "idle"} <= kinds


async def test_reclaim_409_counted_in_event(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    from absd.daemon import STATE_RECLAIM

    write_profile(abs_home, allow_ids=[42])
    log = _events(abs_home)
    poller = make_poller(abs_home, client_factory, engine=FakeEngine(), events=log)
    poller.session_state = STATE_RECLAIM
    poller._handoff_chat_id = 42
    fake.inject_409(2)

    async def rec(_d: float) -> None:
        pass

    await poller.reclaim(sleep=rec)
    done = next(iter_events(abs_home / "daemon" / "events.jsonl", event="reclaim_done"))
    assert done["backoff_409s"] == 2
