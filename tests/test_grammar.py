"""Command grammar incl. the /abs_* slash aliases (Step 2.2). Pure + one e2e."""

from __future__ import annotations

from pathlib import Path

import pytest

from absd.daemon import (
    canonical_command,
    is_pool_cmd,
    is_start,
    is_status,
    normalize_command,
)
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import make_poller


@pytest.mark.parametrize(
    "text,canon",
    [
        ("/abs_start", "ABS START"),
        ("/ABS_START", "ABS START"),
        ("/abs_start@mybot", "ABS START"),
        ("  /abs_status  ", "ABS STATUS"),
        ("/abs_pool@Some_Bot", "ABS POOL"),
        ("ABS START", "ABS START"),  # plain phrase unchanged
        ("/abs_start extra", "/ABS_START EXTRA"),  # 2 tokens → not an alias
        ("/abs_other", "/ABS_OTHER"),  # unknown slash cmd
        ("hello", "HELLO"),
    ],
)
def test_canonical_command(text: str, canon: str) -> None:
    assert canonical_command(text) == canon


def test_slash_aliases_match_commands() -> None:
    assert is_start("/abs_start") and is_start("/abs_start@bot")
    assert is_status("/abs_status")
    assert is_pool_cmd("/abs_pool")
    # not aliases
    assert not is_start("/abs_start now")
    assert not is_status("/abs_stat")


def test_normalize_command_unchanged_for_near_miss() -> None:
    # normalize_command itself is untouched (slash resolution is canonical_command)
    assert normalize_command("ABS  STATUS") == "ABS  STATUS"  # double space preserved


async def test_slash_start_launches_flow(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"
    proj.mkdir()
    from absd.registry import Registry

    Registry(abs_home / "daemon" / "registry.json").add(proj)
    from tests.test_flow_e2e import FakeEngine

    poller = make_poller(abs_home, client_factory, engine=FakeEngine())

    fake.queue_message("/abs_start@mybot", from_id=42)
    await poller.poll_once()
    assert poller.flow is not None and poller.flow.step == "project"


async def test_slash_status_replies(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_profile(abs_home, allow_ids=[42])
    from tests.test_flow_e2e import FakeEngine

    poller = make_poller(abs_home, client_factory, engine=FakeEngine())
    fake.queue_message("/abs_status", from_id=42)
    await poller.poll_once()
    assert "ABS STATUS" in fake.sent_messages[-1]["text"]
    assert poller.pool.read_all() == []  # answered, not pooled
