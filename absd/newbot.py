"""Pure helpers for ``abs start new-bot`` provisioning (Stage 2 of the matrix).

Two decisions in the new-bot flow are pure functions of on-disk state, so they
live here (testable without a bash TTY, network, or real Telegram):

  - :func:`derive_profile_name` — turn a bot's ``@username`` into a profile-dir
    name that ``use_profile`` will accept (``^[A-Za-z0-9_-]+$``).
  - :func:`relay_target` — pick which *existing, already-trusted* profile's bot
    should relay the fresh pairing PIN to the operator's phone, or ``None`` when
    there is no such profile (then the CLI falls back to terminal-only display).

The PIN relay is a *convenience*, not a trust anchor: the PIN travels only to a
chat that is already paired + allowlisted on an existing bot, it is short-lived
(``PAIR_TIMEOUT``) and single-use, and token *entry* stays terminal-only. See
``docs/v3/critique/newbot.md`` for the full argument.

The CLI (``python -m absd.newbot ...``) is what ``abs.sh`` calls. It prints a
profile name (or nothing) to stdout — never a token. ``abs.sh`` reads the chosen
profile's token/chat itself, through its existing ``load_token`` path, so a
credential never crosses this process boundary.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .profiles import Profile, discover

# use_profile's jail (abs.sh ~197): a profile dir name is [A-Za-z0-9_-]+.
_KEEP_RE = re.compile(r"[^A-Za-z0-9_-]+")

# Longest profile name we'll derive. Telegram usernames cap at 32; this is slack.
_MAX_NAME = 64


def derive_profile_name(username: str) -> str | None:
    """Sanitize a Telegram bot ``@username`` into a valid profile name.

    Telegram usernames are ``[A-Za-z0-9_]{5,32}`` (and always end in ``bot``), so
    the sanitize is nearly a no-op — but a leading ``@``, or any surprise char,
    is stripped so the result always satisfies ``use_profile``'s validator. Case
    is preserved (profile names are case-sensitive on disk; ``@MyBot`` →
    ``MyBot``). Returns ``None`` if nothing usable survives (caller then prompts
    for a short name).
    """
    if not username:
        return None
    cleaned = _KEEP_RE.sub("", username.strip().lstrip("@"))
    cleaned = cleaned[:_MAX_NAME]
    return cleaned or None


def _usable_relay(p: Profile) -> bool:
    """True when profile ``p`` can relay a PIN: it has a bot token AND a paired
    chat to send to. Both are required — a token with no chat has nowhere to
    deliver, a chat with no token has no bot to send with."""
    return bool(p.load_token()) and p.chat_id() is not None


def relay_target(abs_home: Path, home: Path | None = None) -> str | None:
    """Name of the existing profile whose bot should relay the pairing PIN.

    Preference: the ``default`` profile if it is usable (it is the operator's
    primary, trusted channel); otherwise the first usable profile in discovery
    order (sorted by name, deterministic); otherwise ``None`` — no trusted bot
    exists yet, so the CLI shows the PIN in the terminal only.

    A brand-new profile being provisioned is, by construction, not yet on disk
    when this runs, so it can never select itself.
    """
    profiles = discover(abs_home, home=home)
    for p in profiles:
        if p.name == "default" and _usable_relay(p):
            return "default"
    for p in profiles:
        if _usable_relay(p):
            return p.name
    return None


def _abs_home(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("ABS_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".abs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="absd.newbot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_derive = sub.add_parser("derive-name", help="sanitize a @username to a profile name")
    p_derive.add_argument("username")

    p_relay = sub.add_parser("relay-target", help="name the PIN-relay profile, if any")
    p_relay.add_argument("--abs-home", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "derive-name":
        name = derive_profile_name(args.username)
        if name is None:
            return 1
        print(name)
        return 0

    if args.cmd == "relay-target":
        name = relay_target(_abs_home(args.abs_home))
        if name is None:
            return 1
        print(name)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
