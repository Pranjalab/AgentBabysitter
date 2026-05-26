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
