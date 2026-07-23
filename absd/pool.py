"""Per-profile message pool.

When a message arrives for a bot with no live session, the daemon acknowledges
it and appends it to that profile's pool so nothing is silently dropped (G3,
D14). The pool is an append-only JSON-lines file at
``~/.abs/profiles/<name>/pool.jsonl``, written 0600 (PLAN.md 4.3 / 5.5).

On-disk record shape (the stable spec a future port re-implements, PLAN.md 4.4)
— one JSON object per line::

    {"update_id": 12, "from_id": 42, "text": "hi",
     "received_at": "2026-07-23T10:00:00Z", "forwarded_at": null}

Discipline:
  - **Append-only, atomic-enough.** Each ``append`` opens ``O_APPEND``, writes
    one complete ``json.dumps(...) + "\\n"`` in a single ``write`` and fsyncs.
    A crash mid-write can at worst leave a torn final line, which ``read_all``
    tolerates (see below) — it never corrupts an earlier record.
  - **Corrupted-line tolerance.** ``read_all`` skips any line that is not valid
    JSON or is missing required fields, counting skips in ``last_read_skipped``.
    A damaged pool file never crashes the daemon (PLAN.md 4.4 restructure rule).
  - **0600 always.** The file is created with mode 0600 and re-``chmod``'d on
    every append, so a pre-existing looser file is tightened (pool contents are
    user data, PLAN.md 5.5).
  - **Dedupe is the caller's job, cheaply.** ``existing_update_ids`` lets the
    poller skip a redelivered ``update_id`` (crash between pool-persist and
    offset-advance re-delivers the batch — D14 says never lose, and we also
    never duplicate).

Pooled messages are kept until explicitly cleared or forwarded (D14);
forwarding stamps ``forwarded_at`` rather than deleting (that lifecycle lands in
Step 1.7 but ``mark_forwarded``/``clear`` are implemented here already).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MODE = 0o600


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with a ``Z`` suffix (seconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PooledMessage:
    """One record in ``pool.jsonl``.

    Fields are the stable on-disk contract (PLAN.md 4.4). ``forwarded_at`` is
    ``None`` until the message is delivered into a session (D14 keeps the record
    either way).
    """

    update_id: int
    from_id: int
    text: str
    received_at: str  # ISO-8601 UTC timestamp
    forwarded_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serializable record for one ``pool.jsonl`` line."""
        return {
            "update_id": self.update_id,
            "from_id": self.from_id,
            "text": self.text,
            "received_at": self.received_at,
            "forwarded_at": self.forwarded_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PooledMessage":
        """Build from a parsed line. Raises ``KeyError``/``TypeError`` on a
        record missing the required fields — caught by ``Pool.read_all``."""
        return cls(
            update_id=int(data["update_id"]),
            from_id=int(data["from_id"]),
            text=str(data["text"]),
            received_at=str(data["received_at"]),
            forwarded_at=(
                None if data.get("forwarded_at") is None else str(data["forwarded_at"])
            ),
        )


class Pool:
    """Append-only pool store for a single profile."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        #: number of malformed lines skipped by the most recent ``read_all``.
        self.last_read_skipped = 0

    # ---- writes ----------------------------------------------------------

    def append(self, message: PooledMessage) -> int:
        """Append one message durably; return the new total pool count.

        Creates the parent dir and the file 0600 if absent; a single
        ``O_APPEND`` write of one JSON line keeps concurrent/partial writes from
        interleaving within a line.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(message.to_dict(), ensure_ascii=False) + "\n"
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, _MODE)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        # Tighten perms even if the file pre-existed with a looser mode.
        os.chmod(self.path, _MODE)
        return self.count()

    def mark_forwarded(self, update_ids: list[int], forwarded_at: str | None = None) -> None:
        """Stamp the given records as forwarded, keeping them (D14).

        Rewrites the file atomically (temp + ``os.replace``). No-op if the pool
        is absent. (Fuller lifecycle is Step 1.7; the primitive lives here.)
        """
        if not self.path.exists():
            return
        stamp = forwarded_at or utc_now_iso()
        wanted = set(update_ids)
        records = self.read_all()
        for rec in records:
            if rec.update_id in wanted and rec.forwarded_at is None:
                rec.forwarded_at = stamp
        self._rewrite(records)

    def clear(self) -> None:
        """Remove all pooled records (the ``ABS CLEAR POOL`` command, Step 1.7)."""
        if self.path.exists():
            self.path.unlink()

    def _rewrite(self, records: list[PooledMessage]) -> None:
        """Atomically replace the file with ``records`` (temp + os.replace)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _MODE)
        try:
            for rec in records:
                os.write(fd, (json.dumps(rec.to_dict(), ensure_ascii=False) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(self.path))
        os.chmod(self.path, _MODE)

    # ---- reads -----------------------------------------------------------

    def read_all(self) -> list[PooledMessage]:
        """Read every pooled record in arrival order.

        Malformed lines (invalid JSON, or missing required fields — e.g. a torn
        final line after a crash) are skipped and counted in
        ``last_read_skipped``; the daemon never crashes on a damaged pool.
        """
        self.last_read_skipped = 0
        if not self.path.exists():
            return []
        out: list[PooledMessage] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    out.append(PooledMessage.from_dict(data))
                except (ValueError, TypeError, KeyError):
                    self.last_read_skipped += 1
        return out

    def count(self) -> int:
        """Total number of well-formed records currently pooled."""
        return len(self.read_all())

    def unforwarded(self) -> list[PooledMessage]:
        """Records not yet delivered into a session (``forwarded_at is None``)."""
        return [m for m in self.read_all() if m.forwarded_at is None]

    def existing_update_ids(self) -> set[int]:
        """The set of ``update_id`` values already pooled — for dedupe on
        redelivery (crash-safety: the poller skips ids it has already stored)."""
        return {m.update_id for m in self.read_all()}
