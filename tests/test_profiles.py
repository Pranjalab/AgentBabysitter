"""Profile discovery + on-disk format parsing (mirrors abs.sh formats)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from absd.profiles import Profile, default_tg_dir, discover
from tests.conftest import write_profile


def test_discover_default_and_named(abs_home: Path) -> None:
    write_profile(abs_home, "default", allow_ids=[42])
    write_profile(abs_home, "work", allow_ids=[7])
    profs = discover(abs_home, home=abs_home / "home")
    assert [p.name for p in profs] == ["default", "work"]  # sorted


def test_discover_ignores_dirs_without_rc(abs_home: Path) -> None:
    write_profile(abs_home, "real")
    (abs_home / "profiles" / "junk").mkdir(parents=True)
    assert [p.name for p in discover(abs_home, home=abs_home)] == ["real"]


def test_discover_empty(abs_home: Path) -> None:
    assert discover(abs_home, home=abs_home) == []


def test_token_parsing(abs_home: Path) -> None:
    write_profile(abs_home, "default", token="999:ABCDEF")
    p = discover(abs_home, home=abs_home)[0]
    assert p.load_token() == "999:ABCDEF"


def test_token_strips_quotes_and_ws(abs_home: Path) -> None:
    write_profile(abs_home, "default")
    p = discover(abs_home, home=abs_home)[0]
    # Overwrite .env with a quoted/whitespaced value like a hand-edited file.
    p.env_path.write_text('TELEGRAM_BOT_TOKEN="  777:XYZ  "\n')
    assert p.load_token() == "777:XYZ"


def test_missing_token_is_none(abs_home: Path) -> None:
    prof_dir = abs_home / "profiles" / "default"
    prof_dir.mkdir(parents=True)
    (prof_dir / "rc.json").write_text(json.dumps({"tg_dir": str(abs_home / "tg")}))
    (abs_home / "tg").mkdir()
    p = discover(abs_home, home=abs_home)[0]
    assert p.load_token() is None


def test_allowlist_string_compare(abs_home: Path) -> None:
    write_profile(abs_home, "default", allow_ids=[42, 100])
    p = discover(abs_home, home=abs_home)[0]
    assert p.allowlist() == ["42", "100"]
    # Telegram delivers from.id as an int — must still match the string list.
    assert p.is_allowed(42) is True
    assert p.is_allowed("42") is True
    assert p.is_allowed(999) is False


def test_dm_policy_and_off(abs_home: Path) -> None:
    write_profile(abs_home, "default", dm_policy="disabled")
    p = discover(abs_home, home=abs_home)[0]
    assert p.dm_policy() == "disabled"
    assert p.is_off() is True
    assert p.should_poll() is False
    assert p.yield_reason() == "inbound off"


def test_blocked(abs_home: Path) -> None:
    write_profile(abs_home, "default", blocked=True)
    p = discover(abs_home, home=abs_home)[0]
    assert p.is_blocked() is True
    assert p.should_poll() is False
    assert p.yield_reason() == "blocked"


def test_live_session_detection(abs_home: Path) -> None:
    # Our own PID is definitely alive.
    write_profile(abs_home, "default", session_pid=os.getpid())
    p = discover(abs_home, home=abs_home)[0]
    assert p.live_session_pid() == os.getpid()
    assert p.has_live_session() is True
    assert p.should_poll() is False
    assert p.yield_reason() == "session live"


def test_dead_session_pid_ignored(abs_home: Path) -> None:
    # A PID that is essentially certain not to exist.
    write_profile(abs_home, "default", session_pid=2_000_000_000)
    p = discover(abs_home, home=abs_home)[0]
    assert p.live_session_pid() is None
    assert p.has_live_session() is False
    assert p.should_poll() is True


def test_should_poll_happy_path(abs_home: Path) -> None:
    write_profile(abs_home, "default")
    p = discover(abs_home, home=abs_home)[0]
    assert p.should_poll() is True
    assert p.yield_reason() is None


def test_default_tg_dir_fallback() -> None:
    home = Path("/home/tester")
    assert default_tg_dir("default", home) == home / ".claude/channels/telegram"
    assert default_tg_dir("work", home) == home / ".claude/channels/telegram-work"


def test_tg_dir_from_rc_wins(abs_home: Path) -> None:
    custom = abs_home / "custom_tg"
    write_profile(abs_home, "default", tg_dir=custom)
    p = Profile.load("default", abs_home, home=Path("/home/tester"))
    assert p.tg_dir == custom  # rc.json .tg_dir, not the default fallback


# ---- kill-ladder writes (D11): set_off / set_blocked -------------------------


def test_set_off_writes_disabled_preserves_keys(abs_home: Path) -> None:
    import stat as _stat

    write_profile(abs_home, "default", allow_ids=[42])
    prof = discover(abs_home, home=abs_home)[0]
    prof.set_off()
    access = json.loads(prof.access_path.read_text())
    assert access["dmPolicy"] == "disabled"
    assert access["allowFrom"] == ["42"]  # preserved
    assert access["ackReaction"] == "👀"  # preserved
    assert _stat.S_IMODE(prof.access_path.stat().st_mode) == 0o600
    assert prof.is_off() is True


def test_set_blocked_writes_true_preserves_keys(abs_home: Path) -> None:
    import stat as _stat

    write_profile(abs_home, "default", allow_ids=[42])
    prof = discover(abs_home, home=abs_home)[0]
    prof.set_blocked()
    rc = json.loads(prof.rc_path.read_text())
    assert rc["blocked"] is True
    assert rc["bot"] == "default_bot"  # preserved
    assert rc["tg_dir"]  # preserved
    assert _stat.S_IMODE(prof.rc_path.stat().st_mode) == 0o600
    assert prof.is_blocked() is True
