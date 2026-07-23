"""Consolidated v3 status dashboard — ``python -m absd.status`` (observability).

One read-only picture of the daemon and every bot it manages, assembled from the
daemon's own on-disk trail (never by contacting the running process):

  - per-profile ``status-<name>.json``  → state, pool depth, live pid, last poll
  - ``recents.json``                    → recent projects per profile
  - ``events.jsonl``                    → the live session's engine/project/age
    (reconstructed from the last ``session_start`` with no later ``session_end``)
  - ``systemctl --user show absd``      → running/stopped + uptime (best-effort)

Everything degrades gracefully: renders plainly when the daemon is **stopped**
(says so), and the caller (``abs status`` / ``abs daemon status``) skips the whole
section on a **v2-only install** where ``absd/`` state is absent. The renderer is
pure (dataclasses in, string out) so it unit-tests without any of those sources.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from absd import __version__
from absd.daemon import read_status_files
from absd.events import (
    EVENT_HANDOFF,
    EVENT_SESSION_END,
    EVENT_SESSION_START,
    iter_events,
)
from absd.recents import Recents

# A poller whose status file hasn't been rewritten in this long, while the daemon
# is running, is treated as a stalled/dead poller in the dashboard.
STALE_POLLER_S = 180


# --------------------------------------------------------------------------- #
# view types (the pure renderer's input)
# --------------------------------------------------------------------------- #


@dataclass
class SessionView:
    engine: str | None = None
    project: str | None = None
    age_s: int | None = None
    attach: str | None = None


@dataclass
class RecentView:
    label: str
    age_s: int | None = None


@dataclass
class ProfileView:
    name: str
    state: str  # polling | yielding-to-session | off | blocked | dead-poller | unknown
    pool_count: int = 0
    session: SessionView | None = None
    recents: list[RecentView] = field(default_factory=list)


@dataclass
class DaemonInfo:
    running: bool | None  # None = couldn't tell (no systemd / not queried)
    version: str = __version__
    uptime_s: int | None = None
    profiles_count: int = 0


# --------------------------------------------------------------------------- #
# rendering (pure)
# --------------------------------------------------------------------------- #


def fmt_age(secs: int | None) -> str:
    """Coarse human age: ``just now`` / ``12m`` / ``3h`` / ``5d`` / ``?``."""
    if secs is None:
        return "?"
    if secs < 0:
        secs = 0
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def render_dashboard(daemon: DaemonInfo, profiles: list[ProfileView]) -> str:
    """Render the full dashboard block (pure). Content, not exact formatting, is
    the contract the tests assert on."""
    lines: list[str] = ["Agent Babysitter daemon (absd)"]

    if daemon.running is True:
        header = f"  daemon    running (absd {daemon.version}"
        if daemon.uptime_s is not None:
            header += f", up {fmt_age(daemon.uptime_s)}"
        header += ")"
    elif daemon.running is False:
        header = f"  daemon    stopped (absd {daemon.version}) — start: abs daemon start"
    else:
        header = f"  daemon    status unknown (absd {daemon.version})"
    lines.append(header)
    lines.append(f"  profiles  {daemon.profiles_count} managed")

    if not profiles:
        lines.append("  (no per-profile state yet)")
        return "\n".join(lines)

    for p in profiles:
        lines.append("")
        lines.append(f"  {p.name}: {p.state}  pool={p.pool_count}")
        if p.session is not None:
            s = p.session
            bits = []
            if s.project:
                bits.append(s.project)
            if s.engine:
                bits.append(f"via {s.engine}")
            bits.append(f"up {fmt_age(s.age_s)}")
            lines.append(f"    session  {'  '.join(bits)}")
            if s.attach:
                lines.append(f"    attach   {s.attach}")
        if p.recents:
            shown = ", ".join(f"{r.label} ({fmt_age(r.age_s)})" for r in p.recents)
            lines.append(f"    recent   {shown}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# data collection (read-only)
# --------------------------------------------------------------------------- #


def _parse_iso(ts: str | None) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _age_s(ts: str | None, now: datetime) -> int | None:
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return max(0, int((now - dt).total_seconds()))


def _live_session_from_events(events_path: Path, profile: str, now: datetime) -> SessionView | None:
    """Reconstruct the currently-live session for ``profile`` from the trail: the
    last ``session_start`` with no later ``session_end``; engine/project come from
    the preceding ``handoff``. Returns None if no session is currently live."""
    pending_project: str | None = None
    pending_engine: str | None = None
    current: SessionView | None = None
    for rec in iter_events(events_path, profile=profile):
        ev = rec.get("event")
        if ev == EVENT_HANDOFF:
            pending_project = rec.get("project")
            pending_engine = rec.get("engine")
        elif ev == EVENT_SESSION_START:
            current = SessionView(
                engine=pending_engine,
                project=pending_project,
                age_s=_age_s(rec.get("ts"), now),
                attach=f"abs attach {profile}",
            )
        elif ev == EVENT_SESSION_END:
            current = None
    return current


def collect(
    abs_home: Path,
    systemd: dict | None = None,
    now: datetime | None = None,
) -> tuple[DaemonInfo, list[ProfileView]]:
    """Assemble the dashboard data from disk (+ an optional systemd dict). All
    read-only; missing sources degrade to empty/unknown, never raise."""
    abs_home = Path(abs_home)
    daemon_dir = abs_home / "daemon"
    now = now or datetime.now(timezone.utc)
    events_path = daemon_dir / "events.jsonl"

    records = read_status_files(daemon_dir)
    recents = Recents(daemon_dir / "recents.json")

    running = systemd.get("running") if systemd else None
    uptime_s = systemd.get("uptime_s") if systemd else None

    # Union of profiles the daemon has status for + any with recents.
    names = {str(r.get("profile")) for r in records if r.get("profile")}
    try:
        import json

        raw = json.loads((daemon_dir / "recents.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            names.update(raw.keys())
    except (OSError, ValueError):
        pass

    by_name = {str(r.get("profile")): r for r in records if r.get("profile")}
    profiles: list[ProfileView] = []
    for name in sorted(names):
        rec = by_name.get(name, {})
        state = str(rec.get("state") or "unknown")
        # dead-poller: the daemon is up but this poller stopped updating its status.
        if running is True and state == "polling":
            age = _age_s(rec.get("updated_at"), now)
            if age is not None and age > STALE_POLLER_S:
                state = "dead-poller"
        pool_count = int(rec.get("pool_count") or 0)

        session = None
        if rec.get("session_pid") or state == "yielding-to-session":
            session = _live_session_from_events(events_path, name, now)
            if session is None:
                # status says a session is live but the trail lacks it (e.g. a
                # terminal launch) — show a minimal live marker.
                session = SessionView(attach=f"abs attach {name}")

        recent_views = [
            RecentView(label=e.label, age_s=_age_s(e.started_at, now))
            for e in recents.list(name)[:3]
        ]
        profiles.append(
            ProfileView(
                name=name,
                state=state,
                pool_count=pool_count,
                session=session,
                recents=recent_views,
            )
        )

    daemon = DaemonInfo(
        running=running, uptime_s=uptime_s, profiles_count=len(profiles)
    )
    return daemon, profiles


def query_systemd(unit: str = "absd.service") -> dict | None:
    """Best-effort ``systemctl --user show`` for running-state + uptime. Returns
    None when systemd is unavailable (so the header reads 'unknown')."""
    try:
        out = subprocess.run(
            [
                "systemctl", "--user", "show", unit,
                "--property=ActiveState",
                "--property=ActiveEnterTimestampMonotonic",
            ],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    props: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    running = props.get("ActiveState") == "active"
    uptime_s = None
    mono = props.get("ActiveEnterTimestampMonotonic", "")
    if running and mono.isdigit() and int(mono) > 0:
        try:
            now_mono = time.clock_gettime(time.CLOCK_MONOTONIC)
            uptime_s = max(0, int(now_mono - int(mono) / 1_000_000))
        except (OSError, ValueError):
            uptime_s = None
    return {"running": running, "uptime_s": uptime_s}


def _default_abs_home() -> Path:
    import os

    return Path(os.environ.get("ABS_HOME") or (Path.home() / ".abs"))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m absd.status")
    parser.add_argument("--abs-home", type=Path, default=_default_abs_home())
    parser.add_argument(
        "--no-systemd", action="store_true", help="skip the systemctl query"
    )
    args = parser.parse_args(argv)

    systemd = None if args.no_systemd else query_systemd()
    daemon, profiles = collect(args.abs_home, systemd=systemd)
    print(render_dashboard(daemon, profiles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
