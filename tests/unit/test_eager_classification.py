"""Approval prompts must be picked up on the FIRST diff that contains them.

Before this change, the bridge only classified the pane in `on_stable`,
which never fired when Claude's UI was animating. The fix wires
`on_change` to also classify and act on `APPROVAL_*` / `TEXT_INPUT`
states immediately, while still letting `on_stable` print the mirror
and act as a safety net for COMPLETE / late-stabilising prompts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cldx.cli import BridgeUI
from cldx.policy_engine import PolicyDecision, PolicyEngine
from cldx.prompt_classifier import PromptType
from cldx.session_picker import Pane


_APPROVAL_SNAPSHOT = (
    "⏺ Bash(rm -rf /tmp/test)\n"
    "  ⎿  Waiting…\n"
    "\n"
    " Do you want to proceed?\n"
    " ❯ 1. Yes\n"
    "   2. Yes, always for this project\n"
    "   3. No\n"
)

_RUNNING_SNAPSHOT = (
    "⏺ Bash(rm -rf /tmp/test)\n"
    "  ⎿  esc to interrupt — Running...\n"
)


def _make_bridge_ui(tmp_path: Path, monkeypatch) -> BridgeUI:
    """Construct a BridgeUI with mocked subprocess + isolated state dir."""
    monkeypatch.setenv("CLDX_HOME", str(tmp_path))

    args = SimpleNamespace(
        poll_interval=1.0,
        mirror_lines=25,
        dry_run=True,            # don't actually send keys to tmux
        no_telegram=True,
    )
    policy = PolicyEngine(
        Path(__file__).resolve().parents[2] / "cldx" / "defaults" / "policy.yml",
        profile_override="auto-approve",
    )
    pane_info = Pane(target="0:0.0", current_command="claude", title="✳")

    ui = BridgeUI(args, "0:0.0", pane_info, policy)
    # Replace the controller with a recording mock so we never touch tmux.
    ui.controller = MagicMock()
    ui.controller.send_yes = AsyncMock(return_value="sent 'y'")
    ui.controller.send_no = AsyncMock(return_value="sent 'n'")
    ui.controller.send_digit = AsyncMock(return_value=None)
    ui.controller.send_enter = AsyncMock(return_value=None)
    ui.controller.send_escape = AsyncMock(return_value=None)
    ui.controller.send_text = AsyncMock(return_value=None)
    return ui


async def test_on_change_acts_on_approval_immediately(tmp_path, monkeypatch):
    """An APPROVAL_MENU in a change event must hit _dispatch_classified."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    await ui.on_change("", _APPROVAL_SNAPSHOT)
    # The signature gets pinned the moment we act, so we know we processed it.
    assert ui.pending_signature is not None, (
        "on_change should classify and dispatch the approval prompt"
    )


async def test_on_change_ignores_idle_running(tmp_path, monkeypatch):
    """Mid-stream RUNNING states must NOT trigger dispatch."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    await ui.on_change("", _RUNNING_SNAPSHOT)
    assert ui.pending_signature is None, (
        "RUNNING snapshots should be ignored in on_change"
    )


async def test_on_change_dedups_same_prompt(tmp_path, monkeypatch):
    """Repeated changes that yield the same signature must fire once only."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    await ui.on_change("", _APPROVAL_SNAPSHOT)
    first_sig = ui.pending_signature
    # Pretend two more change events arrived for the same logical prompt.
    await ui.on_change("", _APPROVAL_SNAPSHOT + "\n   (waiting)\n")
    await ui.on_change("", _APPROVAL_SNAPSHOT + "\n   (still waiting)\n")
    assert ui.pending_signature == first_sig, (
        "signature should remain stable across redraws"
    )


