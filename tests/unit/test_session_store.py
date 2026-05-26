"""Phase 2 — session_store.py: jsonl event log writer + replayer.

Each line in `~/.claudex/sessions/<profile>/<timestamp>.jsonl` is one event.
Event kinds: snapshot, prompt, decision, action, telegram_out, telegram_in.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase2


@pytest.mark.skip(reason="Phase 2 — session_store not implemented yet")
def test_log_event_appends_jsonl_line():
    """SessionStore.log_event(kind, data) should append one JSON line."""


@pytest.mark.skip(reason="Phase 2")
def test_log_event_includes_iso_timestamp():
    """Every event must carry an ISO-8601 timestamp under key `t`."""


@pytest.mark.skip(reason="Phase 2")
def test_replay_yields_events_in_order():
    """SessionStore.replay() iterates events in original chronological order."""


@pytest.mark.skip(reason="Phase 2")
def test_session_dir_per_profile():
    """Sessions for profile X live under ~/.claudex/sessions/X/."""


@pytest.mark.skip(reason="Phase 2")
def test_log_event_atomic_on_concurrent_writers():
    """Two writers must not corrupt each other's lines (line-buffered append)."""


@pytest.mark.skip(reason="Phase 2")
def test_recent_sessions_lists_by_mtime():
    """SessionStore.recent(profile) returns sessions newest-first."""
