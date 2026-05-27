"""Persistent memory for cldx — yolo learned patterns, telegram state, etc.

Lives at ``~/.cldx/memory.json`` (or ``$CLDX_HOME/memory.json``).

Yolo profile semantics:

- The first time a pattern is seen, the user approves or denies it. That
  user decision gets stored in ``approved_patterns.yolo`` or
  ``denied_patterns.yolo``. Future identical prompts auto-fire.
- "Identical" is decided by `normalize_pattern`, which collapses the
  command to ``Tool(first_token)`` — so ``Bash(npm install --save axios)``
  and ``Bash(npm install)`` both normalize to ``Bash(npm)``.
- Destructive patterns are NEVER stored, even on explicit approval.
  The user has to re-approve them every time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cldx._paths import cldx_home


# --- Pattern normalization ------------------------------------------------

_TOOL_CALL_RE = re.compile(
    r"^(?P<tool>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<arg>.*)\)\s*$"
)
_FIRST_TOKEN_RE = re.compile(r"^[\s'\"]*(?P<tok>[^\s'\";]+)")


def normalize_pattern(extracted_command: str | None) -> str | None:
    """Collapse a tool call to ``Tool(first_arg_token)``.

    Returns ``None`` if the input is empty or unparseable.

    Examples
    --------
    >>> normalize_pattern("Bash(npm install --save axios)")
    'Bash(npm)'
    >>> normalize_pattern("Bash(ls -la /tmp)")
    'Bash(ls)'
    >>> normalize_pattern("Read(/x/y/z.py)")
    'Read'
    >>> normalize_pattern("Edit(src/main.py)")
    'Edit'
    >>> normalize_pattern("")  # returns None
    """
    if not extracted_command:
        return None
    m = _TOOL_CALL_RE.match(extracted_command.strip())
    if not m:
        # Not in Tool(arg) form. Treat as a raw token.
        token = _FIRST_TOKEN_RE.match(extracted_command.strip())
        return token.group("tok") if token else None

    tool = m.group("tool")
    arg = m.group("arg").strip()
    if not arg:
        return tool
    tok_match = _FIRST_TOKEN_RE.match(arg)
    if not tok_match:
        return tool
    first_token = tok_match.group("tok")
    # Strip path-y prefixes — first token of "src/foo.py" is just "src/foo.py",
    # which is too specific; collapse to just the tool for non-shell tools.
    if tool in ("Read", "Edit", "Write", "Glob", "Grep", "LS"):
        return tool
    return f"{tool}({first_token})"


# --- Memory store ---------------------------------------------------------

DEFAULT_PROFILE = "auto-approve"


@dataclass
class MemoryData:
    """The on-disk JSON shape, exposed as a typed-ish dataclass."""
    agent_name: str = "Sentinel"
    active_profile: str = DEFAULT_PROFILE
    telegram: dict[str, Any] = field(default_factory=lambda: {"configured": False})
    approved_patterns: dict[str, list[str]] = field(default_factory=dict)
    denied_patterns: dict[str, list[str]] = field(default_factory=dict)
    last_session: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "active_profile": self.active_profile,
            "telegram": self.telegram,
            "approved_patterns": self.approved_patterns,
            "denied_patterns": self.denied_patterns,
            "last_session": self.last_session,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryData":
        return cls(
            agent_name=data.get("agent_name", "Sentinel"),
            active_profile=data.get("active_profile", DEFAULT_PROFILE),
            telegram=data.get("telegram", {"configured": False}),
            approved_patterns=data.get("approved_patterns", {}),
            denied_patterns=data.get("denied_patterns", {}),
            last_session=data.get("last_session", {}),
        )


class Memory:
    """Read/write wrapper over ``~/.cldx/memory.json``.

    Reads happen lazily. Every mutating call writes back to disk so
    yolo learning survives a crash.
    """

    def __init__(self, path: Path | None = None,
                 destructive_patterns: list[str] | None = None) -> None:
        self.path = path or (cldx_home() / "memory.json")
        self.data = self._load()
        self._destructive_res: list[re.Pattern[str]] = []
        for raw in destructive_patterns or []:
            try:
                self._destructive_res.append(re.compile(raw, re.IGNORECASE))
            except re.error:
                continue

    def _load(self) -> MemoryData:
        if not self.path.exists():
            return MemoryData()
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            return MemoryData.from_dict(raw)
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable — fall back to defaults; the next save
            # will overwrite the bad file.
            return MemoryData()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish write: tmp file then rename.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.data.to_dict(), fh, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    # --- Pattern-learning API ---

    def _is_destructive_text(self, text: str) -> bool:
        return any(p.search(text) for p in self._destructive_res)

    def is_approved(self, pattern: str, profile: str) -> bool:
        return pattern in (self.data.approved_patterns.get(profile, []) or [])

    def is_denied(self, pattern: str, profile: str) -> bool:
        return pattern in (self.data.denied_patterns.get(profile, []) or [])

    def learn(self, approve: bool, pattern: str | None, profile: str) -> bool:
        """Remember a user decision. Returns True if stored, False if skipped.

        Skipped cases:
            - pattern is None / empty
            - pattern matches a destructive regex (never learnable)
            - pattern already present in the target list (idempotent)
        """
        if not pattern:
            return False
        if self._is_destructive_text(pattern):
            return False

        target = (
            self.data.approved_patterns if approve
            else self.data.denied_patterns
        )
        # Also remove from the opposite bucket so user can change their mind.
        opposite = (
            self.data.denied_patterns if approve
            else self.data.approved_patterns
        )
        if profile in opposite and pattern in opposite[profile]:
            opposite[profile].remove(pattern)

        bucket = target.setdefault(profile, [])
        if pattern in bucket:
            return False
        bucket.append(pattern)
        self.save()
        return True

    def forget(self, pattern: str, profile: str) -> bool:
        """Remove a pattern from both buckets. Returns True if anything was removed."""
        removed = False
        for bucket_map in (self.data.approved_patterns, self.data.denied_patterns):
            if profile in bucket_map and pattern in bucket_map[profile]:
                bucket_map[profile].remove(pattern)
                removed = True
        if removed:
            self.save()
        return removed

    # --- Profile + session metadata ---

    def set_active_profile(self, name: str) -> None:
        self.data.active_profile = name
        self.save()

    def set_last_session(self, info: dict[str, Any]) -> None:
        self.data.last_session = info
        self.save()

    def set_agent_name(self, name: str) -> None:
        self.data.agent_name = name
        self.save()

    def set_telegram(self, info: dict[str, Any]) -> None:
        self.data.telegram = info
        self.save()

    def approved_count(self, profile: str) -> int:
        return len(self.data.approved_patterns.get(profile, []) or [])

    def denied_count(self, profile: str) -> int:
        return len(self.data.denied_patterns.get(profile, []) or [])
