"""Daemon-side Telegram Bot API client (stub).

Direct Bot API over HTTPS — no bot framework (PLAN.md 4.5 / 4.4). The daemon
needs exactly this surface and nothing more:

  - ``get_updates``   — long-poll for new updates (offset-committing)
  - ``send_message``  — outbound text (pool acks, notices)
  - ``edit_message_text`` — update an existing message (progress edits)
  - ``answer_callback_query`` — ack inline-keyboard taps (the ABS START flow)
  - ``set_my_commands`` — register the "/" command menu for daemon mode

Implementation lands in Step 1.3. The wire shapes (Telegram's update/message
JSON) are the spec (PLAN.md 4.4), so this stub fixes the method surface now and
the automated tests drive it against the local ``fake_telegram`` server from
Step 0.3 — never real Telegram (PLAN.md section 10).

Security (PLAN.md 5.5): the bot token is passed in at construction and must
never be logged or echoed into any message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TelegramClient:
    """Minimal async Bot API client bound to a single bot token.

    ``base_url`` exists so tests can point the client at the local
    ``fake_telegram`` server instead of ``https://api.telegram.org``.
    """

    token: str
    base_url: str = "https://api.telegram.org"

    async def get_updates(
        self, offset: int | None = None, timeout: int = 50
    ) -> list[dict[str, Any]]:
        """Long-poll ``getUpdates``. Returns the raw update objects. (Step 1.3.)"""
        raise NotImplementedError("telegram client lands in Step 1.3")

    async def send_message(
        self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a text message, optionally with an inline keyboard. (Step 1.3.)"""
        raise NotImplementedError("telegram client lands in Step 1.3")

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing message's text/keyboard. (Step 1.3.)"""
        raise NotImplementedError("telegram client lands in Step 1.3")

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None
    ) -> dict[str, Any]:
        """Acknowledge an inline-keyboard callback query. (Step 1.5.)"""
        raise NotImplementedError("telegram client lands in Step 1.3")

    async def set_my_commands(self, commands: list[dict[str, str]]) -> dict[str, Any]:
        """Register the daemon-mode "/" command menu. (Step 1.3.)"""
        raise NotImplementedError("telegram client lands in Step 1.3")
