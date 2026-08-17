"""Step 2.2 — inline-keyboard multi-select on the pool step.

Before this, picking a subset of pooled messages meant typing ``send 1,3`` on a
phone. Now each message gets a ☐/☑ toggle. Two things must stay true:

  - **The one-tap path survives.** With nothing ticked the action button still
    reads "Send all" and still means all — a pure multi-select would have made the
    common case (send everything) cost N+1 taps.
  - **Ticking is not deciding.** A tap repaints the same screen; only the action
    row launches. Getting that wrong forwards a half-made selection.

The keyboard shape and the parser are pure and tested directly; the toggle
round-trip is driven through the real ``Poller`` against fakes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from absd import flow as flow_mod
from absd.daemon import STATE_SESSION_LIVE, Poller
from absd.flow import (
    CB_POOL_ALL,
    CB_POOL_SEND,
    CB_POOL_SKIP,
    CB_POOL_TOGGLE_PREFIX,
    POOL_TOGGLE_MAX,
    build_pool_keyboard,
    parse_pool_choice,
    render_pool_selection,
)
from absd.registry import Registry
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller


def _labels(kb: dict) -> list[str]:
    return [b["text"] for row in kb["inline_keyboard"] for b in row]


def _datas(kb: dict) -> list[str]:
    return [b["callback_data"] for row in kb["inline_keyboard"] for b in row]


# ---- the keyboard ------------------------------------------------------------


def test_every_message_gets_a_toggle() -> None:
    kb = build_pool_keyboard(["alpha", "beta", "gamma"])
    assert _datas(kb)[:3] == [
        f"{CB_POOL_TOGGLE_PREFIX}1",
        f"{CB_POOL_TOGGLE_PREFIX}2",
        f"{CB_POOL_TOGGLE_PREFIX}3",
    ]
    assert _labels(kb)[0].startswith("☐ 1. alpha")


def test_ticked_rows_show_a_check() -> None:
    kb = build_pool_keyboard(["alpha", "beta"], selected={2})
    labels = _labels(kb)
    assert labels[0].startswith("☐ 1.")
    assert labels[1].startswith("☑ 2.")


def test_nothing_ticked_keeps_the_one_tap_send_all() -> None:
    """The whole reason the text protocol was pleasant. It must not regress."""
    kb = build_pool_keyboard(["a", "b"])
    assert "📤 Send all" in _labels(kb)
    assert CB_POOL_ALL in _datas(kb)


def test_ticking_switches_the_action_to_send_n() -> None:
    kb = build_pool_keyboard(["a", "b", "c"], selected={1, 3})
    assert "📤 Send 2" in _labels(kb)
    assert CB_POOL_SEND in _datas(kb)
    assert CB_POOL_ALL not in _datas(kb)


def test_skip_is_always_offered() -> None:
    for selected in ({}, {1}):
        kb = build_pool_keyboard(["a"], selected=set(selected))
        assert CB_POOL_SKIP in _datas(kb)


def test_a_large_pool_drops_the_toggles_rather_than_wall_the_screen() -> None:
    texts = [f"msg {i}" for i in range(POOL_TOGGLE_MAX + 1)]
    kb = build_pool_keyboard(texts)
    assert not any(d.startswith(CB_POOL_TOGGLE_PREFIX) for d in _datas(kb))
    assert _datas(kb) == [CB_POOL_ALL, CB_POOL_SKIP]
    # …and the text tells the user the typed protocol still works.
    assert "send 1,3" in render_pool_selection(texts)


def test_exactly_the_max_still_gets_toggles() -> None:
    texts = [f"msg {i}" for i in range(POOL_TOGGLE_MAX)]
    kb = build_pool_keyboard(texts)
    assert sum(d.startswith(CB_POOL_TOGGLE_PREFIX) for d in _datas(kb)) == POOL_TOGGLE_MAX


def test_no_arguments_keeps_the_old_two_button_row() -> None:
    """Callers that never learned about multi-select must keep working."""
    assert _datas(build_pool_keyboard()) == [CB_POOL_ALL, CB_POOL_SKIP]


def test_button_labels_are_truncated_not_wrapped() -> None:
    kb = build_pool_keyboard(["x" * 400])
    label = _labels(kb)[0]
    assert len(label) < 40 and label.endswith("…")


def test_callback_data_stays_inside_telegrams_64_byte_limit() -> None:
    kb = build_pool_keyboard([f"m{i}" for i in range(POOL_TOGGLE_MAX)], selected={1})
    assert all(len(d.encode("utf-8")) <= 64 for d in _datas(kb))


# ---- the parser --------------------------------------------------------------


def test_toggle_callback_parses_to_an_index() -> None:
    assert parse_pool_choice(f"{CB_POOL_TOGGLE_PREFIX}2", "", 3) == ("toggle", 2)


def test_send_callback_parses() -> None:
    assert parse_pool_choice(CB_POOL_SEND, "", 3) == "send"


@pytest.mark.parametrize("raw", ["0", "9", "x", "", "1e0", "-1"])
def test_a_stale_toggle_index_is_ignored_not_misapplied(raw: str) -> None:
    """A keyboard from a larger pool must not tick a different message."""
    assert parse_pool_choice(f"{CB_POOL_TOGGLE_PREFIX}{raw}", "", 3) is None


def test_the_typed_protocol_is_unchanged() -> None:
    assert parse_pool_choice(None, "send 1,3", 3) == [1, 3]
    assert parse_pool_choice(None, "send all", 3) == "all"
    assert parse_pool_choice(None, "skip", 3) == "skip"


def test_ticks_are_mirrored_in_the_text_body() -> None:
    body = render_pool_selection(["alpha", "beta"], selected={2})
    assert "\n1. alpha" in body
    assert "☑ 2. beta" in body


# ---- the round-trip through the poller --------------------------------------


async def _to_pool_step(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path, n: int
) -> Poller:
    """Pool ``n`` messages, then run ABS START as far as the pool step."""
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"
    proj.mkdir(exist_ok=True)
    Registry(abs_home / "daemon" / "registry.json").add(proj)
    poller = make_poller(abs_home, client_factory, engine=FakeEngine())

    for i in range(n):
        fake.queue_message(f"pooled {i}", from_id=42)
        await poller.poll_once()

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:p:0", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()
    assert poller.flow is not None and poller.flow.step == "pool"
    return poller


async def test_toggle_repaints_in_place_and_does_not_launch(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    poller = await _to_pool_step(abs_home, fake, client_factory, tmp_path, 3)
    sent_before = len(fake.sent_messages)

    fake.queue_callback_query(f"{CB_POOL_TOGGLE_PREFIX}2", from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.flow is not None and poller.flow.step == "pool"  # still deciding
    assert poller.session_state != STATE_SESSION_LIVE
    assert poller.flow.pool_selected == {2}
    assert len(fake.sent_messages) == sent_before  # edited, not re-sent
    assert fake.edited_messages, "the screen should have been edited in place"
    kb = fake.edited_messages[-1]["reply_markup"]["inline_keyboard"]
    assert [b["text"] for row in kb for b in row][1].startswith("☑ 2.")


async def test_toggling_twice_unticks(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    poller = await _to_pool_step(abs_home, fake, client_factory, tmp_path, 3)
    for _ in range(2):
        fake.queue_callback_query(f"{CB_POOL_TOGGLE_PREFIX}1", from_id=42, chat_id=42)
        await poller.poll_once()
    assert poller.flow is not None and poller.flow.pool_selected == set()


async def test_send_forwards_exactly_the_ticked_messages(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    poller = await _to_pool_step(abs_home, fake, client_factory, tmp_path, 3)
    for i in (1, 3):
        fake.queue_callback_query(f"{CB_POOL_TOGGLE_PREFIX}{i}", from_id=42, chat_id=42)
        await poller.poll_once()

    fake.queue_callback_query(CB_POOL_SEND, from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE
    prompt = poller.handoff_marker_path.exists()  # marker written
    assert prompt
    cmd = " ".join(FakeEngineCommand(poller))
    assert "pooled 0" in cmd and "pooled 2" in cmd
    assert "pooled 1" not in cmd


def FakeEngineCommand(poller: Poller) -> list[str]:
    """The argv the engine was asked to run (the last created session)."""
    engine = poller.engine
    assert engine is not None and engine.created  # type: ignore[attr-defined]
    return engine.created[-1]["command"]  # type: ignore[attr-defined]


async def test_send_all_still_works_with_nothing_ticked(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    poller = await _to_pool_step(abs_home, fake, client_factory, tmp_path, 3)
    fake.queue_callback_query(CB_POOL_ALL, from_id=42, chat_id=42)
    await poller.poll_once()
    assert poller.session_state == STATE_SESSION_LIVE
    cmd = " ".join(FakeEngineCommand(poller))
    for i in range(3):
        assert f"pooled {i}" in cmd


async def test_skip_after_ticking_forwards_nothing(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    """The tick is a proposal; Skip overrules it. Forwarding a ticked message the
    user then skipped would deliver something they explicitly declined."""
    poller = await _to_pool_step(abs_home, fake, client_factory, tmp_path, 3)
    fake.queue_callback_query(f"{CB_POOL_TOGGLE_PREFIX}1", from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query(CB_POOL_SKIP, from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE
    cmd = " ".join(FakeEngineCommand(poller))
    assert "pooled" not in cmd
    # …and nothing was marked forwarded, so it is all still in the pool (D14).
    assert len(poller.pool.unforwarded()) == 3


async def test_typed_selection_still_works_alongside_the_keyboard(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    poller = await _to_pool_step(abs_home, fake, client_factory, tmp_path, 3)
    fake.queue_message("send 2", from_id=42)
    await poller.poll_once()
    assert poller.session_state == STATE_SESSION_LIVE
    cmd = " ".join(FakeEngineCommand(poller))
    assert "pooled 1" in cmd and "pooled 0" not in cmd
