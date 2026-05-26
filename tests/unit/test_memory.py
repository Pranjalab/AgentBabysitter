"""Phase 5 — memory.py: ~/.claudex/memory.json read/write + yolo learning.

Yolo pattern granularity is `tool + first arg token`. Destructive
patterns are NEVER added to approved_patterns, even on user approval.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase5


@pytest.mark.skip(reason="Phase 5 — memory not implemented yet")
def test_memory_creates_file_on_first_write(tmp_path):
    """memory.set(...) must create ~/.claudex/memory.json if absent."""


@pytest.mark.skip(reason="Phase 5")
def test_normalize_pattern_collapses_first_arg():
    """`Bash(npm install)` and `Bash(npm install --save axios)` → `Bash(npm)`."""


@pytest.mark.skip(reason="Phase 5")
def test_yolo_remembers_approved_pattern():
    """After user approves once, memory.is_approved(pattern, "yolo") → True."""


@pytest.mark.skip(reason="Phase 5")
def test_yolo_remembers_denied_pattern():
    """After user denies once, memory.is_denied(pattern, "yolo") → True."""


@pytest.mark.skip(reason="Phase 5")
def test_destructive_patterns_never_learned():
    """Calling memory.learn(approve, 'Bash(rm)') must be a no-op."""


@pytest.mark.skip(reason="Phase 5")
def test_memory_survives_restart(tmp_path):
    """Write → close → reopen → values persist."""


@pytest.mark.skip(reason="Phase 5")
def test_memory_handles_corrupted_json_gracefully(tmp_path):
    """If memory.json is broken, start fresh instead of crashing."""
