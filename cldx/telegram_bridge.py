"""Telegram bridge — outbound notifications + inbound reply routing.

When configured (``~/.cldx/config/telegram.env``), cldx will:

- Send a summary to your chat when a policy decision needs human input
  (``ESCALATE_TELEGRAM`` or a destructive op that bypassed the wait bar).
- Accept replies (``y`` / ``n`` / ``<digit>`` / free-form text) from
  the same chat and route them into the tmux pane as if you'd typed
  them at the terminal.
- Notify you when a task completes while you're away.

Auth boundary: messages from chat IDs other than the configured one are
silently dropped — preventing strangers who guess your bot name from
controlling your Claude session.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from cldx._paths import cldx_home
from cldx.agent import Agent
from cldx.policy_engine import DecisionResult
from cldx.prompt_classifier import ClassifiedPrompt
from cldx.summarizer import summarize_with_status
from cldx.telegram_commands import dispatch as dispatch_command
from cldx.telegram_templates import (
    ApprovalCard,
    CompletionCard,
    EscalationCard,
    approval_message,
    completion_message,
    escalation_message,
    greeting_message,
)


# --- Config loader --------------------------------------------------------


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    approval_timeout_seconds: int = 600
    timeout_action: str = "auto_no"

    @classmethod
    def from_environ(cls) -> "TelegramConfig | None":
        """Read from ``os.environ``. Returns None if either var is unset.

        Pair with ``cldx.secrets.load_into_environ()`` at startup so the
        ``~/.cldx/config/telegram.env`` file is automatically reachable.
        """
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return None
        return cls(
            bot_token=token,
            chat_id=chat,
            approval_timeout_seconds=int(
                os.environ.get("TELEGRAM_APPROVAL_TIMEOUT_SECONDS", "600")
            ),
            timeout_action=os.environ.get("TELEGRAM_TIMEOUT_ACTION", "auto_no"),
        )

    @classmethod
    def from_env_file(cls, path: Path | None = None) -> "TelegramConfig | None":
        """Read ``~/.cldx/config/telegram.env``. Returns None if unconfigured.

        Expected format::

            TELEGRAM_BOT_TOKEN=xxx
            TELEGRAM_CHAT_ID=123456
            APPROVAL_TIMEOUT_SECONDS=600
            TIMEOUT_ACTION=auto_no
        """
        path = path or (cldx_home() / "config" / "telegram.env")
        if not path.exists():
            return None
        values: dict[str, str] = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip().strip('"').strip("'")

        token = values.get("TELEGRAM_BOT_TOKEN")
        chat = values.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return None
        return cls(
            bot_token=token,
            chat_id=chat,
            approval_timeout_seconds=int(values.get("APPROVAL_TIMEOUT_SECONDS", 600)),
            timeout_action=values.get("TIMEOUT_ACTION", "auto_no"),
        )


# --- Reply parsing --------------------------------------------------------


_DIGIT_RE = re.compile(r"^\d$")


@dataclass(frozen=True)
class ParsedReply:
    kind: str  # "yes" | "no" | "digit" | "text" | "ignore"
    value: str = ""
    # ``raw_text`` carries the user's exact original message so the
    # handler can fall back to text-injection when there's no pending
    # approval to answer. Without it, a reply like ``y`` or ``1``
    # arrives with ``value=""`` (yes/no) or ``value="1"`` (digit) but
    # we lose what the user actually typed — and we'd have to silently
    # drop the message. With raw_text we can always forward something.
    raw_text: str = ""


def parse_reply(text: str) -> ParsedReply:
    """Translate raw Telegram message text into a routing intent."""
    if text is None:
        return ParsedReply("ignore")
    stripped = text.strip()
    if not stripped:
        return ParsedReply("ignore")

    low = stripped.lower()
    if low in ("y", "yes", "ok", "ack", "👍"):
        return ParsedReply("yes", raw_text=stripped)
    if low in ("n", "no", "stop", "deny", "👎"):
        return ParsedReply("no", raw_text=stripped)
    if _DIGIT_RE.match(low):
        return ParsedReply("digit", value=low, raw_text=stripped)
    return ParsedReply("text", value=stripped, raw_text=stripped)


# --- The bridge -----------------------------------------------------------


# A callback that injects text into the tmux pane / pending prompt. The
# BridgeUI passes its own handler in; tests pass a recording fake.
ReplyHandler = Callable[[ParsedReply, ClassifiedPrompt | None], Awaitable[None]]


class TelegramBridge:
    """Async wrapper around python-telegram-bot.

    Construction never makes a network call — the bot is started by
    ``start()`` and stopped by ``stop()``. ``reply_handler`` is invoked
    on every authorised inbound message; ``notify_*`` methods send
    outbound messages.

    Tests inject a fake ``bot_factory`` so no real Telegram traffic
    happens during ``pytest``.
    """

    def __init__(
        self,
        config: TelegramConfig,
        agent: Agent,
        reply_handler: ReplyHandler,
        bot_factory: Callable[[str], Any] | None = None,
        bridge_ui: Any = None,
    ):
        self.config = config
        self.agent = agent
        self.reply_handler = reply_handler
        self._bot_factory = bot_factory
        self._app: Any = None
        self._pending_prompt: ClassifiedPrompt | None = None
        # Used by slash-command handlers to introspect/mutate BridgeUI
        # state (pending, profile, pause, …). Optional — tests can omit.
        self.bridge_ui = bridge_ui

    @property
    def pending_prompt(self) -> ClassifiedPrompt | None:
        return self._pending_prompt

    # --- lifecycle ---

    def _make_app(self):
        if self._bot_factory is not None:
            return self._bot_factory(self.config.bot_token)
        # Lazy import so the SDK only needs to be installed when actually used.
        from telegram.ext import Application  # type: ignore[import-not-found]
        return Application.builder().token(self.config.bot_token).build()

    async def start(self) -> None:
        self._app = self._make_app()
        # Wire two handlers:
        #   1. CommandHandler — every ``/command`` we recognise routes
        #      to ``cldx.telegram_commands.dispatch``. Crucially these
        #      do NOT get injected into Claude Code.
        #   2. MessageHandler — plain text (no ``/`` prefix) is parsed
        #      as a reply: y / n / digit / text → goes into the pane.
        from telegram.ext import (  # type: ignore[import-not-found]
            CommandHandler,
            MessageHandler,
            filters,
        )
        from cldx.telegram_commands import COMMANDS as _COMMANDS

        for name in _COMMANDS:
            self._app.add_handler(CommandHandler(name, self._on_telegram_command))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_telegram_message)
        )
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app is None:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._app = None

    # --- outbound ---

    async def notify_approval_needed(
        self, prompt: ClassifiedPrompt, decision: DecisionResult
    ) -> None:
        """Summarise the prompt and ask the user via Telegram.

        If the LLM call fails for any reason, the raw (truncated) pane
        context is sent instead — no ``[unsummarized: …]`` marker leaks
        into the chat; the fallback reason is logged locally only.
        """
        self._pending_prompt = prompt
        ctx = prompt.context or prompt.extracted_command or prompt.raw_text or ""
        result = await summarize_with_status("prompt_summary", ctx, self.agent)
        if not result.summarized:
            self._log_local(
                f"LLM summary unavailable ({result.fallback_reason}); "
                f"sending raw context to Telegram."
            )
        # Risk: prefer the ToolCall's refined value (e.g. Bash → destructive
        # for `rm -rf`) when present; fall back to the policy decision's
        # is_destructive flag for prompts without a typed tool.
        tool = getattr(prompt, "tool", None)
        if tool is not None:
            risk = tool.risk
        elif getattr(decision, "is_destructive", False):
            risk = "destructive"
        else:
            risk = "normal"

        # If we have a typed tool, build a richer command line that
        # surfaces the icon + category for instant phone-glance scanning.
        if tool is not None:
            command = f"{tool.icon} {tool.name} · {tool.category}\n{tool.args}"
        else:
            command = prompt.extracted_command or ""

        card = ApprovalCard(
            command=command,
            summary=result.text,
            risk=risk,
            menu_options=tuple(prompt.menu_options or ()),
            profile=getattr(decision, "profile", "") or "",
        )
        await self._send(approval_message(card))

    async def notify_completion(
        self, context: str, task: str = "", duration_s: float = 0.0,
        profile: str = "",
    ) -> None:
        result = await summarize_with_status("completion_summary", context, self.agent)
        if not result.summarized:
            self._log_local(
                f"LLM summary unavailable ({result.fallback_reason}); "
                f"sending raw context."
            )
        card = CompletionCard(
            task=task, summary=result.text,
            duration_s=duration_s, profile=profile,
        )
        await self._send(completion_message(card))

    async def notify_escalation(
        self, context: str, command: str = "", reason: str = "",
        profile: str = "",
    ) -> None:
        result = await summarize_with_status("escalation_summary", context, self.agent)
        if not result.summarized:
            self._log_local(
                f"LLM summary unavailable ({result.fallback_reason}); "
                f"sending raw context."
            )
        card = EscalationCard(
            command=command, summary=result.text,
            reason=reason, profile=profile,
        )
        await self._send(escalation_message(card))

    async def notify_greeting(self, bot_username: str = "", profile: str = "") -> None:
        """Send the one-time welcome message after setup completes."""
        await self._send(greeting_message(bot_username=bot_username, profile=profile))

    def _log_local(self, msg: str) -> None:
        """Local-only diagnostic line. Currently prints; BridgeUI may swap
        this for its rich-aware logger by monkey-patching."""
        print(f"[telegram_bridge] {msg}", flush=True)

    async def _send(self, text: str) -> None:
        if self._app is None:
            return  # bridge not started — silently drop
        await self._app.bot.send_message(chat_id=self.config.chat_id, text=text)

    # --- inbound ---

    async def _on_telegram_message(self, update, context) -> None:  # noqa: ANN001
        # Auth boundary: only the configured chat_id may control cldx.
        incoming_chat_id = str(update.effective_chat.id)
        if incoming_chat_id != str(self.config.chat_id):
            return
        reply = parse_reply(update.message.text or "")
        if reply.kind == "ignore":
            return
        prompt = self._pending_prompt
        try:
            await self.reply_handler(reply, prompt)
        finally:
            # Any non-ignore reply clears pending so the next prompt gets a
            # fresh notification.
            if reply.kind in ("yes", "no", "digit"):
                self._pending_prompt = None

    async def _on_telegram_command(self, update, context) -> None:  # noqa: ANN001
        """Slash-command path — these are cldx-only and never get
        injected into Claude Code."""
        incoming_chat_id = str(update.effective_chat.id)
        if incoming_chat_id != str(self.config.chat_id):
            return
        text = (update.message.text or "").strip()
        reply = await dispatch_command(self.bridge_ui, text)
        if reply is None:
            return  # parse_command saw nothing we recognise — silent drop
        await self._send(reply)

    # --- timeout ---

    async def wait_for_reply_or_timeout(
        self, replied_event: asyncio.Event
    ) -> bool:
        """Block until either the reply event fires or the timeout elapses.

        Returns True if a reply arrived in time; False otherwise. Callers use
        the return value to decide whether to fall back to ``timeout_action``.
        """
        try:
            await asyncio.wait_for(
                replied_event.wait(), timeout=self.config.approval_timeout_seconds
            )
            return True
        except asyncio.TimeoutError:
            return False
