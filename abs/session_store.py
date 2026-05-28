"""Append-only event log for abs sessions.

Each abs run writes one JSONL file under
``~/.abs/sessions/<profile>/<timestamp>.jsonl`` (or wherever
``$CLDX_HOME`` points). Every classification, decision, action, and
inbound/outbound Telegram message is one line.

The log is intentionally simple:

- One JSON object per line, terminated by ``\\n``.
- ``t`` is an ISO-8601 timestamp.
- ``kind`` is the event class (``snapshot``, ``prompt``, ``decision``,
  ``action``, ``telegram_out``, ``telegram_in``, ``note``).
- Payload-specific fields live alongside ``t`` and ``kind`` (free-form).

Why JSONL: append-safe, grep-friendly, replayable line-by-line without
loading the whole file into memory.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from abs._paths import abs_home


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_TS_SAFE_RE = re.compile(r"[^0-9A-Za-z_\-]")


def _stamp_for_filename() -> str:
    """Filesystem-safe timestamp: ``2026-05-26T10-32-15``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def sessions_root() -> Path:
    """Root directory for all session logs. Created lazily."""
    root = abs_home() / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


class SessionStore:
    """Append-only JSONL writer for one abs run.

    The file is opened lazily on the first ``log_event`` call, so creating
    a SessionStore is cheap and side-effect-free.
    """

    def __init__(self, profile: str, pane: str | None = None,
                 root: Path | None = None) -> None:
        self.profile = _TS_SAFE_RE.sub("_", profile) or "default"
        self.pane = pane
        self._root = root or sessions_root()
        self._path: Path | None = None
        self._fh = None
        self._event_count = 0

    # --- file management ---

    @property
    def path(self) -> Path:
        """Path of this session's JSONL file (created on first event)."""
        if self._path is None:
            stamp = _stamp_for_filename()
            self._path = self._root / self.profile / f"{stamp}.jsonl"
            self._path.parent.mkdir(parents=True, exist_ok=True)
        return self._path

    @property
    def event_count(self) -> int:
        return self._event_count

    def _ensure_open(self) -> None:
        if self._fh is None:
            # line buffering so each event is durable without explicit flush
            self._fh = open(self.path, "a", buffering=1, encoding="utf-8")

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- writing ---

    def log_event(self, kind: str, **payload: Any) -> None:
        """Append one event line. ``kind`` and ``payload`` keys must be JSON-safe."""
        self._ensure_open()
        record: dict[str, Any] = {"t": _now_iso(), "kind": kind}
        record.update(payload)
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")  # type: ignore[union-attr]
        self._event_count += 1

    # Convenience helpers -- these are the canonical event kinds abs writes.

    def log_snapshot(self, snapshot: str) -> None:
        self.log_event("snapshot", lines=snapshot.splitlines())

    def log_prompt(self, prompt) -> None:
        self.log_event(
            "prompt",
            type=prompt.type.value,
            command=prompt.extracted_command,
            options=list(prompt.menu_options),
            signature=prompt.signature(),
        )

    def log_decision(self, decision, wait_ms: int = 0) -> None:
        self.log_event(
            "decision",
            decision=decision.decision.value,
            profile=decision.profile,
            reason=decision.reason,
            matched_pattern=decision.matched_pattern,
            wait_ms=wait_ms,
        )

    def log_action(self, keys: str, source: str = "policy") -> None:
        """`source` is one of: 'policy', 'user_terminal', 'user_telegram'."""
        self.log_event("action", keys=keys, source=source)

    def log_note(self, message: str) -> None:
        self.log_event("note", message=message)


# --- reading / replay -----------------------------------------------------


def replay(path: str | os.PathLike) -> Iterator[dict[str, Any]]:
    """Yield each event from a session file in original order.

    Lines that fail to parse are skipped with a synthetic
    ``{"kind": "_corrupt", "raw": ...}`` event so callers can decide what
    to do without crashing.
    """
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"kind": "_corrupt", "raw": line}


def recent_sessions(profile: str | None = None, limit: int = 20) -> list[Path]:
    """Return existing session files newest-first.

    Filters to one profile if given, otherwise scans every profile dir.
    """
    root = sessions_root()
    if profile is not None:
        candidates = list((root / profile).glob("*.jsonl")) if (root / profile).exists() else []
    else:
        candidates = [p for d in root.iterdir() if d.is_dir() for p in d.glob("*.jsonl")]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:limit]


def session_summary(path: str | os.PathLike) -> dict[str, Any]:
    """Cheap summary of a session file: counts + window.

    Used by the startup picker to show "last seen X ago, N events".
    """
    p = Path(path)
    if not p.exists():
        return {}
    counts: dict[str, int] = {}
    first_ts: str | None = None
    last_ts: str | None = None
    for event in replay(p):
        if "kind" in event and not event["kind"].startswith("_"):
            counts[event["kind"]] = counts.get(event["kind"], 0) + 1
        ts = event.get("t")
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
    return {
        "path": str(p),
        "profile": p.parent.name,
        "events": sum(counts.values()),
        "kinds": counts,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }
