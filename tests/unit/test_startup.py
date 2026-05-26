"""Phase 4 — startup.py: greeting banner + session picker."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase4


@pytest.mark.skip(reason="Phase 4 — startup picker not implemented yet")
def test_greeting_shows_agent_and_profile():
    """Header line must include agent name, active profile, telegram status."""


@pytest.mark.skip(reason="Phase 4")
def test_session_list_includes_resumable_and_live_options():
    """Picker shows: resume <recent>, connect <live tmux>, start new, manage."""


@pytest.mark.skip(reason="Phase 4")
def test_start_new_spawns_detached_tmux_with_claude():
    """`start new` runs `tmux new -d -s claudex-N && tmux send-keys claude Enter`."""


@pytest.mark.skip(reason="Phase 4")
def test_resume_replays_previous_events_jsonl():
    """Choosing `resume` re-prints the prior session's events as a transcript."""


@pytest.mark.skip(reason="Phase 4")
def test_no_resumable_sessions_hides_resume_entry():
    """If sessions/<profile>/ is empty, the picker omits the resume row."""
