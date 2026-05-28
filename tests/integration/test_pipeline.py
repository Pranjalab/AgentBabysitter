"""End-to-end: snapshot → classify → policy.decide → expected action.

These tests cover the chain that drives every decision the bridge makes.
They run against each shipped profile so any policy regression surfaces here.
"""

from __future__ import annotations

import pytest

from abs.policy_engine import PolicyDecision, PolicyEngine
from abs.prompt_classifier import PromptClassifier, PromptType


pytestmark = pytest.mark.integration


# --- Legacy "default" profile (fine-grained allow/deny rules) -------------

@pytest.mark.parametrize("snap_name, expected_type, expected_decision", [
    ("safe_ls_yn",          PromptType.APPROVAL_YN,   PolicyDecision.AUTO_YES),
    ("dangerous_rm_menu",   PromptType.APPROVAL_MENU, PolicyDecision.AUTO_NO),
    ("edit_menu",           PromptType.APPROVAL_MENU, PolicyDecision.ESCALATE_TELEGRAM),
    ("running",             PromptType.RUNNING,       PolicyDecision.WAIT_LOCAL),
    ("idle",                PromptType.IDLE,          PolicyDecision.WAIT_LOCAL),
    ("complete",            PromptType.COMPLETE,      PolicyDecision.WAIT_LOCAL),
])
def test_default_profile_pipeline(snapshot, policy_path,
                                   snap_name, expected_type, expected_decision):
    policy = PolicyEngine(policy_path, profile_override="default")
    classifier = PromptClassifier(detection_cfg=policy.detection_config)
    prompt = classifier.classify(snapshot(snap_name))
    assert prompt.type == expected_type, f"{snap_name}: classified as {prompt.type}"
    decision = policy.decide(prompt)
    assert decision.decision == expected_decision, (
        f"{snap_name}: expected {expected_decision.value}, "
        f"got {decision.decision.value} (matched {decision.matched_pattern!r})"
    )


# --- Phase 3 "auto-approve" profile (default) ------------------------------

@pytest.mark.parametrize("snap_name, expected_type, expected_decision, expected_destructive", [
    ("safe_ls_yn",          PromptType.APPROVAL_YN,   PolicyDecision.AUTO_YES,   False),
    ("dangerous_rm_menu",   PromptType.APPROVAL_MENU, PolicyDecision.AUTO_YES,   True),   # destructive flag
    ("edit_menu",           PromptType.APPROVAL_MENU, PolicyDecision.AUTO_YES,   False),
    ("running",             PromptType.RUNNING,       PolicyDecision.WAIT_LOCAL, False),
    ("idle",                PromptType.IDLE,          PolicyDecision.WAIT_LOCAL, False),
    ("complete",            PromptType.COMPLETE,      PolicyDecision.WAIT_LOCAL, False),
])
def test_auto_approve_profile_pipeline(snapshot, policy, classifier,
                                        snap_name, expected_type,
                                        expected_decision, expected_destructive):
    prompt = classifier.classify(snapshot(snap_name))
    assert prompt.type == expected_type
    decision = policy.decide(prompt)
    assert decision.decision == expected_decision
    assert decision.is_destructive is expected_destructive


def test_yolo_profile_approves_edit(snapshot, policy_path):
    policy = PolicyEngine(policy_path, profile_override="yolo")
    classifier = PromptClassifier(detection_cfg=policy.detection_config)
    prompt = classifier.classify(snapshot("edit_menu"))
    result = policy.decide(prompt)
    assert result.decision == PolicyDecision.AUTO_YES
    assert result.wait_interval_seconds == 2.0


def test_restricted_profile_escalates_everything(snapshot, policy_path):
    policy = PolicyEngine(policy_path, profile_override="restricted")
    classifier = PromptClassifier(detection_cfg=policy.detection_config)
    for name in ("safe_ls_yn", "dangerous_rm_menu", "edit_menu"):
        prompt = classifier.classify(snapshot(name))
        result = policy.decide(prompt)
        assert result.decision == PolicyDecision.ESCALATE_TELEGRAM
        assert result.wait_interval_seconds == 0.0


def test_paranoid_profile_escalates_safe_op(snapshot, policy_path):
    policy = PolicyEngine(policy_path, profile_override="paranoid")
    classifier = PromptClassifier(detection_cfg=policy.detection_config)
    prompt = classifier.classify(snapshot("safe_ls_yn"))
    assert policy.decide(prompt).decision == PolicyDecision.ESCALATE_TELEGRAM


def test_signature_stable_across_classifier_invocations(snapshot, classifier):
    """Critical for dedupe — same snapshot must produce the same signature."""
    snap = snapshot("dangerous_rm_menu")
    sig1 = classifier.classify(snap).signature()
    sig2 = classifier.classify(snap).signature()
    assert sig1 == sig2
