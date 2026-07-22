"""Per-profile message pool (stub).

When a message arrives for a bot with no live session, the daemon acknowledges
it and appends it to that profile's pool so nothing is silently dropped (G3,
D14). The pool is an append-only JSON-lines file at
``~/.abs/profiles/<name>/pool.jsonl``, written 0600 (PLAN.md 4.3 / 5.5).

Pooled messages are kept until explicitly cleared or forwarded (D14); forwarding
marks them with ``forwarded_at`` rather than deleting them.

Implementation lands in Step 1.3 (append/read) and Step 1.7 (forward/clear
lifecycle). The on-disk ``pool.jsonl`` record shape is part of the spec
(PLAN.md 4.4), so ``PooledMessage`` fixes those fields now.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


class Pool:
    """Append-only pool store for a single profile. (Steps 1.3 / 1.7.)"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, message: PooledMessage) -> int:
        """Append a message; return the new pool count. (Step 1.3.)"""
        raise NotImplementedError("pool store lands in Step 1.3")

    def read_all(self) -> list[PooledMessage]:
        """Read every pooled record in arrival order. (Step 1.3.)"""
        raise NotImplementedError("pool store lands in Step 1.3")

    def mark_forwarded(self, update_ids: list[int], forwarded_at: str) -> None:
        """Stamp the given records as forwarded, keeping them (D14). (Step 1.7.)"""
        raise NotImplementedError("pool forwarding lands in Step 1.7")

    def clear(self) -> None:
        """Remove all pooled records (the ``ABS CLEAR POOL`` command). (Step 1.7.)"""
        raise NotImplementedError("pool clear lands in Step 1.7")
