"""Structured event log — ``~/.abs/daemon/events.jsonl`` (observability).

An append-only, machine-readable trail of what the daemon did, alongside (never
replacing) the human-readable ``daemon.log``. One JSON object per line so a future
UI, a dashboard, or a debugging human can reconstruct any incident timeline from
this file alone.

Every line has: ``ts`` (ISO-8601 UTC), ``event`` (from the fixed vocabulary
below), ``level`` (``info``/``warning``/``error``), an optional ``profile``, and
event-specific fields. **Metadata only — never user content.** In particular
``message_pooled`` carries the ``update_id`` but NOT the message text: the text
lives only in the per-profile pool (0600), so the event log can never leak what a
user typed even if it is shared for debugging.

Event vocabulary (a STABLE 4.4 spec surface — a future port re-implements these
shapes, not this code):

  daemon_start   {version, profiles: [name, ...]}
  daemon_stop    {reason}
  poller_state   {state, from_state}                      # session-state machine
  handoff        {project, mode, engine, resume: bool}
  session_start  {pane_id, pid}
  session_end    {reason, lived_s}   reason ∈ exited | failed_start |
                                            foreign_takeover_cleared
  reclaim_done   {backoff_409s: int}
  message_pooled {update_id}                              # NO text (metadata only)
  command        {name}                                   # grammar command only
  error          {where, message}
  menu_set       {kind}                                   # idle | session
  engine_kill    {ok: bool}

Discipline:
  - **Single writer per daemon process.** The daemon is single-threaded asyncio,
    so appends are naturally serialized; each write is one ``O_APPEND`` line.
  - **Never raises.** A logging failure must never disturb the daemon — :meth:`emit`
    swallows every error (the trail is a convenience, not a source of truth).
  - **Corruption-tolerant reads.** :func:`iter_events` skips any malformed line
    (including a torn trailing line after a crash mid-write).
  - **Size-capped** at ``max_bytes`` (default 5 MB) with one ``.old`` roll — the
    same crude approach as ``daemon.log``; full rotation polish stays Step 1.8.
  - **0600** under the 0700 daemon dir (5.5).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from absd import rotate as rotate_mod

_MODE = 0o600
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_KEEP = 3

# ---- event vocabulary (stable constants) ------------------------------------

EVENT_DAEMON_START = "daemon_start"
EVENT_DAEMON_STOP = "daemon_stop"
EVENT_POLLER_STATE = "poller_state"
EVENT_HANDOFF = "handoff"
EVENT_SESSION_START = "session_start"
EVENT_SESSION_END = "session_end"
EVENT_RECLAIM_DONE = "reclaim_done"
EVENT_MESSAGE_POOLED = "message_pooled"
EVENT_COMMAND = "command"
EVENT_ERROR = "error"
EVENT_MENU_SET = "menu_set"
EVENT_ENGINE_KILL = "engine_kill"

# session_end reasons.
END_EXITED = "exited"
END_FAILED_START = "failed_start"
END_FOREIGN_TAKEOVER_CLEARED = "foreign_takeover_cleared"

_LEVELS = ("info", "warning", "error")


def utc_now_iso() -> str:
    """Current UTC time, ISO-8601 with a ``Z`` suffix (seconds resolution)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventLog:
    """Append-only JSONL writer for daemon events. One instance per daemon."""

    def __init__(
        self,
        path: Path,
        max_bytes: int = DEFAULT_MAX_BYTES,
        keep: int = DEFAULT_KEEP,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.keep = keep

    def _maybe_roll(self) -> None:
        """Rotate ``.1``…``.keep`` when over the size cap (Step 1.8)."""
        rotate_mod.maybe_rotate(self.path, self.max_bytes, self.keep, mode=_MODE)

    def emit(
        self,
        event: str,
        *,
        profile: str | None = None,
        level: str = "info",
        **fields: Any,
    ) -> dict[str, Any] | None:
        """Append one event line. Returns the record written, or ``None`` if the
        write failed (never raises). ``fields`` are the event-specific values —
        they must be JSON-serializable and must never include user message text."""
        record: dict[str, Any] = {
            "ts": utc_now_iso(),
            "event": event,
            "level": level if level in _LEVELS else "info",
        }
        if profile is not None:
            record["profile"] = profile
        record.update(fields)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._maybe_roll()
            line = json.dumps(record, ensure_ascii=False) + "\n"
            fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, _MODE)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
            os.chmod(self.path, _MODE)
            return record
        except (OSError, ValueError, TypeError):
            return None


def _iter_one_file(path: Path) -> Iterator[dict[str, Any]]:
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict):
                yield rec


def iter_events(
    path: Path,
    profile: str | None = None,
    since: str | None = None,
    event: str | None = None,
    include_rotated: bool = True,
    keep: int = DEFAULT_KEEP,
) -> Iterator[dict[str, Any]]:
    """Yield events in chronological order, filtered — ACROSS rotated files.

    With ``include_rotated`` (default), reads ``<path>.keep`` … ``<path>.1`` then
    the live ``<path>`` so a rolled log still reconstructs a continuous timeline
    (Step 1.8 — closes the obs "reconstruction survives rotation" residual).

    Corruption-tolerant: any line that is not a JSON object is skipped (including a
    torn trailing line after a crash mid-write). Filters:
      - ``profile`` — only events for that profile (events with no profile are
        skipped when a profile filter is given).
      - ``since``   — only events with ``ts >= since`` (ISO-8601 string compare,
        which is chronological for this fixed ``...Z`` format).
      - ``event``   — only events of that name.
    """
    path = Path(path)
    files = rotate_mod.rotated_paths(path, keep=keep) if include_rotated else [path]
    for fpath in files:
        for rec in _iter_one_file(fpath):
            if event is not None and rec.get("event") != event:
                continue
            if profile is not None and rec.get("profile") != profile:
                continue
            if since is not None:
                ts = rec.get("ts")
                if not isinstance(ts, str) or ts < since:
                    continue
            yield rec
