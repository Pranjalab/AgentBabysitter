"""Scaffolding sanity: the absd package imports and is shaped right.

Locks the surface the daemon and later steps build on. The Step 0.1 stubs are
now implemented (Step 1.3); these tests assert the real behavior with no network
(the entry-point smoke uses a temp ABS_HOME with no profiles, so no client is
ever built and nothing reaches Telegram).
"""

from __future__ import annotations

from pathlib import Path

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


def test_main_once_no_profiles_returns_zero(tmp_path: Path) -> None:
    """The real entry point runs cleanly with --once against an empty temp home.

    No profiles ⇒ no Telegram client is built ⇒ no network. Proves the wiring
    (arg parse → config load → discover → run) holds together and exits 0.
    """
    from absd.__main__ import main

    assert main(["--abs-home", str(tmp_path / "abs"), "--once"]) == 0


def test_modules_are_implemented(tmp_path: Path) -> None:
    """The former stubs now have real behavior (Step 1.3)."""
    from absd.config import DaemonConfig, load
    from absd.pool import Pool, PooledMessage
    from absd.telegram import TelegramClient

    # config.load: missing file → defaults (no crash).
    assert load(tmp_path / "config.json") == DaemonConfig()

    # pool: append/read round-trips.
    pool = Pool(tmp_path / "pool.jsonl")
    assert pool.append(
        PooledMessage(update_id=1, from_id=2, text="hi", received_at="1970-01-01T00:00:00Z")
    ) == 1
    assert pool.read_all()[0].text == "hi"

    # Telegram client: token is private and never surfaced via repr (5.5).
    client = TelegramClient(token="x", base_url="http://127.0.0.1:0")
    assert "token" not in repr(client) or "redacted" in repr(client)
    assert not hasattr(client, "token")  # not a public attribute anymore
