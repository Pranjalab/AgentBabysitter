"""Message templates for outbound Telegram traffic.

abs pushes three core message kinds to the user's Telegram chat:

1. **Approval requests** — Claude wants to run something abs can't
   auto-decide. The user can reply ``y``, ``n``, a digit (for menu
   prompts), free-form text (injected into Claude), or ``/stop`` to
   interrupt. The card shows the tool call, an LLM summary, a risk
   tag, and the exact reply options.

2. **Completions** — Claude finished a task. The card shows the user
   prompt that started the task, a summary of what got done, and a
   nudge to send the next task.

3. **Escalations** — same as approval but tagged so the user knows
   we already tried policy and need a human call.

Two ancillary templates round things out: a one-time **greeting** sent
during setup, and a generic **error** card for surfacing internal
problems (LLM down, tmux pane vanished, etc.).

Each template returns plain text (no Telegram markdown / HTML modes) —
Telegram renders the emoji+separator structure fine in every client,
and we avoid edge cases where Markdown parsing breaks on user input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


# Visual separator. A row of em-dashes; Telegram renders them clean in
# both compact and expanded views and they survive copy-paste.
_RULE = "━" * 30


# --- helpers --------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` chars, adding ``…`` if we trimmed."""
    if text is None:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _block(label: str, value: str) -> str:
    """Render a ``LABEL: value`` line, skipped if value is empty."""
    if not value:
        return ""
    return f"{label}: {value}"


# --- templates ------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalCard:
    """Inputs needed to render an approval message."""
    command: str          # e.g. "Bash(rm -rf /tmp/test)"
    summary: str          # LLM-summarised intent (or raw context if LLM disabled)
    risk: str = "normal"  # "normal" | "medium" | "destructive"
    menu_options: Sequence[str] = ()
    profile: str = ""     # current policy profile name


def approval_message(card: ApprovalCard) -> str:
    """Build the visible chat message for an approval prompt.

    The reply legend at the bottom *always* matches the actual options
    available — if ``menu_options`` is empty we hide the digit row.
    """
    risk_icon = {
        "destructive": "🛑",
        "medium": "⚠️",
    }.get(card.risk, "🟡")

    lines = [
        f"{risk_icon} *Agent Babysitter — approval needed*",
        _RULE,
        _block("🔧 Tool", _truncate(card.command, 200)),
        _block("📝 Summary", _truncate(card.summary, 600)),
    ]
    if card.profile:
        lines.append(_block("⚙️  Profile", card.profile))
    if card.risk and card.risk != "normal":
        lines.append(_block("⚠️  Risk", card.risk))

    if card.menu_options:
        lines.append("")
        lines.append("Options:")
        for i, opt in enumerate(card.menu_options, 1):
            lines.append(f"  {i}. {_truncate(opt, 120)}")

    lines.append("")
    lines.append("Reply:")
    lines.append("  ✅ `y`     approve")
    lines.append("  ❌ `n`     deny")
    if card.menu_options:
        digits = " / ".join(str(i + 1) for i in range(len(card.menu_options)))
        lines.append(f"  🔢 {digits}     pick a menu option")
    lines.append("  💬 *any text*  inject as Claude input")
    lines.append("  ✋ /stop   interrupt Claude")
    lines.append("  ℹ️  /help   show all commands")

    return "\n".join(l for l in lines if l is not None)


@dataclass(frozen=True)
class CompletionCard:
    task: str          # what the user asked Claude to do
    summary: str       # LLM-summarised result (or raw context)
    duration_s: float = 0.0
    profile: str = ""


def completion_message(card: CompletionCard) -> str:
    """Build the visible chat message for a task completion."""
    dur = _format_duration(card.duration_s) if card.duration_s else ""

    lines = [
        "✅ *Agent Babysitter — task complete*",
        _RULE,
    ]
    if card.task:
        lines.append(_block("📌 Task", _truncate(card.task, 200)))
    lines.append(_block("📝 Summary", _truncate(card.summary, 800)))
    if dur:
        lines.append(_block("⏱️  Duration", dur))
    if card.profile:
        lines.append(_block("⚙️  Profile", card.profile))

    lines.append("")
    lines.append("💬 Reply with your next task, or /help for options.")
    return "\n".join(l for l in lines if l is not None)


