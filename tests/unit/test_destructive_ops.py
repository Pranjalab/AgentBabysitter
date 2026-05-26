"""Phase 3 — destructive operation detection.

Patterns covered: `rm`, `rmdir`, `unlink`, `mv` over root, `DROP TABLE`,
`git reset --hard`, `git push --force`, `chmod 777`, `> ` redirection
that overwrites, sudo, fork bombs, etc.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase3


@pytest.mark.skip(reason="Phase 3 — destructive detector not extracted yet")
@pytest.mark.parametrize("cmd", [
    "rm -rf /tmp/build",
    "rm -rf ~",
    "unlink /etc/foo",
    "DROP TABLE users",
    "git reset --hard HEAD~3",
    "git push --force origin main",
    "chmod 777 /etc/passwd",
    "sudo rm -rf /",
    ":(){ :|:& };:",
])
def test_destructive_patterns_detected(cmd):
    """The destructive-op detector must flag every pattern above."""


@pytest.mark.skip(reason="Phase 3")
@pytest.mark.parametrize("cmd", [
    "ls -la",
    "cat /etc/hostname",
    "echo hi",
    "git status",
    "npm install",
    "cp foo bar",            # cp is not destructive
])
def test_safe_patterns_not_flagged(cmd):
    """Non-destructive commands must not be flagged."""
