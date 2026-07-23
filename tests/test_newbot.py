"""Pure helpers behind ``abs start new-bot`` (:mod:`absd.newbot`).

Profile-name derivation and PIN-relay target selection are pure functions of a
username / on-disk profile state, tested here without a TTY, network, or real
Telegram. The CLI (``python -m absd.newbot``) is exercised too, since abs.sh
depends on its exit codes and single-line stdout.
"""

from __future__ import annotations

from pathlib import Path

from absd.newbot import derive_profile_name, main, relay_target
from tests.conftest import write_profile


# ---- derive_profile_name -----------------------------------------------------


def test_derive_plain_username() -> None:
    assert derive_profile_name("my_claude_bot") == "my_claude_bot"


def test_derive_strips_leading_at() -> None:
    assert derive_profile_name("@MyClaudeBot") == "MyClaudeBot"


def test_derive_preserves_case() -> None:
    # Profile dirs are case-sensitive on disk; don't fold.
    assert derive_profile_name("CamelBot") == "CamelBot"


def test_derive_strips_invalid_chars() -> None:
    # Anything outside [A-Za-z0-9_-] is dropped so use_profile accepts it.
    assert derive_profile_name("wei rd.bot!") == "weirdbot"


def test_derive_result_is_valid_profile_name() -> None:
    import re

    name = derive_profile_name("@Some_Weird.Bot-42")
    assert name is not None
    assert re.fullmatch(r"[A-Za-z0-9_-]+", name)


def test_derive_empty_returns_none() -> None:
    assert derive_profile_name("") is None
    assert derive_profile_name("@") is None
    assert derive_profile_name("...") is None


def test_derive_truncates_overlong() -> None:
    name = derive_profile_name("a" * 200)
    assert name is not None
    assert len(name) <= 64


# ---- relay_target ------------------------------------------------------------


def test_relay_prefers_default(abs_home: Path) -> None:
    write_profile(abs_home, "default", allow_ids=[42])
    write_profile(abs_home, "other", allow_ids=[7])
    assert relay_target(abs_home, home=abs_home) == "default"


def test_relay_falls_back_to_first_usable(abs_home: Path) -> None:
    # No default; the first (sorted) usable profile relays.
    write_profile(abs_home, "zeta", allow_ids=[7])
    write_profile(abs_home, "alpha", allow_ids=[9])
    assert relay_target(abs_home, home=abs_home) == "alpha"


def test_relay_none_when_no_profiles(abs_home: Path) -> None:
    assert relay_target(abs_home, home=abs_home) is None


def test_relay_skips_profile_without_token(abs_home: Path) -> None:
    # 'default' exists but its .env has no token → not usable → fall through.
    prof = abs_home / "profiles" / "default"
    prof.mkdir(parents=True)
    tg = abs_home / "tgd"
    tg.mkdir(parents=True)
    (prof / "rc.json").write_text(
        '{"tg_dir":"%s","chat_id":"42"}' % tg, encoding="utf-8"
    )
    (tg / ".env").write_text("# no token\n", encoding="utf-8")
    write_profile(abs_home, "usable", allow_ids=[7])
    assert relay_target(abs_home, home=abs_home) == "usable"


def test_relay_skips_profile_without_chat(abs_home: Path) -> None:
    # A token but no chat_id has nowhere to deliver the PIN.
    prof = abs_home / "profiles" / "default"
    prof.mkdir(parents=True)
    tg = abs_home / "tgd"
    tg.mkdir(parents=True)
    (prof / "rc.json").write_text('{"tg_dir":"%s"}' % tg, encoding="utf-8")
    (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:abc\n", encoding="utf-8")
    write_profile(abs_home, "usable", allow_ids=[7])
    assert relay_target(abs_home, home=abs_home) == "usable"


# ---- CLI ---------------------------------------------------------------------


def test_cli_derive_name(capsys) -> None:
    rc = main(["derive-name", "@MyBot"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "MyBot"


def test_cli_derive_name_empty_is_error(capsys) -> None:
    rc = main(["derive-name", "@"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == ""


def test_cli_relay_target(abs_home: Path, capsys) -> None:
    write_profile(abs_home, "default", allow_ids=[42])
    rc = main(["relay-target", "--abs-home", str(abs_home)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "default"


def test_cli_relay_target_none_is_error(abs_home: Path, capsys) -> None:
    rc = main(["relay-target", "--abs-home", str(abs_home)])
    assert rc == 1
    assert capsys.readouterr().out.strip() == ""
