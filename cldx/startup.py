"""Startup greeting + session picker.

Shown when the user runs ``cldx`` without ``--session`` / ``--auto-detect``
/ ``--list-panes``. Prints a banner with the user's last known state
(agent name, profile, telegram status, last session) and then offers a
numbered menu of session options:

- **resume** an existing event log (from `~/.cldx/sessions/<profile>/`)
- **connect** to a live tmux pane that looks like Claude Code
- **start** a brand-new tmux session + claude
- **manage** (placeholder for Phase 4+ admin commands)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cldx import __version__
from cldx.memory import Memory
from cldx.picker import PickerRow, pick_numeric, pick_with_arrows
from cldx.policy_engine import PolicyEngine
from cldx.session_picker import Pane, _looks_like_claude, list_panes
from cldx.session_store import recent_sessions, session_summary


# --- public API ---------------------------------------------------------


@dataclass
class StartupChoice:
    """Result of running the picker."""
    pane: str                       # target pane string (always set)
    resume_from: Path | None = None # path to previous session log to replay


async def run_startup(
    policy: PolicyEngine,
    memory: Memory,
    console: Console | None = None,
    input_fn: Callable[[str], str] = input,
) -> StartupChoice:
    """Show banner + picker, return the user's choice.

    On a real TTY: arrow-key picker with delete-via-`d` confirmation.
    Otherwise (pipes / tests): numeric fallback driven by ``input_fn``.

    This is async because ``pick_with_arrows`` returns a coroutine that
    must run in the caller's event loop — we can't spawn a nested
    ``asyncio.run`` here, that's an error inside ``asyncio.run(main())``.
    """
    import sys

    console = console or Console()
    show_banner(policy, memory, console=console)

    rows = _refresh_pick_rows()
    if not rows:
        return _execute_choice(
            _PickRow(kind="start", label="start new",
                     detail="no panes/sessions found"),
            console,
        )

    # Map cldx-internal rows to picker rows. Resume + connect are deletable;
    # "start new" is not.
    picker_rows: list[PickerRow] = []
    for row in rows:
        if row.kind == "resume":
            picker_rows.append(PickerRow(
                text=f"{row.label.ljust(38)}  {row.detail}",
                payload=row,
                deletable=True,
                delete_hint="delete event log",
            ))
        elif row.kind == "connect":
            picker_rows.append(PickerRow(
                text=f"{row.label.ljust(38)}  {row.detail}",
                payload=row,
                deletable=True,
                delete_hint="kill tmux session",
            ))
        else:  # "start" / "manage"
            picker_rows.append(PickerRow(
                text=f"{row.label.ljust(38)}  {row.detail}",
                payload=row,
                deletable=False,
            ))

    def _on_delete(picker_row: PickerRow) -> None:
        target: _PickRow = picker_row.payload
        if target.kind == "resume" and target.resume_path:
            try:
                target.resume_path.unlink()
                console.print(
                    f"[dim]deleted event log: {target.resume_path.name}[/dim]"
                )
            except OSError as e:
                console.print(f"[red]could not delete: {e}[/red]")
        elif target.kind == "connect" and target.pane:
            session_name = target.pane.split(":", 1)[0]
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", session_name],
                    check=True, capture_output=True, text=True,
                )
                console.print(f"[dim]killed tmux session: {session_name}[/dim]")
            except subprocess.CalledProcessError as e:
                console.print(
                    f"[red]could not kill {session_name}: {e.stderr.strip()}[/red]"
                )

    header = f"Pick a session — [dim]{len(picker_rows)} options[/dim]"

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if is_tty:
        chosen_payload = await pick_with_arrows(
            picker_rows, header=header, on_delete=_on_delete,
        )
    else:
        chosen_payload = pick_numeric(
            picker_rows, header=header, input_fn=input_fn, on_delete=_on_delete,
        )

    if chosen_payload is None:
        raise KeyboardInterrupt("user cancelled session picker")
    return _execute_choice(chosen_payload, console)


def _refresh_pick_rows() -> list["_PickRow"]:
    """Rebuild the list of available actions (panes + sessions + start new)."""
    panes = list_panes()
    claude_panes = [p for p in panes if _looks_like_claude(p)]
    sessions = recent_sessions(limit=5)
    return _build_pick_rows(claude_panes, sessions)


# --- banner -------------------------------------------------------------


def show_banner(policy: PolicyEngine, memory: Memory,
                console: Console | None = None) -> None:
    console = console or Console()
    profile = policy.active_profile_name
    learned = memory.approved_count(profile) if profile == "yolo" else 0
    learned_str = (
        f" ({learned} pattern{'s' if learned != 1 else ''} remembered)"
        if learned else ""
    )

    tg_state = memory.data.telegram.get("configured", False)
    tg_line = "[green]✓ configured[/green]" if tg_state else (
        "[red]✗ not configured[/red]  [dim](run `cldx telegram setup`)[/dim]"
    )

    last = memory.data.last_session or {}
    last_line = "[dim]no prior sessions[/dim]"
    if last:
        ago = _format_ago(last.get("ended_at") or last.get("started_at"))
        events = last.get("events", 0)
        last_line = f"{ago} — {events} event{'s' if events != 1 else ''} on profile {last.get('profile', '?')}"

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("agent", memory.data.agent_name)
    table.add_row("profile", f"[magenta]{profile}[/magenta]{learned_str}")
    table.add_row("telegram", tg_line)
    table.add_row("last run", last_line)

    console.print(Panel(
        table,
        title=f"[bold cyan]cldx[/bold cyan] [dim]v{__version__}[/dim]",
        border_style="cyan",
    ))


def _format_ago(ts: str | None) -> str:
    if not ts:
        return "unknown"
    try:
        # ts is ISO-8601 UTC like "2026-05-26T10:32:15+00:00"
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    now = datetime.now(timezone.utc)
    delta = (now - when).total_seconds()
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


# --- pick list ----------------------------------------------------------


@dataclass
class _PickRow:
    kind: str            # "resume" | "connect" | "start" | "manage"
    label: str
    detail: str
    pane: str | None = None
    resume_path: Path | None = None


def _build_pick_rows(panes: list[Pane], sessions: list[Path]) -> list[_PickRow]:
    rows: list[_PickRow] = []
    # Resume rows (newest 3)
    for path in sessions[:3]:
        summ = session_summary(path)
        when = _format_ago(summ.get("last_ts"))
        events = summ.get("events", 0)
        rows.append(_PickRow(
            kind="resume",
            label=f"resume  {path.parent.name}/{path.stem}",
            detail=f"{when}, {events} events",
            resume_path=path,
        ))
    # Connect rows (live Claude panes)
    for p in panes:
        title = p.title or "(no title)"
        rows.append(_PickRow(
            kind="connect",
            label=f"connect {p.target}",
            detail=f"{title}  [{p.current_command}]",
            pane=p.target,
        ))
    # Always offer a fresh start.
    rows.append(_PickRow(
        kind="start",
        label="start   new tmux + claude",
        detail="creates a detached session and bridges to it",
    ))
    return rows


def _render_pick_table(rows: list[_PickRow], console: Console) -> None:
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("#", style="cyan", width=3)
    table.add_column("action")
    table.add_column("detail", style="dim")
    for i, row in enumerate(rows, start=1):
        table.add_row(str(i), row.label, row.detail)
    console.print(table)


# --- execute ------------------------------------------------------------


def _execute_choice(row: _PickRow, console: Console) -> StartupChoice:
    if row.kind == "resume":
        # Resume: we still need a live pane to bridge against. Use the same
        # auto-detect as a normal launch, with a hint about the replay file.
        pane = _find_or_spawn_pane(console)
        return StartupChoice(pane=pane, resume_from=row.resume_path)

    if row.kind == "connect":
        assert row.pane is not None
        return StartupChoice(pane=row.pane)

    if row.kind == "start":
        pane = spawn_new_claude_session(console=console)
        return StartupChoice(pane=pane)

    raise ValueError(f"unknown pick row kind: {row.kind}")


def _find_or_spawn_pane(console: Console) -> str:
    """Best-effort pane discovery; falls back to spawning a fresh session."""
    panes = [p for p in list_panes() if _looks_like_claude(p)]
    if panes:
        console.print(f"[dim]using existing pane {panes[0].target}[/dim]")
        return panes[0].target
    console.print("[dim]no live Claude pane found, spawning a new one[/dim]")
    return spawn_new_claude_session(console=console)


def spawn_new_claude_session(
    session_prefix: str = "cldx",
    console: Console | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
) -> str:
    """Create a detached tmux session running ``claude`` and return its pane.

    ``runner`` lets tests inject a fake subprocess executor.
    """
    console = console or Console()
    runner = runner or _default_runner

    # Pick a unique session name.
    stamp = int(time.time())
    name = f"{session_prefix}-{stamp}"

    # 1. New detached session running a shell.
    runner(["tmux", "new-session", "-d", "-s", name])
    # 2. Send `claude` + Enter to start Claude Code inside it.
    runner(["tmux", "send-keys", "-t", name, "claude", "Enter"])

    pane = f"{name}:0.0"
    console.print(f"[green]✓ spawned tmux session {name} → pane {pane}[/green]")
    console.print(
        "[dim](attach with `tmux attach -t " + name + "` "
        "from another terminal if you want to see Claude's UI directly)[/dim]"
    )
    return pane


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)
