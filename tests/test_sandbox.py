"""Docker sandbox lifecycle (absd/sandbox.py) — unit shape + gated integration."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

from absd.sandbox import (
    BOX_ABS_DIR,
    BOX_ABS_HOME,
    BOX_HOME,
    BOX_LAUNCHER_PATH,
    CONTAINER_PREFIX,
    CRED_DIR,
    IMAGE_TAG,
    PLUGIN_META_FILES,
    WORKDIR,
    SandboxError,
    SandboxInfo,
    SandboxManager,
    parse_ports,
)


def _mgr(tmp_path: Path) -> SandboxManager:
    abs_home = tmp_path / "abs"
    (abs_home / "daemon").mkdir(parents=True)
    return SandboxManager(abs_home=abs_home, sandbox_root=tmp_path / "sandboxes")


# ---- name jail (reuses flow.validate_folder_name) ----------------------------


@pytest.mark.parametrize(
    "name",
    ["../evil", "/etc", "a b", "..", "", "café", "a/b"],
)
def test_create_rejects_bad_names(tmp_path: Path, name: str) -> None:
    # A bad name is rejected by the SAME jail as the ABS START flow, BEFORE any
    # docker call — so this never creates a container even when the image exists.
    mgr = _mgr(tmp_path)
    with pytest.raises(SandboxError) as e:
        mgr.create(name)
    assert "bad sandbox name" in str(e.value)


def test_valid_names_pass_the_jail() -> None:
    # Valid names pass the name jail (no docker call here — create() is real docker).
    from absd import flow

    for good in ("test1", "my-box_2", "a", "x" * 64):
        ok, _ = flow.validate_folder_name(good)
        assert ok, good


# ---- ports parser ------------------------------------------------------------


def test_parse_ports_valid() -> None:
    assert parse_ports("3000:3000,8080:80") == ["3000:3000", "8080:80"]
    assert parse_ports("3000:3000") == ["3000:3000"]
    assert parse_ports("") == []
    assert parse_ports(None) == []
    assert parse_ports(" 3000:3000 , 8080:80 ") == ["3000:3000", "8080:80"]


@pytest.mark.parametrize(
    "bad",
    ["3000", "3000:", ":80", "abc:80", "3000:80;rm", "$(x):80", "3000:80 8080:80",
     "99999999:80", "0:80", "3000:0"],
)
def test_parse_ports_rejects(bad: str) -> None:
    with pytest.raises(SandboxError):
        parse_ports(bad)


# ---- argv shape (the security surface — no docker) ---------------------------


def test_create_argv_security_shape(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr.create_argv("box", "/host/work", ["3000:3000"])
    assert "--privileged" not in argv                       # 5.6
    assert argv.count("-v") == 1                             # exactly one mount
    assert argv[argv.index("-v") + 1] == f"/host/work:{WORKDIR}"
    assert "--user" in argv and argv[argv.index("--user") + 1] == "dev"
    assert "/var/run/docker.sock" not in " ".join(argv)     # no socket mount
    assert "--restart" in argv and argv[argv.index("--restart") + 1] == "no"
    assert argv[-1] == IMAGE_TAG                             # no command → image CMD
    assert argv[2] == "--name" and argv[3] == f"{CONTAINER_PREFIX}box"
    # ports published
    assert argv[argv.index("-p") + 1] == "3000:3000"


def test_create_argv_no_ports_no_p(tmp_path: Path) -> None:
    argv = _mgr(tmp_path).create_argv("box", "/w", [])
    assert "-p" not in argv


def test_exec_argv_shape(tmp_path: Path) -> None:
    argv = _mgr(tmp_path).exec_argv("box", ["claude", "--help"])
    assert argv[:4] == [argv[0], "exec", "-it", f"{CONTAINER_PREFIX}box"]
    assert argv[-2:] == ["claude", "--help"]


def test_session_exec_argv_shape(tmp_path: Path) -> None:
    # 3.2: docker exec -it into the box running the in-container launcher; NO host
    # mounts added here (the only bind is from create).
    argv = _mgr(tmp_path).session_exec_argv("box", ["default", "--continue", "the prompt"])
    assert argv[:4] == [argv[0], "exec", "-it", f"{CONTAINER_PREFIX}box"]
    assert argv[4] == "absd-session"
    assert argv[5:] == ["default", "--continue", "the prompt"]
    assert "-v" not in argv and "--mount" not in argv  # no new mounts


def test_build_argv_shape(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    argv = mgr.build_argv()
    assert argv[1] == "build"
    assert "-t" in argv and argv[argv.index("-t") + 1] == IMAGE_TAG
    assert any(a.startswith("UID=") for a in argv)  # host uid build arg
    assert "--no-cache" not in argv
    assert "--no-cache" in mgr.build_argv(rebuild=True)


# ---- metadata ----------------------------------------------------------------


def test_metadata_roundtrip_and_0600(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    info = SandboxInfo(
        name="box", host_workdir="/w", ports=["3000:3000"],
        created_at="2026-07-23T00:00:00Z", image_tag=IMAGE_TAG,
    )
    mgr._write_meta({"box": info.to_dict()})
    assert stat.S_IMODE(mgr.meta_path.stat().st_mode) == 0o600
    got = mgr._read_meta()
    assert got["box"]["host_workdir"] == "/w"
    assert got["box"]["ports"] == ["3000:3000"]


def test_meta_corrupt_reads_empty(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    mgr.meta_path.write_text("{ not json ]")
    assert mgr._read_meta() == {}
    assert mgr.list() == []


# ---- credential-copy sanitising (the in-box Telegram fix) --------------------
#
# Live-test finding: a wholesale `docker cp ~/.claude` drags HOST-absolute paths
# into a container whose home is /home/dev. `plugins/known_marketplaces.json`
# records installLocation=<host home>/.claude/plugins/... — unresolvable in the
# box, so the marketplace fails to load ("cache-miss"),
# plugin:telegram@claude-plugins-official can't resolve, and the sandbox session
# runs with NO Telegram channel: the box looks alive but is deaf. These tests pin
# the sanitising so that regression can't come back silently.


class _CpRecorder:
    """Stands in for ``SandboxManager._run``: records argv, and for a ``docker cp``
    of a temp file captures the file's CONTENT (``_cp_text`` deletes it right after,
    so it must be read here)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.copied: dict[str, str] = {}   # box path -> content

    def __call__(self, argv: list[str], check: bool = True, timeout: float | None = None):
        self.calls.append(list(argv))
        if len(argv) >= 4 and argv[1] == "cp" and ":" in argv[3]:
            srcp = Path(argv[2])
            if srcp.is_file():
                self.copied[argv[3].split(":", 1)[1]] = srcp.read_text(encoding="utf-8")

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        return _P()


