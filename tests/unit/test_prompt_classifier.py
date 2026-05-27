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


def test_signature_distinguishes_identical_menus_with_different_commands():
    """Regression — Claude often asks for several approvals in a row
    with IDENTICAL menu text (``1. Yes / 2. Yes, allow all edits ... /
    3. No``) for different tool calls. The signature must differ so
    dispatch dedup doesn't swallow the later prompts and stop the flow.
    """
    from cldx.prompt_classifier import ClassifiedPrompt, PromptType

    same_menu = ("1. Yes",
                 "2. Yes, allow all edits during this session (shift+tab)",
                 "3. No")
    a = ClassifiedPrompt(
        type=PromptType.APPROVAL_MENU,
        extracted_command="Write(test/test_123/test_sin.py)",
        menu_options=same_menu,
    )
    b = ClassifiedPrompt(
        type=PromptType.APPROVAL_MENU,
        extracted_command="Write(test/test_123/test_sin.md)",
        menu_options=same_menu,
    )
    assert a.signature() != b.signature(), (
        "two Write calls with identical menus must NOT collide on signature"
    )


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


# --- completion verb rotation -------------------------------------------


@pytest.mark.parametrize("verb", [
    "Cogitated", "Cooked", "Baked", "Crunched", "Churned",
    "Pondered", "Mused", "Schemed", "Worked",
    "Sautéed",   # accent — \w may miss this on non-Unicode locales; \S survives.
])
def test_completion_matches_any_thinking_verb(verb):
    """Claude rotates the post-task indicator verb. All of these must
    classify as COMPLETE so chat-only replies surface to terminal +
    Telegram (this was the bug: short ``Hi`` replies vanished because
    only the literal 'Cogitated' was matched)."""
    from cldx.prompt_classifier import PromptClassifier
    snap = (
        "❯ Hi\n"
        "⏺ Hi! Need anything else?\n"
        f"✻ {verb} for 1s\n"
        "❯\n"
    )
    # Empty config — exercise the built-in fallback patterns only.
    classifier = PromptClassifier(detection_cfg={})
    p = classifier.classify(snap)
    assert p.type == PromptType.COMPLETE, (
        f"verb {verb!r} should still classify as COMPLETE via built-in fallback"
    )


@pytest.mark.parametrize("time_str", [
    "1s",
    "4.5s",
    "3m",
    "3m 5s",
    "1h 2m",
    "1h 30m 5s",
    "10m 0s",
])
def test_completion_matches_any_time_format(time_str):
    """Claude's "✻ <verb> for <time>" line uses several time shapes —
    single unit, compound, fractional seconds. All must classify as
    COMPLETE so longer tasks also surface their result panel."""
    from cldx.prompt_classifier import PromptClassifier
    snap = (
        "⏺ Done with the task.\n"
        f"✻ Cogitated for {time_str}\n"
        "❯\n"
    )
    classifier = PromptClassifier(detection_cfg={})
    p = classifier.classify(snap)
    assert p.type == PromptType.COMPLETE, (
        f"time format {time_str!r} should classify as COMPLETE"
    )


def test_completion_rejects_lookalikes_without_verb_or_time():
    """A bare star line or a "verb-less" entry must NOT classify as
    completion — that would false-fire on Claude's running indicator."""
    from cldx.prompt_classifier import PromptClassifier
    classifier = PromptClassifier(detection_cfg={})
    for line in ("✻ Working...", "✻ for 5s", "no star at all"):
        snap = f"⏺ x\n{line}\n❯\n"
        p = classifier.classify(snap)
        assert p.type != PromptType.COMPLETE, (
            f"line {line!r} must NOT be classified as COMPLETE"
        )


# --- priority: active prompts beat stale completion lines ---------------


def test_active_approval_beats_stale_completion_in_scrollback(classifier):
    """Reproduces the user's WebSearch bug.

    The pane has a LIVE WebSearch approval at the bottom AND a stale
    ``✻ Sautéed for 2s`` in the scrollback from a previous chat reply.
    Before the fix, the completion pattern won — silently absorbing
    the approval and rendering a "💬 Claude replied" panel instead of
    firing the auto-approve.
    """
    snapshot = (
        "❯ How are you\n"
        "⏺ I'm doing well, thanks for asking!\n"
        "✻ Sautéed for 2s\n"                  # ← stale completion line
        "❯ Do it\n"
        "⏺ Web Search(\"weather Indore\")\n"
        "\n"
        " Tool use\n"
        "\n"
        "   Web Search(\"weather Indore\")\n"
        "\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. Yes, and don't ask again\n"
        "   3. No\n"
        " Esc to cancel · Tab to amend\n"
    )
    p = classifier.classify(snapshot)
    assert p.type == PromptType.APPROVAL_MENU, (
        "Active approval must win over stale completion line. Got "
        f"{p.type.value!r} — the auto-approve flow won't fire."
    )
    assert p.tool is not None
    assert p.tool.name == "WebSearch"


def test_stale_approval_does_not_beat_real_completion(classifier):
    """The reorder must not flip the other direction either. When the
    pane shows a freshly-finished task (no live approval menu visible
    in the tail), the classifier should still return COMPLETE — even
    if a numbered list is sitting higher up in the scrollback for
    unrelated reasons (e.g. Claude listed three options as part of
    the prose answer)."""
    snapshot = (
        "❯ list three colors\n"
        "⏺ Sure!\n"
        "  1. Red\n"
        "  2. Blue\n"
        "  3. Green\n"
        "✻ Worked for 2s\n"
        "❯\n"
        "  ? for shortcuts · ← for agents\n"
    )
    p = classifier.classify(snapshot)
    assert p.type == PromptType.COMPLETE


def test_completion_builtin_works_with_empty_policy():
    """Even with no completion_patterns in policy.yml, the built-in
    fallback must keep detecting Claude's idle indicators."""
    from cldx.prompt_classifier import PromptClassifier
    classifier = PromptClassifier(detection_cfg={})
    p = classifier.classify("⏺ Done\n  ? for shortcuts · ← for agents\n")
    assert p.type == PromptType.COMPLETE
