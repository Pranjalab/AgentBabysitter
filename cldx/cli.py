"""Claude Code Tmux Bridge — second-layer interface.

Runs alongside a tmux pane that's hosting Claude Code. This terminal
becomes a "remote control" for that session:

    - The Claude pane is mirrored here whenever it stabilises.
    - You always have an input bar at the bottom (`claude> `).
    - When Claude asks an approval question, the bridge classifies it,
      checks `config/policy.yml`, and either auto-responds or hands
      control to you.
    - Bare `y` / `n` / digit replies act on a pending prompt; anything
      else is typed straight into Claude's text box. `/`-prefixed words
      are bridge commands (see `/help`).

Phase 2 will let the same loop talk to Telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import signal
import sys
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cldx import __version__
from cldx._paths import resolve_policy_path
from cldx.policy_engine import (
    DecisionResult,
    PolicyDecision,
    PolicyEngine,
    PolicyEngineError,
)
from cldx.prompt_classifier import ClassifiedPrompt, PromptClassifier, PromptType
from cldx.session_picker import SessionPickerError, list_panes, pick_session
from cldx.conversation import extract_assistant_step
from cldx.interaction_log import InteractionLog
from cldx.session_limit import SessionLimit, parse_session_limit
from cldx.session_store import SessionStore
from cldx.telegram_sanitize import (
    clean_for_telegram,
    extract_assistant_reply,
)
from cldx.framed_input import FramedInputSession
from cldx.memory import Memory, normalize_pattern
from cldx.secrets import load_into_environ
from cldx.tmux_controller import TmuxController
from cldx.tmux_monitor import TmuxMonitor
from cldx.wait_bar import animated_countdown_wait, countdown_wait

# force_terminal so colors survive prompt_toolkit's stdout wrapper.
console = Console(force_terminal=True, color_system="truecolor", soft_wrap=True)

DECISION_STYLE = {
    PolicyDecision.AUTO_YES: ("green", "AUTO YES"),
    PolicyDecision.AUTO_NO: ("red", "AUTO NO"),
    PolicyDecision.ESCALATE_TELEGRAM: ("yellow", "ASK"),
    PolicyDecision.WAIT_LOCAL: ("white", "WAIT"),
}


def parse_cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cldx",
        description="Second-layer terminal for a tmux pane running Claude Code.",
    )
    p.add_argument("--version", action="version", version=f"cldx {__version__}")

    # All flags below are for the default (bridge) command. Subcommands
    # below are parsed by their own subparsers.
    p.add_argument("--session", help="Target pane, e.g. '0:0.0'.")
    p.add_argument("--auto-detect", action="store_true",
                   help="Find the first pane running Claude Code.")
    p.add_argument("--profile",
                   help="Override active profile (auto-approve/yolo/restricted/default/paranoid).")
    p.add_argument("--policy", default=None,
                   help="Path to policy.yml. Default: ~/.cldx/config/policy.yml "
                        "if it exists, else the bundled default.")
    p.add_argument("--poll-interval", type=float, default=1.0,
                   help="Seconds between pane snapshots (default 1.0).")
    p.add_argument("--mirror-lines", type=int, default=25,
                   help="How many tail lines of the Claude pane to mirror (default 25).")
    p.add_argument("--dry-run", action="store_true",
                   help="Classify and decide, but don't send keys to tmux.")
    p.add_argument("--list-panes", action="store_true",
                   help="List all tmux panes (with command + title) and exit.")
    p.add_argument("--no-telegram", action="store_true",
                   help="Don't start the Telegram bridge even if configured.")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip the LLM summarizer for THIS run — Telegram gets "
                        "the raw pane (overrides whatever agent_name.yml says).")

    # Subcommands
    sub = p.add_subparsers(dest="cmd", required=False)

    setup_p = sub.add_parser(
        "setup",
        help="Interactive wizard for Telegram bot + Anthropic API key.",
    )
    setup_p.add_argument(
        "target", nargs="?", default="all",
        choices=("all", "telegram", "llm", "anthropic", "bedrock", "gemini", "none"),
        help=(
            "Which integration to configure. `none` persistently disables "
            "the LLM (Telegram gets raw pane)."
        ),
    )

    config_p = sub.add_parser(
        "config", help="Inspect cldx config (secrets are masked).",
    )
    config_p.add_argument(
        "action", nargs="?", default="show", choices=("show",),
        help="config show — print masked summary of all secrets.",
    )

    test_p = sub.add_parser(
        "test",
        help="End-to-end smoke tests against configured services.",
    )
    test_p.add_argument(
        "target", nargs="?", default="llm", choices=("llm",),
        help=(
            "What to test. `llm` runs all three summary modes against the "
            "configured backend and prints the output of each."
        ),
    )

    return p.parse_args()


# --- small helpers ---------------------------------------------------------

def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


_RICH_TAG_RE = __import__("re").compile(r"\[/?[^\[\]]{0,40}?\]")


def _strip_rich_markup(text: str) -> str:
    """Remove ``[green]...[/green]``-style Rich tags from a log line.

    Best-effort only — Rich's full grammar is richer than what this
    matches, but for one-line console messages it cleans up reliably.
    Used so the plain-text InteractionLog stays human-readable.
    """
    if not text:
        return ""
    return _RICH_TAG_RE.sub("", text)


def _find_option_digit(options, keyword: str) -> int | None:
    import re
    pat = re.compile(r"^\s*(\d+)\.\s*(.+)$")
    for opt in options:
        m = pat.match(opt)
        if not m:
            continue
        digit, text = int(m.group(1)), m.group(2).strip()
        if text.lower().startswith(keyword.lower()):
            return digit
    return None


async def _act_yes(controller: TmuxController, prompt: ClassifiedPrompt) -> str:
    if prompt.type == PromptType.APPROVAL_MENU:
        digit = _find_option_digit(prompt.menu_options, "Yes")
        if digit is not None:
            await controller.send_digit(digit)
            return f"sent option {digit} (Yes)"
        await controller.send_enter()
        return "sent Enter (default Yes)"
    await controller.send_yes()
    return "sent 'y'"


async def _act_no(controller: TmuxController, prompt: ClassifiedPrompt) -> str:
    if prompt.type == PromptType.APPROVAL_MENU:
        digit = _find_option_digit(prompt.menu_options, "No")
        if digit is not None:
            await controller.send_digit(digit)
            return f"sent option {digit} (No)"
        await controller.send_escape()
        return "sent Escape"
    await controller.send_no()
    return "sent 'n'"


# --- the bridge ------------------------------------------------------------

class BridgeUI:
    """Wires the monitor, classifier, policy, and prompt_toolkit input loop."""

    def __init__(self, args: argparse.Namespace, pane: str, pane_info,
                 policy: PolicyEngine):
        self.args = args
        self.pane = pane
        self.pane_info = pane_info
        self.policy = policy

        self.classifier = PromptClassifier(detection_cfg=policy.detection_config)
        self.controller = TmuxController(pane)
        self.monitor = TmuxMonitor(pane, poll_interval=args.poll_interval)

        self.pending: ClassifiedPrompt | None = None
        self.pending_signature: str | None = None
        self.last_mirror_tail: str = ""
        self.stop_event = asyncio.Event()
        self.session: PromptSession[str] = PromptSession()
        # Bordered input box (the visible one). The PromptSession above is
        # kept around for any code that still needs a flat prompt, but the
        # main input loop uses `framed`. The suggestion callable is read
        # on every render so the placeholder always reflects Claude's
        # current bottom-pane hint.
        self.framed = FramedInputSession(
            title_fn=self._prompt_title,
            suggestion_fn=lambda: self._claude_suggestion,
        )
        self._action_lock = asyncio.Lock()

        # Phase 2: jsonl event log for this run (machine-replayable).
        self.store = SessionStore(profile=policy.active_profile_name, pane=pane)
        # Plain-text interaction log — everything the user typed, everything
        # Telegram delivered, and every decision cldx made, written under
        # ``~/.cldx/logs/YYYY-MM-DD/HH-MM-SS_<profile>_<pane>.log``.
        self.interaction_log = InteractionLog(
            profile=policy.active_profile_name, pane=pane,
        )
        # Track whether auto-approvals are temporarily paused (set via
        # Telegram /pause). Approval prompts that arrive while paused
        # remain "pending" instead of getting auto-fired.
        self.paused: bool = False
        # When the current task began — used to surface duration in the
        # Telegram completion card.
        self._task_started_at: float | None = None

        # Runtime Telegram forwarding gate. ``True`` means notify_* calls
        # are allowed; ``False`` means we still answer slash commands but
        # don't push approval / completion cards out. Default to True so
        # behaviour matches "Telegram is wired and active" once the bridge
        # connects.
        self.telegram_enabled: bool = True

        # Detected Claude session-limit state. ``None`` outside of a quota
        # hit; populated by the on_change watcher when the banner appears.
        # The background ``_reset_task`` sleeps until ``session_limit.reset_at``
        # and then surfaces the "session reset" notification.
        self.session_limit: SessionLimit | None = None
        self._reset_task: asyncio.Task | None = None
        self._session_limit_seen_sig: str | None = None

        # Phase 3: signal that's set whenever the user types something while
        # a wait-bar is counting down. `None` outside of a wait epoch.
        self._wait_event: asyncio.Event | None = None

        # Phase 5: persistent memory of yolo-learned patterns. Attached to
        # the policy engine so its decide() can short-circuit on hits.
        self.memory = Memory(destructive_patterns=policy.destructive_patterns)
        policy.memory = self.memory

        # Phase 4: optional path to a previous session log we want to replay
        # as a transcript before going live. Set by the startup picker.
        self.resume_from: Path | None = None

        # Phase 7 wiring: telegram bridge is set up in `run()` if configured.
        self.telegram = None
        self._telegram_reply_event: asyncio.Event | None = None

        # State machine for the "task complete" panel:
        #   - `_completion_locked = True` after we render a completion;
        #     suppresses further completion panels until a NEW approval
        #     prompt arrives OR the user injects text manually. This
        #     replaces the earlier hash-based dedup which broke on
        #     Claude's frame-to-frame jitter.
        self._completion_locked: bool = False
        # Marker the most recent tool-call signature inside the pane.
        # We use this to tell "real task" (had tool calls) apart from
        # "Claude just chatted" (no tool calls) — only real tasks get a
        # full green completion panel; chat gets a single log line.
        self._task_started: bool = False
        # Claude's bottom-pane suggestion text (what Claude shows in its
        # input box as a dim placeholder, e.g. "delete it"). Updated on
        # every on_stable; read by the framed input so the user can press
        # Tab to accept it.
        self._claude_suggestion: str = ""

    # --- accessors (used by telegram_commands) ---

    @property
    def pane_target(self) -> str:
        return self.pane

    def set_paused(self, value: bool) -> None:
        self.paused = bool(value)
        self.interaction_log.cldx_note(
            f"paused={self.paused} (via Telegram)"
        )

    # --- printing (safe under patch_stdout) ---

    def log(self, msg: str) -> None:
        # Use console.print so Rich markup ([green]...[/green]) renders.
        # patch_stdout(raw=True) lets ANSI codes pass through cleanly.
        console.print(f"[dim][{_now()}][/dim] {msg}")
        # Strip Rich markup tags for the plain log — quick best-effort.
        try:
            self.interaction_log.terminal_out(_strip_rich_markup(msg))
        except Exception:  # noqa: BLE001 — logging must never crash the UI
            pass

    def print_rich(self, renderable) -> None:
        console.print(renderable)

    # --- mirror ---

    def _normalize_tail(self, snapshot: str) -> str:
        """Stable representation of the pane tail for dedup.

        Strips trailing whitespace on every line and collapses runs of
        blank lines into single blanks. This makes the mirror dedup
        survive Claude's subtle frame-to-frame redraws (cursor position
        shifts, "Cogitated for Ns" timer ticking, trailing spaces).
        """
        lines = snapshot.splitlines()[-self.args.mirror_lines:]
        out: list[str] = []
        prev_blank = False
        for line in lines:
            line = line.rstrip()
            if not line:
                if prev_blank:
                    continue
                prev_blank = True
            else:
                prev_blank = False
            out.append(line)
        return "\n".join(out).strip()

    def _print_mirror(self, snapshot: str, force: bool = False) -> None:
        """Render the mirror panel with Claude's own ANSI styling preserved.

        Dedup is based on the ANSI-STRIPPED tail (stable across frame
        jitter), but the DISPLAY uses the raw-with-ANSI version from
        `self.monitor.last_raw_snapshot` so dim placeholder text and
        coloured tool calls survive. Falls back to plain text only if
        the raw isn't available yet.
        """
        tail = self._normalize_tail(snapshot)
        if not tail:
            return
        if not force and tail == self.last_mirror_tail:
            return
        self.last_mirror_tail = tail

        raw = getattr(self.monitor, "last_raw_snapshot", "") or ""
        if raw:
            raw_lines = raw.splitlines()[-self.args.mirror_lines:]
            body: Text | str = Text.from_ansi("\n".join(raw_lines).rstrip())
        else:
            body = tail

        title = f"claude pane @ {_now()}"
        self.print_rich(Panel(body, title=title, border_style="blue", expand=False))

    # --- event handlers ---

    # Prompt types we'll act on the moment we see them in a change event
    # (not waiting for the pane to fully stabilise). Idle/Running/Complete
    # are noisy mid-stream signals — we only handle those in on_stable.
    _EAGER_TYPES = frozenset({
        PromptType.APPROVAL_YN,
        PromptType.APPROVAL_MENU,
        PromptType.TEXT_INPUT,
    })

    async def on_change(self, _new_content: str, snapshot: str) -> None:
        """Fired on every pane diff. Catches approval prompts the moment they
        render, so we don't have to wait for the pane to go fully quiet."""
        async with self._action_lock:
            # Session-limit banner can appear mid-stream — check before
            # we burn cycles on classification.
            self._check_session_limit(snapshot)
            prompt = self.classifier.classify(snapshot)
            if prompt.type not in self._EAGER_TYPES:
                return
            await self._dispatch_classified(prompt, snapshot, source="change")

    async def on_stable(self, snapshot: str) -> None:
        """Pane settled. Mirror it; also act as a safety net if an approval
        prompt arrived without ever triggering an on_change diff (rare).

        Order matters: we classify BEFORE printing the mirror. If we're
        sitting in post-completion idle (already showed the green panel
        for this task), we skip the mirror reprint — the user has
        already seen the final state and doesn't need it again every
        time Claude's UI redraws a "Cooked for Ns" counter.
        """
        async with self._action_lock:
            prompt = self.classifier.classify(snapshot)

            # Refresh Claude's bottom-pane suggestion so the framed input
            # shows the right placeholder text (Tab to accept).
            self._claude_suggestion = self._extract_suggestion(snapshot)

            if prompt.type == PromptType.COMPLETE and self._completion_locked:
                # Same task already finalised — stay quiet, no mirror reprint.
                return

            self._print_mirror(snapshot)

            if prompt.type in (PromptType.IDLE, PromptType.RUNNING):
                return

            if prompt.type == PromptType.COMPLETE:
                await self._handle_completion(snapshot)
                return

            await self._dispatch_classified(prompt, snapshot, source="stable")

    # Markers that indicate Claude actually USED a tool, not just chatted.
    # When none of these appear in the recent snapshot, the "completion"
    # is just a conversational reply and we render it as a one-liner
    # rather than a full green panel.
    # NOTE: this hardcoded regex used to live here but it missed any tool
    # added after the original list (most painfully ``WebSearch`` /
    # ``Web Search``). ``_pane_has_tool_calls`` now delegates to
    # ``cldx.tool_call.parse_tool_call`` which is driven by the
    # canonical ``TOOL_REGISTRY`` and handles multi-word display names.
    # Keep the compiled pattern here as a stable fallback for anything
    # that doesn't go through ``parse_tool_call`` (currently nothing
    # does, but the lookup is cheap and the regex documents intent).
    _TOOL_CALL_PATTERN = re.compile(
        r"⏺\s*[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)*\s*\("
    )

    # A line that looks like a SUBMITTED user message in Claude Code's pane:
    # starts with `❯ `, has visible text, and is NOT a menu option ("❯ 1. Yes").
    _USER_INPUT_PATTERN = re.compile(r"^\s*❯\s+(?!\d+\.\s)\S")

    # Trailing UI chrome we strip when extracting just the latest task.
    _UI_CHROME_PATTERNS = (
        re.compile(r"\?\s*for\s*shortcuts"),
        re.compile(r"^\s*[─━]+\s*$"),
        re.compile(r"esc\s*to\s*(cancel|interrupt)"),
        re.compile(r"^\s*$"),
    )

    @classmethod
    def _pane_has_tool_calls(cls, snapshot: str) -> bool:
        """Did Claude actually do work, or just reply with text?

        Delegates to ``cldx.tool_call.parse_tool_call`` so the answer
        stays consistent with the registry (single source of truth).
        That parser recognises:

        - every canonical name in ``TOOL_REGISTRY``
        - the multi-word display variants Claude prints in the pane
          (``Web Search``, ``Web Fetch``, ``Multi Edit``, …)
        - any future tool spec added to the registry, automatically

        Without this, turns that used only the newer tools (notably
        ``WebSearch``) were silently misclassified as chat replies —
        they ran through the truncated 💬 cyan panel instead of the
        full ✓ green completion card.
        """
        from cldx.tool_call import parse_tool_call as _parse_tool
        # Limit to the recent pane so we don't false-fire on tool calls
        # from much-older turns sitting deep in scrollback.
        tail = "\n".join(snapshot.splitlines()[-80:])
        return _parse_tool(tail) is not None

    # Any ❯ line — input area, suggestion, OR submitted message.
    _ANY_CARET_PATTERN = re.compile(r"^\s*❯\s")
    # Menu options like "❯ 1. Yes" — NOT user input.
    _MENU_CARET_PATTERN = re.compile(r"^\s*❯\s+\d+\.\s")
    # Claude's response / tool marker.
    _ASSISTANT_PATTERN = re.compile(r"^\s*⏺")

    @classmethod
    def _extract_suggestion(cls, snapshot: str) -> str:
        """Find Claude's bottom-pane suggestion text, if any.

        Claude Code shows a dim placeholder ('delete it', 'run the tests',
        etc.) in its input box between two ``─`` separators. We look for
        the bottom-most ``❯ <text>`` line that has NO ``⏺`` (tool call)
        line after it — that's the live input/suggestion, not a previously
        submitted user message.

        Returns the suggestion text (without the ``❯ `` prefix), or empty
        string if no suggestion is visible.
        """
        lines = snapshot.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            if cls._MENU_CARET_PATTERN.match(line):
                continue
            m = re.match(r"^\s*❯\s+(\S.*?)\s*$", line)
            if not m:
                continue
            text = m.group(1)
            # If there's a ⏺ line below this one, this is a submitted user
            # message, not the live input area — keep scanning upward.
            if any(
                cls._ASSISTANT_PATTERN.match(lines[j])
                for j in range(i + 1, len(lines))
            ):
                continue
            return text
        return ""

    @classmethod
    def _extract_current_task(cls, snapshot: str) -> str:
        """Return only the latest task's content.

        Algorithm:

        1. Find every ``❯ ...`` line that isn't a menu option (``❯ 1. Yes``).
        2. Walk backwards through those lines. The FIRST one we find that
           has a ``⏺`` (Claude response/tool marker) somewhere after it is
           the "submitted user message" for the latest task. Lines after
           it without a corresponding ``⏺`` are the live input/suggestion
           area at the bottom.
        3. End the slice at the NEXT ``❯`` line after the submitted one
           (that's where the input area starts) or at end-of-snapshot.
        4. Strip trailing UI chrome (``? for shortcuts``, ``─`` separators,
           ``esc to cancel`` hints, blank lines).

        Falls back to the snapshot tail if no anchor is found — rare,
        only happens during the initial banner.
        """
        lines = snapshot.splitlines()

        user_indices = [
            i for i, line in enumerate(lines)
            if cls._ANY_CARET_PATTERN.match(line)
            and not cls._MENU_CARET_PATTERN.match(line)
        ]
        if not user_indices:
            return "\n".join(lines[-20:]).strip()

        # Find the latest ❯ line that has a ⏺ AFTER it (a submitted task).
        last_submitted = None
        for ui in reversed(user_indices):
            if any(
                cls._ASSISTANT_PATTERN.match(lines[j])
                for j in range(ui + 1, len(lines))
            ):
                last_submitted = ui
                break
        if last_submitted is None:
            return "\n".join(lines[-20:]).strip()

        # End at the NEXT ❯ line (the input/suggestion area).
        end = len(lines)
        for ui in user_indices:
            if ui > last_submitted:
                end = ui
                break

        slice_lines = lines[last_submitted:end]
        while slice_lines and any(
            p.search(slice_lines[-1]) for p in cls._UI_CHROME_PATTERNS
        ):
            slice_lines.pop()
        return "\n".join(slice_lines).strip()

    async def _handle_completion(self, snapshot: str) -> None:
        """One-shot 'task complete' flow.

        The completion panel is GREEN (regardless of whether the LLM
        produced a real summary or fell back to raw context). A Telegram
        status line appears under the panel so the user always knows
        whether the result was forwarded.

        We use ``_completion_locked`` as a state flag instead of a
        content hash: Claude's UI has frame-to-frame jitter that defeats
        hash-based dedup, but a flag-based lock works reliably because
        we only clear it on actual NEW activity (new approval prompt OR
        user text injection).

        If the pane shows no tool-call markers (just a conversational
        reply), we skip the panel entirely and log a single line — chat
        is not a "task".
        """
        if self._completion_locked:
            return
        self._completion_locked = True
        self.store.log_event("complete")

        is_real_task = self._pane_has_tool_calls(snapshot)

        # Conversational reply (no tool calls) → small cyan card,
        # NOT the big green completion panel. We still surface the
        # response in both the terminal and Telegram so every interaction
        # closes the loop.
        if not is_real_task:
            # Use the structural ⏺...✻ extractor — same rule applies to
            # chat-only turns. Fall back to the older string-stripping
            # extractor if the structural one returns nothing (rare).
            reply_text = extract_assistant_step(snapshot)
            if not reply_text:
                reply_text = extract_assistant_reply(snapshot)
            if not reply_text:
                # Last resort — broader current-task slice, cleaned.
                reply_text = clean_for_telegram(
                    self._extract_current_task(snapshot)
                )

            # Terminal: compact cyan panel.
            self.print_rich(Panel(
                reply_text or "(empty reply)",
                title="[bold cyan]💬 Claude replied[/bold cyan]",
                border_style="cyan",
                expand=False,
            ))
            self.interaction_log.claude_out(reply_text)

            # Telegram: small "Claude says" message, gated by enabled flag.
            await self._telegram_chat_reply(reply_text)

            self.pending = None
            self.pending_signature = None
            self._update_prompt_label()
            return

        # Trim the snapshot down to just the current task — the LLM gets
        # this richer slice (includes the user's ❯ question for context).
        task_text = self._extract_current_task(snapshot)
        # The VISIBLE body uses the structural ⏺...✻ extractor so the
        # green panel + Telegram message show just Claude's response,
        # not the user's own question rehashed at the top. This is the
        # user-requested format: "everything between the line that starts
        # with ⏺ and the line that ends with ✻".
        visible_step = extract_assistant_step(snapshot) or task_text

        # Real task: build the summary text we'll show + (maybe) send.
        from cldx.agent import Agent
        from cldx.summarizer import summarize_with_status
        try:
            agent = Agent.load()
            if getattr(self.args, "no_llm", False):
                agent.model = "none:raw"
            result = await summarize_with_status(
                "completion_summary", task_text, agent,
            )
        except Exception as e:  # noqa: BLE001
            from cldx.summarizer import SummaryResult
            result = SummaryResult(
                text=task_text,
                summarized=False,
                fallback_reason=str(e),
            )

        # When the LLM didn't produce a summary (disabled / failed), the
        # raw ``result.text`` is the LLM-input text (which contained the
        # user's question). Swap it for the clean structural slice so the
        # panel/Telegram don't echo the user back to themselves.
        if not result.summarized:
            result = type(result)(
                text=visible_step,
                summarized=False,
                fallback_reason=result.fallback_reason,
            )

        # ALWAYS green panel — the result is the result, regardless of
        # whether the LLM produced a real summary or we fell back. The
        # subtitle reveals which.
        if result.summarized:
            subtitle = f"[dim]summarised via {Agent.load().backend}[/dim]"
        else:
            subtitle = (
                f"[dim]raw pane (LLM unavailable: "
                f"{result.fallback_reason})[/dim]"
            )

        # Telegram status, surfaced as the LAST line inside the green panel.
        telegram_line = ""
        telegram_send_succeeded = False
        if getattr(self.args, "no_telegram", False):
            telegram_line = (
                "\n\n[dim]Telegram: disabled for this run (--no-telegram)[/dim]"
            )
        elif self.telegram is None:
            telegram_line = (
                "\n\n[yellow]Telegram: ✗ not configured "
                "(run `cldx setup telegram`)[/yellow]"
            )
        elif not self.telegram_enabled:
            telegram_line = (
                "\n\n[dim]Telegram: forwarding off (/telegram on to re-enable)[/dim]"
            )
        else:
            try:
                clean_body = clean_for_telegram(result.text)
                await self.telegram._send(
                    f"✅ *Task complete*\n{'━' * 20}\n{clean_body}"
                )
                telegram_line = "\n\n[bold green]Telegram: ✓ sent[/bold green]"
                telegram_send_succeeded = True
            except Exception as e:  # noqa: BLE001
                telegram_line = f"\n\n[red]Telegram: ✗ send failed ({e})[/red]"

        body = f"{result.text}\n\n{subtitle}{telegram_line}"
        self.print_rich(Panel(
            body,
            title="[bold green]✓ Task complete[/bold green]",
            border_style="green",
            expand=False,
        ))

        if telegram_send_succeeded:
            self.store.log_event(
                "telegram_out", summary=result.text, mode="completion_summary",
            )

        # Clear pending state — next prompt starts fresh.
        self.pending = None
        self.pending_signature = None
        self._update_prompt_label()

    # --- session limit + reset watcher ---------------------------------

    def _check_session_limit(self, snapshot: str) -> None:
        """Detect the Claude session-limit banner in ``snapshot``.

        Fires once per unique limit line — the same banner staying on
        screen across many frames doesn't re-trigger notifications. When
        a new limit is detected, we surface it to the terminal + Telegram
        and start the reset-watcher task.
        """
        limit = parse_session_limit(snapshot)
        if limit is None:
            return
        sig = limit.raw_line
        if sig and sig == self._session_limit_seen_sig:
            return  # same banner, already handled
        self._session_limit_seen_sig = sig
        self.session_limit = limit

        message = (
            f"Claude session limit reached. Resets at {limit.label}"
            + (f" ({limit.timezone_str})" if limit.timezone_str else "")
            + "."
        )
        # Terminal: red panel so it can't be missed.
        self.print_rich(Panel(
            message + "\n\n[dim]cldx will alert you the moment the session "
            "reopens.[/dim]",
            title="[bold red]⏰ Session limit reached[/bold red]",
            border_style="red",
            expand=False,
        ))
        self.interaction_log.cldx_note(
            f"session limit detected — resets at {limit.label} "
            f"({limit.timezone_str or 'local'})"
        )
        self.store.log_event(
            "session_limit",
            label=limit.label,
            timezone=limit.timezone_str,
            reset_at=limit.reset_at.isoformat(),
        )

        # Telegram: send via the same enabled-gate path.
        if (
            not getattr(self.args, "no_telegram", False)
            and self.telegram is not None
            and self.telegram_enabled
        ):
            body = (
                f"⏰ *Session limit reached*\n{'━' * 20}\n"
                f"{message}\n\nI'll ping you the moment Claude is back."
            )
            try:
                asyncio.create_task(self.telegram._send(body))
                self.interaction_log.telegram_out("session limit notice")
            except Exception as e:  # noqa: BLE001
                self.log(f"[dim]Telegram session-limit send failed: {e}[/dim]")

        # (Re)start the reset watcher — cancel any in-flight task first
        # so a fresh limit doesn't double-fire.
        if self._reset_task is not None and not self._reset_task.done():
            self._reset_task.cancel()
        self._reset_task = asyncio.create_task(
            self._session_reset_watcher(limit)
        )
        self._update_prompt_label()

    async def _session_reset_watcher(self, limit: SessionLimit) -> None:
        """Sleep until ``limit.reset_at``, then notify and clear state."""
        from datetime import datetime as _dt, timezone as _tz
        delay = max(0.0, limit.seconds_until_reset(_dt.now(_tz.utc)))
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        body = (
            "Claude session has been reset. Would you like to continue "
            "your previous task or start new work?"
        )

        # Terminal: green panel.
        self.print_rich(Panel(
            body,
            title="[bold green]✅ Session reset[/bold green]",
            border_style="green",
            expand=False,
        ))
        self.interaction_log.cldx_note("session reset — banner cleared")

        # Telegram (gated).
        if (
            not getattr(self.args, "no_telegram", False)
            and self.telegram is not None
            and self.telegram_enabled
        ):
            tg_body = f"✅ *Session reset*\n{'━' * 20}\n{body}"
            try:
                await self.telegram._send(tg_body)
                self.interaction_log.telegram_out("session reset notice")
            except Exception as e:  # noqa: BLE001
                self.log(f"[dim]Telegram session-reset send failed: {e}[/dim]")

        # Clear the limit state so the dynamic header drops the tag.
        self.session_limit = None
        self._session_limit_seen_sig = None
        self._update_prompt_label()

    def _show_help(self) -> None:
        """Render the /help panel — grouped, with every command this build
        actually understands. Kept in sync with ``_handle_slash`` by hand;
        each section here corresponds to a branch over there."""
        body = (
            "[bold]Approval shortcuts[/bold] (only when a prompt is pending)\n"
            "  [cyan]/y[/cyan] / [cyan]/n[/cyan] / [cyan]/<digit>[/cyan]   "
            "approve / deny / pick menu option\n"
            "  [cyan]/skip[/cyan]                  clear the pending prompt without acting\n"
            "\n"
            "[bold]Telegram[/bold]\n"
            "  [cyan]/telegram[/cyan]              show current state\n"
            "  [cyan]/telegram on[/cyan]           enable Telegram forwarding (starts bridge if needed)\n"
            "  [cyan]/telegram off[/cyan]          silence outbound Telegram cards\n"
            "\n"
            "[bold]Inspection[/bold]\n"
            "  [cyan]/snapshot[/cyan]              show the cldx view of the pane + classifier output\n"
            "  [cyan]/refresh[/cyan]               reprint the mirror panel\n"
            "  [cyan]/panes[/cyan]                 list tmux panes (active one marked)\n"
            "\n"
            "[bold]Modes[/bold]\n"
            "  [cyan]/profile[/cyan]               show current policy profile\n"
            "  [cyan]/profile <name>[/cyan]        switch profile (e.g. /profile yolo)\n"
            "\n"
            "[bold]Raw / exit[/bold]\n"
            "  [cyan]/raw <keys>[/cyan]            send named tmux keys to the pane (e.g. /raw C-c)\n"
            "  [cyan]/quit[/cyan]                  exit cldx\n"
            "\n"
            "[dim]Anything not starting with '/' is typed into Claude.[/dim]"
        )
        self.print_rich(Panel(
            body,
            title="[bold cyan]cldx — terminal commands[/bold cyan]",
            border_style="cyan",
            expand=False,
        ))

    async def _handle_telegram_toggle(self, arg: str) -> None:
        """Implement ``/telegram on``, ``/telegram off``, and ``/telegram``.

        Behaviour:
        - ``/telegram`` (no arg) → report current state.
        - ``/telegram on``  → enable forwarding; if the bridge isn't yet
          running, try to start it (credentials must already be configured).
        - ``/telegram off`` → flip the gate; the bridge keeps running so
          inbound replies / slash commands still work, but outbound cards
          are suppressed until re-enabled.
        """
        arg = arg.strip().lower()
        if not arg:
            state = "off"
            if self.telegram is not None and self.telegram_enabled:
                state = "on"
            elif self.telegram is None:
                state = "not configured"
            self.log(f"telegram forwarding: {state}")
            return

        if arg not in ("on", "off"):
            self.log("usage: /telegram on | /telegram off")
            return

        if arg == "off":
            if self.telegram is None:
                self.log("[dim]telegram not running — nothing to disable.[/dim]")
                return
            self.telegram_enabled = False
            self.log("[yellow]telegram forwarding OFF[/yellow]")
            self.interaction_log.cldx_note("telegram forwarding disabled (/telegram off)")
            self._update_prompt_label()
            return

        # arg == "on"
        if self.telegram is not None:
            self.telegram_enabled = True
            self.log("[green]telegram forwarding ON[/green]")
            self.interaction_log.cldx_note("telegram forwarding enabled (/telegram on)")
            self._update_prompt_label()
            return

        # Bridge wasn't running — try to start it now.
        self.log("[dim]bringing telegram bridge online…[/dim]")
        prev_no_telegram = getattr(self.args, "no_telegram", False)
        if prev_no_telegram:
            # /telegram on overrides --no-telegram for the rest of the run.
            self.args.no_telegram = False
        await self._maybe_start_telegram()
        if self.telegram is None:
            self.log(
                "[yellow]telegram still not connected — check `cldx setup telegram`[/yellow]"
            )
            return
        self.telegram_enabled = True
        self.log("[green]telegram forwarding ON[/green]")
        self._update_prompt_label()

    async def _telegram_chat_reply(self, reply_text: str) -> None:
        """Forward a chat-only Claude reply to Telegram if forwarding is on.

        Skips the big "task complete" card — this is the small "Claude
        said something" path. The text is sanitized before sending so
        any leaked pane chrome doesn't reach the user's phone.
        """
        if getattr(self.args, "no_telegram", False):
            return
        if self.telegram is None or not self.telegram_enabled:
            return
        clean = clean_for_telegram(reply_text or "")
        if not clean:
            return
        body = f"💬 *Claude*\n{'━' * 20}\n{clean}"
        try:
            await self.telegram._send(body)
            self.interaction_log.telegram_out(f"chat reply: {clean}")
            self.store.log_event(
                "telegram_out", mode="chat_reply", summary=clean,
            )
        except Exception as e:  # noqa: BLE001
            self.log(f"[dim]Telegram chat send failed: {e}[/dim]")

    async def _dispatch_classified(
        self, prompt: ClassifiedPrompt, snapshot: str, source: str,
    ) -> None:
        """Common path: dedup by signature, persist, hand off to policy."""
        decision = self.policy.decide(prompt)
        sig = prompt.signature()
        if sig == self.pending_signature:
            return  # same prompt as last fire — already handled / awaiting
        self.pending_signature = sig
        # New actionable prompt = new task. Allow the next completion
        # panel to fire, and remember we're inside a task now.
        self._completion_locked = False
        self._task_started = True

        if self.args.verbose if hasattr(self.args, "verbose") else False:
            self.log(f"[dim]· detected {prompt.type.value} via {source}[/dim]")

        # Phase 2: persist every actionable prompt + its decision.
        self.store.log_prompt(prompt)
        self.store.log_decision(decision)

        await self._handle_decision(prompt, decision)

    async def _handle_decision(
        self, prompt: ClassifiedPrompt, decision: DecisionResult
    ) -> None:
        if self.args.dry_run:
            self.pending = prompt
            self._render_decision_panel(
                prompt, decision,
                title="[bold]DRY-RUN — would act[/bold]",
                border_style="dim",
                footer=f"[dim]dry-run: not acting (reason: {decision.reason})[/dim]",
            )
            self._update_prompt_label()
            return

        # Phase 3: destructive ops bypass the wait bar entirely. Render as
        # RED panel (user MUST decide). Same treatment for escalate-to-user
        # decisions in the restricted profile.
        if decision.is_destructive and decision.decision in (
            PolicyDecision.AUTO_YES, PolicyDecision.AUTO_NO
        ):
            self.pending = prompt
            self._render_decision_panel(
                prompt, decision,
                title="[bold red]⚠ NEEDS YOUR APPROVAL — destructive op[/bold red]",
                border_style="red",
                footer=(
                    "[bold red]waiting indefinitely[/bold red] "
                    "[dim](policy would have "
                    f"{decision.decision.value}; type y/n/<digit> or `/skip`)[/dim]"
                ),
            )
            self.store.log_note("destructive op pending — bypassed auto")
            if self.telegram is not None and self.telegram_enabled:
                try:
                    await self.telegram.notify_approval_needed(prompt, decision)
                    self.log("[cyan]→ destructive op alert sent to Telegram[/cyan]")
                except Exception as e:  # noqa: BLE001
                    self.log(f"[red]Telegram send failed: {e}[/red]")
            self._update_prompt_label()
            return

        if decision.decision == PolicyDecision.AUTO_YES:
            # YELLOW panel — auto-decision pending, countdown starts.
            self._render_decision_panel(
                prompt, decision,
                title="[bold yellow]⏳ AUTO-APPROVE — firing in {:.1f}s[/bold yellow]".format(
                    decision.wait_interval_seconds
                ),
                border_style="yellow",
                footer="[dim]type to override · /skip to leave to you[/dim]",
            )
            await self._auto_with_wait(prompt, decision, action="yes")
        elif decision.decision == PolicyDecision.AUTO_NO:
            self._render_decision_panel(
                prompt, decision,
                title="[bold yellow]⏳ AUTO-DENY — firing in {:.1f}s[/bold yellow]".format(
                    decision.wait_interval_seconds
                ),
                border_style="yellow",
                footer="[dim]type to override · /skip to leave to you[/dim]",
            )
            await self._auto_with_wait(prompt, decision, action="no")
        elif decision.decision == PolicyDecision.ESCALATE_TELEGRAM:
            # RED panel — user (or Telegram user) must respond.
            self.pending = prompt
            self._render_decision_panel(
                prompt, decision,
                title="[bold red]⚠ NEEDS YOUR APPROVAL[/bold red]",
                border_style="red",
                footer=(
                    f"[bold red]waiting for your reply[/bold red] "
                    f"[dim]({decision.reason}; type y/n/<digit> or `/skip`)[/dim]"
                ),
            )
            if self.telegram is not None and self.telegram_enabled:
                try:
                    await self.telegram.notify_approval_needed(prompt, decision)
                    self.log("[cyan]→ sent to Telegram[/cyan]")
                except Exception as e:  # noqa: BLE001
                    self.log(f"[red]Telegram send failed: {e}[/red]")
        else:
            # WAIT_LOCAL fall-through.
            self.pending = prompt
            self._render_decision_panel(
                prompt, decision,
                title="[bold]… waiting[/bold]",
                border_style="dim",
                footer=f"[dim]({decision.reason})[/dim]",
            )

        self._update_prompt_label()

    def _render_decision_panel(
        self,
        prompt: ClassifiedPrompt,
        decision: DecisionResult,
        title: str,
        border_style: str,
        footer: str,
    ) -> None:
        """Compact panel summarising an approval decision.

        When the prompt carries a typed ``tool`` (``ToolCall``), surface
        the icon + category + risk inline so the user can tell at a
        glance whether this is a Read (safe), a Write (elevated), or
        a destructive Bash. Falls back to the plain ``extracted_command``
        string for tools we don't yet have specs for."""
        body = Table.grid(padding=(0, 1))
        body.add_column(style="bold", no_wrap=True)
        body.add_column(overflow="fold")
        body.add_row("type", prompt.type.value)
        if prompt.tool is not None:
            tool = prompt.tool
            risk_color = {
                "destructive": "red",
                "elevated":    "yellow",
                "normal":      "white",
                "safe":        "green",
            }.get(tool.risk, "white")
            body.add_row(
                "tool",
                f"{tool.icon} [bold]{tool.name}[/bold] "
                f"[dim]· {tool.category}[/dim] "
                f"[{risk_color}]· {tool.risk}[/{risk_color}]",
            )
            if tool.args:
                body.add_row("args", tool.args)
        elif prompt.extracted_command:
            body.add_row("tool", prompt.extracted_command)
        if prompt.menu_options:
            body.add_row("options", "\n".join(prompt.menu_options))
        body.add_row("profile", decision.profile)
        if decision.matched_pattern:
            body.add_row(
                "matched", f"[dim]{decision.matched_pattern}[/dim]"
            )
        # Append the footer as the last row spanning both columns.
        body.add_row("", footer)
        self.print_rich(Panel(
            body, title=title, border_style=border_style, expand=False,
        ))

    # --- wait-bar coordination ----------------------------------------------

    async def _auto_with_wait(
        self,
        prompt: ClassifiedPrompt,
        decision: DecisionResult,
        action: str,
    ) -> None:
        """Run the per-profile countdown, then fire the auto action.

        While the wait is active, `self.pending` points at this prompt so
        the input loop's y/n/digit/text shortcuts target the right thing,
        and any input the user supplies sets `self._wait_event`, which
        cancels the wait early.
        """
        interval = decision.wait_interval_seconds
        if interval <= 0:
            await self._fire_auto(prompt, action)
            return

        self._wait_event = asyncio.Event()
        self.pending = prompt
        self._update_prompt_label()

        # The yellow decision panel (rendered by _handle_decision before
        # we got called) already shows "firing in N.Ns". For long waits
        # we still emit a one-line heartbeat so the user knows time is
        # passing while still able to override.
        midpoint_logged = {"done": False}

        def heartbeat(remaining: float, total: float) -> None:
            if midpoint_logged["done"]:
                return
            if remaining <= total / 2:
                midpoint_logged["done"] = True
                self.log(
                    f"[dim]  …{remaining:.1f}s remaining "
                    f"(still time to override)[/dim]"
                )

        try:
            # Use the animated variant only for waits long enough to
            # benefit from a heartbeat; otherwise plain countdown_wait
            # keeps the noise down.
            if interval >= 1.5:
                result = await animated_countdown_wait(
                    interval, self._wait_event, on_tick=heartbeat,
                    tick_interval=0.5,
                )
            else:
                result = await countdown_wait(interval, self._wait_event)
        finally:
            self._wait_event = None

        if result.overridden:
            # User typed something — input loop is handling it.
            self.log("[cyan]wait cancelled by your input[/cyan]")
            self.store.log_note(
                f"wait cancelled by user after {result.elapsed:.2f}s"
            )
            return

        # Timer won; the prompt may still be pending if the user typed nothing
        # but the input loop also cleared it (race). Re-check.
        if self.pending is not prompt:
            return
        await self._fire_auto(prompt, action)

    def _replay_transcript(self, path: Path) -> None:
        """Phase 4: print a condensed view of a prior session's events."""
        from cldx.session_store import replay
        console.print(Panel(
            f"[dim]replaying {path.name}[/dim]",
            title="[bold]previous session[/bold]",
            border_style="dim",
        ))
        for event in replay(path):
            kind = event.get("kind", "?")
            t = event.get("t", "")
            if kind == "prompt":
                cmd = event.get("command") or event.get("type", "?")
                console.print(f"[dim]{t}[/dim] [yellow]prompt[/yellow] {cmd}")
            elif kind == "decision":
                d = event.get("decision", "?")
                reason = event.get("reason", "")
                console.print(f"[dim]{t}[/dim] [magenta]→ {d}[/magenta] ({reason})")
            elif kind == "action":
                console.print(f"[dim]{t}[/dim] [cyan]action[/cyan] {event.get('keys','?')}")
            elif kind == "complete":
                console.print(f"[dim]{t}[/dim] [green]✓ task complete[/green]")
            # snapshot / session_end / note: skip for brevity
        console.print(Panel("[dim]end of replay — live monitor starting[/dim]",
                            border_style="dim"))

    # --- Telegram wiring (Phase 7 runtime) -------------------------------

    async def _maybe_start_telegram(self) -> None:
        """Boot a TelegramBridge if creds are present in env / config files."""
        if getattr(self.args, "no_telegram", False):
            return
        try:
            from cldx.agent import Agent
            from cldx.telegram_bridge import TelegramBridge, TelegramConfig
        except ImportError as e:
            self.log(f"[yellow]Telegram deps not installed: {e}[/yellow]")
            return

        cfg = TelegramConfig.from_environ()
        if cfg is None:
            self.log("[dim]Telegram not configured (run `cldx setup telegram` to enable)[/dim]")
            return

        agent = Agent.load()
        # --no-llm flag overrides the configured backend so EVERY summary
        # call routed through this bridge (approval / escalation /
        # completion) skips the upstream LLM and sends the raw context.
        if getattr(self.args, "no_llm", False):
            agent.model = "none:raw"
            self.log("[dim]--no-llm: Telegram messages will use raw pane context[/dim]")
        self.telegram = TelegramBridge(
            cfg, agent,
            reply_handler=self._telegram_reply_handler,
            bridge_ui=self,
        )
        try:
            await self.telegram.start()
            self.log(f"[green]✓ Telegram bridge connected (chat {cfg.chat_id})[/green]")
            self.interaction_log.cldx_note(
                f"telegram bridge connected (chat={cfg.chat_id})"
            )
        except Exception as e:  # noqa: BLE001
            self.log(f"[red]Failed to start Telegram bridge: {e}[/red]")
            self.interaction_log.cldx_note(f"telegram bridge failed: {e}")
            self.telegram = None

    async def _telegram_reply_handler(self, reply, _pending) -> None:
        """Route inbound Telegram replies through the same paths terminal input uses.

        `_pending` is the ClassifiedPrompt the bridge thinks is pending; we
        ignore it and use `self.pending` directly so the source of truth is
        always the live state.
        """
        async with self._action_lock:
            current = self.pending
            kind = reply.kind
            value = reply.value

            # Log the raw inbound reply.
            inbound_repr = value if kind == "text" else (
                value if kind == "digit" else kind
            )
            self.interaction_log.telegram_in(f"{kind}: {inbound_repr}")

            # If a yes/no/digit reply arrives with NO pending approval,
            # the user is just chatting — they typed "1" / "y" / "n" as
            # part of a conversation. Forward the original text to Claude
            # instead of silently dropping it. Symmetric to the terminal
            # path, which already does this.
            if current is None and kind in ("yes", "no", "digit"):
                text_to_send = reply.raw_text or reply.value or kind
                await self.controller.send_text(text_to_send)
                self.log(
                    f"[cyan]→ injected:[/cyan] {text_to_send} "
                    f"[dim](via Telegram — no pending prompt)[/dim]"
                )
                self.store.log_action(
                    keys=f"text:{text_to_send}", source="user_telegram",
                )
                self.interaction_log.cldx_action(
                    f"sent text {text_to_send!r} (via Telegram, no pending)"
                )
                self._completion_locked = False
                self.pending_signature = None
                self._update_prompt_label()
                return

            if kind == "yes" and current is not None:
                outcome = await _act_yes(self.controller, current)
                self.log(f"[green]→ {outcome}[/green] [dim](via Telegram)[/dim]")
                self.store.log_action(keys=outcome, source="user_telegram")
                self.interaction_log.cldx_action(f"{outcome} (via Telegram yes)")
                self._learn_from_user(current, approve=True)
                self.pending = None

            elif kind == "no" and current is not None:
                outcome = await _act_no(self.controller, current)
                self.log(f"[red]→ {outcome}[/red] [dim](via Telegram)[/dim]")
                self.store.log_action(keys=outcome, source="user_telegram")
                self.interaction_log.cldx_action(f"{outcome} (via Telegram no)")
                self._learn_from_user(current, approve=False)
                self.pending = None

            elif kind == "digit" and current is not None:
                await self.controller.send_digit(int(value))
                self.log(f"[cyan]→ sent option {value}[/cyan] [dim](via Telegram)[/dim]")
                self.store.log_action(keys=f"digit:{value}", source="user_telegram")
                self.interaction_log.cldx_action(f"sent digit {value} (via Telegram)")
                self.pending = None

            elif kind == "text":
                await self.controller.send_text(value)
                self.log(f"[cyan]→ injected:[/cyan] {value} [dim](via Telegram)[/dim]")
                self.store.log_action(keys=f"text:{value}", source="user_telegram")
                self.interaction_log.cldx_action(f"sent text {value!r} (via Telegram)")
                self.pending = None
                # CRITICAL: Telegram text injection starts a new task, just
                # like the terminal path does. Clearing the completion lock
                # here is what lets the NEXT chat-reply surface to terminal
                # + Telegram. Without it, the first reply locks the panel
                # and every subsequent Telegram-driven turn is silently
                # absorbed by the COMPLETE+locked short-circuit in on_stable.
                self._completion_locked = False
                # Also reset pending_signature so dispatch dedup doesn't
                # collide with the previous prompt's signature.
                self.pending_signature = None

            self._update_prompt_label()
            # Also cancel any in-flight wait bar.
            if self._wait_event is not None:
                self._wait_event.set()

    def _learn_from_user(self, prompt: ClassifiedPrompt, approve: bool) -> None:
        """Phase 5: in yolo profile, remember the user's decision."""
        if self.policy.active_profile_name != "yolo":
            return
        pattern = normalize_pattern(prompt.extracted_command)
        if pattern is None:
            return
        stored = self.memory.learn(approve, pattern, "yolo")
        if stored:
            verb = "approved" if approve else "denied"
            self.log(f"[dim]yolo learned: {verb} {pattern!r} → future matches auto-fire[/dim]")
            self.store.log_event("yolo_learn", pattern=pattern, approved=approve)

    async def _fire_auto(self, prompt: ClassifiedPrompt, action: str) -> None:
        cmd = prompt.extracted_command or prompt.type.value
        self.interaction_log.cldx_decision(f"auto-{action} {cmd}")
        if action == "yes":
            outcome = await _act_yes(self.controller, prompt)
            self.log(f"[green]→ {outcome}[/green]")
        else:
            outcome = await _act_no(self.controller, prompt)
            self.log(f"[red]→ {outcome}[/red]")
        self.store.log_action(keys=outcome, source="policy")
        self.interaction_log.cldx_action(outcome)
        self.pending = None
        self._update_prompt_label()

    # --- input ---

    def _update_prompt_label(self) -> None:
        """Refresh the prompt prefix to reflect pending state."""
        try:
            # prompt_toolkit's app may not be running yet on first call.
            app = self.session.app
            if app and app.is_running:
                app.invalidate()
        except Exception:
            pass

    def _prompt_label(self) -> str:
        """Kept for backwards compat / tests. The framed input now uses
        :meth:`_prompt_title` for its border title instead."""
        if self.pending is None:
            return "claude> "
        if self.pending.type == PromptType.APPROVAL_MENU and self.pending.menu_options:
            digits = "/".join(
                str(i + 1) for i in range(len(self.pending.menu_options))
            )
            return f"claude ({digits}|y|n)> "
        if self.pending.type == PromptType.APPROVAL_YN:
            return "claude (y/n)> "
        return "claude (reply)> "

    def _prompt_title(self) -> str:
        """Title shown on the framed input box (no trailing '> ').

        Assembled from three layers, in order:

        1. **Backend tag** — always ``Claude + TMUX``; ``+ Telegram`` is
           appended whenever the bridge is connected AND enabled.
        2. **Session-limit tag** — if a quota banner was detected, append
           ``(Resets at HH:MM)`` so the user can see at a glance when
           Claude will be available again.
        3. **Pending-prompt suffix** — ``(y / n)`` or ``(1/2/3 | y | n)``
           when there's a live approval prompt to answer.
        """
        parts = ["Claude + TMUX"]
        if self.telegram is not None and self.telegram_enabled:
            parts.append("+ Telegram")
        base = " ".join(parts)
        if self.session_limit is not None:
            base += f" (Resets at {self.session_limit.label})"

        if self.pending is None:
            return f" {base} "
        if self.pending.type == PromptType.APPROVAL_MENU and self.pending.menu_options:
            digits = "/".join(
                str(i + 1) for i in range(len(self.pending.menu_options))
            )
            return f" {base} ({digits} | y | n) "
        if self.pending.type == PromptType.APPROVAL_YN:
            return f" {base} (y / n) "
        return f" {base} (reply) "

    async def _input_loop(self) -> None:
        self.log("[dim]ready. /help for commands. Ctrl-D to quit.[/dim]")
        while not self.stop_event.is_set():
            try:
                # Framed input box (the Claude-Code-styled bordered prompt).
                text = await self.framed.prompt_async()
            except (EOFError, KeyboardInterrupt):
                self.stop_event.set()
                self.monitor.stop()
                return

            text = (text or "").strip()
            if not text:
                continue
            try:
                await self._handle_input(text)
            except Exception as e:
                self.log(f"[red]input error:[/red] {e}")

    async def _handle_input(self, text: str) -> None:
        # Phase 3: any input cancels a running wait bar so the auto-fire
        # doesn't race the user's reply.
        if self._wait_event is not None:
            self._wait_event.set()

        # Plain-text interaction log — every keystroke the user submitted.
        if text:
            self.interaction_log.terminal_in(text)

        if text.startswith("/"):
            await self._handle_slash(text[1:].strip())
            return

        low = text.lower()
        pending = self.pending

        # Context-sensitive shortcuts only when a prompt is pending.
        if pending is not None:
            if low in ("y", "yes"):
                async with self._action_lock:
                    outcome = await _act_yes(self.controller, pending)
                self.log(f"[green]→ {outcome}[/green]  (you said yes)")
                self.store.log_action(keys=outcome, source="user_terminal")
                self._learn_from_user(pending, approve=True)
                self.pending = None
                self._update_prompt_label()
                return
            if low in ("n", "no"):
                async with self._action_lock:
                    outcome = await _act_no(self.controller, pending)
                self.log(f"[red]→ {outcome}[/red]  (you said no)")
                self.store.log_action(keys=outcome, source="user_terminal")
                self._learn_from_user(pending, approve=False)
                self.pending = None
                self._update_prompt_label()
                return
            if low.isdigit() and pending.type == PromptType.APPROVAL_MENU:
                async with self._action_lock:
                    await self.controller.send_digit(int(low))
                self.log(f"[cyan]→ sent option {low}[/cyan]")
                self.store.log_action(keys=f"digit:{low}", source="user_terminal")
                self.pending = None
                self._update_prompt_label()
                return

        # Plain text → inject into Claude's text box.
        async with self._action_lock:
            await self.controller.send_text(text)
        self.log(f"[cyan]→ injected:[/cyan] {text}")
        self.store.log_action(keys=f"text:{text}", source="user_terminal")
        # Sending text usually clears any pending menu, so reset.
        self.pending = None
        # A new user message starts a new logical "task" — let the next
        # completion render a fresh green panel (and decide whether it's
        # a chat-only reply or a real tool-using task).
        self._completion_locked = False
        self._update_prompt_label()

    async def _handle_slash(self, cmd: str) -> None:
        if not cmd:
            return
        head, _, rest = cmd.partition(" ")
        head = head.lower()
        rest = rest.strip()

        if head in ("q", "quit", "exit"):
            self.log("bye.")
            self.stop_event.set()
            self.monitor.stop()
            return

        if head == "help":
            self._show_help()
            return

        if head == "snapshot":
            try:
                raw = await self.monitor.capture()
                snap = self.monitor.strip_ansi(raw)
            except Exception as e:  # noqa: BLE001
                self.log(f"[red]capture failed:[/red] {e}")
                return
            prompt = self.classifier.classify(snap)
            self.print_rich(Panel(
                snap.strip() or "(empty)",
                title=f"[bold]pane snapshot ({len(snap.splitlines())} lines)[/bold]",
                border_style="cyan",
            ))
            self.log(f"[bold]classified as:[/bold] {prompt.type.value}")
            if prompt.extracted_command:
                self.log(f"  extracted_command: {prompt.extracted_command}")
            if prompt.menu_options:
                self.log("  menu_options:")
                for opt in prompt.menu_options:
                    self.log(f"    {opt}")
            if prompt.matched_pattern:
                self.log(f"  matched detection pattern: {prompt.matched_pattern}")
            self.log(f"  signature: {prompt.signature()}")
            self.log(f"  pending_signature: {self.pending_signature}")
            return

        if head == "skip":
            self.pending = None
            self.pending_signature = None
            self.log("pending cleared.")
            self._update_prompt_label()
            return

        if head == "refresh":
            self.last_mirror_tail = ""  # force reprint
            try:
                raw = await self.monitor.capture()
                snap = self.monitor.strip_ansi(raw)
                self._print_mirror(snap)
            except Exception as e:
                self.log(f"[red]capture failed:[/red] {e}")
            return

        if head == "panes":
            try:
                panes = list_panes()
            except SessionPickerError as e:
                self.log(f"[red]{e}[/red]")
                return
            for p in panes:
                marker = " (watching)" if p.target == self.pane else ""
                self.log(f"{p.target}  cmd={p.current_command}  title={p.title}{marker}")
            return

        if head == "profile":
            if not rest:
                self.log(f"current profile: {self.policy.active_profile_name}")
                return
            try:
                self.policy = PolicyEngine(
                    resolve_policy_path(self.args.policy),
                    profile_override=rest,
                )
                self.classifier = PromptClassifier(
                    detection_cfg=self.policy.detection_config
                )
                self.log(f"switched profile → {self.policy.active_profile_name}")
            except PolicyEngineError as e:
                self.log(f"[red]{e}[/red]")
            return

        if head == "raw":
            if not rest:
                self.log("usage: /raw <keys>  e.g. /raw C-c")
                return
            async with self._action_lock:
                await self.controller.send_raw_keys(rest)
            self.log(f"sent raw keys: {rest}")
            return

        if head == "telegram":
            await self._handle_telegram_toggle(rest)
            return

        # /y, /n, /<digit>
        if head in ("y", "yes"):
            if self.pending is None:
                async with self._action_lock:
                    await self.controller.send_yes()
                self.log("→ sent 'y' (no pending prompt)")
                return
            async with self._action_lock:
                outcome = await _act_yes(self.controller, self.pending)
            self.log(f"[green]→ {outcome}[/green]")
            self.pending = None
            self._update_prompt_label()
            return

        if head in ("n", "no"):
            if self.pending is None:
                async with self._action_lock:
                    await self.controller.send_no()
                self.log("→ sent 'n' (no pending prompt)")
                return
            async with self._action_lock:
                outcome = await _act_no(self.controller, self.pending)
            self.log(f"[red]→ {outcome}[/red]")
            self.pending = None
            self._update_prompt_label()
            return

        if head.isdigit():
            async with self._action_lock:
                await self.controller.send_digit(int(head))
            self.log(f"→ sent digit {head}")
            self.pending = None
            self._update_prompt_label()
            return

        self.log(f"[yellow]unknown command:[/yellow] /{cmd}  (try /help)")

    # --- top-level run ---

    async def run(self) -> int:
        # Header
        header = Table.grid(padding=(0, 2))
        header.add_column(style="bold")
        header.add_column()
        header.add_row("pane", self.pane)
        if self.pane_info:
            header.add_row("cmd", self.pane_info.current_command)
            header.add_row("title", self.pane_info.title or "(none)")
        header.add_row("profile", self.policy.active_profile_name)
        header.add_row("poll", f"{self.args.poll_interval:.1f}s")
        header.add_row("mode", "dry-run" if self.args.dry_run else "live")
        self.print_rich(
            Panel(header, title="[bold]claude-tmux-bridge[/]", border_style="cyan")
        )

        # Phase 7 wiring: if telegram credentials are loaded, start the bridge.
        await self._maybe_start_telegram()

        # Optional: replay prior session's events before going live.
        if self.resume_from is not None:
            self._replay_transcript(self.resume_from)

        # Initial mirror + classification.
        try:
            raw = await self.monitor.capture()
            snap = self.monitor.strip_ansi(raw)
            self.monitor.last_snapshot = snap
            self._print_mirror(snap)
            first = self.classifier.classify(snap)
            self.log(f"initial: {first.type.value}"
                     + (f" — {first.extracted_command}"
                        if first.extracted_command else ""))
        except Exception as e:
            self.log(f"[red]initial capture failed:[/red] {e}")
            return 2

        loop = asyncio.get_running_loop()

        def _stop() -> None:
            self.stop_event.set()
            self.monitor.stop()

        for s in (signal.SIGTERM,):
            try:
                loop.add_signal_handler(s, _stop)
            except NotImplementedError:
                pass

        # Background: monitor pane. We hook BOTH callbacks so approval
        # prompts get caught the moment they appear (on_change) even when
        # Claude's UI keeps the pane animating and on_stable never fires.
        monitor_task = asyncio.create_task(
            self.monitor.watch(
                on_change=self.on_change,
                on_stable=self.on_stable,
            )
        )

        # Foreground: input loop, with patch_stdout so log() / Rich prints
        # appear above the input bar instead of clobbering it.
        with patch_stdout(raw=True):
            input_task = asyncio.create_task(self._input_loop())
            try:
                done, pending = await asyncio.wait(
                    {monitor_task, input_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                for t in done:
                    exc = t.exception()
                    if exc:
                        self.log(f"[red]task error:[/red] {exc}")
            finally:
                if self.telegram is not None:
                    try:
                        await self.telegram.stop()
                    except Exception as e:  # noqa: BLE001
                        self.log(f"[dim]Telegram shutdown: {e}[/dim]")
                if self._reset_task is not None and not self._reset_task.done():
                    self._reset_task.cancel()
                self.store.log_event("session_end", events=self.store.event_count)
                self.store.close()
                self.interaction_log.close()

        return 0


# --- entry point -----------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    if args.list_panes:
        try:
            panes = list_panes()
        except SessionPickerError as e:
            console.print(f"[red]session error:[/] {e}")
            return 2
        if not panes:
            console.print("[yellow]no tmux panes found — is tmux running?[/]")
            return 0
        table = Table(title="tmux panes", show_lines=False, header_style="bold")
        table.add_column("target", style="cyan")
        table.add_column("cmd", style="magenta")
        table.add_column("title")
        for p in panes:
            table.add_row(p.target, p.current_command, p.title or "(none)")
        console.print(table)
        return 0

    try:
        policy = PolicyEngine(
            resolve_policy_path(args.policy),
            profile_override=args.profile,
        )
    except PolicyEngineError as e:
        console.print(f"[red]policy error:[/] {e}")
        return 2

    # Phase 4: if the user gave us no session hint at all, run the
    # startup picker (banner + arrow-key menu). They can still pass
    # --session or --auto-detect to skip it.
    resume_from = None
    if args.session is None and not args.auto_detect:
        from cldx.memory import Memory
        from cldx.startup import run_startup
        try:
            choice = await run_startup(policy, Memory(), console=console)
            pane = choice.pane
            resume_from = choice.resume_from
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold]bye.[/]")
            return 0
    else:
        try:
            pane = pick_session(cli_arg=args.session, auto_detect=args.auto_detect)
        except SessionPickerError as e:
            console.print(f"[red]session error:[/] {e}")
            return 2

    pane_info = next((p for p in list_panes() if p.target == pane), None)
    if args.auto_detect:
        # Print what we selected so the user can confirm it's the right pane
        # (useful when --auto-detect found exactly one match and skipped the
        # picker — they might still have meant a different session).
        title = pane_info.title if pane_info else ""
        console.print(
            f"[dim]→ auto-detected pane [cyan]{pane}[/cyan]"
            + (f"  ({title})" if title else "")
            + "[/dim]"
        )

    ui = BridgeUI(args, pane, pane_info, policy)
    if resume_from is not None:
        ui.resume_from = resume_from
    return await ui.run()


def _run_setup_subcommand(args: argparse.Namespace) -> int:
    """Dispatch `cldx setup [target]`. All wizards return cleanly on Ctrl-C."""
    from cldx.setup_wizard import (
        run_anthropic_setup,
        run_bedrock_setup,
        run_disable_llm,
        run_full_setup,
        run_gemini_setup,
        run_llm_setup,
        run_telegram_setup,
    )
    try:
        if args.target == "anthropic":
            run_anthropic_setup(console=console)
        elif args.target == "bedrock":
            run_bedrock_setup(console=console)
        elif args.target == "gemini":
            run_gemini_setup(console=console)
        elif args.target == "llm":
            run_llm_setup(console=console)
        elif args.target == "none":
            run_disable_llm(console=console)
        elif args.target == "telegram":
            run_telegram_setup(console=console)
        else:  # "all"
            run_full_setup(console=console)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[bold]bye.[/]")
        return 130
    return 0


def _run_config_subcommand(args: argparse.Namespace) -> int:
    """Dispatch `cldx config show` (only action so far)."""
    from cldx.setup_wizard import show_config
    if args.action == "show":
        show_config(console=console)
    return 0


def _run_test_subcommand(args: argparse.Namespace) -> int:
    """Dispatch `cldx test <target>`."""
    if args.target == "llm":
        from cldx.llm_test import run_llm_test
        return asyncio.run(run_llm_test(console=console))
    return 0


def main() -> None:
    # Load any saved secrets into the process environment before parsing
    # subcommands, so wizards / bridge / summarizer all see them.
    load_into_environ()

    args = parse_cli_args()

    # Subcommand dispatch
    if args.cmd == "setup":
        sys.exit(_run_setup_subcommand(args))
    if args.cmd == "config":
        sys.exit(_run_config_subcommand(args))
    if args.cmd == "test":
        sys.exit(_run_test_subcommand(args))

    # Default: run the bridge.
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
