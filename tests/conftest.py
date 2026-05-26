"""Shared pytest fixtures.

`snapshot(name)` loads a captured Claude Code pane snapshot from the
`tests/fixtures/snapshots/` directory. `policy` returns a fully-loaded
PolicyEngine bound to the project's shipped `config/policy.yml`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project root importable as `src.*` during tests.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.policy_engine import PolicyEngine  # noqa: E402
from src.prompt_classifier import PromptClassifier  # noqa: E402


FIXTURES = REPO_ROOT / "tests" / "fixtures"
SNAPSHOTS = FIXTURES / "snapshots"


@pytest.fixture
def snapshot():
    """Returns a loader function: `snapshot("safe_ls_yn")` → file text."""

    def _load(name: str) -> str:
        path = SNAPSHOTS / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(f"no snapshot fixture: {path}")
        return path.read_text()

    return _load


@pytest.fixture
def policy_path() -> Path:
    return REPO_ROOT / "config" / "policy.yml"


@pytest.fixture
def policy(policy_path) -> PolicyEngine:
    return PolicyEngine(policy_path)


@pytest.fixture
def classifier(policy) -> PromptClassifier:
    return PromptClassifier(detection_cfg=policy.detection_config)
