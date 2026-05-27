"""Classifier behavior on real-shaped Claude Code snapshots."""

from __future__ import annotations

import pytest

from cldx.prompt_classifier import PromptType


def test_idle_snapshot_classifies_as_idle(snapshot, classifier):
    p = classifier.classify(snapshot("idle"))
    assert p.type == PromptType.IDLE


def test_running_snapshot_classifies_as_running(snapshot, classifier):
    p = classifier.classify(snapshot("running"))
    assert p.type == PromptType.RUNNING


def test_complete_snapshot_classifies_as_complete(snapshot, classifier):
    p = classifier.classify(snapshot("complete"))
    assert p.type == PromptType.COMPLETE


def test_safe_ls_yn_classifies_as_approval_yn(snapshot, classifier):
    p = classifier.classify(snapshot("safe_ls_yn"))
    assert p.type == PromptType.APPROVAL_YN
    assert p.extracted_command == "Bash(ls -la)"


def test_dangerous_rm_menu_classifies_as_menu(snapshot, classifier):
    p = classifier.classify(snapshot("dangerous_rm_menu"))
    assert p.type == PromptType.APPROVAL_MENU
    assert p.extracted_command == "Bash(rm -rf /tmp/build)"


def test_edit_menu_classifies_as_menu(snapshot, classifier):
    p = classifier.classify(snapshot("edit_menu"))
    assert p.type == PromptType.APPROVAL_MENU
    assert p.extracted_command == "Write(test/test_sample.py)"


def test_menu_options_extracted_in_order(snapshot, classifier):
    p = classifier.classify(snapshot("dangerous_rm_menu"))
    assert p.menu_options[0].startswith("1. Yes")
    assert "3. No" in p.menu_options[-1]
    assert len(p.menu_options) == 3


def test_yn_prompts_have_no_menu_options(snapshot, classifier):
    p = classifier.classify(snapshot("safe_ls_yn"))
    assert p.menu_options == ()


def test_idle_has_no_command(snapshot, classifier):
    p = classifier.classify(snapshot("idle"))
    assert p.extracted_command is None


def test_signature_stable_when_trailing_lines_change(snapshot, classifier):
    """Same logical prompt + different bottom lines → same signature."""
    base = snapshot("dangerous_rm_menu")
    later = base + "\n   (still waiting)\n"
    assert classifier.classify(base).signature() == classifier.classify(later).signature()


def test_signature_differs_for_different_prompts(snapshot, classifier):
    a = classifier.classify(snapshot("dangerous_rm_menu"))
    b = classifier.classify(snapshot("edit_menu"))
    assert a.signature() != b.signature()


def test_menu_options_ignore_unrelated_numbered_lines(classifier):
    """Numbered lines outside the prompt area shouldn't be captured."""
    snap = """
1. some preamble line
2. another preamble
3. final preamble

⏺ Bash(echo hi)
 Do you want to proceed?
 ❯ 1. Yes
   2. No
"""
    p = classifier.classify(snap)
    assert p.type == PromptType.APPROVAL_MENU
    # Only the post-anchor options should be picked up, not the preamble.
    assert p.menu_options == ("1. Yes", "2. No")


def test_classifier_returns_idle_for_empty_string(classifier):
    p = classifier.classify("")
    assert p.type == PromptType.IDLE


def test_classifier_handles_malformed_user_pattern(monkeypatch, policy):
    """A bad user-supplied regex should be skipped, not raise."""
    cfg = dict(policy.detection_config)
    cfg["approval_yn_patterns"] = list(cfg.get("approval_yn_patterns", [])) + ["[unterminated"]
    from cldx.prompt_classifier import PromptClassifier
    pc = PromptClassifier(detection_cfg=cfg)
    # Should not raise.
    p = pc.classify("Do you want to proceed? (y/n)")
    assert p.type == PromptType.APPROVAL_YN
