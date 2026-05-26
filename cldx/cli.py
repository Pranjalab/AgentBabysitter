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
import signal
import sys
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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
from cldx.session_store import SessionStore
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

    # Subcommands
    sub = p.add_subparsers(dest="cmd", required=False)

    setup_p = sub.add_parser(
        "setup",
        help="Interactive wizard for Telegram bot + Anthropic API key.",
    )
    setup_p.add_argument(
        "target", nargs="?", default="all",
        choices=("all", "telegram", "llm", "anthropic", "bedrock", "gemini"),
        help="Which integration to configure (default: all = pick LLM + Telegram).",
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
        self._action_lock = asyncio.Lock()

        # Phase 2: jsonl event log for this run.
        self.store = SessionStore(profile=policy.active_profile_name, pane=pane)

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

    # --- printing (safe under patch_stdout) ---

    def log(self, msg: str) -> None:
        # Use console.print so Rich markup ([green]...[/green]) renders.
        # patch_stdout(raw=True) lets ANSI codes pass through cleanly.
        console.print(f"[dim][{_now()}][/dim] {msg}")

    def print_rich(self, renderable) -> None:
        console.print(renderable)

    # --- mirror ---

    def _print_mirror(self, snapshot: str) -> None:
        tail = "\n".join(snapshot.splitlines()[-self.args.mirror_lines:]).rstrip()
        if not tail or tail == self.last_mirror_tail:
            return
        self.last_mirror_tail = tail
        title = f"claude pane @ {_now()}"
        self.print_rich(Panel(tail, title=title, border_style="blue", expand=False))

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
            prompt = self.classifier.classify(snapshot)
            if prompt.type not in self._EAGER_TYPES:
                return
            await self._dispatch_classified(prompt, snapshot, source="change")

    async def on_stable(self, snapshot: str) -> None:
        """Pane settled. Mirror it; also act as a safety net if an approval
        prompt arrived without ever triggering an on_change diff (rare)."""
        async with self._action_lock:
            self._print_mirror(snapshot)
            prompt = self.classifier.classify(snapshot)

            if prompt.type in (PromptType.IDLE, PromptType.RUNNING):
                return

            if prompt.type == PromptType.COMPLETE:
                self.log("[green]✓ task complete[/green]")
                self.store.log_event("complete")
                if self.telegram is not None:
                    try:
                        await self.telegram.notify_completion(snapshot)
                    except Exception as e:  # noqa: BLE001
                        self.log(f"[red]Telegram completion notify failed: {e}[/red]")
                self.pending = None
                self.pending_signature = None
                return

            await self._dispatch_classified(prompt, snapshot, source="stable")

    async def _dispatch_classified(
        self, prompt: ClassifiedPrompt, snapshot: str, source: str,
    ) -> None:
        """Common path: dedup by signature, persist, hand off to policy."""
        decision = self.policy.decide(prompt)
        sig = prompt.signature()
        if sig == self.pending_signature:
            return  # same prompt as last fire — already handled / awaiting
        self.pending_signature = sig

        if self.args.verbose if hasattr(self.args, "verbose") else False:
            self.log(f"[dim]· detected {prompt.type.value} via {source}[/dim]")

        # Phase 2: persist every actionable prompt + its decision.
        self.store.log_prompt(prompt)
        self.store.log_decision(decision)

        await self._handle_decision(prompt, decision)

    async def _handle_decision(
        self, prompt: ClassifiedPrompt, decision: DecisionResult
    ) -> None:
        color, label = DECISION_STYLE[decision.decision]
        cmd = prompt.extracted_command or prompt.raw_text or "(?)"
        self.log(f"[{color}][{label}][/{color}] {prompt.type.value}: {cmd}")
        if prompt.menu_options:
            for opt in prompt.menu_options:
                self.log(f"    {opt}")

        if self.args.dry_run:
            self.pending = prompt
            self.log(f"[dim]dry-run: not acting (reason: {decision.reason})[/dim]")
            self._update_prompt_label()
            return

        # Phase 3: destructive ops bypass the wait bar entirely.
        if decision.is_destructive and decision.decision in (
            PolicyDecision.AUTO_YES, PolicyDecision.AUTO_NO
        ):
            self.pending = prompt
            self.log(
                f"[red bold]⚠ destructive op detected[/red bold] — "
                f"waiting indefinitely for your reply (policy would have "
                f"{decision.decision.value})"
            )
            self.store.log_note("destructive op pending — bypassed auto")
            if self.telegram is not None:
                try:
                    await self.telegram.notify_approval_needed(prompt, decision)
                    self.log("[cyan]→ destructive op alert sent to Telegram[/cyan]")
                except Exception as e:  # noqa: BLE001
                    self.log(f"[red]Telegram send failed: {e}[/red]")
            self._update_prompt_label()
            return

        if decision.decision == PolicyDecision.AUTO_YES:
            await self._auto_with_wait(prompt, decision, action="yes")
        elif decision.decision == PolicyDecision.AUTO_NO:
            await self._auto_with_wait(prompt, decision, action="no")
        elif decision.decision == PolicyDecision.ESCALATE_TELEGRAM:
            self.pending = prompt
            self.log(f"[yellow]→ waiting for your reply (reason: {decision.reason})[/yellow]")
            if self.telegram is not None:
                try:
                    await self.telegram.notify_approval_needed(prompt, decision)
                    self.log("[cyan]→ sent to Telegram[/cyan]")
                except Exception as e:  # noqa: BLE001
                    self.log(f"[red]Telegram send failed: {e}[/red]")
        else:
            self.pending = prompt
            self.log(f"[dim]→ waiting (reason: {decision.reason})[/dim]")

        self._update_prompt_label()

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

        color = "green" if action == "yes" else "red"
        self.log(
            f"[{color}]⏳ auto-{action} in {interval:.1f}s[/{color}] "
            f"[dim](type to override)[/dim]"
        )

        # For longer waits, emit a midpoint heartbeat so the user knows
        # we're still alive. Animation isn't safe under patch_stdout, so
        # we just log discrete checkpoints rather than redrawing.
        midpoint_logged = {"done": False}

        def heartbeat(remaining: float, total: float) -> None:
            if midpoint_logged["done"]:
                return
            if remaining <= total / 2:
                midpoint_logged["done"] = True
                self.log(
                    f"[dim]  …{remaining:.1f}s remaining (still time to override)[/dim]"
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
        self.telegram = TelegramBridge(
            cfg, agent,
            reply_handler=self._telegram_reply_handler,
        )
        try:
            await self.telegram.start()
            self.log(f"[green]✓ Telegram bridge connected (chat {cfg.chat_id})[/green]")
        except Exception as e:  # noqa: BLE001
            self.log(f"[red]Failed to start Telegram bridge: {e}[/red]")
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

            if current is None and kind in ("yes", "no", "digit"):
                self.log(
                    f"[dim]Telegram reply {kind!r} arrived but nothing's pending — "
                    f"ignored.[/dim]"
                )
                return

            if kind == "yes" and current is not None:
                outcome = await _act_yes(self.controller, current)
                self.log(f"[green]→ {outcome}[/green] [dim](via Telegram)[/dim]")
                self.store.log_action(keys=outcome, source="user_telegram")
                self._learn_from_user(current, approve=True)
                self.pending = None

            elif kind == "no" and current is not None:
                outcome = await _act_no(self.controller, current)
                self.log(f"[red]→ {outcome}[/red] [dim](via Telegram)[/dim]")
                self.store.log_action(keys=outcome, source="user_telegram")
                self._learn_from_user(current, approve=False)
                self.pending = None

            elif kind == "digit" and current is not None:
                await self.controller.send_digit(int(value))
                self.log(f"[cyan]→ sent option {value}[/cyan] [dim](via Telegram)[/dim]")
                self.store.log_action(keys=f"digit:{value}", source="user_telegram")
                self.pending = None

            elif kind == "text":
                await self.controller.send_text(value)
                self.log(f"[cyan]→ injected:[/cyan] {value} [dim](via Telegram)[/dim]")
                self.store.log_action(keys=f"text:{value}", source="user_telegram")
                self.pending = None

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
        if action == "yes":
            outcome = await _act_yes(self.controller, prompt)
            self.log(f"[green]→ {outcome}[/green]")
        else:
            outcome = await _act_no(self.controller, prompt)
            self.log(f"[red]→ {outcome}[/red]")
        self.store.log_action(keys=outcome, source="policy")
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

    async def _input_loop(self) -> None:
        self.log("[dim]ready. /help for commands. Ctrl-D to quit.[/dim]")
        while not self.stop_event.is_set():
            try:
                text = await self.session.prompt_async(
                    lambda: self._prompt_label()
                )
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
            self.log(
                "commands: /y /n /<digit>  approve/deny pending prompt; "
                "/skip clear pending; /refresh reprint mirror; "
                "/snapshot show what cldx currently thinks the pane contains; "
                "/profile <name> switch policy profile; /panes list tmux panes; "
                "/raw <keys> send named tmux keys (e.g. 'C-c'); "
                "/quit exit. Anything else types into Claude."
            )
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
                self.store.log_event("session_end", events=self.store.event_count)
                self.store.close()

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
    # startup picker (banner + numbered menu). They can still pass
    # --session or --auto-detect to skip it.
    resume_from = None
    if args.session is None and not args.auto_detect:
        from cldx.memory import Memory
        from cldx.startup import run_startup
        try:
            choice = run_startup(policy, Memory(), console=console)
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
