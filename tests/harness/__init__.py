"""Local test harness for the ABS v3 daemon.

Everything here is a *fake* the automated suite runs against so no test ever
touches real Telegram or Anthropic traffic (PLAN.md section 10):

  - ``fake-claude`` (bash): mimics the Claude Code process shape.
  - ``fake_telegram.FakeTelegram`` (aiohttp): mimics the Bot API surface the
    daemon uses, with offset tracking and fault injection.
"""
