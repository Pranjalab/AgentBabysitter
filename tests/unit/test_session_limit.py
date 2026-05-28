"""session_limit — parse Claude Code's session-limit banner."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from abs.session_limit import SessionLimit, parse_session_limit


# --- happy paths ----------------------------------------------------------


def test_parse_basic_with_timezone():
    """The exact example the user gave."""
    text = "You've hit your session limit · resets 7:50pm (Asia/Calcutta)"
    # Pin "now" so the test is deterministic.
    now = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    limit = parse_session_limit(text, now=now)
    assert limit is not None
    assert limit.label == "7:50 pm"
    assert limit.timezone_str == "Asia/Calcutta"
    # 7:50pm Asia/Calcutta = 14:20 UTC.
    assert limit.reset_at.hour == 14
    assert limit.reset_at.minute == 20


def test_parse_24_hour_no_ampm():
    text = "Session limit reached. Resets at 21:30 UTC"
    now = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    limit = parse_session_limit(text, now=now)
    assert limit is not None
    assert limit.label == "21:30"  # no am/pm in original
    # 21:30 UTC same day.
    assert limit.reset_at.hour == 21
    assert limit.reset_at.minute == 30


def test_parse_approaching_phrasing():
    text = "Approaching usage limit · resets 11:00 am"
    now = datetime(2026, 5, 27, 8, 0, 0, tzinfo=timezone.utc)
    limit = parse_session_limit(text, now=now)
    assert limit is not None
    assert limit.label == "11:00 am"


def test_parse_returns_none_on_unrelated_text():
    assert parse_session_limit("regular pane content") is None
    assert parse_session_limit("") is None
    assert parse_session_limit(None) is None  # type: ignore[arg-type]


def test_parse_handles_buried_in_snapshot():
    text = (
        "Some banner content\n"
        "⏺ Bash(ls)\n"
        "  ⎿ file1\n"
        "You've hit your session limit · resets 9:30pm\n"
        "More stuff below\n"
    )
    limit = parse_session_limit(
        text, now=datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc),
    )
    assert limit is not None
    assert limit.label == "9:30 pm"


# --- AM/PM disambiguation -------------------------------------------------


def test_pm_in_future_today_same_day():
    """Now is 10am, reset at 7:50pm → same day."""
    now_utc = datetime(2026, 5, 27, 4, 30, 0, tzinfo=timezone.utc)  # 10am Calcutta
    text = "session limit · resets 7:50pm (Asia/Calcutta)"
    limit = parse_session_limit(text, now=now_utc)
    assert limit is not None
    # Reset is later same day → not rolled forward.
    assert limit.reset_at.day == 27


def test_past_time_rolls_to_tomorrow():
    """Now is 8pm, banner says 'resets 7am' → must be tomorrow's 7am."""
    now_utc = datetime(2026, 5, 27, 14, 30, 0, tzinfo=timezone.utc)  # 8pm Calcutta
    text = "session limit · resets 7:00am (Asia/Calcutta)"
    limit = parse_session_limit(text, now=now_utc)
    assert limit is not None
    assert limit.reset_at.day == 28  # next day


def test_seconds_until_reset_positive_for_future():
    now = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    limit = parse_session_limit(
        "session limit · resets 12:00pm UTC", now=now,
    )
    assert limit is not None
    delta = limit.seconds_until_reset(now)
    assert delta > 0


# --- robustness -----------------------------------------------------------


def test_parse_with_unknown_timezone_string_falls_back():
    """Random TZ strings shouldn't crash — just use local."""
    text = "session limit · resets 7:50pm (FAKE_TZ_123)"
    now = datetime(2026, 5, 27, 4, 30, 0, tzinfo=timezone.utc)
    limit = parse_session_limit(text, now=now)
    assert limit is not None
    assert limit.label == "7:50 pm"


def test_parse_handles_compact_form():
    """No space between time and am/pm, no parenthesised tz."""
    text = "you've hit your session limit · resets 7:50pm"
    limit = parse_session_limit(
        text, now=datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc),
    )
    assert limit is not None
    assert limit.label == "7:50 pm"


def test_parse_is_case_insensitive():
    text = "SESSION LIMIT REACHED · RESETS 7:50PM"
    limit = parse_session_limit(
        text, now=datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc),
    )
    assert limit is not None
    assert limit.label.lower().endswith("pm")
