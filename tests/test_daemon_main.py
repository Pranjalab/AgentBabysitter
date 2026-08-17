"""End-to-end --once smoke test for `python -m absd` (against fakes only)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from absd.__main__ import _amain, parse_args
from absd.config import DaemonConfig, save
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram


async def test_once_pools_two_messages_end_to_end(
    abs_home: Path, fake: FakeTelegram, monkeypatch
) -> None:
    write_profile(abs_home, "default", allow_ids=[42])
    # Point every client at the fake (the documented test-only seam) + a temp HOME.
    monkeypatch.setenv("ABS_TELEGRAM_BASE_URL", fake.base_url)
    monkeypatch.setenv("HOME", str(abs_home / "home"))

    fake.queue_message("task one", from_id=42)
    fake.queue_message("task two", from_id=42)

    args = parse_args(["--abs-home", str(abs_home), "--once"])
    rc = await _amain(args)
    assert rc == 0

    pool_file = abs_home / "profiles" / "default" / "pool.jsonl"
    lines = pool_file.read_text().strip().splitlines()
    assert len(lines) == 2
    texts = [json.loads(x)["text"] for x in lines]
    assert texts == ["task one", "task two"]

    # Two acks sent, offset committed to the daemon state dir.
    assert len(fake.sent_messages) == 2
    offset_file = abs_home / "daemon" / "poller-default.json"
    assert offset_file.exists()
    assert json.loads(offset_file.read_text())["offset"] >= 1


async def test_once_invalid_config_returns_2(
    abs_home: Path, fake: FakeTelegram, monkeypatch
) -> None:
    write_profile(abs_home, "default", allow_ids=[42])
    (abs_home / "daemon" / "config.json").write_text(json.dumps({"engine": "bogus"}))
    monkeypatch.setenv("ABS_TELEGRAM_BASE_URL", fake.base_url)
    args = parse_args(["--abs-home", str(abs_home), "--once"])
    assert await _amain(args) == 2


async def test_once_no_profiles_is_clean(
    abs_home: Path, fake: FakeTelegram, monkeypatch
) -> None:
    monkeypatch.setenv("ABS_TELEGRAM_BASE_URL", fake.base_url)
    args = parse_args(["--abs-home", str(abs_home), "--once"])
    assert await _amain(args) == 0


async def test_once_respects_valid_config(
    abs_home: Path, fake: FakeTelegram, monkeypatch
) -> None:
    write_profile(abs_home, "default", allow_ids=[42])
    save(abs_home / "daemon" / "config.json", DaemonConfig(engine="tmux", poll_timeout_s=0))
    monkeypatch.setenv("ABS_TELEGRAM_BASE_URL", fake.base_url)
    monkeypatch.setenv("HOME", str(abs_home / "home"))
    fake.queue_message("hello", from_id=42)
    args = parse_args(["--abs-home", str(abs_home), "--once"])
    assert await _amain(args) == 0
    assert len(fake.sent_messages) == 1
