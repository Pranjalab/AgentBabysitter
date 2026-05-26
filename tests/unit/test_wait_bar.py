"""Phase 3 — 2s wait bar with keystroke override.

The bar shows a countdown during `wait_interval_seconds` (per-profile);
any keystroke cancels the timer and routes to manual approval.
Destructive operations bypass the bar entirely (wait forever).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.phase3


@pytest.mark.skip(reason="Phase 3 — wait bar not implemented yet")
async def test_wait_bar_auto_approves_after_interval():
    """If no keystroke arrives within `wait_interval_seconds`, action fires."""


@pytest.mark.skip(reason="Phase 3")
async def test_wait_bar_keystroke_cancels_auto_action():
    """Any keystroke during the countdown → no auto-action; manual takes over."""


@pytest.mark.skip(reason="Phase 3")
async def test_wait_bar_respects_per_profile_interval():
    """Profile A with 3.0s and profile B with 0.5s each use their own value."""


@pytest.mark.skip(reason="Phase 3")
async def test_wait_bar_zero_means_instant_auto():
    """`wait_interval_seconds: 0` skips the bar and fires immediately."""


@pytest.mark.skip(reason="Phase 3")
async def test_destructive_op_skips_wait_bar_entirely():
    """`rm`, `unlink`, `DROP`, etc. must never auto-fire even if profile is auto-approve."""


@pytest.mark.skip(reason="Phase 3")
async def test_keystroke_y_during_bar_approves_manually():
    """`y` during countdown counts as a manual yes (not an auto-yes)."""


@pytest.mark.skip(reason="Phase 3")
async def test_keystroke_text_during_bar_injects_into_pane():
    """Free-form text during countdown cancels auto and injects the text."""
