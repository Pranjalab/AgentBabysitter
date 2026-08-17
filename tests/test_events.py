"""Structured event log (absd/events.py): writer, reader, rotation, tolerance."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from absd.events import (
    EVENT_HANDOFF,
    EVENT_MESSAGE_POOLED,
    EventLog,
    iter_events,
)


def test_emit_writes_line_with_core_fields(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    rec = log.emit(EVENT_HANDOFF, profile="default", project="/p", mode="normal")
    assert rec is not None
    line = json.loads(path.read_text().strip())
    assert line["event"] == "handoff"
    assert line["profile"] == "default"
    assert line["project"] == "/p"
    assert line["level"] == "info"
    assert line["ts"].endswith("Z")


def test_file_is_0600(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    EventLog(path).emit("daemon_start", version="x", profiles=[])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_iter_events_filters(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.emit("poller_state", profile="a", state="polling", from_state="idle")
    log.emit(EVENT_MESSAGE_POOLED, profile="a", update_id=1)
    log.emit(EVENT_MESSAGE_POOLED, profile="b", update_id=2)

    # by profile
    assert [e["profile"] for e in iter_events(path, profile="a")] == ["a", "a"]
    # by event
    pooled = list(iter_events(path, event="message_pooled"))
    assert {e["update_id"] for e in pooled} == {1, 2}
    # by profile + event
    assert [e["update_id"] for e in iter_events(path, profile="b", event="message_pooled")] == [2]


def test_iter_events_since(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    # write explicit timestamps by hand (emit uses now); craft lines directly
    path.write_text(
        "\n".join(
            json.dumps({"ts": ts, "event": "x", "level": "info"})
            for ts in ["2026-07-23T10:00:00Z", "2026-07-23T12:00:00Z", "2026-07-23T14:00:00Z"]
        )
        + "\n"
    )
    got = [e["ts"] for e in iter_events(path, since="2026-07-23T12:00:00Z")]
    assert got == ["2026-07-23T12:00:00Z", "2026-07-23T14:00:00Z"]


def test_corrupt_trailing_line_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.emit("daemon_start", version="x", profiles=[])
    # simulate a crash mid-write: a torn trailing line
    with path.open("a") as fh:
        fh.write('{"ts": "2026-07-23T10:00:00Z", "event": "handof')  # no newline, truncated
    events = list(iter_events(path))
    assert len(events) == 1  # the good line survives, the torn one is skipped
    assert events[0]["event"] == "daemon_start"


def test_rotation_rolls_generations(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path, max_bytes=200, keep=3)  # tiny cap to force rolls
    for i in range(200):
        log.emit("message_pooled", profile="a", update_id=i)
    # rotated into .1/.2/.3 (logrotate-style), no .4 kept
    assert (tmp_path / "events.jsonl.1").exists()
    assert not (tmp_path / "events.jsonl.4").exists()
    # iter_events reads ACROSS rotated files chronologically; nothing lost from the
    # kept generations, and the newest event is present.
    ids = [e["update_id"] for e in iter_events(path)]
    assert ids == sorted(ids)  # chronological across rotation
    assert ids[-1] == 199


def test_emit_never_raises_on_bad_field(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    # a non-serializable field → emit returns None, does NOT raise
    assert log.emit("error", where="x", message=object()) is None


def test_iter_missing_file_is_empty(tmp_path: Path) -> None:
    assert list(iter_events(tmp_path / "nope.jsonl")) == []
