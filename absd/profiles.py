"""Read-only discovery of ABS profiles from an ``ABS_HOME`` root.

The daemon must READ, in production, the exact on-disk state ``abs.sh`` writes —
it never writes these files itself (that stays the CLI's job; the daemon only
adds its own files under ``~/.abs/daemon/`` and the per-profile ``pool.jsonl``).
Every format below is mirrored from a specific ``abs.sh`` function; those
functions are the spec (PLAN.md 4.4). **Nothing here is exercised against the
real ``~/.abs`` in tests — fixtures reproduce these formats byte-for-byte
(PLAN.md section 10).**

Formats mirrored from ``abs.sh``
--------------------------------
``$ABS_HOME/profiles/<name>/rc.json``  (``write_state`` / ``state_set``)
    The per-profile state ("state.json" in PLAN.md terms; the file is
    ``rc.json``). A JSON object::

        {"user_id":"42","chat_id":"42","bot":"my_bot","quiet":false,
         "default_quiet":false,"tg_dir":"/home/u/.claude/channels/telegram"}

    The daemon reads two fields for D11:
      - ``.tg_dir``   — where this profile's telegram dir lives (``use_profile``).
      - ``.blocked``  — ``ABS BLOCK`` sets ``.blocked = true`` (``_hook_control``);
        it survives restart and lifts only via ``abs setup`` (``cmd_on`` refuses
        while blocked). ``true`` ⇒ the daemon must not poll this profile.

``$TG_DIR/.env``  (``save_token`` / ``load_token``, abs.sh ~447 / ~373)
    One line: ``TELEGRAM_BOT_TOKEN=<token>``. ``load_token`` strips surrounding
    quotes and whitespace and takes the first match; we mirror that exactly.

``$TG_DIR/access.json``  (``write_access`` / ``set_policy``, abs.sh ~544 / ~851)
    The telegram plugin's allowlist file (ABS only touches two keys)::

        {"dmPolicy":"allowlist","allowFrom":["42"],"groups":{},
         "ackReaction":"👀","replyToMode":"first","textChunkLimit":4096,
         "chunkMode":"newline"}

      - ``.allowFrom`` — allowlisted Telegram user ids, stored as **strings**
        (``write_access`` builds them with ``jq --arg``). Telegram delivers
        ``from.id`` as an integer, so :meth:`Profile.is_allowed` compares as
        strings. (D10 — the daemon enforces this itself.)
      - ``.dmPolicy``  — ``"allowlist"`` normally; ``ABS OFF`` / ``abs off`` set
        it to ``"disabled"`` (``set_policy disabled``), re-enabled only from the
        terminal (``cmd_on``). ``"disabled"`` ⇒ the daemon must not poll (D11).

``$TG_DIR`` resolution  (``default_tg_dir`` / ``use_profile``, abs.sh ~188 / ~197)
    Prefer ``rc.json``'s ``.tg_dir`` (always written by ``write_state``); else
    the default: profile ``default`` → ``$HOME/.claude/channels/telegram``;
    any other ``<name>`` → ``$HOME/.claude/channels/telegram-<name>``.

``$ABS_HOME/profiles/<name>/session.pid``  (``cmd_run``, abs.sh ~2485)
    A single decimal PID: the live Claude Code session's own PID (``exec``'d, so
    ``$$`` is the session). Present + ``kill -0`` alive ⇒ a session is live and
    the daemon must yield (PLAN.md 4.1). ``abs exit`` removes it.

Surprises worth flagging (see the 1.3 critique):
  - ``allowFrom`` holds **strings**, not ints — a naive ``in`` against an int
    ``from.id`` would silently reject every allowed user. Handled here.
  - ``OFF`` is not a field in ``rc.json`` at all — it lives in *access.json*'s
    ``dmPolicy``. ``BLOCK`` *is* in ``rc.json`` (``.blocked``). The two flags
    live in two different files; both are honored (:meth:`Profile.should_poll`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_TOKEN_RE = re.compile(r"^TELEGRAM_BOT_TOKEN=(.*)$")


def default_tg_dir(name: str, home: Path) -> Path:
    """Mirror ``abs.sh`` ``default_tg_dir``: the default profile keeps the
    plugin's own dir; a named profile gets a ``-<name>`` suffix."""
    if name == "default":
        return home / ".claude" / "channels" / "telegram"
    return home / ".claude" / "channels" / f"telegram-{name}"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class Profile:
    """A discovered profile: its paths and the state the daemon reads.

    Read-only snapshot taken at discovery time for the paths; the *dynamic*
    facts (token, allowlist, blocked/off, live pid) are re-read on demand so a
    long-running poller sees ``abs off``/``abs exit`` taking effect without a
    daemon restart (``set_policy`` says "no restart needed").
    """

    name: str
    abs_home: Path
    profile_dir: Path
    tg_dir: Path
    rc_path: Path

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(cls, name: str, abs_home: Path, home: Path) -> "Profile":
        """Build a profile view. ``home`` seeds the ``default_tg_dir`` fallback
        (kept explicit so tests never depend on the real ``$HOME``)."""
        abs_home = Path(abs_home)
        profile_dir = abs_home / "profiles" / name
        rc_path = profile_dir / "rc.json"
        rc = _read_json(rc_path) or {}
        tg_dir_str = rc.get("tg_dir")
        tg_dir = Path(tg_dir_str) if tg_dir_str else default_tg_dir(name, home)
        return cls(
            name=name,
            abs_home=abs_home,
            profile_dir=profile_dir,
            tg_dir=tg_dir,
            rc_path=rc_path,
        )

    # ---- derived paths ---------------------------------------------------

    @property
    def env_path(self) -> Path:
        return self.tg_dir / ".env"

    @property
    def access_path(self) -> Path:
        return self.tg_dir / "access.json"

    @property
    def session_pid_path(self) -> Path:
        return self.profile_dir / "session.pid"

    @property
    def pool_path(self) -> Path:
        return self.profile_dir / "pool.jsonl"

    # ---- dynamic state (re-read on demand) -------------------------------

    def load_token(self) -> str | None:
        """Read the bot token from ``.env`` (``load_token`` semantics): first
        ``TELEGRAM_BOT_TOKEN=`` line, surrounding quotes/whitespace stripped.
        Returns ``None`` if absent/empty. Never logged (PLAN.md 5.5)."""
        try:
            with self.env_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    m = _TOKEN_RE.match(line.strip())
                    if m:
                        # abs.sh: tr -d '"'\''[:space:]' — strip quotes + all ws.
                        tok = m.group(1).strip().strip("\"'").strip()
                        return tok or None
        except OSError:
            return None
        return None

    def allowlist(self) -> list[str]:
        """The ``allowFrom`` user-id list from ``access.json`` (strings)."""
        data = _read_json(self.access_path) or {}
        raw = data.get("allowFrom")
        if not isinstance(raw, list):
            return []
        return [str(x) for x in raw]

    def dm_policy(self) -> str:
        """The ``dmPolicy`` from ``access.json`` (default ``"allowlist"`` when
        unset, matching the plugin's own default before ABS writes the file)."""
        data = _read_json(self.access_path) or {}
        policy = data.get("dmPolicy")
        return str(policy) if isinstance(policy, str) else "allowlist"

    def is_allowed(self, from_id: int | str) -> bool:
        """True if ``from_id`` is in the allowlist (string compare — D10 / 5.1)."""
        return str(from_id) in self.allowlist()

    def is_blocked(self) -> bool:
        """True if ``ABS BLOCK`` set ``.blocked`` in ``rc.json`` (D11)."""
        rc = _read_json(self.rc_path) or {}
        return rc.get("blocked") is True

    def is_off(self) -> bool:
        """True if inbound is switched off (``dmPolicy == "disabled"``, D11)."""
        return self.dm_policy() == "disabled"

    def live_session_pid(self) -> int | None:
        """The live session PID if ``session.pid`` exists and the process is
        alive (``os.kill(pid, 0)``), else ``None`` (PLAN.md 4.1 yield check)."""
        try:
            raw = self.session_pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw or not raw.isdigit():
            return None
        pid = int(raw)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            # Exists but owned by another user: treat as alive (can't signal it,
            # but it is clearly a running process holding the pid).
            return pid
        except OSError:
            return None
        return pid

    def has_live_session(self) -> bool:
        return self.live_session_pid() is not None

    def should_poll(self) -> bool:
        """The IDLE_POLLING precondition (PLAN.md 4.1 + D11): poll only when no
        session is live, not blocked, and not switched off. Cheap enough to call
        before every poll cycle."""
        if self.has_live_session():
            return False
        if self.is_blocked():
            return False
        if self.is_off():
            return False
        return True

    def yield_reason(self) -> str | None:
        """Human-readable reason the poller is yielding, or ``None`` if it may
        poll. Used only for debug logging (never sent to Telegram)."""
        if self.has_live_session():
            return "session live"
        if self.is_blocked():
            return "blocked"
        if self.is_off():
            return "inbound off"
        return None


def discover(abs_home: Path, home: Path | None = None) -> list[Profile]:
    """Enumerate profiles under ``$ABS_HOME/profiles`` (those with an ``rc.json``,
    mirroring ``abs.sh`` ``list_profiles``). Sorted by name for determinism.

    ``home`` seeds the ``default_tg_dir`` fallback; defaults to ``Path.home()``
    but tests pass a temp dir so nothing touches the real ``$HOME``.
    """
    abs_home = Path(abs_home)
    home = Path(home) if home is not None else Path.home()
    profiles_dir = abs_home / "profiles"
    if not profiles_dir.is_dir():
        return []
    names = sorted(
        d.name
        for d in profiles_dir.iterdir()
        if d.is_dir() and (d / "rc.json").is_file()
    )
    return [Profile.load(n, abs_home, home) for n in names]
