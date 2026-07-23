"""Recent session launches — ``~/.abs/daemon/recents.json`` (Step 2.2 resume-first).

Every successful session launch — from the daemon's HANDOFF *and* from a terminal
``abs`` — is recorded here so the ABS START flow can offer a one-tap "▶ Resume"
instead of walking the project + mode screens every time.

On-disk format (stable spec a future port re-implements, PLAN.md 4.4) — a JSON
object keyed by profile, each value a most-recent-first list capped at 5::

    {"default": [{"path": "/home/u/Projects/llm", "label": "llm",
                  "mode": "normal", "started_at": "2026-07-23T15:21:00Z",
                  "profile": "default"}, ...]}

Discipline:
  - **stdlib-only**, 0600 under the 0700 daemon dir (4.3 / 5.5).
  - **Dedup by resolved path** within a profile: re-launching the same path moves
    it to the top and refreshes its mode + timestamp (never a duplicate row).
  - **Corruption-tolerant**: a malformed file reads as empty, never crashes the
    daemon (4.4). A future port re-implements the shape, not this code.

The terminal launch path records via ``python -m absd.recents add`` from
``abs.sh`` (guarded so absd's absence never breaks a plain v2 launch); the daemon
records in-process at HANDOFF.
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
CAP = 5  # most-recent entries kept per profile
VALID_MODES = ("normal", "away")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_abs_home() -> Path:
    return Path(os.environ.get("ABS_HOME") or (Path.home() / ".abs"))


@dataclass
class RecentEntry:
    path: str
    label: str
    mode: str
    started_at: str
    profile: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "label": self.label,
            "mode": self.mode,
            "started_at": self.started_at,
            "profile": self.profile,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RecentEntry":
        path = str(data["path"])
        mode = str(data.get("mode") or "normal")
        if mode not in VALID_MODES:
            mode = "normal"
        return cls(
            path=path,
            label=str(data.get("label") or Path(path).name),
            mode=mode,
            started_at=str(data.get("started_at") or ""),
            profile=str(data.get("profile") or ""),
        )


def resolve_label(abs_home: Path, path: str) -> str:
    """Label for ``path``: a registered project's label if one matches (by
    resolved path), else the basename. Import kept local to avoid a cycle."""
    from absd.registry import Registry

    try:
        resolved = str(Path(path).expanduser().resolve())
    except OSError:
        resolved = str(path)
    try:
        for entry in Registry(Path(abs_home) / "daemon" / "registry.json").read():
            if entry.path == resolved:
                return entry.label
    except Exception:
        pass
    return Path(resolved).name


class Recents:
    """The per-profile recent-launch store backed by ``recents.json``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # ---- io ----

    def _read_raw(self) -> dict[str, list[dict[str, Any]]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def list(self, profile: str) -> list[RecentEntry]:
        """Most-recent-first entries for ``profile`` (corruption-tolerant)."""
        rows = self._read_raw().get(profile)
        if not isinstance(rows, list):
            return []
        out: list[RecentEntry] = []
        for row in rows:
            if isinstance(row, dict) and "path" in row:
                try:
                    out.append(RecentEntry.from_dict(row))
                except (KeyError, TypeError, ValueError):
                    continue
        return out

    def _write(self, store: dict[str, list[dict[str, Any]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _MODE)
        try:
            os.write(fd, (json.dumps(store, indent=2) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(self.path))
        os.chmod(self.path, _MODE)

    # ---- mutations ----

    def record(
        self,
        profile: str,
        path: str,
        label: str,
        mode: str,
        started_at: str | None = None,
    ) -> None:
        """Record a launch: dedup by resolved path (move to top, refresh mode +
        timestamp), cap at :data:`CAP`, most-recent-first."""
        if mode not in VALID_MODES:
            mode = "normal"
        try:
            resolved = str(Path(path).expanduser().resolve())
        except OSError:
            resolved = str(path)
        entry = RecentEntry(
            path=resolved,
            label=label or Path(resolved).name,
            mode=mode,
            started_at=started_at or _utc_now_iso(),
            profile=profile,
        )
        store = self._read_raw()
        rows = [
            r
            for r in (store.get(profile) or [])
            if isinstance(r, dict) and str(r.get("path")) != resolved
        ]
        rows.insert(0, entry.to_dict())
        store[profile] = rows[:CAP]
        self._write(store)

    def remove(self, profile: str, path: str) -> bool:
        """Drop ``path`` from ``profile``'s recents (e.g. the dir vanished).
        Returns True if a row was removed."""
        try:
            resolved = str(Path(path).expanduser().resolve())
        except OSError:
            resolved = str(path)
        store = self._read_raw()
        rows = store.get(profile)
        if not isinstance(rows, list):
            return False
        kept = [
            r
            for r in rows
            if isinstance(r, dict) and str(r.get("path")) not in (resolved, str(path))
        ]
        if len(kept) == len(rows):
            return False
        store[profile] = kept
        self._write(store)
        return True


# --------------------------------------------------------------------------- #
# CLI — `python -m absd.recents add` (abs.sh terminal launch path)
# --------------------------------------------------------------------------- #


def _recents_for(abs_home: Path) -> Recents:
    return Recents(Path(abs_home) / "daemon" / "recents.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m absd.recents")
    parser.add_argument("--abs-home", type=Path, default=default_abs_home())
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="record a launch")
    p_add.add_argument("--profile", required=True)
    p_add.add_argument("--path", required=True)
    p_add.add_argument("--mode", default="normal", choices=VALID_MODES)
    p_add.add_argument("--label", default=None)

    p_list = sub.add_parser("list", help="print recents for a profile")
    p_list.add_argument("--profile", required=True)

    args = parser.parse_args(argv)
    recents = _recents_for(args.abs_home)

    if args.command == "add":
        label = args.label or resolve_label(args.abs_home, args.path)
        recents.record(args.profile, args.path, label, args.mode)
        return 0
    if args.command == "list":
        for e in recents.list(args.profile):
            print(f"{e.label}\t{e.mode}\t{e.path}\t{e.started_at}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
