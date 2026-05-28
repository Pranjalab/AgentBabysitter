"""Policy engine: profile loading, pattern matching, decision precedence."""

from __future__ import annotations

import pytest

from abs.policy_engine import PolicyDecision, PolicyEngine, PolicyEngineError
from abs.prompt_classifier import ClassifiedPrompt, PromptType


# --- Fixtures: the bundled policy ships with `auto-approve` as default ----

@pytest.fixture
def default_engine(policy_path):
    """Engine pinned to the fine-grained legacy 'default' profile."""
    return PolicyEngine(policy_path, profile_override="default")


# --- Profile loading -------------------------------------------------------

def test_loads_active_profile_by_default(policy):
    """Phase 3: the shipped active_profile is now 'auto-approve'."""
    assert policy.active_profile_name == "auto-approve"


def test_profile_override(policy_path):
    p = PolicyEngine(policy_path, profile_override="yolo")
    assert p.active_profile_name == "yolo"


def test_unknown_profile_raises(policy_path):
    with pytest.raises(PolicyEngineError):
        PolicyEngine(policy_path, profile_override="does-not-exist")


def test_missing_policy_file_raises(tmp_path):
    with pytest.raises(PolicyEngineError):
        PolicyEngine(tmp_path / "nonexistent.yml")


# --- Decision precedence (use the legacy fine-grained 'default' profile) ---

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


def test_auto_deny_takes_precedence_over_approve(default_engine):
    """In the legacy 'default' profile, rm -rf inside Bash() denies."""
    result = default_engine.decide(_prompt("Bash(rm -rf /tmp/x)"))
    assert result.decision == PolicyDecision.AUTO_NO


def test_auto_approve_matches_bash_ls(default_engine):
    result = default_engine.decide(_prompt("Bash(ls -la)"))
    assert result.decision == PolicyDecision.AUTO_YES


def test_escalates_unknown_edit(default_engine):
    result = default_engine.decide(_prompt("Edit(src/foo.py)"))
    assert result.decision == PolicyDecision.ESCALATE_TELEGRAM


def test_falls_through_to_default_action(default_engine):
    """Something matching no pattern hits the profile's default."""
    result = default_engine.decide(_prompt("totally unknown command"))
    assert result.decision == default_engine.default_action


# --- Profile-specific behavior ---------------------------------------------

def test_auto_approve_profile_yes_for_anything(policy):
    """Phase 3: auto-approve says yes to everything (destructive flagged separately)."""
    result = policy.decide(_prompt("git commit -am stuff"))
    assert result.decision == PolicyDecision.AUTO_YES


def test_auto_approve_profile_carries_wait_interval(policy):
    result = policy.decide(_prompt("Bash(npm install)"))
    assert result.wait_interval_seconds == 2.0


def test_auto_approve_flags_destructive(policy):
    """rm -rf is auto-yes (no auto_deny in this profile) but flagged destructive."""
    result = policy.decide(_prompt("Bash(rm -rf /tmp/x)"))
    assert result.is_destructive is True


def test_yolo_profile_auto_yes_for_unknown(policy_path):
    p = PolicyEngine(policy_path, profile_override="yolo")
    result = p.decide(_prompt("anything goes"))
    assert result.decision == PolicyDecision.AUTO_YES


def test_yolo_profile_carries_wait_interval(policy_path):
    p = PolicyEngine(policy_path, profile_override="yolo")
    result = p.decide(_prompt("Bash(npm install)"))
    assert result.wait_interval_seconds == 2.0


def test_yolo_flags_destructive(policy_path):
    """yolo also auto-yes's destructive ops but flags them for the wait-bar bypass."""
    p = PolicyEngine(policy_path, profile_override="yolo")
    result = p.decide(_prompt("rm -rf /"))
    assert result.is_destructive is True


def test_paranoid_profile_escalates_safe_ops(policy_path):
    p = PolicyEngine(policy_path, profile_override="paranoid")
    result = p.decide(_prompt("Bash(ls -la)"))
    assert result.decision == PolicyDecision.ESCALATE_TELEGRAM


def test_restricted_escalates_unknown(policy_path):
    p = PolicyEngine(policy_path, profile_override="restricted")
    result = p.decide(_prompt("git commit -am 'stuff'"))
    assert result.decision == PolicyDecision.ESCALATE_TELEGRAM


def test_restricted_has_zero_wait(policy_path):
    p = PolicyEngine(policy_path, profile_override="restricted")
    result = p.decide(_prompt("anything"))
    assert result.wait_interval_seconds == 0.0


# --- Decision metadata -----------------------------------------------------

def test_decision_includes_matched_pattern(default_engine):
    result = default_engine.decide(_prompt("Bash(rm -rf /x)"))
    assert result.matched_pattern is not None
    assert "rm" in result.matched_pattern.lower() or "rf" in result.matched_pattern.lower()


def test_decision_includes_reason_for_fallthrough(default_engine):
    result = default_engine.decide(_prompt("xxx unmatched xxx"))
    assert "default_action" in result.reason


def test_decision_includes_is_destructive_default(policy):
    """Non-destructive prompts must have is_destructive=False."""
    result = policy.decide(_prompt("Read(/x/y.py)"))
    assert result.is_destructive is False
