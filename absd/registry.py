"""Project registry — ``~/.abs/daemon/registry.json`` (PLAN.md 4.3 / Step 1.5).

The daemon offers, in the ABS START flow, a keyboard of *registered projects*
plus the direct children of a configured *workspace root*. Registration and the
workspace-root setting are **terminal-only** (5.3 / D6): a compromised phone can
never name an arbitrary path, so the write surface here is reachable only from
``abs project …`` / ``abs config workspace-root …`` at the desk — never from a
Telegram update.

On-disk format (the stable spec a future port re-implements, PLAN.md 4.4) — a
JSON array, one object per registered project::

    [{"path": "/home/u/Projects/web", "label": "web",
      "added_at": "2026-07-23T10:00:00Z"}]

Discipline:
  - **stdlib-only** (``json``, ``pathlib``, ``dataclasses``); 0600 file under the
    0700 daemon dir like the rest of ``~/.abs`` (4.3 / 5.5).
  - **Paths are stored resolved and absolute.** ``add`` rejects a non-directory
    up front; the *daemon* additionally re-checks existence when it builds the
    keyboard, so a project deleted after registration simply drops out.
  - **Idempotent add / tolerant rm.** Re-adding a path updates nothing and does
    not duplicate; removing an unregistered path is a no-op that reports so.

This module is import-safe and has a thin ``python -m absd.registry`` CLI that
``abs.sh`` shells into (keeping the logic in Python, portable and testable, not
in bash — PLAN.md 4.4).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MODE = 0o600


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_abs_home() -> Path:
    return Path(os.environ.get("ABS_HOME") or (Path.home() / ".abs"))


@dataclass
class RegistryEntry:
    """One registered project. ``label`` defaults to the path's basename."""

    path: str
    label: str
    added_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "label": self.label, "added_at": self.added_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RegistryEntry":
        path = str(data["path"])
        label = str(data.get("label") or Path(path).name)
        return cls(path=path, label=label, added_at=str(data.get("added_at") or ""))


class Registry:
    """The registered-project list backed by ``registry.json``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # ---- io ----

    def read(self) -> list[RegistryEntry]:
        """Load entries in file order. A malformed file / row reads as empty /
        skipped rather than raising (the daemon must never crash on it, 4.4)."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        out: list[RegistryEntry] = []
        for row in raw:
            if not isinstance(row, dict) or "path" not in row:
                continue
            try:
                out.append(RegistryEntry.from_dict(row))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _write(self, entries: list[RegistryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _MODE)
        try:
            payload = json.dumps([e.to_dict() for e in entries], indent=2) + "\n"
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(self.path))
        os.chmod(self.path, _MODE)

    # ---- mutations (terminal-only, 5.3) ----

    def add(self, directory: Path) -> tuple[bool, str]:
        """Register ``directory`` (must exist). Returns ``(changed, message)``.

        Resolves to an absolute path and deduplicates; re-adding is a no-op.
        """
        d = Path(directory).expanduser()
        if not d.is_dir():
            return False, f"not a directory: {directory}"
        resolved = str(d.resolve())
        entries = self.read()
        if any(e.path == resolved for e in entries):
            return False, f"already registered: {resolved}"
        entries.append(
            RegistryEntry(path=resolved, label=Path(resolved).name, added_at=_utc_now_iso())
        )
        self._write(entries)
        return True, f"registered: {resolved}"

    def remove(self, directory: Path) -> tuple[bool, str]:
        """Unregister ``directory`` (matched by resolved path). No-op if absent."""
        d = Path(directory).expanduser()
        # Match on the resolved path, falling back to the raw string so a since-
        # deleted directory can still be removed by the path it was added under.
        try:
            resolved = str(d.resolve())
        except OSError:
            resolved = str(d)
        entries = self.read()
        kept = [e for e in entries if e.path != resolved and e.path != str(d)]
        if len(kept) == len(entries):
            return False, f"not registered: {resolved}"
        self._write(kept)
        return True, f"unregistered: {resolved}"


# --------------------------------------------------------------------------- #
# CLI — `python -m absd.registry` (abs.sh shells into this; terminal-only)
# --------------------------------------------------------------------------- #


def _registry_for(abs_home: Path) -> Registry:
    return Registry(Path(abs_home) / "daemon" / "registry.json")


def _cmd_project(args: argparse.Namespace) -> int:
    reg = _registry_for(args.abs_home)
    if args.action == "list":
        entries = reg.read()
        if not entries:
            print("No registered projects. Add one with: abs project add <dir>")
            return 0
        for e in entries:
            exists = "" if Path(e.path).is_dir() else "  (missing)"
            print(f"{e.label}\t{e.path}{exists}")
        return 0
    if args.action == "add":
        changed, msg = reg.add(Path(args.dir))
        print(msg)
        return 0 if changed or "already" in msg else 1
    if args.action == "rm":
        changed, msg = reg.remove(Path(args.dir))
        print(msg)
        return 0 if changed else 1
    return 2


def _cmd_workspace_root(args: argparse.Namespace) -> int:
    from absd import config as config_mod

    cfg_path = Path(args.abs_home) / "daemon" / "config.json"
    cfg = config_mod.load(cfg_path)
    if args.show or args.dir is None:
        print(cfg.workspace_root)
        return 0
    d = Path(args.dir).expanduser()
    if not d.is_dir():
        print(f"not a directory: {args.dir}", file=sys.stderr)
        return 1
    cfg.workspace_root = str(d.resolve())
    config_mod.save(cfg_path, cfg)
    print(f"workspace-root = {cfg.workspace_root}")
    return 0


def _cmd_targets(args: argparse.Namespace) -> int:
    """The start targets the ABS START flow / terminal start-menu offer: registered
    projects + direct children of the workspace root (NO new-folder here). Reuses
    the same enumeration the daemon uses, so terminal and Telegram agree."""
    import json as _json

    from absd import config as config_mod
    from absd import flow as flow_mod

    abs_home = Path(args.abs_home)
    entries = [(e.path, e.label) for e in _registry_for(abs_home).read()]
    cfg = config_mod.load(abs_home / "daemon" / "config.json")
    root = Path(cfg.workspace_root).expanduser() if cfg.workspace_root else None
    root = root if (root is not None and root.is_dir()) else None
    options = [
        o
        for o in flow_mod.enumerate_project_options(entries, root)
        if o.kind == "project"
    ]
    if args.json:
        print(_json.dumps([{"label": o.label, "path": o.path} for o in options]))
    else:
        for o in options:
            print(f"{o.label}\t{o.path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m absd.registry",
        description="ABS project registry + workspace-root (terminal-only).",
    )
    parser.add_argument(
        "--abs-home", type=Path, default=default_abs_home(), help="ABS home root"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_project = sub.add_parser("project", help="add|list|rm registered projects")
    p_project.add_argument("action", choices=("add", "list", "rm"))
    p_project.add_argument("dir", nargs="?", default=None, help="project directory")

    p_ws = sub.add_parser("workspace-root", help="get/set the daemon workspace root")
    p_ws.add_argument("dir", nargs="?", default=None, help="new workspace root")
    p_ws.add_argument("--show", action="store_true", help="print the current value")

    p_tgt = sub.add_parser("targets", help="list start targets (registered + workspace children)")
    p_tgt.add_argument("--json", action="store_true", help="emit a JSON array")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "project":
        if args.action in ("add", "rm") and not args.dir:
            print(f"abs project {args.action} needs a directory", file=sys.stderr)
            return 2
        return _cmd_project(args)
    if args.command == "workspace-root":
        return _cmd_workspace_root(args)
    if args.command == "targets":
        return _cmd_targets(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
