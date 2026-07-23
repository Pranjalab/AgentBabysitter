"""Pool forwarding as a flow step before HANDOFF (Step 1.7 corrections 1 + 2)."""

from __future__ import annotations

from pathlib import Path

from absd import flow as flow_mod
from absd.daemon import STATE_SESSION_LIVE, Poller
from absd.pool import PooledMessage, utc_now_iso
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, _register, make_poller


def _seed_pool(poller: Poller, *texts: str) -> None:
    for i, t in enumerate(texts, start=1):
        poller.pool.append(PooledMessage(i, 42, t, utc_now_iso()))


def _prompt_from_argv(command: list[str]) -> str | None:
    if "--prompt" not in command:
        return None
    return command[command.index("--prompt") + 1]


async def _flow_to_mode(poller, fake, proj_cb="as:p:0"):
    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()
    fake.queue_callback_query(proj_cb, from_id=42, chat_id=42)
    await poller.poll_once()
    fake.queue_callback_query("as:m:n", from_id=42, chat_id=42)
    await poller.poll_once()


async def test_no_pool_no_selection_straight_handoff(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)

    await _flow_to_mode(poller, fake)  # no pool → mode completes straight to handoff
    assert poller.session_state == STATE_SESSION_LIVE
    assert "--prompt" not in engine.created[0]["command"]


async def test_send_all_forwards_as_initial_prompt(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)
    _seed_pool(poller, "first task", "second task")

    await _flow_to_mode(poller, fake)
    # mode done → pool-selection step (not yet handed off)
    assert poller.flow is not None and poller.flow.step == "pool"
    assert "waiting" in fake.sent_messages[-1]["text"]
    assert len(engine.created) == 0

    fake.queue_callback_query("as:pool:all", from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE
    prompt = _prompt_from_argv(engine.created[0]["command"])
    assert prompt is not None
    assert prompt.startswith(flow_mod.OFFLINE_PROMPT_PREFIX)
    assert "first task" in prompt and "second task" in prompt
    # forwarded_at set only after the successful launch; kept in the file (D14)
    assert poller.pool.unforwarded() == []
    assert len(poller.pool.read_all()) == 2


async def test_pick_subset_forwards_selected_only(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)
    _seed_pool(poller, "alpha", "beta", "gamma")

    await _flow_to_mode(poller, fake)
    fake.queue_message("send 1,3", from_id=42)  # numbered pick
    await poller.poll_once()

    prompt = _prompt_from_argv(engine.created[0]["command"])
    assert "alpha" in prompt and "gamma" in prompt and "beta" not in prompt
    # only the two forwarded are marked; beta stays unforwarded (D14)
    unfwd = [m.text for m in poller.pool.unforwarded()]
    assert unfwd == ["beta"]


async def test_skip_keeps_all_unforwarded(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)
    _seed_pool(poller, "keep me", "and me")

    await _flow_to_mode(poller, fake)
    fake.queue_callback_query("as:pool:skip", from_id=42, chat_id=42)
    await poller.poll_once()

    assert poller.session_state == STATE_SESSION_LIVE
    assert "--prompt" not in engine.created[0]["command"]
    assert len(poller.pool.unforwarded()) == 2  # nothing forwarded (D14)


async def test_prompt_is_one_argv_element_exact_content(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    # quotes / newline / emoji must survive as ONE argv element, exact content.
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)
    tricky = 'he said "run it"\nline two 🎉 & <tag>'
    _seed_pool(poller, tricky)

    await _flow_to_mode(poller, fake)
    fake.queue_callback_query("as:pool:all", from_id=42, chat_id=42)
    await poller.poll_once()

    cmd = engine.created[0]["command"]
    assert cmd.count("--prompt") == 1
    prompt = cmd[cmd.index("--prompt") + 1]
    assert prompt == flow_mod.OFFLINE_PROMPT_PREFIX + "\n" + tricky  # exact, one element


async def test_forwarded_only_on_successful_launch(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    # engine fails → messages stay UNforwarded (D14).
    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    engine = FakeEngine(fail=True)
    poller = make_poller(abs_home, client_factory, engine=engine)
    _seed_pool(poller, "must survive")

    await _flow_to_mode(poller, fake)
    fake.queue_callback_query("as:pool:all", from_id=42, chat_id=42)
    await poller.poll_once()

    assert engine.created == []  # launch failed
    assert len(poller.pool.unforwarded()) == 1  # NOT marked forwarded


async def test_resume_path_also_offers_selection(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    from absd.recents import Recents

    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "llm"; proj.mkdir()
    Recents(abs_home / "daemon" / "recents.json").record("default", str(proj), "llm", "normal")
    engine = FakeEngine()
    poller = make_poller(abs_home, client_factory, engine=engine)
    _seed_pool(poller, "resume-time task")

    fake.queue_message("ABS START", from_id=42)
    await poller.poll_once()  # recents screen
    fake.queue_callback_query("as:r:0", from_id=42, chat_id=42)  # resume top
    await poller.poll_once()
    assert poller.flow is not None and poller.flow.step == "pool"  # selection before handoff
    fake.queue_callback_query("as:pool:all", from_id=42, chat_id=42)
    await poller.poll_once()

    cmd = engine.created[0]["command"]
    assert "--continue" in cmd and "--prompt" in cmd  # resume AND pool prompt
    assert "resume-time task" in cmd[cmd.index("--prompt") + 1]


async def test_pool_selection_honors_flow_timeout(
    abs_home: Path, fake: FakeTelegram, client_factory, tmp_path: Path
) -> None:
    from absd.daemon import FLOW_EXPIRED_MSG

    write_profile(abs_home, allow_ids=[42])
    proj = tmp_path / "web"; proj.mkdir()
    _register(abs_home, proj)
    now = [1000.0]
    poller = make_poller(
        abs_home, client_factory, engine=FakeEngine(),
        clock=lambda: now[0], flow_timeout_s=300.0,
    )
    _seed_pool(poller, "pending")

    await _flow_to_mode(poller, fake)
    assert poller.flow.step == "pool"
    now[0] += 301  # past the timeout while on the selection screen
    fake.queue_message("send all", from_id=42)
    await poller.poll_once()

    assert poller.flow is None  # expired
    assert any(m["text"] == FLOW_EXPIRED_MSG for m in fake.sent_messages)
    assert poller.pool.unforwarded()  # not forwarded (expired, D14)
