"""Phase 5 — Memory + normalize_pattern + yolo learning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abs.memory import Memory, MemoryData, normalize_pattern


# --- normalize_pattern ----------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Bash(npm install)", "Bash(npm)"),
    ("Bash(npm install --save axios)", "Bash(npm)"),
    ("Bash(ls -la)", "Bash(ls)"),
    ("Bash(git push)", "Bash(git)"),
    ("Read(/x/y.py)", "Read"),
    ("Edit(src/main.py)", "Edit"),
    ("Write(out.txt)", "Write"),
    ("Glob(*.py)", "Glob"),
    ("LS(/etc)", "LS"),
    ("", None),
    (None, None),
    ("just a string", "just"),
])
def test_normalize_pattern(raw, expected):
    assert normalize_pattern(raw) == expected


# --- Memory: file lifecycle ----------------------------------------------

@pytest.fixture
def mem_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ABS_HOME", str(tmp_path))
    return tmp_path / "memory.json"


def test_memory_starts_with_defaults_when_no_file(mem_path):
    m = Memory()
    assert m.data.agent_name == "Sentinel"
    assert m.data.active_profile == "auto-approve"
    assert m.data.approved_patterns == {}


def test_memory_creates_file_on_first_write(mem_path):
    m = Memory()
    assert not mem_path.exists()
    m.set_agent_name("Aria")
    assert mem_path.exists()
    saved = json.loads(mem_path.read_text())
    assert saved["agent_name"] == "Aria"


def test_memory_round_trips_through_disk(mem_path):
    m1 = Memory()
    m1.learn(approve=True, pattern="Bash(npm)", profile="yolo")
    m1.learn(approve=False, pattern="Bash(curl)", profile="yolo")

    m2 = Memory()
    assert m2.is_approved("Bash(npm)", "yolo") is True
    assert m2.is_denied("Bash(curl)", "yolo") is True


def test_memory_handles_corrupted_json_gracefully(mem_path):
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    mem_path.write_text("not json at all }{ ")
    m = Memory()
    assert m.data.agent_name == "Sentinel"


# --- Yolo learning --------------------------------------------------------

def test_yolo_remembers_approved_pattern(mem_path):
    m = Memory()
    assert m.learn(approve=True, pattern="Bash(npm)", profile="yolo") is True
    assert m.is_approved("Bash(npm)", "yolo") is True


def test_yolo_remembers_denied_pattern(mem_path):
    m = Memory()
    assert m.learn(approve=False, pattern="Bash(curl)", profile="yolo") is True
    assert m.is_denied("Bash(curl)", "yolo") is True


def test_learn_is_idempotent(mem_path):
    m = Memory()
    assert m.learn(approve=True, pattern="X", profile="yolo") is True
    assert m.learn(approve=True, pattern="X", profile="yolo") is False
    assert len(m.data.approved_patterns["yolo"]) == 1


def test_changing_decision_removes_from_opposite_bucket(mem_path):
    m = Memory()
    m.learn(approve=True, pattern="X", profile="yolo")
    m.learn(approve=False, pattern="X", profile="yolo")
    assert m.is_approved("X", "yolo") is False
    assert m.is_denied("X", "yolo") is True


def test_destructive_patterns_never_learned(mem_path):
    m = Memory(destructive_patterns=[r"\brm\b", r"sudo"])
    stored = m.learn(approve=True, pattern="Bash(rm)", profile="yolo")
    assert stored is False
    assert m.is_approved("Bash(rm)", "yolo") is False


def test_forget_removes_pattern(mem_path):
    m = Memory()
    m.learn(approve=True, pattern="X", profile="yolo")
    assert m.forget("X", "yolo") is True
    assert m.is_approved("X", "yolo") is False
    assert m.forget("X", "yolo") is False


def test_learn_skips_empty_pattern(mem_path):
    m = Memory()
    assert m.learn(approve=True, pattern="", profile="yolo") is False
    assert m.learn(approve=True, pattern=None, profile="yolo") is False  # type: ignore[arg-type]


# --- Counts + metadata ----------------------------------------------------

def test_approved_count_tracks_growth(mem_path):
    m = Memory()
    assert m.approved_count("yolo") == 0
    m.learn(approve=True, pattern="A", profile="yolo")
    m.learn(approve=True, pattern="B", profile="yolo")
    assert m.approved_count("yolo") == 2


def test_set_last_session_persists(mem_path):
    m = Memory()
    info = {"id": "abc", "events": 14, "profile": "yolo"}
    m.set_last_session(info)
    m2 = Memory()
    assert m2.data.last_session == info


# --- Policy integration ---------------------------------------------------

def test_yolo_decide_short_circuits_on_approved_pattern(mem_path, policy_path):
    from abs.policy_engine import PolicyDecision, PolicyEngine
    from abs.prompt_classifier import ClassifiedPrompt, PromptType

    mem = Memory()
    mem.learn(approve=True, pattern="Bash(npm)", profile="yolo")

    engine = PolicyEngine(policy_path, profile_override="yolo", memory=mem)
    prompt = ClassifiedPrompt(
        type=PromptType.APPROVAL_YN,
        extracted_command="Bash(npm install --save axios)",
        context="Bash(npm install --save axios)",
    )
    result = engine.decide(prompt)
    assert result.decision == PolicyDecision.AUTO_YES
    assert "yolo memory" in result.reason


def test_yolo_decide_short_circuits_on_denied_pattern(mem_path, policy_path):
    from abs.policy_engine import PolicyDecision, PolicyEngine
    from abs.prompt_classifier import ClassifiedPrompt, PromptType

    mem = Memory()
    mem.learn(approve=False, pattern="Bash(curl)", profile="yolo")

    engine = PolicyEngine(policy_path, profile_override="yolo", memory=mem)
    prompt = ClassifiedPrompt(
        type=PromptType.APPROVAL_YN,
        extracted_command="Bash(curl https://x)",
        context="Bash(curl https://x)",
    )
    result = engine.decide(prompt)
    assert result.decision == PolicyDecision.AUTO_NO
    assert "yolo memory" in result.reason


def test_yolo_decide_skips_memory_for_destructive(mem_path, policy_path):
    """Even if the user approved 'Bash(rm)' (impossible — learning blocks it),
    a destructive prompt must NOT short-circuit through memory."""
    from abs.policy_engine import PolicyEngine
    from abs.prompt_classifier import ClassifiedPrompt, PromptType

    mem = Memory()
    mem.data.approved_patterns["yolo"] = ["Bash(rm)"]

    engine = PolicyEngine(policy_path, profile_override="yolo", memory=mem)
    prompt = ClassifiedPrompt(
        type=PromptType.APPROVAL_YN,
        extracted_command="Bash(rm -rf /tmp)",
        context="Bash(rm -rf /tmp)",
    )
    result = engine.decide(prompt)
    assert result.is_destructive is True
    assert "yolo memory" not in result.reason