def _creds_fixture(tmp_path: Path, host_home: Path) -> Path:
    """A miniature ~/.claude with the pieces the sanitiser touches."""
    src = host_home / ".claude"
    (src / "plugins").mkdir(parents=True)
    (src / "plugins" / "known_marketplaces.json").write_text(json.dumps({
        "claude-plugins-official": {
            "source": {"source": "github", "repo": "anthropics/claude-plugins-official"},
            "installLocation": f"{host_home}/.claude/plugins/marketplaces/claude-plugins-official",
        }
    }))
    (src / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "telegram@claude-plugins-official": {
            "path": f"{host_home}/.claude/plugins/cache/claude-plugins-official/telegram/0.0.6"
        }
    }))
    (src / "settings.json").write_text(json.dumps({
        "model": "opus",
        "hooks": {"SessionEnd": [{"command": f"node {host_home}/.ccgram/dist/x.js"}]},
    }))
    (host_home / ".claude.json").write_text(json.dumps({
        "officialMarketplaceAutoInstalled": True,
        "projects": {f"{host_home}/Projects/secret": {"history": ["…"]}},
    }))
    return src


def _run_copy(tmp_path: Path) -> tuple[_CpRecorder, Path]:
    host_home = tmp_path / "home" / "pranjal"
    host_home.mkdir(parents=True)
    src = _creds_fixture(tmp_path, host_home)
    mgr = _mgr(tmp_path)
    rec = _CpRecorder()
    mgr._run = rec  # type: ignore[method-assign]
    mgr._copy_credentials("box", src)
    return rec, host_home


