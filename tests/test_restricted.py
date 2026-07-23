"""Restricted assistant (Stage 3): prompt, launcher argv, profile fields, no-creds
sandbox, backoff, and the daemon keep-alive loop (relaunch / login-needed cap /
once-per-down-transition / ABS START refusal). Fakes + one gated real-docker test.
No real claude, telegram, login, or (except the gated test) docker.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from absd.config import DaemonConfig, restricted_backoff, validate, ConfigError
from absd.daemon import (
    RESTRICTED_CONTROL_REFUSED,
    RESTRICTED_LOGIN_NEEDED_MSG,
    STATE_KEEP_ALIVE_DOWN,
    STATE_SESSION_LIVE,
)
from absd.flow import build_sandbox_launcher_argv
from absd.prompts import RESTRICTED_SYSTEM_PROMPT, RESTRICTED_UPGRADE_MESSAGE
from absd.profiles import Profile, discover
from absd.sandbox import CRED_DIR, SandboxManager
from tests.conftest import write_profile
from tests.harness.fake_telegram import FakeTelegram
from tests.test_flow_e2e import FakeEngine, make_poller


# ---- helpers -----------------------------------------------------------------


def write_restricted_profile(abs_home: Path, name: str = "default", *, sandbox: str = "asst",
                             paused: bool = False, allow_ids=None) -> None:
    """A profile marked exactly as `abs restricted create` writes it."""
    write_profile(abs_home, name, allow_ids=allow_ids or [42])
    rc_path = abs_home / "profiles" / name / "rc.json"
    rc = json.loads(rc_path.read_text())
    rc.update({"restricted": True, "model": "haiku", "sandbox": sandbox, "keep_alive": True})
    if paused:
        rc["paused"] = True
    rc_path.write_text(json.dumps(rc))


class FakeSandbox:
    """A SandboxManager fake with controllable in-box login state (Stage 3)."""

    def __init__(self, *, creds: bool = True) -> None:
        self._boxes = {"asst": "running"}
        self.started: list[str] = []
        self.creds = creds

    def docker_available(self) -> bool:
        return True

    def image_present(self) -> bool:
        return True

    def host_workdir(self, name: str) -> str:
        return f"/sb/{name}"

    def ensure_running(self, name: str) -> None:
        self.started.append(name)
        self._boxes[name] = "running"

    def creds_present(self, name: str) -> bool:
        return self.creds

    def session_exec_argv(self, name: str, launcher_args: list[str]) -> list[str]:
        return ["docker", "exec", "-it", f"absd-sbx-{name}", "absd-session", *launcher_args]


class DeadEngine(FakeEngine):
    """create_session 'succeeds' but the session is never alive — models claude
    exiting instantly inside the box (e.g. not logged in)."""

    def is_alive(self, profile, pane_id=None) -> bool:
        return False


async def fast_sleep(_d: float) -> None:
    import asyncio
    await asyncio.sleep(0)


def make_restricted_poller(abs_home, client_factory, engine, sandbox, **cfg_kw):
    poller = make_poller(
        abs_home, client_factory, engine=engine, sandbox_mgr=sandbox,
        session_start_grace_s=0.0, **cfg_kw,
    )
    return poller


# ---- 1. restricted prompt content (pure) -------------------------------------


def test_prompt_contains_upgrade_message() -> None:
    assert RESTRICTED_UPGRADE_MESSAGE in RESTRICTED_SYSTEM_PROMPT


def test_prompt_has_no_code_rule_and_no_session_control() -> None:
    p = RESTRICTED_SYSTEM_PROMPT.lower()
    assert "refuse" in p or "do not write" in p
    assert "code" in p
    # session control disclaimer
    assert "start" in p and "stop" in p
    assert "operator" in p


def test_prompt_matches_bundled_file() -> None:
    # Single source: the constant is read from the file the Dockerfile bakes in.
    f = Path(__file__).resolve().parents[1] / "docker" / "sandbox" / "restricted-prompt.txt"
    assert RESTRICTED_UPGRADE_MESSAGE in f.read_text()


# ---- 2. launcher argv shape --------------------------------------------------


def test_launcher_argv_restricted_haiku() -> None:
    argv = build_sandbox_launcher_argv("asst", away=False, model="haiku", restricted=True)
    assert argv[0] == "asst"
    assert "--restricted" in argv
    assert argv[argv.index("--model") + 1] == "haiku"


def test_launcher_argv_normal_unchanged() -> None:
    # A normal sandbox session (3.2) passes none of the new options → byte-for-byte
    # what 3.2 built.
    assert build_sandbox_launcher_argv("p", away=True, resume=True) == [
        "p", "--permission-mode", "acceptEdits", "--continue",
    ]


def test_launcher_argv_append_system_prompt() -> None:
    argv = build_sandbox_launcher_argv("p", away=False, append_system_prompt="EXTRA")
    assert argv[argv.index("--append-system-prompt") + 1] == "EXTRA"


# ---- 3. restricted profile fields round-trip ---------------------------------


def test_profile_restricted_fields(abs_home: Path) -> None:
    write_restricted_profile(abs_home, "assistant", sandbox="asst")
    prof = Profile.load("assistant", abs_home, abs_home)
    assert prof.is_restricted() is True
    assert prof.keep_alive() is True
    assert prof.model() == "haiku"
    assert prof.sandbox_name() == "asst"
    assert prof.is_paused() is False


def test_profile_paused_field(abs_home: Path) -> None:
    write_restricted_profile(abs_home, "assistant", paused=True)
    prof = Profile.load("assistant", abs_home, abs_home)
    assert prof.is_paused() is True


def test_normal_profile_not_restricted(abs_home: Path) -> None:
    write_profile(abs_home, "plain", allow_ids=[42])
    prof = Profile.load("plain", abs_home, abs_home)
    assert prof.is_restricted() is False
    assert prof.keep_alive() is False
    assert prof.model() is None


def test_restricted_list_cli(abs_home: Path, capsys) -> None:
    from absd.restricted import list_restricted

    write_restricted_profile(abs_home, "helper", sandbox="helperbox")
    write_profile(abs_home, "plain", allow_ids=[7])
    rows = list_restricted(abs_home, home=abs_home)
    assert [r["name"] for r in rows] == ["helper"]
    assert rows[0]["sandbox"] == "helperbox"
    assert rows[0]["model"] == "haiku"


# ---- 4. restricted_backoff (pure) --------------------------------------------


def test_restricted_backoff_grows_and_caps() -> None:
    assert restricted_backoff(0, 5.0, 120.0) == 5.0
    assert restricted_backoff(1, 5.0, 120.0) == 10.0
    assert restricted_backoff(2, 5.0, 120.0) == 20.0
    assert restricted_backoff(10, 5.0, 120.0) == 120.0  # capped
    assert restricted_backoff(-1, 5.0, 120.0) == 5.0
    assert restricted_backoff(3, 0.0, 120.0) == 0.0


def test_config_restricted_validation() -> None:
    with pytest.raises(ConfigError):
        validate(DaemonConfig(restricted_relaunch_cap=0))
    with pytest.raises(ConfigError):
        validate(DaemonConfig(keep_alive_check_s=0))
    with pytest.raises(ConfigError):
        validate(DaemonConfig(restricted_relaunch_backoff_max_s=1, restricted_relaunch_backoff_s=5))


# ---- 5. no-creds sandbox create (mocked docker) ------------------------------


class _RecordingManager(SandboxManager):
    """SandboxManager whose docker calls are recorded, not run."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(abs_home=tmp_path / "abs", sandbox_root=tmp_path / "sb")
        (tmp_path / "abs" / "daemon").mkdir(parents=True)
        self.calls: list[list[str]] = []

    def _run(self, argv, check=True, timeout=None):
        self.calls.append(list(argv))
        class _P:
            returncode = 0
            stdout = ""
            stderr = ""
        return _P()

    def image_present(self) -> bool:
        return True

    def is_created(self, name: str) -> bool:
        return False