async def test_on_stable_still_prints_mirror_and_acts(tmp_path, monkeypatch):
    """on_stable must continue to mirror + serve as fallback for late prompts."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    await ui.on_stable(_APPROVAL_SNAPSHOT)
    # Mirror was printed (last_mirror_tail set), and the prompt got pinned.
    assert ui.last_mirror_tail != ""
    assert ui.pending_signature is not None


# --- Completion flow ----------------------------------------------------

_COMPLETION_SNAPSHOT = (
    "⏺ Bash(mkdir -p /tmp/cldx_check)\n"
    "  ⎿  Done\n"
    "\n"
    "⏺ Write(/tmp/cldx_check.py)\n"
    "  ⎿  Wrote 1 lines\n"
    "       1 print('hello world')\n"
    "\n"
    "⏺ Created /tmp/cldx_check.py with 'hello world'.\n"
    "\n"
    "✻ Cogitated for 4s\n"
    "\n"
    "❯ cat /tmp/cldx_check.py\n"
    "  ? for shortcuts · ← for agents\n"
)

_CHAT_ONLY_SNAPSHOT = (
    "❯ hi\n"
    "\n"
    "⏺ Hi! What would you like to work on?\n"
    "\n"
    "✻ Crunched for 1s\n"
    "\n"
    "❯\n"
    "  ? for shortcuts · ← for agents\n"
)


async def test_completion_flow_fires_once_then_dedups(tmp_path, monkeypatch):
    """Two on_stable calls with the same completed pane → one panel only."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    from cldx.summarizer import SummaryResult

    async def fake_status(mode, ctx, agent):
        return SummaryResult(text="Created /tmp/cldx_check.py", summarized=True)

    monkeypatch.setattr("cldx.summarizer.summarize_with_status", fake_status)

    await ui.on_stable(_COMPLETION_SNAPSHOT)
    assert ui._completion_locked is True, (
        "first completion should lock the panel"
    )

    # Second on_stable must short-circuit on the lock, not re-fire.
    await ui.on_stable(_COMPLETION_SNAPSHOT)
    assert ui._completion_locked is True


async def test_completion_lock_resets_on_new_approval(tmp_path, monkeypatch):
    """After a completion + a fresh approval prompt, the lock clears so the
    next completion (a new task) can render again."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    from cldx.summarizer import SummaryResult

    async def fake_status(mode, ctx, agent):
        return SummaryResult(text="ok", summarized=True)

    monkeypatch.setattr("cldx.summarizer.summarize_with_status", fake_status)

    await ui.on_stable(_COMPLETION_SNAPSHOT)
    assert ui._completion_locked is True

    await ui.on_change("", _APPROVAL_SNAPSHOT)
    assert ui._completion_locked is False, (
        "new approval prompt should clear the completion lock"
    )


async def test_completion_falls_back_to_raw_on_llm_failure(tmp_path, monkeypatch):
    """If the LLM errors, we still set the lock + show the panel with raw context."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)

    async def boom(*_a, **_kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("cldx.summarizer.summarize_with_status", boom)

    await ui.on_stable(_COMPLETION_SNAPSHOT)
    assert ui._completion_locked is True


async def test_chat_only_reply_skips_completion_panel(tmp_path, monkeypatch):
    """A snapshot with no tool calls must NOT render the green completion panel."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)

    # If we'd entered the "real task" branch, the summarizer would have been
    # called; make sure that doesn't happen.
    calls: list[str] = []

    async def trace_summarize(*_a, **_kw):
        calls.append("called")
        raise AssertionError("summarize should not run for chat-only completions")

    monkeypatch.setattr("cldx.summarizer.summarize_with_status", trace_summarize)
    await ui.on_stable(_CHAT_ONLY_SNAPSHOT)
    assert calls == []
    # The lock IS still set so we don't re-log "Claude replied" twice.
    assert ui._completion_locked is True


def test_pane_has_tool_calls_detects_each_tool(tmp_path, monkeypatch):
    """Every tool from the registry triggers the filter. WebSearch and
    the multi-word display variants (Web Search, Web Fetch, Multi Edit,
    Notebook Edit, Slash Command, Tool Search) are explicitly checked —
    the original hardcoded regex missed them, which is exactly the bug
    that misrouted weather-search turns through the chat-only panel."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    for marker in (
        # Classic set
        "⏺ Bash(echo hi)", "⏺ Read(/x)", "⏺ Edit(/y)",
        "⏺ Write(/z)", "⏺ Grep(foo)", "⏺ Glob(*.py)",
        # Newer single-word tools (would have been missed before fix)
        "⏺ WebSearch(\"weather\")",
        "⏺ TodoWrite(...)",
        # Multi-word display variants (also missed)
        "⏺ Web Search(\"weather\")",
        "⏺ Web Fetch(\"https://x\")",
        "⏺ Multi Edit(file.py)",
        "⏺ Notebook Edit(nb.ipynb)",
    ):
        assert ui._pane_has_tool_calls(marker), (
            f"_pane_has_tool_calls failed to recognise {marker!r}"
        )
    assert not ui._pane_has_tool_calls("⏺ Hi! What's up?")
    assert not ui._pane_has_tool_calls("just a plain message")


