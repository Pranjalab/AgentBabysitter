"""``python -m absd.engines.cli`` — the thin adapter the bash CLI shells into.

``abs sessions`` and ``abs attach`` in ``abs.sh`` are shims that ``exec`` this
module (PLAN.md Step 1.1). Keeping the logic here — not in bash — means the tmux
interaction, table formatting, and attach dispatch are all unit-testable Python
and portable (PLAN.md 4.4). stdlib only.

Subcommands:
  sessions [--json] [--socket NAME]
      List ABS engine sessions. Human table (PROFILE / ALIVE / CWD) by default;
      ``--json`` emits a JSON array for scripting.
  attach [profile] [--socket NAME]
      Attach to a running session. With a profile: attach that one. Without:
      attach the sole live session, or explain if there are zero or several.
      On success this REPLACES the process with tmux (via execvp) so the user
      lands directly in the session; with no live session it prints a helpful
      note and exits 1.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict

from absd.engines.base import SessionInfo
from absd.engines.tmux import DEFAULT_SOCKET, EngineError, TmuxEngine


def format_sessions_table(sessions: list[SessionInfo]) -> str:
    """Render sessions as an aligned PROFILE / ALIVE / CWD table (pure function).

    Empty list -> a single friendly line. Column widths adapt to content.
    """
    if not sessions:
        return "No ABS sessions."
    rows = [
        (s.profile, "yes" if s.alive else "no", s.cwd or "-") for s in sessions
    ]
    headers = ("PROFILE", "ALIVE", "CWD")
    w0 = max(len(headers[0]), *(len(r[0]) for r in rows))
    w1 = max(len(headers[1]), *(len(r[1]) for r in rows))
    lines = [f"{headers[0]:<{w0}}  {headers[1]:<{w1}}  {headers[2]}"]
    for prof, alive, cwd in rows:
        lines.append(f"{prof:<{w0}}  {alive:<{w1}}  {cwd}")
    return "\n".join(lines)


def _cmd_sessions(engine: TmuxEngine, as_json: bool) -> int:
    sessions = engine.list_sessions()
    if as_json:
        print(json.dumps([asdict(s) for s in sessions]))
    else:
        print(format_sessions_table(sessions))
    return 0


def _cmd_attach(engine: TmuxEngine, profile: str | None) -> int:
    """Resolve the target session and exec into tmux. Returns an exit code only
    on the no-attach paths; on success it never returns (execvp replaces us)."""
    live = [s for s in engine.list_sessions() if s.alive]

    if profile is None:
        if not live:
            print(
                "No live ABS sessions. Start one with ABS START from Telegram, "
                "or run `abs` at the terminal.",
                file=sys.stderr,
            )
            return 1
        if len(live) > 1:
            names = ", ".join(s.profile for s in live)
            print(
                f"Multiple live sessions ({names}). "
                f"Specify one: abs attach <profile>",
                file=sys.stderr,
            )
            return 1
        profile = live[0].profile
    else:
        if not engine.is_alive(profile):
            print(
                f"No live session for profile '{profile}'. Start one with "
                f"ABS START from Telegram, or run `abs --profile {profile}` at "
                f"the terminal.",
                file=sys.stderr,
            )
            return 1

    argv = engine.attach_argv(profile)
    # Replace this process with tmux so the user lands in the session directly.
    # abs.sh `exec`s us, so execvp here means bash -> python -> tmux, seamless.
    try:
        os.execvp(argv[0], argv)
    except OSError as exc:  # tmux vanished between listing and exec
        print(f"Could not attach ({shlex.join(argv)}): {exc}", file=sys.stderr)
        return 1
    return 0  # unreachable; keeps type-checkers happy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m absd.engines.cli",
        description="ABS session-engine adapter (tmux backend).",
    )
    parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET,
        help=f"tmux socket name (default: {DEFAULT_SOCKET!r}); tests override it.",
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = TmuxEngine(socket_name=args.socket)
    try:
        if args.command == "sessions":
            return _cmd_sessions(engine, as_json=args.json)
        if args.command == "attach":
            return _cmd_attach(engine, profile=args.profile)
    except EngineError as exc:
        print(f"abs: engine error: {exc}", file=sys.stderr)
        return 1
    return 2  # unknown command; argparse should have caught it


if __name__ == "__main__":
    raise SystemExit(main())
