"""Telegram message templates — structural assertions, not exact text."""

from __future__ import annotations

import pytest

from cldx.telegram_templates import (
    ApprovalCard,
    CompletionCard,
    EscalationCard,
    approval_message,
    completion_message,
    error_message,
    escalation_message,
    greeting_message,
    help_message,
)


# --- approval -------------------------------------------------------------


def test_approval_basic_structure():
    msg = approval_message(ApprovalCard(
        command="Bash(rm -rf /tmp/test)",
        summary="Cleaning up test artifacts.",
        risk="destructive",
        profile="auto-approve",
    ))
    # Header + rule + tool + summary all present.
    assert "approval needed" in msg.lower()
    assert "Bash(rm -rf /tmp/test)" in msg
    assert "Cleaning up test artifacts." in msg
    assert "destructive" in msg.lower()
    assert "auto-approve" in msg
    # Reply legend always present.
    assert "`y`" in msg and "`n`" in msg
    assert "/stop" in msg and "/help" in msg
    # No digit row when there are no menu options.
    assert "menu option" not in msg


def test_approval_with_menu_lists_digits():
    msg = approval_message(ApprovalCard(
        command="Bash(rm -rf /tmp)",
        summary="Remove dir.",
        menu_options=("1. Yes", "2. Yes always", "3. No"),
    ))
    assert "1. Yes" in msg
    assert "3. No" in msg
    # Digit legend includes 1 / 2 / 3.
    assert "1 / 2 / 3" in msg


def test_approval_risk_icon_changes():
    """Different risk levels show different icons."""
    msg_dest = approval_message(ApprovalCard(
        command="x", summary="x", risk="destructive",
    ))
    msg_med = approval_message(ApprovalCard(
        command="x", summary="x", risk="medium",
    ))
    msg_norm = approval_message(ApprovalCard(
        command="x", summary="x", risk="normal",
    ))
    assert "🛑" in msg_dest
    assert "⚠️" in msg_med
    assert "🟡" in msg_norm


def test_approval_truncates_long_summary():
    long = "x" * 5000
    msg = approval_message(ApprovalCard(command="cmd", summary=long))
    # The summary value gets clipped; the message itself stays well under 4kb.
    assert len(msg) < 2000
    assert "…" in msg


# --- completion -----------------------------------------------------------


def test_completion_includes_task_and_duration():
    msg = completion_message(CompletionCard(
        task="Add framed input box",
        summary="Built FramedInputSession with Tab-accept and 8 passing tests.",
        duration_s=263.0,
        profile="auto-approve",
    ))
    assert "task complete" in msg.lower()
    assert "Add framed input box" in msg
    assert "4m 23s" in msg  # 263 = 4m 23s
    assert "Built FramedInputSession" in msg
    # Reply nudge.
    assert "next task" in msg.lower()


def test_completion_omits_duration_when_zero():
    msg = completion_message(CompletionCard(
        task="x", summary="y", duration_s=0,
    ))
    assert "Duration" not in msg


def test_completion_omits_task_when_empty():
    msg = completion_message(CompletionCard(task="", summary="y"))
    assert "Task" not in msg
    assert "y" in msg


# --- escalation -----------------------------------------------------------


def test_escalation_shows_reason():
    msg = escalation_message(EscalationCard(
        command="Bash(curl example.com)",
        summary="Downloading something.",
        reason="no policy match",
        profile="restricted",
    ))
    assert "decision needed" in msg.lower()
    assert "Bash(curl example.com)" in msg
    assert "no policy match" in msg
    assert "restricted" in msg


# --- greeting / help / error ---------------------------------------------


def test_greeting_lists_commands():
    msg = greeting_message(bot_username="MyBot", profile="auto-approve")
    assert "Welcome" in msg
    for command in ("/help", "/status", "/panes", "/stop", "/pause", "/resume"):
        assert command in msg
    assert "auto-approve" in msg


def test_help_lists_all_commands():
    msg = help_message(profile="yolo", pending="Bash(ls)")
    for command in (
        "/help", "/status", "/panes", "/snapshot",
        "/stop", "/yes", "/no", "/cancel",
        "/pause", "/resume", "/profile",
    ):
        assert command in msg
    assert "yolo" in msg
    assert "Bash(ls)" in msg


def test_error_message_handles_empty_detail():
    msg = error_message("LLM timeout")
    assert "LLM timeout" in msg


def test_error_message_truncates_long_detail():
    msg = error_message("X", "y" * 5000)
    assert len(msg) < 1000
    assert "…" in msg
