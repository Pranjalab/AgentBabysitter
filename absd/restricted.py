"""Restricted-assistant profile helpers (Stage 3) — ``python -m absd.restricted``.

The provisioning + login flows live in ``abs.sh`` (interactive terminal bits: token
entry, PIN pairing, ``docker exec`` login). This module is the small pure/read piece
``abs.sh`` shells into: enumerate restricted profiles for ``abs restricted list``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .profiles import discover


def _abs_home(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser()
    env = os.environ.get("ABS_HOME")
    return Path(env).expanduser() if env else (Path.home() / ".abs")


def list_restricted(abs_home: Path, home: Path | None = None) -> list[dict]:
    """Every restricted profile as ``{name, sandbox, model, paused, bot}`` dicts."""
    out: list[dict] = []
    for p in discover(abs_home, home=home):
        if not p.is_restricted():
            continue
        out.append(
            {
                "name": p.name,
                "sandbox": p.sandbox_name() or "",
                "model": p.model() or "",
                "paused": p.is_paused(),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m absd.restricted")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="list restricted-assistant profiles")
    p_list.add_argument("--abs-home", default=None)
    args = parser.parse_args(argv)

    if args.cmd == "list":
        rows = list_restricted(_abs_home(args.abs_home))
        if not rows:
            print("No restricted assistants. Create one: abs restricted create <name>")
            return 0
        for r in rows:
            flag = " (paused)" if r["paused"] else ""
            print(f"{r['name']}\tsandbox={r['sandbox']}\tmodel={r['model']}{flag}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
