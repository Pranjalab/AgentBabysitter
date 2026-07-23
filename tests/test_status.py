"""Consolidated dashboard (absd/status.py): pure renderer + collector."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from absd.events import EventLog
from absd.recents import Recents
from absd.status import (
    DaemonInfo,
    ProfileView,
    RecentView,
    SessionView,
    collect,
    fmt_age,
    render_dashboard,
)

NOW = datetime(2026, 7, 23, 15, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- pure renderer -----------------------------------------------------------


def test_render_running_with_session_and_recents() -> None:
    daemon = DaemonInfo(running=True, version="2.6.0", uptime_s=3600, profiles_count=1)
    profiles = [
        ProfileView(
            name="default",
            state="yielding-to-session",
            pool_count=2,
            session=SessionView(
                engine="herdr", project="/home/u/Projects/llm", age_s=180,
                attach="abs attach default",
            ),
            recents=[RecentView("llm", 180), RecentView("web", 7200)],
        )
    ]
    out = render_dashboard(daemon, profiles)
    assert "running (absd 2.6.0, up 1h)" in out
    assert "1 managed" in out
    assert "default: yielding-to-session  pool=2" in out
    assert "/home/u/Projects/llm" in out and "via herdr" in out
    assert "abs attach default" in out
    assert "llm (3m)" in out and "web (2h)" in out


def test_render_stopped() -> None:
    out = render_dashboard(DaemonInfo(running=False), [])
    assert "stopped" in out
    assert "abs daemon start" in out


def test_render_unknown() -> None:
    out = render_dashboard(DaemonInfo(running=None), [])
    assert "unknown" in out


def test_render_polling_no_session() -> None:
    daemon = DaemonInfo(running=True, profiles_count=1)
    profiles = [ProfileView(name="work", state="polling", pool_count=0)]
    out = render_dashboard(daemon, profiles)
    assert "work: polling  pool=0" in out
    assert "session" not in out  # no live session line


def test_fmt_age() -> None:
    assert fmt_age(None) == "?"
    assert fmt_age(30) == "just now"
    assert fmt_age(600) == "10m"
    assert fmt_age(7200) == "2h"
    assert fmt_age(2 * 86400) == "2d"


# ---- collect -----------------------------------------------------------------


def _status_file(abs_home: Path, name: str, **fields) -> None:
    rec = {"profile": name, "updated_at": _iso(NOW), **fields}
    (abs_home / "daemon" / f"status-{name}.json").write_text(json.dumps(rec))


def test_collect_reconstructs_live_session_from_events(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    _status_file(abs_home, "default", state="yielding-to-session", pool_count=1, session_pid=1234)
    log = EventLog(abs_home / "daemon" / "events.jsonl")
    # craft a handoff + session_start 3 minutes ago (no session_end → live)
    path = abs_home / "daemon" / "events.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"ts": _iso(NOW), "event": "handoff", "profile": "default",
                        "project": "/p/llm", "engine": "herdr", "mode": "normal"}),
            json.dumps({"ts": _iso(datetime(2026, 7, 23, 14, 57, 0, tzinfo=timezone.utc)),
                        "event": "session_start", "profile": "default",
                        "pane_id": "w1:p1", "pid": 1234}),
        ]) + "\n"
    )
    _ = log
    Recents(abs_home / "daemon" / "recents.json").record("default", str(tmp_path), "proj", "normal")

    daemon, profiles = collect(abs_home, systemd={"running": True, "uptime_s": 10}, now=NOW)
    assert daemon.running is True
    assert len(profiles) == 1
    p = profiles[0]
    assert p.state == "yielding-to-session"
    assert p.session is not None
    assert p.session.engine == "herdr"
    assert p.session.project == "/p/llm"
    assert p.session.age_s == 180  # 3 minutes
    assert p.recents and p.recents[0].label == "proj"


def test_collect_dead_poller_detection(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    stale = datetime(2026, 7, 23, 14, 50, 0, tzinfo=timezone.utc)  # 10 min old
    (abs_home / "daemon" / "status-work.json").write_text(
        json.dumps({"profile": "work", "state": "polling", "updated_at": _iso(stale)})
    )
    daemon, profiles = collect(abs_home, systemd={"running": True}, now=NOW)
    assert profiles[0].state == "dead-poller"


def test_collect_daemon_stopped_still_renders(tmp_path: Path) -> None:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    _status_file(abs_home, "default", state="polling", pool_count=3)
    daemon, profiles = collect(abs_home, systemd={"running": False}, now=NOW)
    assert daemon.running is False
    assert profiles[0].pool_count == 3
    # a stale status while stopped is NOT flagged dead-poller (daemon isn't running)
    assert profiles[0].state == "polling"
    out = render_dashboard(daemon, profiles)
    assert "stopped" in out


def test_collect_v2_only_install_is_empty(tmp_path: Path) -> None:
    # no daemon dir at all (v2-only) → no profiles, unknown daemon
    abs_home = tmp_path / "abs"
    abs_home.mkdir()
    daemon, profiles = collect(abs_home, systemd=None, now=NOW)
    assert profiles == []
    assert daemon.running is None
