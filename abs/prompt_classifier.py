"""Classify the current state of a Claude Code pane snapshot."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from abs.tool_call import ToolCall  # circular-safe under TYPE_CHECKING


class PromptType(str, Enum):
    APPROVAL_YN = "approval_yn"           # "(y/n)" style
    APPROVAL_MENU = "approval_menu"       # "❯ 1. Yes  2. No"
    TEXT_INPUT = "text_input"             # Free-form text expected
    RUNNING = "running"                   # Claude is working
    IDLE = "idle"                         # Nothing happening
    COMPLETE = "complete"                 # Task done


@dataclass
class ClassifiedPrompt:
    type: PromptType
    raw_text: str = ""                    # The matched prompt block
    extracted_command: str | None = None  # e.g. "npm install" or "rm -rf dist"
    context: str = ""                     # Last ~10 lines, for human review
    matched_pattern: str | None = None    # Which pattern fired
    menu_options: tuple[str, ...] = ()    # ("1. Yes", "2. ...", "3. No") for menus
    # Typed view of the tool call this approval is about. Populated when
    # the snapshot contains a recognisable ``⏺ Tool(args)`` line. ``None``
    # for plain Y/N prompts that don't reference a tool (e.g. "Continue?").
    tool: "ToolCall | None" = None

    def signature(self) -> str:
        """Stable fingerprint for deduping repeated detections of the same prompt.

        The signature has to satisfy two competing properties:

        1. **Stable across redraws.** Claude Code repaints its TUI on a
           timer (cursor blink, "Cogitated for Ns" counter); the same
           logical prompt can produce slightly different snapshots from
           frame to frame.
        2. **Distinguishes consecutive prompts.** Claude often asks for
           several approvals in a row with identical menu options
           (``1. Yes / 2. Yes, allow all edits ... / 3. No``) for
           different tool calls (``Write(a.py)``, ``Write(b.md)``).
           Successive prompts MUST hash differently or dispatch dedup
           swallows the later ones and the auto-approval flow stops.

        We assemble the fingerprint from ``(type, extracted_command,
        menu_options)``. ``extracted_command`` is what differs between
        consecutive approvals; menu options keep the signature stable
        under cosmetic redraws. Empty parts are omitted so a Y/N prompt
        without a menu still gets a useful key.
        """
        parts = [self.type.value]
        if self.tool is not None:
            parts.append(f"{self.tool.name}({self.tool.args})")
        elif self.extracted_command:
            parts.append(self.extracted_command)
        if self.menu_options:
            parts.append("|".join(self.menu_options))
        if (
            self.tool is None
            and not self.extracted_command
            and not self.menu_options
        ):
            # Last-ditch fallback: hash the raw text so we still dedup.
            parts.append(self.raw_text)
        return "|".join(parts)


# Generous box-drawing characters Claude Code uses around tool-call panels.
_BOX_CHARS = "│┃┆┇║╎╏▏▕▎▍"
_BOX_PREFIX_RE = re.compile(rf"^\s*[{_BOX_CHARS}]\s?")

# Recognised tool-call lines like `Bash(npm install)`, `Edit(foo.py)`.
_TOOL_CALL_RE = re.compile(
    r"\b(?P<tool>Bash|Read|Edit|Write|Glob|Grep|LS|WebFetch|Run)\b"
    r"\s*\(\s*(?P<arg>[^)]*?)\s*\)"
)

# "Run: <something>" / "Run command: <something>" style lines.
_RUN_HINT_RE = re.compile(
    r"(?:^|\b)(?:Run(?:\s+command)?|Execute|Command)\s*[:\-]\s*(?P<cmd>.+?)\s*$",
    re.MULTILINE,
)

# Menu option lines like "❯ 1. Yes" or "   3. No".
_MENU_OPTION_RE = re.compile(r"^\s*[❯>]?\s*(\d+)\.\s*(.+?)\s*$")

# Claude Code approval UI — two structural anchors used for Gate 1 detection.
#
# Both must be present IN ORDER (cursor strictly before footer) for the pane
# to be classified as APPROVAL_MENU.  A single anchor anywhere in the window
# is NOT enough — stale scrollback can contain either one alone.
#
# _APPROVAL_CURSOR_LINE_RE: the ``❯`` selection cursor on option 1.
#   Only present while a menu is live; never appears in prose or scrollback.
#   Note: ``❯`` (U+276F) is distinct from ``>`` — we match both defensively.
#
# _APPROVAL_FOOTER_RE: the ``Esc to cancel`` footer.
#   Always the LAST line of the approval choices block.  Task-list items
#   that Claude shows as context appear AFTER this line (outside the block).
_APPROVAL_CURSOR_LINE_RE = re.compile(r"^\s*[❯>]\s*\d+\.", re.MULTILINE)
_APPROVAL_FOOTER_RE = re.compile(r"Esc to cancel", re.IGNORECASE)


def _compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled = []
    for raw in patterns or []:
        try:
            compiled.append(re.compile(raw, re.MULTILINE))
        except re.error:
            # Skip malformed user patterns rather than crashing the watcher.
            continue
    return compiled


@dataclass
class _DetectionPatterns:
    approval_yn: list[re.Pattern[str]] = field(default_factory=list)
    approval_menu: list[re.Pattern[str]] = field(default_factory=list)
    text_input: list[re.Pattern[str]] = field(default_factory=list)
    completion: list[re.Pattern[str]] = field(default_factory=list)
    running: list[re.Pattern[str]] = field(default_factory=list)


class PromptClassifier:
    """Classify pane snapshots into a `ClassifiedPrompt`.

    Pattern lists come from `policy.yml -> detection`, so users can adapt
    when Claude Code's UI changes without editing this file.
    """

    # Built-in completion signals — applied IN ADDITION to whatever the
    # user's policy.yml specifies. This protects users whose ``~/.abs/
    # config/policy.yml`` predates the verb-rotation fix.
    #
    # The structural shape of Claude Code's end-of-turn line is:
    #
    #     ✻ <verb> for <time>
    #
    # where:
    #   - ``<verb>`` rotates randomly across many words, sometimes with
    #     accented letters (Cogitated, Cooked, Baked, Crunched, Churned,
    #     Pondered, Mused, Worked, Sautéed, …). We use ``\S+`` instead
    #     of ``\w+`` so locale-sensitive Unicode word boundaries don't
    #     cost us a match.
    #   - ``<time>`` can be a single unit (``1s``, ``4.5s``, ``3m``) OR
    #     a compound like ``3m 5s`` / ``1h 2m`` / ``1h 30m 5s``. The
    #     repeating group ``(\d+\.?\d*\s*[smhd]\s*)+`` covers all forms.
    _BUILTIN_COMPLETION_PATTERNS: tuple[str, ...] = (
        r"✻\s+\S+\s+for\s+(?:\d+(?:\.\d+)?\s*[smhd]\s*)+",
        r"\? for shortcuts",
    )

    def __init__(
        self,
        detection_cfg: dict | None = None,
        tail_lines: int = 80,
        detection_lines: int = 40,
    ):
        cfg = detection_cfg or {}
        self.tail_lines = tail_lines
        # Approval/running/completion patterns are matched against the last
        # ``detection_lines`` only — keeps stale scrollback from re-firing.
        # ``_extract_command`` still searches the full ``tail_lines`` window
        # so the ``⏺ Bash(...)`` indicator is found even when it's well
        # above the live menu.
        self.detection_lines = detection_lines
        # Merge user patterns with the built-in fallbacks. Dedup so a user
        # who already listed our generic pattern doesn't compile it twice.
        user_completion = list(cfg.get("completion_patterns", []))
        for builtin in self._BUILTIN_COMPLETION_PATTERNS:
            if builtin not in user_completion:
                user_completion.append(builtin)
        self.patterns = _DetectionPatterns(
            approval_yn=_compile_patterns(cfg.get("approval_yn_patterns", [])),
            approval_menu=_compile_patterns(cfg.get("approval_menu_patterns", [])),
            text_input=_compile_patterns(cfg.get("text_input_patterns", [])),
            completion=_compile_patterns(user_completion),
            running=_compile_patterns(cfg.get("running_patterns", [])),
        )

    # --- Public API ---------------------------------------------------------

    def classify(self, snapshot: str) -> ClassifiedPrompt:
        # ``tail`` is the wide window — used for command extraction and
        # context display so we can reach ``⏺ Bash(...)`` lines that sit
        # well above the live menu.
        # ``dtail`` is the narrow detection window — pattern matching runs
        # here only, preventing stale ``❯ 1. Yes`` entries from previous
        # (already answered) approvals that are still visible in scrollback
        # from triggering false positives.
        tail = self._tail(snapshot, self.tail_lines)
        dtail = self._tail(snapshot, self.detection_lines)
        context = tail

        # Order: ACTIVE PROMPTS > completion > running > idle.
        #
        # Why active first: when Claude is asking for approval, the pane
        # tail contains BOTH the live ``❯ 1. Yes`` menu AND (often) a
        # stale ``✻ <verb> for Ns`` line from a *previous* chat reply
        # that's still visible in the scrollback. If completion wins,
        # we silently absorb the approval and the auto-approve never
        # fires. The live approval is the actionable state, so it must
        # take priority.

        # Gate 1 — Approval menu (structural two-signal ordered check).
        #
        # BOTH signals must be present in order:
        #   1. ``❯ <digit>.``    — live cursor on option 1 (never in prose/scrollback)
        #   2. ``Esc to cancel`` — footer that closes the choices block
        #
        # Option extraction is BOUNDED between these two indices: anything
        # that appears AFTER ``Esc to cancel`` (task-plan items Claude
        # sometimes appends as context) is excluded automatically.
        lines_dtail = dtail.splitlines()
        approval_idxs = self._detect_approval_two_signal(lines_dtail)
        if approval_idxs is not None:
            cursor_idx, footer_idx = approval_idxs
            from abs.tool_call import parse_tool_call as _parse_tool
            return ClassifiedPrompt(
                type=PromptType.APPROVAL_MENU,
                raw_text="\n".join(lines_dtail[cursor_idx : footer_idx + 1]),
                extracted_command=self._extract_command(tail),
                context=context,
                matched_pattern="structural:❯digit+Esc_to_cancel",
                menu_options=self._extract_menu_options_bounded(
                    lines_dtail, cursor_idx, footer_idx
                ),
                tool=_parse_tool(tail),
            )

        match = self._first_match(self.patterns.approval_yn, dtail)
        if match:
            from abs.tool_call import parse_tool_call as _parse_tool
            return ClassifiedPrompt(
                type=PromptType.APPROVAL_YN,
                raw_text=match.group(0),
                extracted_command=self._extract_command(tail),
                context=context,
                matched_pattern=match.re.pattern,
                tool=_parse_tool(tail),
            )

        match = self._first_match(self.patterns.text_input, dtail)
        if match:
            return ClassifiedPrompt(
                type=PromptType.TEXT_INPUT,
                raw_text=match.group(0),
                context=context,
                matched_pattern=match.re.pattern,
            )

        match = self._first_match(self.patterns.completion, dtail)
        if match:
            return ClassifiedPrompt(
                type=PromptType.COMPLETE,
                raw_text=match.group(0),
                context=context,
                matched_pattern=match.re.pattern,
            )

        match = self._first_match(self.patterns.running, dtail)
        if match:
            return ClassifiedPrompt(
                type=PromptType.RUNNING,
                raw_text=match.group(0),
                context=context,
                matched_pattern=match.re.pattern,
            )

        return ClassifiedPrompt(type=PromptType.IDLE, context=context)

    # --- Helpers ------------------------------------------------------------

    @staticmethod
    def _tail(snapshot: str, n: int) -> str:
        lines = snapshot.splitlines()
        return "\n".join(lines[-n:])

    @staticmethod
    def _first_match(patterns: list[re.Pattern[str]], text: str) -> re.Match | None:
        for pat in patterns:
            m = pat.search(text)
            if m:
                return m
        return None

    @staticmethod
    def _extract_command(tail: str) -> str | None:
        """Best-effort: pull out the command/argument Claude is asking about.

        Scans top-down so the result is stable across redraws — the first
        `⏺ Bash(...)` indicator in the snapshot wins, even if the pane scrolls
        and exposes more text below.
        """
        # 1. `Bash(...)`, `Edit(...)`, etc. — top-down for stability.
        for line in tail.splitlines():
            cleaned = _BOX_PREFIX_RE.sub("", line).strip()
            if not cleaned:
                continue
            m = _TOOL_CALL_RE.search(cleaned)
            if m:
                tool = m.group("tool")
                arg = m.group("arg").strip()
                return f"{tool}({arg})" if arg else tool

        # 2. "Run: ..." / "Run command: ..."
        m = _RUN_HINT_RE.search(tail)
        if m:
            return m.group("cmd").strip()

        return None

    @staticmethod
    def _detect_approval_two_signal(
        lines: list[str],
    ) -> tuple[int, int] | None:
        """Gate 1 for approval detection — ordered two-signal structural check.

        Scans ``lines`` (the last ``detection_lines`` of a pane snapshot)
        and returns ``(cursor_idx, footer_idx)`` when BOTH anchors are found
        in the correct top-to-bottom order:

          * cursor_idx  — last line matching ``❯ <digit>.`` (live menu cursor)
          * footer_idx  — last line matching ``Esc to cancel`` (approval footer)
          * cursor_idx < footer_idx  (cursor BEFORE footer)

        Returns ``None`` when either signal is missing or they appear in the
        wrong order (footer above cursor means stale scrollback, not live menu).

        Why two signals?  Each one alone can appear in scrollback:
          • ``Esc to cancel`` persists in scroll after an answered approval.
          • ``❯ 1.`` style lines appear in Claude's prose numbered lists.
        Together, in order, they uniquely identify a LIVE approval box.
        """
        n = len(lines)
        # Bottom-up: find the last (most recent) footer.
        footer_idx = next(
            (i for i in range(n - 1, -1, -1)
             if _APPROVAL_FOOTER_RE.search(lines[i])),
            None,
        )
        if footer_idx is None:
            return None
        # Then find the last cursor line that sits ABOVE the footer.
        cursor_idx = next(
            (i for i in range(footer_idx - 1, -1, -1)
             if _APPROVAL_CURSOR_LINE_RE.search(lines[i])),
            None,
        )
        if cursor_idx is None:
            return None
        return cursor_idx, footer_idx

    @staticmethod
    def _extract_menu_options_bounded(
        lines: list[str],
        cursor_idx: int,
        footer_idx: int,
    ) -> tuple[str, ...]:
        """Collect menu options strictly between cursor line and footer line.

        Iterates lines[cursor_idx : footer_idx] — this hard upper bound means
        task-plan items that Claude appends AFTER ``Esc to cancel`` are never
        mistaken for options, regardless of how many there are.
        """
        opts: list[str] = []
        for i in range(cursor_idx, footer_idx):
            cleaned = _BOX_PREFIX_RE.sub("", lines[i])
            m = _MENU_OPTION_RE.match(cleaned)
            if m:
                opts.append(f"{m.group(1)}. {m.group(2).strip()}")
        return tuple(opts)
