"""Policy engine: profile loading, pattern matching, decision precedence."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.policy_engine import PolicyDecision, PolicyEngine, PolicyEngineError
from src.prompt_classifier import ClassifiedPrompt, PromptType


# --- Profile loading --------------------------------------------------------

def test_loads_active_profile_by_default(policy):
    assert policy.active_profile_name == "default"


def test_profile_override(policy_path):
    p = PolicyEngine(policy_path, profile_override="yolo")
    assert p.active_profile_name == "yolo"


def test_unknown_profile_raises(policy_path):
    with pytest.raises(PolicyEngineError):
        PolicyEngine(policy_path, profile_override="does-not-exist")


def test_missing_policy_file_raises(tmp_path):
    with pytest.raises(PolicyEngineError):
        PolicyEngine(tmp_path / "nonexistent.yml")


# --- Decision precedence ----------------------------------------------------

def _prompt(cmd: str, ptype: PromptType = PromptType.APPROVAL_YN) -> ClassifiedPrompt:
    return ClassifiedPrompt(type=ptype, extracted_command=cmd, context=cmd)


def test_idle_prompt_returns_wait_local(policy):
    result = policy.decide(ClassifiedPrompt(type=PromptType.IDLE))
    assert result.decision == PolicyDecision.WAIT_LOCAL


def test_running_prompt_returns_wait_local(policy):
    result = policy.decide(ClassifiedPrompt(type=PromptType.RUNNING))
    assert result.decision == PolicyDecision.WAIT_LOCAL


def test_complete_prompt_returns_wait_local(policy):
    result = policy.decide(ClassifiedPrompt(type=PromptType.COMPLETE))
    assert result.decision == PolicyDecision.WAIT_LOCAL


def test_auto_deny_takes_precedence_over_approve(policy):
    """A `rm -rf` inside a safe `Bash()` should still deny."""
    result = policy.decide(_prompt("Bash(rm -rf /tmp/x)"))
    assert result.decision == PolicyDecision.AUTO_NO


def test_auto_approve_matches_bash_ls(policy):
    result = policy.decide(_prompt("Bash(ls -la)"))
    assert result.decision == PolicyDecision.AUTO_YES


def test_escalates_unknown_edit(policy):
    result = policy.decide(_prompt("Edit(src/foo.py)"))
    assert result.decision == PolicyDecision.ESCALATE_TELEGRAM


def test_falls_through_to_default_action(policy):
    """Something matching no pattern hits the default."""
    result = policy.decide(_prompt("totally unknown command"))
    assert result.decision == policy.default_action


# --- Profile-specific behavior ---------------------------------------------

def test_yolo_profile_auto_yes_for_unknown(policy_path):
    p = PolicyEngine(policy_path, profile_override="yolo")
    result = p.decide(_prompt("anything goes"))
    assert result.decision == PolicyDecision.AUTO_YES


def test_yolo_still_denies_rm_rf_root(policy_path):
    p = PolicyEngine(policy_path, profile_override="yolo")
    result = p.decide(_prompt("rm -rf /"))
    assert result.decision == PolicyDecision.AUTO_NO


def test_paranoid_profile_escalates_safe_ops(policy_path):
    p = PolicyEngine(policy_path, profile_override="paranoid")
    result = p.decide(_prompt("Bash(ls -la)"))
    # paranoid escalates by default and has no auto_approve.
    assert result.decision == PolicyDecision.ESCALATE_TELEGRAM


def test_restricted_escalates_unknown(policy_path):
    p = PolicyEngine(policy_path, profile_override="restricted")
    result = p.decide(_prompt("git commit -am 'stuff'"))
    assert result.decision == PolicyDecision.ESCALATE_TELEGRAM


# --- Decision metadata ------------------------------------------------------

def test_decision_includes_matched_pattern(policy):
    result = policy.decide(_prompt("Bash(rm -rf /x)"))
    assert result.matched_pattern is not None
    assert "rm" in result.matched_pattern.lower() or "rf" in result.matched_pattern.lower()


def test_decision_includes_reason_for_fallthrough(policy):
    result = policy.decide(_prompt("xxx unmatched xxx"))
    assert "default_action" in result.reason
