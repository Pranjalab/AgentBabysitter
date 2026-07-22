"""Scaffolding sanity: the Step 0.1 package skeleton imports and is shaped right.

These lock the surface the daemon (Step 1.3+) and later steps build on. No I/O,
no network. Stubs are expected to raise ``NotImplementedError`` — that is the
contract for this step, not a bug.
"""

from __future__ import annotations

import pytest

from absd.engines import Engine, SessionInfo


class _DummyEngine:
    """A minimal object that structurally satisfies the Engine protocol."""

    name = "dummy"

    def available(self) -> bool:
        return False

    def create_session(self, profile, cwd, command, env) -> None:  # noqa: ANN001
        ...

    def is_alive(self, profile) -> bool:  # noqa: ANN001
        return False

    def kill(self, profile) -> None:  # noqa: ANN001
        ...

    def attach_command(self, profile) -> str:  # noqa: ANN001
        return ""

    def list_sessions(self) -> list[SessionInfo]:
        return []


def test_engine_is_runtime_checkable() -> None:
    """A conforming object passes the runtime-checkable Engine protocol."""
    assert isinstance(_DummyEngine(), Engine)


def test_session_info_round_trip() -> None:
    """SessionInfo carries the documented fields."""
    info = SessionInfo(profile="work", name="abs-work", alive=True, pid=1234)
    assert info.profile == "work"
    assert info.name == "abs-work"
    assert info.alive is True
    assert info.pid == 1234
    assert info.cwd is None


def test_main_stub_reports_not_implemented() -> None:
    """The daemon entry stub exits non-zero (nothing starts polling yet)."""
    from absd.__main__ import main

    assert main([]) == 1


def test_stubs_raise_not_implemented() -> None:
    """Placeholder modules expose the surface but no logic yet."""
    from pathlib import Path

    from absd.config import load
    from absd.pool import Pool, PooledMessage
    from absd.telegram import TelegramClient

    with pytest.raises(NotImplementedError):
        load(Path("/nonexistent/config.json"))

    pool = Pool(Path("/nonexistent/pool.jsonl"))
    with pytest.raises(NotImplementedError):
        pool.read_all()
    with pytest.raises(NotImplementedError):
        pool.append(
            PooledMessage(update_id=1, from_id=2, text="hi", received_at="1970-01-01T00:00:00Z")
        )

    # The Telegram client stub constructs (token stored, not logged) but has no
    # implemented calls yet; it is driven against fake_telegram in later steps.
    client = TelegramClient(token="x", base_url="http://127.0.0.1:0")
    assert client.token == "x"