def _has_cp(calls: list[list[str]]) -> bool:
    return any(len(c) >= 2 and c[1] == "cp" for c in calls)


def test_create_no_creds_skips_cred_copy(tmp_path: Path) -> None:
    mgr = _RecordingManager(tmp_path)
    # A real creds source EXISTS but must be ignored when no_creds=True.
    creds = tmp_path / "creds"; creds.mkdir()
    (creds / ".credentials.json").write_text("{}")
    mgr.create("asst", creds_src=creds, no_creds=True)
    assert not _has_cp(mgr.calls), "no_creds create must not docker cp credentials"
    # sanity: it DID create the container
    assert any(c[1] == "create" for c in mgr.calls)


def test_create_with_creds_does_copy(tmp_path: Path) -> None:
    mgr = _RecordingManager(tmp_path)
    creds = tmp_path / "creds"; creds.mkdir()
    (creds / ".credentials.json").write_text("{}")
    mgr.create("normal", creds_src=creds, no_creds=False)
    assert _has_cp(mgr.calls), "default create must docker cp credentials in"


# ---- 6. daemon keep-alive: relaunch (not idle-poll) --------------------------


async def test_keep_alive_relaunches_with_haiku_restricted(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_restricted_profile(abs_home, "default", sandbox="asst")
    engine = FakeEngine()
    sandbox = FakeSandbox(creds=True)
    poller = make_restricted_poller(abs_home, client_factory, engine, sandbox)

    poller.session_state = STATE_KEEP_ALIVE_DOWN
    await poller._keep_alive_step(fast_sleep)

    # It LAUNCHED the restricted session (did not idle-poll for a flow).
    assert poller.session_state == STATE_SESSION_LIVE
    assert len(engine.created) == 1
    cmd = engine.created[0]["command"]
    # docker exec … absd-session <profile> --restricted --model haiku
    assert "--restricted" in cmd
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert "absd-sbx-asst" in cmd  # exec targets the dedicated sandbox container
    # No Telegram polling happened on the relaunch path.
    assert fake.getupdates_calls == 0
    # No ABS START flow machinery was touched.
    assert poller.flow is None


# ---- 7. keep-alive: failed relaunches hit the cap → login-needed, stop --------


async def test_keep_alive_cap_then_login_needed(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_restricted_profile(abs_home, "default", sandbox="asst")
    engine = DeadEngine()  # every launch dies instantly (not-logged-in box)
    sandbox = FakeSandbox(creds=True)
    poller = make_restricted_poller(
        abs_home, client_factory, engine, sandbox,
        restricted_relaunch_cap=2, restricted_relaunch_backoff_s=0.0,
    )
    poller.session_state = STATE_KEEP_ALIVE_DOWN

    # Drive cycles until it gives up. Each fast death: relaunch cycle then a watch
    # cycle that detects death. Cap=2 → after 2 fast deaths it stops relaunching.
    launches = 0
    for _ in range(12):
        await poller._keep_alive_step(fast_sleep)
        launches = len(engine.created)
        if poller._relaunch_failures >= 2 and poller._login_notified:
            break

    assert poller._relaunch_failures == 2, "cap reached"
    # It stopped relaunching at the cap — no runaway relaunch loop.
    assert launches == 2
    # The operator was told to log in.
    login_msgs = [m for m in fake.sent_messages if "needs login" in m.get("text", "")]
    assert len(login_msgs) == 1
    assert login_msgs[0]["text"] == RESTRICTED_LOGIN_NEEDED_MSG.format(name="default")

    # A few more cycles must NOT relaunch again and NOT re-send the login message.
    for _ in range(5):
        await poller._keep_alive_step(fast_sleep)
    assert len(engine.created) == 2
    login_msgs = [m for m in fake.sent_messages if "needs login" in m.get("text", "")]
    assert len(login_msgs) == 1  # once per down-transition, no spam


# ---- 8. keep-alive: creds absent → login-needed once, recovers after login ----


async def test_keep_alive_no_creds_then_login(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_restricted_profile(abs_home, "default", sandbox="asst")
    engine = FakeEngine()
    sandbox = FakeSandbox(creds=False)  # box not logged in yet
    poller = make_restricted_poller(abs_home, client_factory, engine, sandbox)
    poller.session_state = STATE_KEEP_ALIVE_DOWN

    # While not logged in: never launches; notifies exactly once.
    for _ in range(4):
        await poller._keep_alive_step(fast_sleep)
    assert len(engine.created) == 0
    login_msgs = [m for m in fake.sent_messages if "needs login" in m.get("text", "")]
    assert len(login_msgs) == 1

    # Operator logs in → next cycle relaunches on its own.
    sandbox.creds = True
    await poller._keep_alive_step(fast_sleep)  # detects creds transition (resets)
    await poller._keep_alive_step(fast_sleep)  # relaunch
    assert len(engine.created) == 1
    assert poller.session_state == STATE_SESSION_LIVE


# ---- 9. keep-alive: ABS START refused in the down-window (no normal launch) ---


async def test_restricted_refuses_abs_start_when_down(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    write_restricted_profile(abs_home, "default", sandbox="asst")
    engine = FakeEngine()
    sandbox = FakeSandbox(creds=False)  # down + waiting on login → polls
    poller = make_restricted_poller(abs_home, client_factory, engine, sandbox)
    poller.session_state = STATE_KEEP_ALIVE_DOWN

    fake.queue_message("ABS START", from_id=42)
    await poller._keep_alive_step(fast_sleep)

    # Refused with the control message — NOT a normal flow / session launch.
    refusals = [m for m in fake.sent_messages if m.get("text") == RESTRICTED_CONTROL_REFUSED]
    assert len(refusals) == 1
    assert poller.flow is None
    assert len(engine.created) == 0
    assert poller.session_state == STATE_KEEP_ALIVE_DOWN


async def test_restricted_refuses_control_from_non_operator_dropped(
    abs_home: Path, fake: FakeTelegram, client_factory
) -> None:
    # Allowlist first (D10): a non-allowlisted sender gets NOTHING (no refusal leak).
    write_restricted_profile(abs_home, "default", sandbox="asst", allow_ids=[42])
    engine = FakeEngine()
    sandbox = FakeSandbox(creds=False)
    poller = make_restricted_poller(abs_home, client_factory, engine, sandbox)
    poller.session_state = STATE_KEEP_ALIVE_DOWN

    fake.queue_message("ABS START", from_id=999)  # not allowlisted
    await poller._keep_alive_step(fast_sleep)
    # The non-operator sender got NOTHING — no refusal, no offline note (no liveness
    # leak). (The operator's own login-needed DM is a separate, expected message.)
    assert not any(
        m.get("chat_id") == 999 or m.get("text") == RESTRICTED_CONTROL_REFUSED
        for m in fake.sent_messages
    )


# ---- 10. gated real-docker: a no-creds box has NO credentials inside ----------


def _docker_ok() -> bool:
    try:
        return subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, timeout=10,
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


@pytest.mark.skipif(not _docker_ok(), reason="docker not available")
def test_integration_no_creds_box_has_no_credentials(tmp_path: Path) -> None:
    """Real: create a --no-creds sandbox, start it, and confirm there is NO
    credentials file inside — the restricted box's containment layer. Full teardown."""
    from absd.sandbox import CONTAINER_PREFIX

    name = f"absd-rtest-{uuid.uuid4().hex[:8]}"
    container = f"{CONTAINER_PREFIX}{name}"
    mgr = SandboxManager(abs_home=(tmp_path / "abs"), sandbox_root=(tmp_path / "sb"))
    (tmp_path / "abs" / "daemon").mkdir(parents=True)
    fake_claude = tmp_path / "fake-claude"; fake_claude.mkdir()
    (fake_claude / ".credentials.json").write_text('{"token":"SHOULD-NOT-BE-COPIED"}')
    try:
        mgr.build()  # cached (image present)
        mgr.create(name, creds_src=fake_claude, no_creds=True)
        mgr.start(name)
        assert mgr.is_running(name)
        # No credentials file inside the box.
        probe = subprocess.run(
            ["docker", "exec", container, "test", "-e", f"{CRED_DIR}/.credentials.json"],
            capture_output=True, timeout=20,
        )
        assert probe.returncode != 0, "no_creds box must NOT contain a credentials file"
        # And the manager's own login check agrees.
        assert mgr.creds_present(name) is False
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)
