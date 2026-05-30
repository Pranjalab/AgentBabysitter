"""Classifier feedback log for abs.

Records auto-approved prompts, manual user interventions, and task
completions. The stored snapshots can be replayed to tune the detection
patterns in policy.yml or to retrain a future classifier.

Storage: ~/.abs/feedback/YYYY-MM-DD.jsonl  (one rolling file per day)

Entry schema:
    {
        "t":              ISO-8601 timestamp,
        "label":          "auto_approved" | "manual_intervened"
                          | "result" | "misclassified",
        "snapshot":       raw pane text at the time of the event,
        "classification": {type, command, options, pattern} (optional),
        "original_label": set when relabeling an existing entry (optional)
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from abs._paths import abs_home


# All valid labels (open-ended — callers may use others, these are canonical).
AUTO_APPROVED = "auto_approved"
MANUAL_INTERVENED = "manual_intervened"
RESULT = "result"
MISCLASSIFIED = "misclassified"


def feedback_dir() -> Path:
    d = abs_home() / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _today_file() -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return feedback_dir() / f"{day}.jsonl"


def save(
    snapshot: str,
    label: str,
    *,
    classification: dict[str, Any] | None = None,
) -> None:
    """Append one feedback entry to today's log. Never raises."""
    try:
        record: dict[str, Any] = {
            "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": label,
            "snapshot": snapshot,
        }
        if classification:
            record["classification"] = classification
        path = _today_file()
        with path.open("a", buffering=1, encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass  # feedback is best-effort — never crash the bridge


def load_recent(
    n: int = 30,
    labels: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``n`` entries newest-first, optionally filtered by label."""
    entries: list[dict[str, Any]] = []
    files = sorted(feedback_dir().glob("*.jsonl"), reverse=True)
    for path in files:
        if len(entries) >= n:
            break
        try:
            raw_lines = path.read_text("utf-8").splitlines()
        except OSError:
            continue
        for raw in reversed(raw_lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if labels and entry.get("label") not in labels:
                continue
            entry.setdefault("_file", path.name)
            entries.append(entry)
            if len(entries) >= n:
                break
    return entries


def mark_misclassified(entry: dict[str, Any]) -> None:
    """Re-save an entry with label='misclassified' (appends to today's log)."""
    record = {k: v for k, v in entry.items() if not k.startswith("_")}
    record["original_label"] = entry.get("label", "unknown")
    record["label"] = MISCLASSIFIED
    record["t"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save(record.get("snapshot", ""), MISCLASSIFIED, classification=record.get("classification"))


def feedback_summary() -> dict[str, int]:
    """Counts per label across all feedback files."""
    counts: dict[str, int] = {}
    for path in feedback_dir().glob("*.jsonl"):
        try:
            for raw in path.read_text("utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                label = rec.get("label", "unknown")
                counts[label] = counts.get(label, 0) + 1
        except OSError:
            continue
    return counts
