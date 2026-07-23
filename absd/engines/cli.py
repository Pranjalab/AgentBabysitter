"""``python -m absd.engines.cli`` — the thin adapter the bash CLI shells into.

``abs sessions`` and ``abs attach`` in ``abs.sh`` are shims that ``exec`` this
module (PLAN.md Step 1.1). Keeping the logic here — not in bash — means the tmux
interaction, table formatting, and attach dispatch are all unit-testable Python
and portable (PLAN.md 4.4). stdlib only.

**Engine resolution (live-demo fix).** The daemon launches sessions via the engine
in ``~/.abs/daemon/config.json`` ("auto" → herdr when present). ``abs attach`` /
``abs sessions`` must look in the SAME place, or a herdr-launched session is
invisible to a tmux-defaulting CLI (exactly what happened in the demo). So:

  * the **default** engine follows ``config.json`` (``resolve_default_engine_name``),
    the same "auto → herdr else tmux" resolution the daemon uses;
  * with **no explicit engine**, ``sessions`` lists across BOTH available backends
    (each row tagged with its engine) and ``attach`` searches both and picks the
    one that actually owns the session — so nothing is ever invisible;
  * ``--engine`` / ``$ABS_ENGINE`` remain explicit overrides (tests/debug).

Subcommands:
  sessions [--json] [--engine E] [--socket NAME]
  attach [profile] [--engine E] [--socket NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict
from pathlib import Path

from absd import config as config_mod
from absd.engines import HerdrEngine, get_engine
from absd.engines.base import Engine, EngineError, SessionInfo
from absd.engines.tmux import DEFAULT_SOCKET, TmuxEngine

_ENGINE_CHOICES = ("tmux", "herdr", "auto")


# --------------------------------------------------------------------------- #
# engine resolution (config-aware) — the bug-2 fix
# --------------------------------------------------------------------------- #


def _abs_home() -> Path:
    return Path(os.environ.get("ABS_HOME") or (Path.home() / ".abs"))


def resolve_default_engine_name() -> str:
    """The engine name the DAEMON would use: ``$ABS_ENGINE`` if set (explicit
    override), else the ``engine`` field of ``~/.abs/daemon/config.json`` (default
    ``"auto"``). Returns one of ``auto``/``herdr``/``tmux`` — resolve a concrete
    backend with :func:`get_engine`, which maps ``auto`` identically to the daemon
    (herdr when present, else tmux)."""
    override = os.environ.get("ABS_ENGINE")
    if override:
        return override
    try:
        cfg = config_mod.load(_abs_home() / "daemon" / "config.json")
        return cfg.engine
    except Exception:
        return "auto"


def _build_engine(name: str, socket: str = DEFAULT_SOCKET) -> Engine:
    """Construct one engine by name. tmux honours ``--socket`` (tests + the
    isolated ``abs`` socket); herdr/auto take none."""
    if name == "tmux":
        return TmuxEngine(socket_name=socket)
    if name == "herdr":
        return HerdrEngine()
    return get_engine(name)  # "auto" (or anything get_engine accepts)


def _safe_available(engine: Engine) -> bool:
    try:
        return engine.available()
    except Exception:
        return False


def available_engines(socket: str = DEFAULT_SOCKET) -> list[Engine]:
    """Every backend whose binary is present, ordered so the config-preferred
    engine comes first (so a single found session / display favours it). Used when
    no explicit engine is given — nothing a running session lives in is invisible."""
    pref = resolve_default_engine_name()
    candidates = [TmuxEngine(socket_name=socket), HerdrEngine()]
    avail = [e for e in candidates if _safe_available(e)]

    def rank(e: Engine) -> int:
        if pref == e.name:
            return 0
        if pref == "auto" and e.name == "herdr":
            return 0
        return 1

    avail.sort(key=rank)
    return avail


# --------------------------------------------------------------------------- #
# rendering + resolution (pure)
# --------------------------------------------------------------------------- #


def format_sessions_table(sessions: list[SessionInfo]) -> str:
    """Render sessions as an aligned PROFILE / ALIVE / CWD table (single engine)."""
    if not sessions:
        return "No ABS sessions."
    rows = [(s.profile, "yes" if s.alive else "no", s.cwd or "-") for s in sessions]
    headers = ("PROFILE", "ALIVE", "CWD")
    w0 = max(len(headers[0]), *(len(r[0]) for r in rows))
    w1 = max(len(headers[1]), *(len(r[1]) for r in rows))
    lines = [f"{headers[0]:<{w0}}  {headers[1]:<{w1}}  {headers[2]}"]
    for prof, alive, cwd in rows:
        lines.append(f"{prof:<{w0}}  {alive:<{w1}}  {cwd}")
    return "\n".join(lines)


def format_engine_sessions_table(rows: list[tuple[str, SessionInfo]]) -> str:
    """Render sessions across engines with an ENGINE column (pure).

    ``rows`` is ``[(engine_name, SessionInfo), …]``. Nothing is ever hidden — a
    session in either backend shows up, tagged with which one owns it.
    """
    if not rows:
        return "No ABS sessions."
    data = [
        (name, s.profile, "yes" if s.alive else "no", s.cwd or "-") for name, s in rows
    ]
    headers = ("ENGINE", "PROFILE", "ALIVE", "CWD")
    w0 = max(len(headers[0]), *(len(r[0]) for r in data))
    w1 = max(len(headers[1]), *(len(r[1]) for r in data))
    w2 = max(len(headers[2]), *(len(r[2]) for r in data))
    lines = [f"{headers[0]:<{w0}}  {headers[1]:<{w1}}  {headers[2]:<{w2}}  {headers[3]}"]
    for eng, prof, alive, cwd in data:
        lines.append(f"{eng:<{w0}}  {prof:<{w1}}  {alive:<{w2}}  {cwd}")
    return "\n".join(lines)


def resolve_attach_target(
    profile: str | None, live: list[tuple[str, str]]
) -> tuple[str | None, str]:
    """Pick which engine to attach (pure). ``live`` is ``[(engine_name, profile), …]``
    of ALIVE sessions across all searched engines.

    Returns ``(engine_name, profile)`` on success, or ``(None, error_message)`` —
    the caller prints the message to stderr and exits 1.
    """
    if profile is None:
        if not live:
            return None, (
                "No live ABS sessions. Start one with ABS START from Telegram, "
                "or run `abs` at the terminal."
            )
        if len(live) > 1:
            listed = ", ".join(f"{p} ({e})" for e, p in live)
            return None, (
                f"Multiple live sessions ({listed}). "
                f"Specify one: abs attach <profile>"
            )
        eng, prof = live[0]
        return eng, prof
    owners = [(e, p) for e, p in live if p == profile]
    if not owners:
        return None, (
            f"No live session for profile '{profile}'. Start one with ABS START "
            f"from Telegram, or run `abs --profile {profile}` at the terminal."
        )
    if len(owners) > 1:
        engs = ", ".join(e for e, _ in owners)
        return None, (
            f"Session '{profile}' exists in multiple engines ({engs}). "
            f"Specify one: abs attach {profile} --engine <name>"
        )
    eng, prof = owners[0]
    return eng, prof


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def _engines_for(explicit: str | None, socket: str) -> list[Engine]:
    """The engines an operation should consider: just the explicit one, else all
    available backends (config-preferred first)."""
    if explicit is not None:
        return [_build_engine(explicit, socket)]
    return available_engines(socket)


def _collect_rows(engines: list[Engine]) -> list[tuple[str, SessionInfo]]:
    rows: list[tuple[str, SessionInfo]] = []
    for eng in engines:
        try:
            for s in eng.list_sessions():
                rows.append((eng.name, s))
        except EngineError:
            continue
    return rows


def _cmd_sessions(explicit: str | None, socket: str, as_json: bool) -> int:
    engines = _engines_for(explicit, socket)
    rows = _collect_rows(engines)
    if as_json:
        print(json.dumps([{**asdict(s), "engine": name} for name, s in rows]))
    else:
        print(format_engine_sessions_table(rows))
    return 0


def _cmd_attach(profile: str | None, explicit: str | None, socket: str) -> int:
    engines = _engines_for(explicit, socket)
    by_name = {e.name: e for e in engines}
    live = [(name, s.profile) for name, s in _collect_rows(engines) if s.alive]

    engine_name, result = resolve_attach_target(profile, live)
    if engine_name is None:
        print(result, file=sys.stderr)
        return 1

    engine = by_name[engine_name]
    target = result
    if not engine.is_alive(target):  # raced with teardown between list and attach
        print(f"No live session for '{target}' anymore.", file=sys.stderr)
        return 1
    argv = engine.attach_argv(target)
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:  # the multiplexer vanished between listing and exec
        print(f"Could not attach ({shlex.join(argv)}): {exc}", file=sys.stderr)
        return 1
    return 0  # unreachable; keeps type-checkers happy


# --------------------------------------------------------------------------- #
# argparse + main
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m absd.engines.cli",
        description="ABS session-engine adapter (config-aware: follows the daemon).",
    )
    parser.add_argument(
        "--engine",
        choices=_ENGINE_CHOICES,
        default=None,
        help=(
            "session backend override. Default: follow the daemon "
            "(config.json / $ABS_ENGINE); with no override, sessions/attach search "
            "every available backend."
        ),
    )
    parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET,
        help=f"tmux socket name (default: {DEFAULT_SOCKET!r}); tmux engine only.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_sessions = sub.add_parser("sessions", help="list ABS engine sessions")
    p_sessions.add_argument(
        "--json", action="store_true", help="emit JSON instead of a table"
    )

    p_attach = sub.add_parser("attach", help="attach to a running session")
    p_attach.add_argument(
        "profile", nargs="?", default=None, help="profile to attach (optional)"
    )
    return parser


def _explicit_engine(args: argparse.Namespace) -> str | None:
    """An explicit engine override, or None to follow the config default and
    search all backends. ``--engine`` wins over ``$ABS_ENGINE``."""
    if args.engine is not None:
        return args.engine
    return os.environ.get("ABS_ENGINE")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    explicit = _explicit_engine(args)
    try:
        if args.command == "sessions":
            return _cmd_sessions(explicit, args.socket, as_json=args.json)
        if args.command == "attach":
            return _cmd_attach(args.profile, explicit, args.socket)
    except EngineError as exc:
        print(f"abs: engine error: {exc}", file=sys.stderr)
        return 1
    return 2  # unknown command; argparse should have caught it


if __name__ == "__main__":
    raise SystemExit(main())