@dataclass(frozen=True)
class EscalationCard:
    command: str
    summary: str
    reason: str = ""
    profile: str = ""


def escalation_message(card: EscalationCard) -> str:
    """Build the visible chat message when policy can't auto-decide."""
    lines = [
        "🚨 *Agent Babysitter — decision needed*",
        _RULE,
        _block("🔧 Tool", _truncate(card.command, 200)),
        _block("📝 Summary", _truncate(card.summary, 600)),
        _block("🤔 Why escalated", _truncate(card.reason, 200)),
    ]
    if card.profile:
        lines.append(_block("⚙️  Profile", card.profile))

    lines.append("")
    lines.append("Reply:")
    lines.append("  ✅ `y`  approve")
    lines.append("  ❌ `n`  deny")
    lines.append("  💬 *text*  custom instruction")
    lines.append("  ℹ️  /help  show all commands")
    return "\n".join(l for l in lines if l is not None)


def greeting_message(bot_username: str = "", profile: str = "") -> str:
    """Sent once by the setup wizard after a successful test ping.

    Welcomes the user and orients them to the commands they can run.
    """
    lines = [
        "👋 *Welcome to Agent Babysitter!*",
        _RULE,
        "",
        "Thanks for connecting — your bot is live and ready to bridge",
        "approvals from your laptop to this chat.",
        "",
        "Here's what you can do:",
        "",
        "  ✅ `y` / `n`      respond to approval prompts",
        "  💬 *any text*     inject input into Claude Code",
        "  ✋ /stop         interrupt the current Claude task",
        "  📋 /status       show pane + profile + pending state",
        "  📺 /panes        list available tmux panes",
        "  🔄 /profile      switch policy profile",
        "  ⏸️  /pause        queue approvals (don't auto-fire)",
        "  ▶️  /resume       resume auto-handling",
        "  ℹ️  /help         show this list anytime",
        "",
        "Whenever Claude needs human input, you'll get a card here.",
        "Approve from your phone — your laptop stays unattended.",
    ]
    if profile:
        lines.append("")
        lines.append(f"_Current profile: {profile}_")
    return "\n".join(lines)


def error_message(title: str, detail: str = "") -> str:
    """Generic error card — for LLM failures, dropped panes, etc."""
    lines = [
        f"🛑 *Agent Babysitter — {title}*",
        _RULE,
    ]
    if detail:
        lines.append(_truncate(detail, 600))
    return "\n".join(lines)


def help_message(profile: str = "", pending: str = "") -> str:
    """The response to ``/help``. Listed commands match
    ``abs.telegram_commands.COMMANDS``."""
    lines = [
        "ℹ️ *Agent Babysitter — commands*",
        _RULE,
        "",
        "*Approvals & input*",
        "  ✅ `y` / `n`      yes / no on a pending prompt",
        "  💬 *any text*     inject into Claude Code",
        "",
        "*Control*",
        "  /stop           send ESC — interrupt Claude",
        "  /yes /no        explicit y/n (when text is ambiguous)",
        "  /cancel         clear any pending prompt",
        "",
        "*Inspection*",
        "  /status         active pane + profile + pending",
        "  /panes          list tmux panes",
        "  /snapshot       send current pane content",
        "",
        "*Modes*",
        "  /pause          stop auto-approving",
        "  /resume         re-enable auto-approval",
        "  /profile <name> switch policy profile",
        "",
        "*Meta*",
        "  /help           show this message",
    ]
    if profile:
        lines.append("")
        lines.append(f"_Profile: {profile}_")
    if pending:
        lines.append(f"_Pending: {pending}_")
    return "\n".join(lines)


# --- internal -------------------------------------------------------------


def _format_duration(seconds: float) -> str:
    """Render a seconds-count like ``2m 14s`` / ``45s`` / ``1h 3m``."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}m"
