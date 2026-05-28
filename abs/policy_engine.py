"""Decide what to do with a classified prompt, based on `policy.yml`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from .prompt_classifier import ClassifiedPrompt, PromptType


class PolicyDecision(str, Enum):
    AUTO_YES = "auto_yes"
    AUTO_NO = "auto_no"
    ESCALATE_TELEGRAM = "escalate_telegram"
    WAIT_LOCAL = "wait_local"             # Do nothing, wait for the user


@dataclass
class DecisionResult:
    decision: PolicyDecision
    profile: str
    matched_pattern: str | None = None
    reason: str = ""
    # Phase 3:
    wait_interval_seconds: float = 0.0   # how long to wait before auto-firing
    is_destructive: bool = False         # destructive ops always pend, no wait


class PolicyEngineError(RuntimeError):
    pass


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    out = []
    for raw in patterns or []:
        try:
            out.append(re.compile(raw, re.IGNORECASE | re.MULTILINE))
        except re.error:
            continue
    return out


class PolicyEngine:
    """Evaluate a `ClassifiedPrompt` against the active policy profile.

    Evaluation order per profile:
        1. `auto_deny`       → AUTO_NO
        2. `auto_approve`    → AUTO_YES
        3. `escalate_to_telegram` → ESCALATE_TELEGRAM
        4. profile's `default_action`
    """

    def __init__(self, policy_path: str | Path, profile_override: str | None = None,
                 memory=None):
        self.policy_path = Path(policy_path)
        if not self.policy_path.exists():
            raise PolicyEngineError(f"policy file not found: {self.policy_path}")
        with self.policy_path.open() as f:
            self.config: dict = yaml.safe_load(f) or {}

        # Optional Memory instance (Phase 5). Used by the yolo profile to
        # short-circuit decisions on learned patterns.
        self.memory = memory

        profile_name = profile_override or self.config.get("active_profile", "default")
        profiles = self.config.get("profiles", {})
        if profile_name not in profiles:
            raise PolicyEngineError(
                f"profile {profile_name!r} not found in {self.policy_path}"
            )
        self.active_profile_name = profile_name
        self.profile = profiles[profile_name]

        self._auto_deny = _compile(self.profile.get("auto_deny", []))
        self._auto_approve = _compile(self.profile.get("auto_approve", []))
        self._escalate = _compile(self.profile.get("escalate_to_telegram", []))

        self.default_action = PolicyDecision(
            self.profile.get("default_action", "escalate_telegram")
        )

        # Phase 3: global destructive list + per-profile wait interval.
        self._destructive = _compile(self.config.get("destructive_patterns", []))
        self.wait_interval_seconds = float(
            self.profile.get("wait_interval_seconds", 0.0)
        )

    # --- Telegram config passthrough ---------------------------------------

    @property
    def detection_config(self) -> dict:
        return self.config.get("detection", {}) or {}

    @property
    def telegram_config(self) -> dict:
        return self.config.get("telegram", {}) or {}

    # --- Decision ----------------------------------------------------------

    def decide(self, prompt: ClassifiedPrompt) -> DecisionResult:
        if prompt.type in (PromptType.IDLE, PromptType.RUNNING, PromptType.COMPLETE):
            return DecisionResult(
                decision=PolicyDecision.WAIT_LOCAL,
                profile=self.active_profile_name,
                reason=f"prompt type {prompt.type.value} has no actionable decision",
            )

        haystack = self._haystack(prompt)
        destructive_match = self._first_match(self._destructive, haystack)
        is_destructive = destructive_match is not None

        # Phase 5: yolo profile short-circuits on learned patterns.
        if (
            self.active_profile_name == "yolo"
            and self.memory is not None
            and not is_destructive
        ):
            from abs.memory import normalize_pattern  # local import to avoid cycle
            normalized = normalize_pattern(prompt.extracted_command)
            if normalized:
                if self.memory.is_denied(normalized, "yolo"):
                    return DecisionResult(
                        decision=PolicyDecision.AUTO_NO,
                        profile=self.active_profile_name,
                        matched_pattern=normalized,
                        reason="yolo memory: denied",
                        wait_interval_seconds=0.0,
                        is_destructive=False,
                    )
                if self.memory.is_approved(normalized, "yolo"):
                    return DecisionResult(
                        decision=PolicyDecision.AUTO_YES,
                        profile=self.active_profile_name,
                        matched_pattern=normalized,
                        reason="yolo memory: approved",
                        wait_interval_seconds=self.wait_interval_seconds,
                        is_destructive=False,
                    )

        match = self._first_match(self._auto_deny, haystack)
        if match:
            return DecisionResult(
                decision=PolicyDecision.AUTO_NO,
                profile=self.active_profile_name,
                matched_pattern=match.re.pattern,
                reason="matched auto_deny",
                wait_interval_seconds=0.0,           # deny is always instant
                is_destructive=is_destructive,
            )

        match = self._first_match(self._auto_approve, haystack)
        if match:
            return DecisionResult(
                decision=PolicyDecision.AUTO_YES,
                profile=self.active_profile_name,
                matched_pattern=match.re.pattern,
                reason="matched auto_approve",
                wait_interval_seconds=self.wait_interval_seconds,
                is_destructive=is_destructive,
            )

        match = self._first_match(self._escalate, haystack)
        if match:
            return DecisionResult(
                decision=PolicyDecision.ESCALATE_TELEGRAM,
                profile=self.active_profile_name,
                matched_pattern=match.re.pattern,
                reason="matched escalate_to_telegram",
                wait_interval_seconds=0.0,
                is_destructive=is_destructive,
            )

        return DecisionResult(
            decision=self.default_action,
            profile=self.active_profile_name,
            reason="fell through to default_action",
            wait_interval_seconds=(
                self.wait_interval_seconds
                if self.default_action == PolicyDecision.AUTO_YES
                else 0.0
            ),
            is_destructive=is_destructive,
        )

    # --- Destructive-op detection (always wait for user, no countdown) ----

    def is_destructive(self, prompt: ClassifiedPrompt) -> bool:
        return self._first_match(self._destructive, self._haystack(prompt)) is not None

    @property
    def destructive_patterns(self) -> list[str]:
        """The raw destructive-pattern strings, for display / introspection."""
        return list(self.config.get("destructive_patterns", []) or [])

    # --- Helpers -----------------------------------------------------------

    @staticmethod
    def _haystack(prompt: ClassifiedPrompt) -> str:
        parts = [
            prompt.extracted_command or "",
            prompt.raw_text or "",
            prompt.context or "",
        ]
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _first_match(
        patterns: list[re.Pattern[str]], text: str
    ) -> re.Match[str] | None:
        for pat in patterns:
            m = pat.search(text)
            if m:
                return m
        return None