async def test_websearch_completion_renders_full_green_panel(tmp_path, monkeypatch):
    """Regression — WebSearch turns must take the real-task path, not
    the chat-only path.

    Before the fix, ``_pane_has_tool_calls`` had a hardcoded list of
    tools that didn't include WebSearch. So even though Claude clearly
    did work (two web searches + a long prose result), cldx rendered
    the truncated 💬 cyan chat-reply panel instead of the ✓ green
    completion card. The user lost the bulk of the weather details
    because chat-reply was capped at 12 lines.
    """
    from cldx.summarizer import SummaryResult

    ui = _make_bridge_ui(tmp_path, monkeypatch)

    async def fake_summary(mode, ctx, agent):
        # Mimic the no-LLM "raw fallback" path the user is on.
        return SummaryResult(
            text=ctx, summarized=False,
            fallback_reason="LLM disabled (model: none:*)",
        )

    monkeypatch.setattr("cldx.summarizer.summarize_with_status", fake_summary)

    snapshot = (
        "❯ Search for Indore and Khandwa\n"
        "⏺ Web Search(\"weather Indore today\")\n"
        "  ⎿ Did 1 search in 4s\n"
        "⏺ Web Search(\"weather Khandwa today\")\n"
        "  ⎿ Did 1 search in 4s\n"
        "⏺ Here's the weather for Indore and Khandwa today:\n"
        "  Indore: 90°F, breezy and very warm\n"
        "  Khandwa: 100°F, hazy sunshine\n"
        "✻ Brewed for 10s\n"
        "❯\n"
        "  ? for shortcuts · ← for agents\n"
    )

    # The pre-condition: this snapshot must be recognised as tool-using.
    assert ui._pane_has_tool_calls(snapshot), (
        "WebSearch turn must trigger the tool-call filter so it routes "
        "through the real-task green panel, not the chat-reply path"
    )

    # End-to-end: the on_stable path classifies COMPLETE and runs
    # _handle_completion, which must set the lock (signalling that the
    # green panel was rendered for a real task).
    await ui.on_stable(snapshot)
    assert ui._completion_locked is True


async def test_mirror_suppressed_after_completion_locks(tmp_path, monkeypatch):
    """Once we've shown a green completion panel for a task, further
    on_stable events with the same COMPLETE classification must NOT
    re-print the blue mirror panel."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)

    from cldx.summarizer import SummaryResult

    async def fake_status(mode, ctx, agent):
        return SummaryResult(text="ok", summarized=True)

    monkeypatch.setattr("cldx.summarizer.summarize_with_status", fake_status)

    # First on_stable → completion fires, lock set, mirror printed once.
    await ui.on_stable(_COMPLETION_SNAPSHOT)
    assert ui._completion_locked is True
    first_mirror = ui.last_mirror_tail

    # Force an internal state change that would normally bust the mirror
    # dedup (e.g. inject some whitespace in the snapshot). The mirror
    # MUST still not reprint because the completion is locked.
    jittered = _COMPLETION_SNAPSHOT + "\n    \n"   # different bytes, same logical state
    await ui.on_stable(jittered)

    # Mirror cache unchanged → no second mirror panel rendered.
    assert ui.last_mirror_tail == first_mirror


async def test_mirror_resumes_when_new_task_starts(tmp_path, monkeypatch):
    """Lock clears on new approval prompt, so the next stable refreshes mirror."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)

    from cldx.summarizer import SummaryResult

    async def fake_status(mode, ctx, agent):
        return SummaryResult(text="ok", summarized=True)

    monkeypatch.setattr("cldx.summarizer.summarize_with_status", fake_status)

    await ui.on_stable(_COMPLETION_SNAPSHOT)
    assert ui._completion_locked is True

    # A new approval prompt arrives → lock should clear.
    await ui.on_change("", _APPROVAL_SNAPSHOT)
    assert ui._completion_locked is False


def test_mirror_dedup_survives_trailing_whitespace_jitter(tmp_path, monkeypatch):
    """Two snapshots that differ only by trailing spaces / blank lines
    must produce the same normalized tail (no duplicate panel)."""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    a = "line1   \nline2\n\n\n"
    b = "line1\nline2  \n\n"
    assert ui._normalize_tail(a) == ui._normalize_tail(b)


def test_mirror_dedup_collapses_blank_runs(tmp_path, monkeypatch):
    """Runs of N blank lines collapse to one, so a frame that briefly
    inserts a spare blank doesn't bust dedup. (We DO keep a single blank
    because paragraph breaks are real structure.)"""
    ui = _make_bridge_ui(tmp_path, monkeypatch)
    a = "x\n\ny"          # one blank separator
    b = "x\n\n\n\ny"      # four blank separators
    assert ui._normalize_tail(a) == ui._normalize_tail(b)
