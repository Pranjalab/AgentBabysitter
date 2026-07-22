"""Daemon entry point: ``python -m absd``.

This is the process systemd (a user unit) will launch in Phase 1. For now it is
a stub that refuses to run so that no half-built daemon ever starts polling a
real bot token. The real entry point (Step 1.3+) will:

  1. load ``~/.abs/daemon/config.json`` (see ``absd.config.DaemonConfig``),
  2. enumerate configured profiles under ``~/.abs/profiles/``,
  3. start one asyncio task per profile running the 4.1 state machine,
  4. install signal handlers for clean shutdown.

Kept intentionally logic-free (PLAN.md 4.4): the entry point only wires pieces
together; all behavior lives in unit-testable pure functions and the state
machine.
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Stub entry point. Prints a not-implemented notice and exits non-zero.

    Mirrors the ``abs daemon`` bash stub so the Python and shell surfaces agree
    while the daemon is unbuilt.
    """
    _ = argv if argv is not None else sys.argv[1:]
    print("absd: not implemented yet — see PLAN.md", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess, not pytest
    raise SystemExit(main())
