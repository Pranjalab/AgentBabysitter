"""Phase 3 — destructive operation detection.

Patterns covered: `rm -rf`, `unlink`, `DROP TABLE`, `git reset --hard`,
`git push --force`, `chmod 777`, fork bombs, sudo, etc. These bypass the
wait bar entirely and pend until a human approves them.
"""

from __future__ import annotations

import pytest

from cldx.policy_engine import PolicyEngine
from cldx.prompt_classifier import ClassifiedPrompt, PromptType


def _prompt(cmd: str) -> ClassifiedPrompt:
    return ClassifiedPrompt(
        type=PromptType.APPROVAL_YN,
        extracted_command=cmd,
        context=cmd,
    )


@pytest.mark.parametrize("cmd", [
    "rm -rf /tmp/build",
    "rm -rf ~/project",
    "Bash(rm -rf /tmp/x)",
    "unlink /etc/foo",
    "Bash(unlink x.txt)",
    "DROP TABLE users",
    "DROP DATABASE production",
    "TRUNCATE TABLE logs",
    "git reset --hard HEAD~3",
    "git push --force origin main",
    "git push -f origin main",
    "git clean -fd",
    "chmod 777 /etc/passwd",
    "chown -R nobody /",
    "sudo apt remove everything",
    ":(){ :|:& };:",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
])
def test_destructive_patterns_detected(policy, cmd):
    """Every command in the table must be flagged destructive."""
    assert policy.is_destructive(_prompt(cmd)) is True, (
        f"{cmd!r} should be flagged destructive"
    )


@pytest.mark.parametrize("cmd", [
    "ls -la",
    "cat /etc/hostname",
    "echo hi",
    "git status",
    "git commit -am 'wip'",
    "npm install",
    "cp foo bar",
    "Bash(ls -la)",
    "Read(/x/y.py)",
    "Edit(src/main.py)",
    "Write(out.txt)",
])
def test_safe_patterns_not_flagged(policy, cmd):
    """Non-destructive commands must not trigger the destructive flag."""
    assert policy.is_destructive(_prompt(cmd)) is False, (
        f"{cmd!r} should not be flagged destructive"
    )


def test_destructive_flag_propagates_into_decision(policy):
    """`decide()` must expose is_destructive on the result."""
    result = policy.decide(_prompt("Bash(rm -rf /tmp/x)"))
    assert result.is_destructive is True


def test_destructive_patterns_list_is_introspectable(policy):
    """For UI / debugging, the raw pattern list is reachable."""
    patterns = policy.destructive_patterns
    assert isinstance(patterns, list)
    assert any("rm" in p for p in patterns)
