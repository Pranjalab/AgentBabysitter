"""Phase 2 — session_store.py: jsonl event log writer + replayer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cldx.session_store import (
    SessionStore,
    recent_sessions,
    replay,
    session_summary,
)


@pytest.fixture
def store_root(tmp_path, monkeypatch):
    """Redirect $CLDX_HOME to a tmpdir so tests don't touch real state."""
    monkeypatch.setenv("CLDX_HOME", str(tmp_path))
    return tmp_path / "sessions"


def test_log_event_appends_jsonl_line(store_root):
    store = SessionStore(profile="default")
    store.log_event("note", message="hi")
    store.close()

    text = store.path.read_text()
    assert text.endswith("\n")
    line = json.loads(text)
    assert line["kind"] == "note"
    assert line["message"] == "hi"
    assert "t" in line


def test_log_event_includes_iso_timestamp(store_root):
    store = SessionStore(profile="default")
    store.log_event("snapshot", lines=["a", "b"])
    store.close()
    line = json.loads(store.path.read_text())
    assert "T" in line["t"] and ("+" in line["t"] or "Z" in line["t"])


def test_session_dir_per_profile(store_root):
    a = SessionStore(profile="auto-approve")
    a.log_note("first")
    a.close()
    b = SessionStore(profile="yolo")
    b.log_note("second")
    b.close()

    assert a.path.parent.name == "auto-approve"
    assert b.path.parent.name == "yolo"
    assert a.path != b.path


def test_replay_yields_events_in_order(store_root):
    store = SessionStore(profile="default")
    store.log_event("snapshot", lines=["x"])
    store.log_event("decision", decision="auto_yes")
    store.log_event("action", keys="y")
    store.close()

    events = list(replay(store.path))
    assert [e["kind"] for e in events] == ["snapshot", "decision", "action"]
    assert events[1]["decision"] == "auto_yes"


def test_replay_skips_corrupt_lines(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"kind":"ok"}\n{not json}\n{"kind":"again"}\n')
    events = list(replay(p))
    assert events[0]["kind"] == "ok"
    assert events[1]["kind"] == "_corrupt"
    assert events[2]["kind"] == "again"


def test_recent_sessions_lists_newest_first(store_root):
    import time
    for profile in ("a", "b"):
        s = SessionStore(profile=profile)
        s.log_note("hello")
        s.close()
        time.sleep(0.01)
    files = recent_sessions()
    assert len(files) == 2
    assert files[0].stat().st_mtime >= files[1].stat().st_mtime


def test_recent_sessions_filters_by_profile(store_root):
    SessionStore(profile="alpha").log_note("a")
    SessionStore(profile="beta").log_note("b")
    alpha_only = recent_sessions(profile="alpha")
    assert len(alpha_only) == 1
    assert alpha_only[0].parent.name == "alpha"


def test_session_summary_counts_kinds(store_root):
    s = SessionStore(profile="default")
    s.log_event("snapshot", lines=["x"])
    s.log_event("snapshot", lines=["y"])
    s.log_event("decision", decision="auto_yes")
    s.close()

    summary = session_summary(s.path)
    assert summary["events"] == 3
    assert summary["kinds"]["snapshot"] == 2
    assert summary["kinds"]["decision"] == 1


def test_session_store_context_manager(store_root):
    with SessionStore(profile="default") as s:
        s.log_note("inside")
    assert s._fh is None
    assert s.path.exists()


def test_log_prompt_serializes_classified_prompt(store_root):
    from cldx.prompt_classifier import ClassifiedPrompt, PromptType
    p = ClassifiedPrompt(
        type=PromptType.APPROVAL_MENU,
        extracted_command="Bash(npm install)",
        menu_options=("1. Yes", "2. No"),
    )
    store = SessionStore(profile="default")
    store.log_prompt(p)
    store.close()
    line = json.loads(store.path.read_text())
    assert line["kind"] == "prompt"
    assert line["type"] == "approval_menu"
    assert line["command"] == "Bash(npm install)"
    assert line["options"] == ["1. Yes", "2. No"]


def test_log_action_records_source(store_root):
    store = SessionStore(profile="default")
    store.log_action(keys="y", source="user_terminal")
    store.close()
    line = json.loads(store.path.read_text())
    assert line["source"] == "user_terminal"
    assert line["keys"] == "y"


def test_path_safe_profile_name(store_root):
    s = SessionStore(profile="auto/approve")
    s.log_note("hi")
    s.close()
    assert "/" not in s.path.parent.name


def test_event_count_increments(store_root):
    s = SessionStore(profile="default")
    assert s.event_count == 0
    s.log_note("a")
    assert s.event_count == 1
    s.log_note("b")
    assert s.event_count == 2