def test_plugin_metadata_is_rehomed_into_the_box(tmp_path: Path) -> None:
    # THE fix: the box's marketplace metadata must point at /home/dev, not the host
    # home — otherwise "cache-miss" and no Telegram channel in the sandbox.
    rec, host_home = _run_copy(tmp_path)
    for base in PLUGIN_META_FILES:
        body = rec.copied[f"{CRED_DIR}/plugins/{base}"]
        assert str(host_home) not in body, f"{base} still carries host paths"
        assert BOX_HOME in body
        json.loads(body)  # still valid JSON
    loc = json.loads(rec.copied[f"{CRED_DIR}/plugins/known_marketplaces.json"])
    assert loc["claude-plugins-official"]["installLocation"] == (
        f"{BOX_HOME}/.claude/plugins/marketplaces/claude-plugins-official"
    )


def test_rehome_skips_file_without_host_paths(tmp_path: Path) -> None:
    host_home = tmp_path / "home" / "p"
    host_home.mkdir(parents=True)
    src = _creds_fixture(tmp_path, host_home)
    # No host prefix inside → nothing to rewrite → no cp for this file.
    (src / "plugins" / "installed_plugins.json").write_text(json.dumps({"a": "b"}))
    mgr = _mgr(tmp_path)
    rec = _CpRecorder()
    mgr._run = rec  # type: ignore[method-assign]
    mgr._rehome_plugin_metadata("box", src)
    assert f"{CRED_DIR}/plugins/installed_plugins.json" not in rec.copied
    assert f"{CRED_DIR}/plugins/known_marketplaces.json" in rec.copied


def test_rehome_never_installs_unparseable_json(tmp_path: Path) -> None:
    host_home = tmp_path / "home" / "p"
    host_home.mkdir(parents=True)
    src = _creds_fixture(tmp_path, host_home)
    (src / "plugins" / "known_marketplaces.json").write_text(f"{{ broken {host_home} ]")
    mgr = _mgr(tmp_path)
    rec = _CpRecorder()
    mgr._run = rec  # type: ignore[method-assign]
    mgr._rehome_plugin_metadata("box", src)
    # Contains the host path, but the rewrite wouldn't parse → leave the copy alone.
    assert f"{CRED_DIR}/plugins/known_marketplaces.json" not in rec.copied


def test_home_config_copied_without_host_project_history(tmp_path: Path) -> None:
    rec, host_home = _run_copy(tmp_path)
    body = json.loads(rec.copied[f"{BOX_HOME}/.claude.json"])
    assert body["officialMarketplaceAutoInstalled"] is True   # kept: needed config
    assert str(host_home) not in json.dumps(body)             # dropped: host detail


def test_box_workspace_is_pre_trusted(tmp_path: Path) -> None:
    # Without this Claude Code blocks on "Is this a project you… trust?" forever —
    # a deadlock for a daemon-launched session (nobody can answer), so the Telegram
    # channel never starts and the box is deaf. Found by live test.
    rec, _ = _run_copy(tmp_path)
    body = json.loads(rec.copied[f"{BOX_HOME}/.claude.json"])
    assert body["projects"][WORKDIR]["hasTrustDialogAccepted"] is True
    assert list(body["projects"]) == [WORKDIR]   # only the box workspace, nothing host


def test_hooks_are_stripped_from_box_settings(tmp_path: Path) -> None:
    rec, host_home = _run_copy(tmp_path)
    body = json.loads(rec.copied[f"{CRED_DIR}/settings.json"])
    assert "hooks" not in body           # host paths would fire "Cannot find module"
    assert body["model"] == "opus"       # everything else preserved


def test_stale_runtime_state_is_cleared_on_start_not_create(tmp_path: Path) -> None:
    # bot.pid names a HOST pid (meaningless in the box's PID namespace) and a copied
    # inbox would replay host messages inside the box. The cleanup MUST run on start,
    # not during create: `docker exec` only works on a RUNNING container, and create
    # leaves the box stopped — doing it at create time silently no-ops (found live).
    rec, _ = _run_copy(tmp_path)
    assert not [c for c in rec.calls if "find" in c], "create must not docker-exec"

    mgr = _mgr(tmp_path / "second")  # fresh abs_home: _mgr mkdirs its daemon dir
    rec2 = _CpRecorder()
    mgr._run = rec2  # type: ignore[method-assign]
    mgr.start("box")
    finds = [c for c in rec2.calls if "find" in c]
    assert any(c[-3:] == ["-name", "bot.pid", "-delete"] for c in finds)
    assert any("*/inbox/*" in c and c[-1] == "-delete" for c in finds)
    for c in finds:
        assert c[1] == "exec" and f"{CRED_DIR}/channels" in c
        assert not any("shell" in part for part in c)


