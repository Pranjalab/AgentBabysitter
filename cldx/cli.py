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
from cldx.tmux_controller import TmuxController
from cldx.tmux_monitor import TmuxMonitor

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
    p.add_argument("--session", help="Target pane, e.g. '0:0.0'.")
    p.add_argument("--auto-detect", action="store_true",
                   help="Find the first pane running Claude Code.")
    p.add_argument("--profile",
                   help="Override active profile (default/yolo/restricted/paranoid).")
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

    async def on_stable(self, snapshot: str) -> None:
        """Pane settled. Mirror it, classify it, then act per policy."""
        async with self._action_lock:
            self._print_mirror(snapshot)
            prompt = self.classifier.classify(snapshot)

            if prompt.type in (PromptType.IDLE, PromptType.RUNNING):
                return

            if prompt.type == PromptType.COMPLETE:
                self.log("[green]✓ task complete[/green]")
                self.store.log_event("complete")
                self.pending = None
                self.pending_signature = None
                return

            decision = self.policy.decide(prompt)
            sig = prompt.signature()
            if sig == self.pending_signature:
                return  # same prompt as last fire — already handled / awaiting
            self.pending_signature = sig

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

        if decision.decision == PolicyDecision.AUTO_YES:
            outcome = await _act_yes(self.controller, prompt)
            self.log(f"[green]→ {outcome}[/green]")
            self.store.log_action(keys=outcome, source="policy")
            self.pending = None
        elif decision.decision == PolicyDecision.AUTO_NO:
            outcome = await _act_no(self.controller, prompt)
            self.log(f"[red]→ {outcome}[/red]")
            self.store.log_action(keys=outcome, source="policy")
            self.pending = None
        elif decision.decision == PolicyDecision.ESCALATE_TELEGRAM:
            self.pending = prompt
            self.log(f"[yellow]→ waiting for your reply (reason: {decision.reason})[/yellow]")
        else:
            self.pending = prompt
            self.log(f"[dim]→ waiting (reason: {decision.reason})[/dim]")

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
                self.pending = None
                self._update_prompt_label()
                return
            if low in ("n", "no"):
                async with self._action_lock:
                    outcome = await _act_no(self.controller, pending)
                self.log(f"[red]→ {outcome}[/red]  (you said no)")
                self.store.log_action(keys=outcome, source="user_terminal")
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
                "/profile <name> switch policy profile; /panes list tmux panes; "
                "/raw <keys> send named tmux keys (e.g. 'C-c'); "
                "/quit exit. Anything else types into Claude."
            )
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

        # Background: monitor pane.
        monitor_task = asyncio.create_task(
            self.monitor.watch(on_stable=self.on_stable)
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
        pane = pick_session(cli_arg=args.session, auto_detect=args.auto_detect)
    except SessionPickerError as e:
        console.print(f"[red]session error:[/] {e}")
        return 2

    pane_info = next((p for p in list_panes() if p.target == pane), None)

    try:
        policy = PolicyEngine(
            resolve_policy_path(args.policy),
            profile_override=args.profile,
        )
    except PolicyEngineError as e:
        console.print(f"[red]policy error:[/] {e}")
        return 2

    ui = BridgeUI(args, pane, pane_info, policy)
    return await ui.run()


def main() -> None:
    args = parse_cli_args()
    try:
        sys.exit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
