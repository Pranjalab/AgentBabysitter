"""End-to-end: snapshot → classify → policy.decide → expected action.

These tests cover the chain that drives every decision the bridge makes.
They use the project's shipped `config/policy.yml` so any policy change
that breaks the contract surfaces here.
"""

from __future__ import annotations

import pytest

from src.policy_engine import PolicyDecision, PolicyEngine
from src.prompt_classifier import PromptClassifier, PromptType


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("snap_name, expected_type, expected_decision", [
    ("safe_ls_yn",          PromptType.APPROVAL_YN,   PolicyDecision.AUTO_YES),
    ("dangerous_rm_menu",   PromptType.APPROVAL_MENU, PolicyDecision.AUTO_NO),
    ("edit_menu",           PromptType.APPROVAL_MENU, PolicyDecision.ESCALATE_TELEGRAM),
    ("running",             PromptType.RUNNING,       PolicyDecision.WAIT_LOCAL),
    ("idle",                PromptType.IDLE,          PolicyDecision.WAIT_LOCAL),
    ("complete",            PromptType.COMPLETE,      PolicyDecision.WAIT_LOCAL),
])
def test_default_profile_pipeline(snapshot, classifier, policy,
                                   snap_name, expected_type, expected_decision):
    prompt = classifier.classify(snapshot(snap_name))
    assert prompt.type == expected_type, f"{snap_name}: classified as {prompt.type}"
    decision = policy.decide(prompt)
    assert decision.decision == expected_decision, (
        f"{snap_name}: expected {expected_decision.value}, "
        f"got {decision.decision.value} (matched {decision.matched_pattern!r})"
    )


def test_yolo_profile_approves_edit(snapshot, policy_path):
    policy = PolicyEngine(policy_path, profile_override="yolo")
    classifier = PromptClassifier(detection_cfg=policy.detection_config)
    prompt = classifier.classify(snapshot("edit_menu"))
    assert policy.decide(prompt).decision == PolicyDecision.AUTO_YES


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
