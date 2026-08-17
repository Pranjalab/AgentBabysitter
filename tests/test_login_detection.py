"""Login detection (Step 1.6): pre-launch credential precheck + failed-start note."""

from __future__ import annotations

import pathlib
from pathlib import Path

from absd.daemon import (
    FAILED_START_MSG,
    LOGIN_MISSING_MSG,
    STATE_IDLE,
    STATE_RECLAIM,
)
from absd.events import END_FAILED_START, iter_events
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, _register, make_poller


async def _drive_to_handoff(poller, fake, proj_cb="as:p:0"):
    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query(proj_cb, from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()


async def test_missing_creds_blocks_launch(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    from absd.events import EventLog

    log = EventLog(abs_home / "daemon" / "events.jsonl")
    missing = tmp_path / "nope.json"  # does not exist
    poller = make_poller(abs_home, client_factory, engine=engine, events=log, creds_path=missing)

    await _drive_to_handoff(poller, fake)

    assert engine.created == []  # never launched
    assert poller.session_state == STATE_IDLE
    assert fake.sent_messages[-1]["text"] == LOGIN_MISSING_MSG
    # error{where:login_precheck} emitted
    errs = list(iter_events(abs_home / "daemon" / "events.jsonl", event="error"))
    assert any(e.get("where") == "login_precheck" for e in errs)


async def test_empty_creds_blocks_launch(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    empty = tmp_path / "empty.json"
    empty.write_text("")  # present but zero-length
    poller = make_poller(abs_home, client_factory, engine=engine, creds_path=empty)

    await _drive_to_handoff(poller, fake)
    assert engine.created == []
    assert fake.sent_messages[-1]["text"] == LOGIN_MISSING_MSG


async def test_present_creds_launches(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    creds = tmp_path / "creds.json"
    creds.write_text("{}")  # non-empty
    poller = make_poller(abs_home, client_factory, engine=engine, creds_path=creds)

    await _drive_to_handoff(poller, fake)
    assert len(engine.created) == 1  # launched


def test_credentials_never_opened_for_read(
    abs_home: Path, client_factory, tmp_path: Path, monkeypatch
) -> None:
    # PRESENCE + SIZE via stat ONLY — the file must never be opened/read.
    write_profile(abs_home, allow_ids=[42])
    creds = tmp_path / "creds.json"
    creds.write_text("super-secret-token")
    poller = make_poller(abs_home, client_factory, engine=FakeEngine(), creds_path=creds)

    orig_open = pathlib.Path.open
    orig_read_text = pathlib.Path.read_text
    orig_read_bytes = pathlib.Path.read_bytes

    def guard_open(self, *a, **k):
        assert str(self) != str(creds), "credentials file opened for read!"
        return orig_open(self, *a, **k)

    def guard_read_text(self, *a, **k):
        assert str(self) != str(creds), "credentials file read_text!"
        return orig_read_text(self, *a, **k)

    def guard_read_bytes(self, *a, **k):
        assert str(self) != str(creds), "credentials file read_bytes!"
        return orig_read_bytes(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "open", guard_open)
    monkeypatch.setattr(pathlib.Path, "read_text", guard_read_text)
    monkeypatch.setattr(pathlib.Path, "read_bytes", guard_read_bytes)

    assert poller._credentials_present() is True  # via stat, no read


async def test_failed_start_gets_login_note(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # A session that never came alive (failed_start) → the login-issue note.
    write_profile(abs_home, allow_ids=[42])
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    poller.session_state = STATE_RECLAIM
    poller._session_end_reason = END_FAILED_START
    poller._handoff_chat_id = 42

    async def rec(_d: float) -> None:
        pass

    await poller.reclaim(sleep=rec)
    assert any(m["text"] == FAILED_START_MSG for m in fake.sent_messages)