def test_ensure_running_leaves_a_live_box_untouched(tmp_path: Path) -> None:
    # An already-running box may be hosting a live session whose bot.pid is real —
    # never delete it out from under the in-box poller.
    mgr = _mgr(tmp_path)
    rec = _CpRecorder()

    def _run(argv, check=True, timeout=None):
        if argv[1] == "inspect" and "{{.State.Running}}" in argv:
            class _P:
                returncode = 0
                stdout = "true"
                stderr = ""
            return _P()
        return rec(argv, check, timeout)

    mgr._run = _run  # type: ignore[method-assign]
    mgr.ensure_running("box")
    assert not [c for c in rec.calls if "find" in c or c[1] == "start"]


# ---- v4: ABS inside the box --------------------------------------------------
#
# The sandbox is meant to be a complete isolated ABS environment. Without the code
# sync there is no `abs` in the box at all, and the in-box launcher silently falls
# back to bare claude — no status bar, no Bash guard, no ABS EXIT. Without the
# profile seed abs.sh refuses to launch ("profile is not paired"), because that
# state lives under ~/.abs, which the ~/.claude credential copy never touches.


def _abs_home_with_profile(tmp_path: Path, profile: str, rc: dict) -> SandboxManager:
    mgr = _mgr(tmp_path)
    pdir = mgr.abs_home / "profiles" / profile
    pdir.mkdir(parents=True)
    (pdir / "rc.json").write_text(json.dumps(rc), encoding="utf-8")
    return mgr


