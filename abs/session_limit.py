"""Detect Claude Code session-limit banners in pane snapshots.

Claude Code prints a one-line banner when the user has exhausted the
5-hour rolling Pro window — something like::

    You've hit your session limit · resets 7:50pm (Asia/Calcutta)
    Approaching usage limit · resets 11:00 am
    Session limit reached. Resets at 21:30 UTC

We can't reach the underlying quota API, but we *can* watch the pane
and surface that banner to the user proactively (terminal + Telegram)
and remember the reset time so the dynamic header can show it and a
background task can fire a "session reset" notification when the time
comes.

``parse_session_limit`` is purely structural — it parses regardless of
whether ``zoneinfo`` is installed; the returned ``reset_at`` is in the
parsed timezone when available, otherwise the local timezone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


# A loose match — Claude has tweaked the wording multiple times. We look
# for the keyword pair "session limit" + "reset(s|ting)" and then the
# time expression that follows. The ``(?i)`` makes the whole pattern
# case-insensitive.
_LIMIT_LINE_RE = re.compile(
    r"(?i)"
    r"(?:hit|reached|approaching)?\s*(?:your\s+)?"
    r"(?:claude\s+)?(?:usage\s+|session\s+)limit"
    r"[^\n]*?"
    r"reset(?:s|ting)?\s+(?:at\s+)?"
    r"(\d{1,2})\s*:\s*(\d{2})"
    # AM/PM is optional. We tie its leading whitespace to the marker
    # being present so a missing am/pm doesn't gobble the space that
    # belongs to a following bareword timezone (e.g. "21:30 UTC").
    r"(?:\s*([ap]m))?"
    # Timezone: either parenthesised (Asia/Calcutta) or a bareword tag
    # (UTC, PST, IST, …). We capture from whichever shape matches.
    r"(?:\s*\(([^)]+)\)|\s+([A-Za-z][A-Za-z0-9_/+\-]{1,40}))?"
)


@dataclass(frozen=True)
class SessionLimit:
    """A parsed session-limit banner."""
    reset_at: datetime           # absolute UTC datetime
    label: str                   # display string e.g. "7:50 pm"
    timezone_str: str = ""       # original parenthesised tz (if any)
    raw_line: str = ""           # the exact banner line we matched

    def seconds_until_reset(self, now: Optional[datetime] = None) -> float:
        """How many seconds from ``now`` until the reset.

        Negative if the reset is in the past (caller should check before
        sleeping).
        """
        now = now or datetime.now(timezone.utc)
        return (self.reset_at - now).total_seconds()


def parse_session_limit(
    text: str,
    now: Optional[datetime] = None,
) -> SessionLimit | None:
    """Parse a session-limit banner from ``text``.

    Returns the matched ``SessionLimit`` or ``None`` if no banner is
    present. When the banner gives a 12-hour clock with no AM/PM, the
    parser assumes the next reasonable wall-clock instance (i.e. if it's
    08:00 and the banner says "resets 7:50", we interpret that as 7:50 PM
    same day — never in the past).

    ``now`` is injectable for tests; defaults to the current time in the
    parsed timezone (if any) or local timezone.
    """
    if not text:
        return None
    m = _LIMIT_LINE_RE.search(text)
    if not m:
        return None
    hour_s, minute_s, ampm, paren_tz, bare_tz = m.groups()
    tz_str = paren_tz or bare_tz
    hour = int(hour_s)
    minute = int(minute_s)

    tz = _resolve_timezone(tz_str)
    now_local = (now.astimezone(tz) if now else datetime.now(tz))

    # Apply AM/PM if present.
    if ampm:
        ampm_lower = ampm.lower()
        if ampm_lower.startswith("p") and hour < 12:
            hour += 12
        elif ampm_lower.startswith("a") and hour == 12:
            hour = 0

    # Build the reset datetime in the parsed timezone.
    reset_local = now_local.replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )
    # If reset has already passed today, roll to tomorrow.
    if reset_local <= now_local:
        # Without AM/PM we may have picked the wrong half of the day.
        if not ampm and hour < 12:
            candidate = reset_local.replace(hour=hour + 12)
            if candidate > now_local:
                reset_local = candidate
            else:
                reset_local = reset_local + timedelta(days=1)
        else:
            reset_local = reset_local + timedelta(days=1)

    return SessionLimit(
        reset_at=reset_local.astimezone(timezone.utc),
        label=_format_label(hour_s, minute_s, ampm),
        timezone_str=(tz_str or "").strip(),
        raw_line=m.group(0).strip(),
    )


def _resolve_timezone(tz_str: Optional[str]):
    """Resolve a timezone string to a tzinfo, falling back to local."""
    if not tz_str:
        return _local_tz()
    cleaned = tz_str.strip()
    try:
        from zoneinfo import ZoneInfo  # py 3.9+
        return ZoneInfo(cleaned)
    except Exception:  # noqa: BLE001
        # Some pane snapshots contain abbreviations like "IST" / "PT"
        # that aren't valid IANA zones; just use local in that case.
        return _local_tz()


def _local_tz():
    """Best-effort local timezone (used when zoneinfo lookup fails)."""
    return datetime.now().astimezone().tzinfo or timezone.utc


def _format_label(hour_s: str, minute_s: str, ampm: Optional[str]) -> str:
    """Render a user-facing label like ``7:50 pm`` / ``21:30``."""
    if ampm:
        return f"{int(hour_s)}:{minute_s} {ampm.lower()}"
    return f"{hour_s}:{minute_s}"
