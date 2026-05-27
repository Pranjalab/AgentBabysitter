"""Structural extractors for Claude Code's pane conventions.

Claude Code follows a strict visual convention for every conversation
turn::

    ❯ <user message>                ← what the user typed
    ⏺ <first action or response>   ← Claude's turn starts here
      <continuation lines>
      ⎿ <tool result>
    ⏺ <next action / final answer>
      <continuation lines>
    ✻ <verb> for <time>            ← turn ends here

This module gives us reliable, structural ways to pull out exactly
those slices — no pattern-list maintenance, no false positives from
"the line happens to contain a `Yes` in it".

Two public extractors:

- :func:`extract_assistant_step` returns Claude's current turn content
  — every ⏺ block between the latest user message and the trailing ✻,
  with ⏺ / ⎿ markers preserved for visual hierarchy.
- :func:`extract_pending_approval` returns ``(question, options)`` for
  the live approval prompt at the bottom of the pane, anchored on
  ``Do you want to proceed?`` followed by ``❯ 1. Yes``.

These complement (rather than replace) :mod:`cldx.prompt_classifier` —
the classifier still decides the `PromptType`; these functions extract
the human-meaningful payload once the state is known.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# --- regex building blocks ------------------------------------------------

# ``✻ <verb> for <time>`` — Claude Code's end-of-turn indicator. Verb
# rotates randomly (Cogitated, Cooked, Baked, Crunched, Sautéed, …);
# time can be ``1s`` / ``4.5s`` / ``3m 5s`` / ``1h 30m 5s``. ``\S+`` is
# used for the verb so accented letters survive a non-Unicode locale.
_END_INDICATOR_RE = re.compile(
    r"^✻\s+\S+\s+for\s+(?:\d+(?:\.\d+)?\s*[smhd]\s*)+"
)

# A *submitted* user line: starts with ``❯`` then visible content.
# Excludes the empty input area (``❯`` alone) and menu options
# (``❯ 1. Yes``) — those aren't user prose.
_SUBMITTED_USER_RE = re.compile(r"^❯\s+\S")
_MENU_OPTION_RE = re.compile(r"^❯\s+\d+\.\s")

# Approval-prompt anchor: appears immediately above the ``❯ 1. Yes`` line.
_APPROVAL_QUESTION_RE = re.compile(r"^\s*Do you want to proceed\?", re.IGNORECASE)
_APPROVAL_FIRST_OPTION_RE = re.compile(r"^\s*❯\s*1\.\s*Yes", re.IGNORECASE)


# --- public API -----------------------------------------------------------


def extract_assistant_step(snapshot: str) -> str:
    """Return Claude's current turn content from a pane snapshot.

    Algorithm (purely structural — no fragile keyword lists):

    1. Find the most recent ``✻ <verb> for <time>`` line. If none is
       present, treat the end of the snapshot as the boundary (Claude
       is still working).
    2. Find the most recent submitted user message (``❯ <text>``) *before*
       that boundary.
    3. Walk forward from just after the user message. Return everything
       from the FIRST ``⏺`` line up to (but not including) the ``✻``
       line. The ⏺ markers and indented ⎿ continuations are preserved
       so the panel keeps Claude Code's visual hierarchy.

    Returns an empty string when there's no ⏺ content in the relevant
    slice (e.g. the user just typed and Claude hasn't started yet).
    """
    if not snapshot:
        return ""
    lines = snapshot.splitlines()
    n = len(lines)

    # 1. Locate the trailing ✻ line; default to end-of-snapshot.
    end_idx = n
    for i in range(n - 1, -1, -1):
        if _END_INDICATOR_RE.match(lines[i].lstrip()):
            end_idx = i
            break

    # 2. Locate the latest submitted user message before end_idx.
    user_idx = -1
    for i in range(end_idx - 1, -1, -1):
        stripped = lines[i].lstrip()
        if not _SUBMITTED_USER_RE.match(stripped):
            continue
        if _MENU_OPTION_RE.match(stripped):
            continue
        user_idx = i
        break

    # 3. Find the FIRST ⏺ line strictly after the user message.
    start_idx: int | None = None
    for i in range(user_idx + 1, end_idx):
        if lines[i].lstrip().startswith("⏺"):
            start_idx = i
            break
    if start_idx is None:
        return ""

    return "\n".join(lines[start_idx:end_idx]).rstrip()


@dataclass(frozen=True)
class PendingApproval:
    """The structural shape of a Claude Code approval prompt."""
    question: str               # e.g. "Do you want to proceed?"
    options: tuple[str, ...]    # ("1. Yes", "2. ...", "3. No")


def extract_pending_approval(snapshot: str) -> PendingApproval | None:
    """Return the live approval shown at the bottom of the pane, if any.

    Approvals in Claude Code have a stable structural shape::

        Do you want to proceed?
        ❯ 1. Yes
          2. <something>
          3. No

    This function anchors on the ``Do you want to proceed?`` line and
    the immediately-following ``❯ 1. Yes`` option, then collects every
    subsequent numbered option line. Returns ``None`` if the anchor
    pair isn't present (no live approval pending).
    """
    if not snapshot:
        return None
    lines = snapshot.splitlines()
    n = len(lines)
    # Walk from the bottom — the live approval, if any, is near the end.
    for i in range(n - 1, -1, -1):
        if not _APPROVAL_QUESTION_RE.match(lines[i]):
            continue
        # The ``❯ 1. Yes`` line must appear within a few lines below.
        for j in range(i + 1, min(i + 5, n)):
            if _APPROVAL_FIRST_OPTION_RE.match(lines[j]):
                options = _collect_options_from(lines, j)
                question = lines[i].strip()
                return PendingApproval(
                    question=question, options=tuple(options),
                )
        # ``Do you want to proceed?`` without a ``❯ 1. Yes`` below it
        # is unusual — keep scanning further back just in case.
    return None


def _collect_options_from(lines: list[str], first_idx: int) -> list[str]:
    """Pull contiguous ``N. text`` lines starting at ``first_idx``.

    Strips the ``❯`` caret from the first option (only the active option
    has it). Stops at the first non-option line.
    """
    options: list[str] = []
    pat = re.compile(r"^\s*(?:❯\s*)?(\d+)\.\s*(.+?)\s*$")
    for line in lines[first_idx:]:
        m = pat.match(line)
        if not m:
            break
        options.append(f"{m.group(1)}. {m.group(2)}")
    return options