def test_abs_code_is_synced_into_the_box_at_start(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    rec = _CpRecorder()
    mgr._run = rec  # type: ignore[method-assign]
    mgr.start("box")
    dests = [c[3].split(":", 1)[1] for c in rec.calls if c[1] == "cp"]
    assert f"{BOX_ABS_DIR}/abs.sh" in dests
    assert f"{BOX_ABS_DIR}/absd" in dests
    # The launcher lives on PATH, not in /opt/abs — ship it too, so a launcher fix
    # reaches an existing box without a 4-minute image rebuild.
    assert BOX_LAUNCHER_PATH in dests


def test_directory_sync_copies_contents_so_it_is_idempotent(tmp_path: Path) -> None:
    # `docker cp src dest` NESTS (dest/src) when dest already exists, so the second
    # sync of a long-lived box would build /opt/abs/absd/absd. `src/.` copies the
    # CONTENTS instead, which is stable across any number of runs.
    mgr = _mgr(tmp_path)
    rec = _CpRecorder()
    mgr._run = rec  # type: ignore[method-assign]
    mgr._install_abs_into_box("box")
    dir_cps = [c for c in rec.calls if c[1] == "cp" and c[3].endswith(f"{BOX_ABS_DIR}/absd")]
    assert dir_cps, "absd package was not synced"
    assert dir_cps[0][2].endswith(f"absd{os.sep}."), dir_cps[0][2]


def test_abs_sync_is_skipped_on_a_pre_v4_box(tmp_path: Path) -> None:
    # An old box has no /opt/abs skeleton (no venv either), so a half-copied tree
    # would be worse than nothing: leave it alone and let the launcher fall back.
    mgr = _mgr(tmp_path)
    calls: list[list[str]] = []

    def _run(argv, check=True, timeout=None):
        calls.append(list(argv))

        class _P:
            returncode = 1 if argv[:3] == ["docker", "exec", "absd-sbx-box"] else 0
            stdout = ""
            stderr = ""

        return _P()

    mgr._docker = "docker"
    mgr._run = _run  # type: ignore[method-assign]
    assert mgr._install_abs_into_box("box") is False
    assert not [c for c in calls if c[1] == "cp"]


def test_profile_state_is_seeded_without_host_paths(tmp_path: Path) -> None:
    mgr = _abs_home_with_profile(tmp_path, "work", {
        "chat_id": "12345",
        "bot": "workbot",
        "tg_dir": "/home/pranjal/.claude/channels/telegram-work",
        "voice_sample": "/home/pranjal/voice/me.wav",
        "quiet": False,
    })
    rec = _CpRecorder()
    mgr._run = rec  # type: ignore[method-assign]
    mgr._seed_profile_state("box", "work")
    body = json.loads(rec.copied[f"{BOX_ABS_HOME}/profiles/work/rc.json"])
    assert body["chat_id"] == "12345"        # without this abs.sh refuses to launch
    assert body["bot"] == "workbot"
    # Host-absolute paths are the recurring bug class in this file — unresolvable in
    # the box, so they are dropped rather than copied in.
    assert "tg_dir" not in body
    assert "voice_sample" not in body
    assert "/home/pranjal" not in json.dumps(body)


def test_seeding_an_unknown_profile_is_a_no_op(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    rec = _CpRecorder()
    mgr._run = rec  # type: ignore[method-assign]
    mgr._seed_profile_state("box", "nope")
    assert not rec.copied


def test_prepare_session_seeds_only_when_the_box_has_abs(tmp_path: Path) -> None:
    # Seeding a pre-v4 box would leave pairing state for a launcher that can't use
    # it; the two steps travel together.
    mgr = _abs_home_with_profile(tmp_path, "work", {"chat_id": "1"})
    calls: list[list[str]] = []

    def _run(argv, check=True, timeout=None):
        calls.append(list(argv))

        class _P:
            returncode = 1 if argv[3:4] == ["test"] else 0
            stdout = ""
            stderr = ""

        return _P()

    mgr._run = _run  # type: ignore[method-assign]
    mgr.prepare_session("box", "work")
    assert not [c for c in calls if c[1] == "cp"]


# ---- gated real-docker integration ------------------------------------------


def _docker_ok() -> bool:
    try:
        return subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, timeout=10,
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _inspect(container: str, fmt: str) -> str:
    return subprocess.run(
        ["docker", "inspect", "-f", fmt, container],
        capture_output=True, text=True, timeout=20,
    ).stdout.strip()


@pytest.mark.skipif(not _docker_ok(), reason="docker not available")
def test_integration_create_inspect_bind_creds(tmp_path: Path) -> None:
    """Real end-to-end: build (cached), create a throwaway sandbox, assert the 5.6
    security shape via `docker inspect`, the bind-mount roundtrip, net access, and
    the credential COPY-not-mount divergence. Full teardown even on failure. Uses a
    FIXTURE creds dir — never the real ~/.claude (kept safe; production copies
    ~/.claude, and this proves the same copy/divergence mechanism)."""
    name = f"absd-test-{uuid.uuid4().hex[:8]}"
    container = f"{CONTAINER_PREFIX}{name}"
    mgr = SandboxManager(
        abs_home=(tmp_path / "abs"), sandbox_root=(tmp_path / "sandboxes"),
    )
    (tmp_path / "abs" / "daemon").mkdir(parents=True)
    # fixture credentials (NOT the real ~/.claude)
    fake_claude = tmp_path / "fake-claude"
    fake_claude.mkdir()
    (fake_claude / ".credentials.json").write_text('{"token":"FAKE-SANDBOX-TEST"}')

    try:
        mgr.build()  # cached after the first run
        assert mgr.image_present()
        mgr.create(name, ports=None, creds_src=fake_claude)

        # --- 5.6 security shape via docker inspect ---
        assert _inspect(container, "{{.HostConfig.Privileged}}") == "false"
        assert _inspect(container, "{{.Config.User}}") == "dev"
        binds = json.loads(_inspect(container, "{{json .HostConfig.Binds}}"))
        workdir = str((tmp_path / "sandboxes" / name).resolve())
        assert binds == [f"{workdir}:{WORKDIR}"]  # exactly the one workdir

        mgr.start(name)
        assert mgr.is_running(name)

        # --- bind roundtrip (host <-> container) ---
        (Path(workdir) / "from_host.txt").write_text("hello-from-host")
        seen = subprocess.run(
            ["docker", "exec", container, "cat", f"{WORKDIR}/from_host.txt"],
            capture_output=True, text=True, timeout=20,
        )
        assert seen.stdout.strip() == "hello-from-host"
        subprocess.run(
            ["docker", "exec", container, "sh", "-c", f"echo hi > {WORKDIR}/from_container.txt"],
            capture_output=True, timeout=20,
        )
        assert (Path(workdir) / "from_container.txt").read_text().strip() == "hi"

        # --- credentials: exist inside, owned by dev, and a COPY (divergence) ---
        owner = subprocess.run(
            ["docker", "exec", container, "stat", "-c", "%U", "/home/dev/.claude/.credentials.json"],
            capture_output=True, text=True, timeout=20,
        )
        assert owner.returncode == 0, owner.stderr
        assert owner.stdout.strip() == "dev"
        # modify INSIDE → the fixture host copy is unchanged (proof of copy-not-mount)
        subprocess.run(
            ["docker", "exec", container, "sh", "-c",
             "echo MUTATED > /home/dev/.claude/.credentials.json"],
            capture_output=True, timeout=20,
        )
        assert (fake_claude / ".credentials.json").read_text() == '{"token":"FAKE-SANDBOX-TEST"}'

        # --- net access (default bridge) ---
        net = subprocess.run(
            ["docker", "exec", container, "curl", "-sI", "--max-time", "15", "https://example.com"],
            capture_output=True, text=True, timeout=25,
        )
        assert "HTTP" in net.stdout, f"no net status line: {net.stdout!r} {net.stderr!r}"
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=30)


# ---- login_ok: is the box ACTUALLY authenticated ------------------------------
#
# The bug this exists for: `creds_present` answers "is the file there", which is
# not "can it authenticate". The copy made at create-time expires while the
# host's credentials keep refreshing, and a box in that state starts fine, polls
# Telegram fine, and answers nothing at all.


class _AuthStub:
    """Stands in for `docker exec … claude auth status`."""

    def __init__(self, stdout: str = "", returncode: int = 0, boom: bool = False) -> None:
        self.stdout, self.returncode, self.boom = stdout, returncode, boom
        self.argv: list[str] | None = None

    def __call__(self, argv, check=True, timeout=None):  # noqa: ANN001
        self.argv = argv
        if self.boom:
            raise SandboxError("docker timed out")
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_login_ok_true_when_the_box_reports_logged_in(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    stub = _AuthStub(json.dumps({"loggedIn": True, "subscriptionType": "max"}))
    mgr._run = stub  # type: ignore[method-assign]
    assert mgr.login_ok("box") is True
    # The probe must be `claude auth status` — it costs no inference, unlike
    # actually prompting the model to prove it can reach the API.
    assert stub.argv is not None and stub.argv[-3:] == ["claude", "auth", "status"]


def test_login_ok_false_is_the_case_creds_present_gets_wrong(tmp_path: Path) -> None:
    # The whole point: a box can hold a present, non-empty, well-formed
    # credentials file and still not be logged in.
    mgr = _mgr(tmp_path)
    mgr._run = _AuthStub(json.dumps({"loggedIn": False}))  # type: ignore[method-assign]
    assert mgr.login_ok("box") is False


def test_login_ok_tolerates_chatter_around_the_json(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    mgr._run = _AuthStub('warning: something\n{"loggedIn": true}\nbye\n')  # type: ignore[method-assign]
    assert mgr.login_ok("box") is True


@pytest.mark.parametrize(
    "stub",
    [
        _AuthStub(boom=True),                      # docker error / timeout
        _AuthStub("", returncode=1),               # container not running
        _AuthStub("not json at all", returncode=0),
        _AuthStub(json.dumps({"other": 1})),       # no loggedIn key
        _AuthStub(json.dumps({"loggedIn": "yes"})),  # not a bool — don't guess
    ],
)
def test_login_ok_is_none_when_it_cannot_tell(tmp_path: Path, stub: _AuthStub) -> None:
    # None means "unknown", and every caller must fail open on it. Refusing to
    # launch because a probe broke would be a worse bug than the silence.
    mgr = _mgr(tmp_path)
    mgr._run = stub  # type: ignore[method-assign]
    assert mgr.login_ok("box") is None
