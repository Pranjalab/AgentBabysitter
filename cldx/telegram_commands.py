"""Slash-command handlers for the Telegram bridge.

When the user types a command starting with ``/`` in Telegram (e.g.
``/status``, ``/panes``), it is routed here instead of being injected
into Claude Code as text. Each command produces a reply string that
the bridge sends back to the chat.

Design:
- Commands are pure async functions ``handler(bridge, args_text) -> str``.
- ``bridge`` is the live ``BridgeUI`` instance — passed as ``Any`` to
  avoid the import cycle (``cli`` imports this module; this module
  must not import from ``cli``).
- All state mutation (pause, profile switch) goes through ``bridge``
  setters so the rest of the system sees the change immediately.
- Handlers never raise. On failure they return a one-line error string
  that becomes the user-visible reply.

The registry ``COMMANDS`` is what ``TelegramBridge`` reads when wiring
``CommandHandler`` instances into ``python-telegram-bot``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from cldx.telegram_templates import error_message, help_message


CommandHandler = Callable[[Any, str], Awaitable[str]]


# --- individual handlers --------------------------------------------------


async def cmd_help(bridge: Any, _args: str) -> str:
    profile = _profile(bridge)
    pending = _pending_label(bridge)
    return help_message(profile=profile, pending=pending)


async def cmd_status(bridge: Any, _args: str) -> str:
    pane = getattr(bridge, "pane_target", None) or "?"
    profile = _profile(bridge) or "?"
    pending = _pending_label(bridge) or "—"
    paused = "yes" if _is_paused(bridge) else "no"
    lines = [
        "📋 *cldx — status*",
        "━" * 30,
        f"📺 Pane: `{pane}`",
        f"⚙️  Profile: {profile}",
        f"⏸️  Paused: {paused}",
        f"📝 Pending: {pending}",
    ]
    tail = _short_tail(bridge, max_lines=6)
    if tail:
        lines.append("")
        lines.append("Recent pane output:")
        lines.append("```")
        lines.append(tail)
        lines.append("```")
    return "\n".join(lines)


async def cmd_panes(bridge: Any, _args: str) -> str:
    try:
        from cldx.session_picker import list_panes
        panes = list_panes()
    except Exception as e:  # noqa: BLE001
        return error_message("could not list panes", str(e))
    if not panes:
        return "No tmux panes found (start one with `tmux new`)."
    active = getattr(bridge, "pane_target", "") or ""
    lines = ["📺 *cldx — panes*", "━" * 30]
    for p in panes:
        marker = " ← active" if p.target == active else ""
        lines.append(f"  `{p.target}`  {p.current_command}{marker}")
    return "\n".join(lines)


async def cmd_snapshot(bridge: Any, _args: str) -> str:
    snap = ""
    monitor = getattr(bridge, "monitor", None)
    if monitor is not None:
        snap = getattr(monitor, "last_snapshot", "") or ""
    if not snap:
        return "(pane is empty or monitor hasn't captured yet)"
    # Telegram caps messages around 4096 chars; trim to fit comfortably.
    lines = snap.splitlines()[-40:]
    trimmed = "\n".join(lines)
    if len(trimmed) > 3500:
        trimmed = trimmed[-3500:]
    return "📺 *pane snapshot*\n━" * 1 + "\n```\n" + trimmed + "\n```"


async def cmd_stop(bridge: Any, _args: str) -> str:
    controller = getattr(bridge, "controller", None)
    if controller is None:
        return error_message("no controller wired — cannot send ESC")
    try:
        await controller.send_escape()
        # Clear pending so we don't auto-reply to a prompt the user just nuked.
        if hasattr(bridge, "pending"):
            bridge.pending = None
        if hasattr(bridge, "_update_prompt_label"):
            try:
                bridge._update_prompt_label()
            except Exception:  # noqa: BLE001
                pass
        return "✋ Sent ESC — Claude interrupted."
    except Exception as e:  # noqa: BLE001
        return error_message("interrupt failed", str(e))


async def cmd_cancel(bridge: Any, _args: str) -> str:
    """Drop any pending prompt without sending a key — i.e. ignore it
    and wait for the next thing Claude does."""
    if not hasattr(bridge, "pending"):
        return "Nothing pending."
    if bridge.pending is None:
        return "Nothing pending."
    bridge.pending = None
    if hasattr(bridge, "_update_prompt_label"):
        try:
            bridge._update_prompt_label()
        except Exception:  # noqa: BLE001
            pass
    return "🧹 Pending prompt cleared."


async def cmd_yes(bridge: Any, _args: str) -> str:
    """Explicit yes — equivalent to typing ``y`` but unambiguous."""
    return await _dispatch_reply(bridge, kind="yes")


async def cmd_no(bridge: Any, _args: str) -> str:
    return await _dispatch_reply(bridge, kind="no")


async def cmd_pause(bridge: Any, _args: str) -> str:
    if _is_paused(bridge):
        return "Already paused. Use /resume to re-enable auto-approval."
    if not _set_paused(bridge, True):
        return error_message("pause not supported on this build")
    return "⏸️  Paused. Approvals will queue instead of auto-firing."


async def cmd_resume(bridge: Any, _args: str) -> str:
    if not _is_paused(bridge):
        return "Not paused."
    if not _set_paused(bridge, False):
        return error_message("resume not supported on this build")
    return "▶️ Resumed. Auto-approval is back on."


async def cmd_profile(bridge: Any, args: str) -> str:
    """``/profile`` (no args): list available profiles + current.
    ``/profile <name>``: switch to that profile, if valid."""
    policy = getattr(bridge, "policy", None)
    if policy is None:
        return error_message("policy engine not wired")
    profiles = list(getattr(policy, "profiles", {}).keys()) or [policy.active_profile_name]
    current = policy.active_profile_name
    target = args.strip()
    if not target:
        marked = ["*" + p + "*" if p == current else p for p in profiles]
        return "⚙️  Profiles: " + ", ".join(marked) + f"\n(current: *{current}*)"
    if target not in profiles:
        return (
            f"Unknown profile {target!r}. Available: " + ", ".join(profiles)
        )
    if not hasattr(policy, "set_active_profile"):
        return error_message("policy engine doesn't support runtime switching")
    try:
        policy.set_active_profile(target)
    except Exception as e:  # noqa: BLE001
        return error_message("profile switch failed", str(e))
    return f"⚙️  Switched profile to *{target}*."


# --- registry -------------------------------------------------------------


COMMANDS: dict[str, CommandHandler] = {
    "help": cmd_help,
    "start": cmd_help,         # Telegram convention — show help on /start too
    "status": cmd_status,
    "panes": cmd_panes,
    "snapshot": cmd_snapshot,
    "stop": cmd_stop,
    "cancel": cmd_cancel,
    "yes": cmd_yes,
    "no": cmd_no,
    "pause": cmd_pause,
    "resume": cmd_resume,
    "profile": cmd_profile,
}


def is_command(text: str) -> bool:
    """True if ``text`` starts with ``/`` and a known command name."""
    if not text or not text.startswith("/"):
        return False
    name, _, _rest = text[1:].partition(" ")
    name = name.split("@", 1)[0]  # /help@MyBot → help
    return name.lower() in COMMANDS


def parse_command(text: str) -> tuple[str, str] | None:
    """Split ``"/profile yolo"`` → ``("profile", "yolo")``.

    Returns None if the text isn't a known cldx command. Strips the
    Telegram ``@BotName`` suffix that group chats add.
    """
    if not text or not text.startswith("/"):
        return None
    raw, _, args = text[1:].partition(" ")
    name = raw.split("@", 1)[0].lower()
    if name not in COMMANDS:
        return None
    return name, args.strip()


async def dispatch(bridge: Any, text: str) -> str | None:
    """Execute the command in ``text`` against ``bridge``.

    Returns the reply string to send back, or ``None`` if ``text`` isn't
    a recognised cldx command (caller should fall through to the
    regular text-injection path).
    """
    parsed = parse_command(text)
    if parsed is None:
        return None
    name, args = parsed
    handler = COMMANDS[name]
    try:
        return await handler(bridge, args)
    except Exception as e:  # noqa: BLE001 — handler bug must never crash the bot
        return error_message(f"/{name} failed", str(e))


# --- bridge accessors -----------------------------------------------------
#
# Tiny helpers that read state defensively. Handlers stay readable and
# we keep one place to update if BridgeUI's surface shifts.


def _profile(bridge: Any) -> str:
    policy = getattr(bridge, "policy", None)
    if policy is None:
        return ""
    return getattr(policy, "active_profile_name", "") or ""


def _pending_label(bridge: Any) -> str:
    pending = getattr(bridge, "pending", None)
    if pending is None:
        return ""
    cmd = getattr(pending, "extracted_command", "") or ""
    return cmd or getattr(pending, "type", "?")


def _short_tail(bridge: Any, max_lines: int = 6) -> str:
    monitor = getattr(bridge, "monitor", None)
    if monitor is None:
        return ""
    snap = getattr(monitor, "last_snapshot", "") or ""
    if not snap:
        return ""
    return "\n".join(snap.splitlines()[-max_lines:])


def _is_paused(bridge: Any) -> bool:
    return bool(getattr(bridge, "paused", False))


def _set_paused(bridge: Any, value: bool) -> bool:
    """Try to update the paused state. Returns True on success."""
    if hasattr(bridge, "set_paused"):
        try:
            bridge.set_paused(value)
            return True
        except Exception:  # noqa: BLE001
            return False
    if hasattr(bridge, "paused"):
        try:
            bridge.paused = value
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


async def _dispatch_reply(bridge: Any, kind: str) -> str:
    """Route /yes and /no through the same reply path as text replies."""
    from cldx.telegram_bridge import ParsedReply  # local import: avoid cycle
    handler = getattr(bridge, "_telegram_reply_handler", None)
    if handler is None:
        return error_message("no reply handler on bridge")
    if getattr(bridge, "pending", None) is None:
        return "Nothing pending."
    try:
        await handler(ParsedReply(kind=kind), None)
    except Exception as e:  # noqa: BLE001
        return error_message(f"/{kind} failed", str(e))
    return "✅ ok." if kind == "yes" else "❌ ok."
