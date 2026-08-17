"""Shared fixtures for the absd daemon suite.

Everything here builds a *temp* ABS_HOME with fake tokens and points clients at
the local FakeTelegram server — no test touches the real ``~/.abs``,
``~/.claude``, real tokens, or api.telegram.org (PLAN.md section 10 / the Step
1.3 safety rule).

``write_profile`` reproduces, byte-for-byte, the on-disk formats ``abs.sh``
writes (see ``absd.profiles`` docstrings for the source functions): ``rc.json``
(write_state), ``.env`` (save_token), ``access.json`` (write_access), and the
``session.pid`` convention (cmd_run).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest
import pytest_asyncio

from absd.telegram import TelegramClient
from tests.harness.fake_telegram import FakeTelegram


def write_profile(
    abs_home: Path,
    name: str = "default",
    *,
    token: str = "123456789:FAKE-TEST-TOKEN",
    allow_ids: list[int] | None = None,
    dm_policy: str = "allowlist",
    blocked: bool = False,
    session_pid: int | None = None,
    tg_dir: Path | None = None,
) -> Path:
    """Create a profile under ``abs_home`` mirroring abs.sh's on-disk formats.

    Returns the profile directory. ``tg_dir`` defaults to a temp dir under
    ``abs_home`` (NOT the real ~/.claude), recorded in rc.json's ``.tg_dir`` so
    discovery uses it directly.
    """
    allow_ids = [42] if allow_ids is None else allow_ids
    profile_dir = abs_home / "profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    if tg_dir is None:
        tg_dir = abs_home / "tg" / name
    tg_dir.mkdir(parents=True, exist_ok=True)

    # rc.json — write_state shape, plus .blocked when BLOCK is set.
    rc: dict[str, Any] = {
        "user_id": str(allow_ids[0]) if allow_ids else "0",
        "chat_id": str(allow_ids[0]) if allow_ids else "0",
        "bot": f"{name}_bot",
        "quiet": False,
        "default_quiet": False,
        "tg_dir": str(tg_dir),
    }
    if blocked:
        rc["blocked"] = True
    _write(profile_dir / "rc.json", json.dumps(rc))

    # .env — save_token: a single unquoted TELEGRAM_BOT_TOKEN= line.
    _write(tg_dir / ".env", f"TELEGRAM_BOT_TOKEN={token}\n")

    # access.json — write_access shape. allowFrom holds STRING ids.
    access = {
        "dmPolicy": dm_policy,
        "allowFrom": [str(i) for i in allow_ids],
        "groups": {},
        "ackReaction": "👀",
        "replyToMode": "first",
        "textChunkLimit": 4096,
        "chunkMode": "newline",
    }
    _write(tg_dir / "access.json", json.dumps(access))

    if session_pid is not None:
        _write(profile_dir / "session.pid", f"{session_pid}\n")

    return profile_dir


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


@pytest.fixture
def abs_home(tmp_path: Path) -> Path:
    """A fresh temp ABS_HOME root with the daemon dir created."""
    home = tmp_path / "abs"
    (home / "daemon").mkdir(parents=True, exist_ok=True)
    return home


@pytest_asyncio.fixture
async def fake() -> Iterator[FakeTelegram]:
    """A started FakeTelegram bound to 127.0.0.1:<ephemeral>."""
    server = FakeTelegram()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest_asyncio.fixture
async def client_factory(fake: FakeTelegram) -> Iterator[Any]:
    """Factory yielding TelegramClients pointed at the fake; all closed on teardown."""
    made: list[TelegramClient] = []

    def _make(token: str = "TEST") -> TelegramClient:
        c = TelegramClient(token, base_url=fake.base_url)
        made.append(c)
        return c

    try:
        yield _make
    finally:
        for c in made:
            await c.close()
