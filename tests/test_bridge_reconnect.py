"""The Telegram bridge: a conflict is visible, a stray holder is cleared, and a
resume only ever resumes something that exists.

24 Sep, from the terminal: "when the Telegram connection drops it's not able to
reconnect properly", with a paste that also showed a second bug — the menu
offered "Resume reel (3m ago)" and the launch died on "No conversation found to
continue".

Two independent findings, both fixed here:

* **The reconnect.** The plugin retries polling with backoff on any error, so a
  dropped connection heals itself. What it cannot survive is 409 Conflict — a
  second poller on the same bot token — which it gives up on after eight
  attempts, staying alive and deaf. Two pollers is the passive signal for that,
  and it is free to read, so the status bar shows it and a hook clears the stray
  while the plugin is still retrying. `abs reconnect` does the same on purpose
  and says what only a session restart can fix.
* **The resume.** ABS records a recent when a session LAUNCHES; a session nobody
  spoke in leaves a history file of metadata with no messages, so `--continue`
  refuses and the launch dies at a shell prompt. Now the menu checks for a real
  message first and starts fresh in that folder instead.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ABS_SH = REPO / "abs.sh"
PROFILE = "bridge"

USER_LINE = json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}})
META_ONLY = "\n".join(json.dumps({"type": t}) for t in
                      ("system", "mode", "cost-state", "last-prompt", "file-history-snapshot"))


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    abs_home = home / ".abs"
    prof = abs_home / "profiles" / PROFILE
    prof.mkdir(parents=True)
    tg = tmp_path / "tg"
    tg.mkdir()
    (tg / ".env").write_text("TELEGRAM_BOT_TOKEN=123:fake\n")
    (prof / "rc.json").write_text(json.dumps({
        "bot": "testbot", "chat_id": 42, "tg_dir": str(tg), "reply_mode": "text",
    }))
    _home, _abs_home, _tg = home, abs_home, tg

    class Box:
        home = _home
        abs_home = _abs_home
        tg = _tg

        def history(self, project: Path, content: str):
            """Write a Claude Code history file for `project`, as Claude Code does."""
            slug = str(project).replace("/", "-")
            d = home / ".claude" / "projects" / slug
            d.mkdir(parents=True, exist_ok=True)
            (d / "abc123.jsonl").write_text(content + "\n")

        def mcp_log(self, project: Path, stderr_lines):
            slug = str(project).replace("/", "-")
            d = home / ".cache" / "claude-cli-nodejs" / slug / "mcp-logs-plugin-telegram-telegram"
            d.mkdir(parents=True, exist_ok=True)
            (d / "2026-09-24T10-00-00-000Z.jsonl").write_text(
                "\n".join(json.dumps({"error": f"Server stderr: {l}\n"}) for l in stderr_lines) + "\n")

        def probe(self, snippet, cwd=None, **extra):
            src = ABS_SH.read_text().replace('main "$@"\n', "")
            script = tmp_path / "probe.sh"
            script.write_text(src + f'\nuse_profile {PROFILE}\n{snippet}\n')
            env = dict(os.environ, HOME=str(home), ABS_HOME=str(abs_home))
            env.pop("TELEGRAM_STATE_DIR", None)
            env.update(extra)
            return subprocess.run(["bash", str(script)], capture_output=True, text=True,
                                  env=env, cwd=str(cwd or tmp_path))

    return Box()


# ---- the resume that had nothing to resume -------------------------------------


def test_a_folder_whose_last_session_said_nothing_is_not_resumable(box, tmp_path):
    proj = tmp_path / "reel"
    proj.mkdir()
    box.history(proj, META_ONLY)
    r = box.probe(f'has_resumable_conversation "{proj}" && echo YES || echo NO')
    assert r.stdout.strip().endswith("NO"), r.stdout


def test_a_folder_with_a_real_conversation_is(box, tmp_path):
    proj = tmp_path / "work"
    proj.mkdir()
    box.history(proj, META_ONLY + "\n" + USER_LINE)
    assert box.probe(f'has_resumable_conversation "{proj}" && echo YES || echo NO').stdout.strip().endswith("YES")


def test_no_history_at_all_is_not_resumable(box, tmp_path):
    proj = tmp_path / "brand-new"
    proj.mkdir()
    assert box.probe(f'has_resumable_conversation "{proj}" && echo YES || echo NO').stdout.strip().endswith("NO")


def test_the_menu_starts_fresh_instead_of_launching_a_doomed_continue(box, tmp_path):
    """The reported failure: `--continue` printed "No conversation found to
    continue" and the session died at a shell prompt."""
    proj = tmp_path / "reel"
    proj.mkdir()
    box.history(proj, META_ONLY)
    recents = json.dumps([{"path": str(proj), "mode": "normal", "label": "reel"}])
    r = box.probe(f"_start_menu_apply '{recents}' 0; echo CONTINUE=$MENU_CONTINUE CWD=$START_CWD")
    assert "CONTINUE=0" in r.stdout
    assert f"CWD={proj}" in r.stdout
    assert "no conversation to resume" in (r.stdout + r.stderr).lower()


def test_a_real_conversation_still_resumes(box, tmp_path):
    proj = tmp_path / "work"
    proj.mkdir()
    box.history(proj, USER_LINE)
    recents = json.dumps([{"path": str(proj), "mode": "normal", "label": "work"}])
    r = box.probe(f"_start_menu_apply '{recents}' 0; echo CONTINUE=$MENU_CONTINUE")
    assert "CONTINUE=1" in r.stdout


# ---- the bridge: what can be known without asking Telegram ----------------------


def test_one_poller_for_one_live_profile_is_the_healthy_shape(box, tmp_path):
    r = box.probe('pollers_running() { echo 111; }; pollers_accounted() { printf 1; }; '
                  'bridge_health; echo "S=$BRIDGE_STATE D=$BRIDGE_DETAIL"')
    assert "S=unknown" in r.stdout          # nothing claimed either way
    assert "as it should be" in r.stdout


def test_two_pollers_for_two_profiles_is_two_projects_not_a_conflict(box, tmp_path):
    """This test used to assert the opposite, and that is how the bug shipped.

    3.7.2 read two `server.ts` processes as a 409 on the premise that one poller
    per machine is correct — true only with one bot. The operator started a second
    one on 1 Oct, exactly as ABS had just advised, and both sessions showed
    "telegram conflict". A 409 is two pollers on the SAME token; this is two
    pollers on two tokens."""
    r = box.probe('pollers_running() { printf "111\\n222\\n"; }; pollers_accounted() { printf 2; }; '
                  'bridge_health; echo "S=$BRIDGE_STATE D=$BRIDGE_DETAIL"')
    assert "S=retrying" not in r.stdout, r.stdout


def test_a_poller_nobody_accounts_for_is_still_a_conflict(box, tmp_path):
    r = box.probe('pollers_running() { printf "111\\n222\\n"; }; pollers_accounted() { printf 1; }; '
                  'bridge_health; echo "S=$BRIDGE_STATE D=$BRIDGE_DETAIL"')
    assert "S=retrying" in r.stdout
    assert "unaccounted" in r.stdout


def test_the_plugins_own_words_win_when_the_log_has_them(box, tmp_path):
    proj = tmp_path / "logged"
    proj.mkdir()
    box.mcp_log(proj, ["telegram channel: polling as @testbot"])
    r = box.probe('bridge_health; echo "S=$BRIDGE_STATE"', cwd=proj)
    assert "S=healthy" in r.stdout

    box.mcp_log(proj, ["telegram channel: polling as @testbot",
                       "telegram channel: 409 Conflict, retrying in 3s"])
    r = box.probe('bridge_health; echo "S=$BRIDGE_STATE"', cwd=proj)
    assert "S=retrying" in r.stdout

    box.mcp_log(proj, ["telegram channel: 409 Conflict persists after 8 attempts — another poller is holding the bot token. Exiting."])
    r = box.probe('bridge_health; echo "S=$BRIDGE_STATE D=$BRIDGE_DETAIL"', cwd=proj)
    assert "S=deaf" in r.stdout
    assert "gave up" in r.stdout


def test_a_network_wobble_reads_as_retrying_not_deaf(box, tmp_path):
    """The plugin survives these on its own; the operator should not be told to
    restart a session that is about to recover."""
    proj = tmp_path / "wobble"
    proj.mkdir()
    box.mcp_log(proj, ["telegram channel: polling error: FetchError ETIMEDOUT, retrying in 2s"])
    r = box.probe('bridge_health; echo "S=$BRIDGE_STATE"', cwd=proj)
    assert "S=retrying" in r.stdout


def test_reconnect_never_asks_telegram_whether_anyone_is_polling(box):
    """A getUpdates probe answers "nobody is polling" about a healthy bridge
    whenever it lands between two long polls — it did exactly that on a live
    session — and it costs that poller its current poll. It is not run."""
    src = ABS_SH.read_text()
    body = src[src.index("cmd_reconnect() {"):src.index("cmd_prompt() {")]
    assert "getUpdates" not in body.replace("# ", "")[:0] or "tg_api getUpdates" not in body


# ---- the watchdog clears a stray while the plugin can still recover --------------


# The watchdog silences poller_reclaim (it runs on every turn and must print
# nothing into the operator's terminal), so these watch for a FILE it leaves
# rather than for output.

def _watch(box, tmp_path, pollers, verdict="orphan"):
    marker = tmp_path / "reclaimed"
    box.probe(f'pollers_running() {{ printf "{pollers}"; }}; profile_live_pid() {{ echo 999; }}; '
              f'poller_verdict() {{ POLLER_VERDICT={verdict}; }}; '
              f'poller_reclaim() {{ : > "{marker}"; }}; _bridge_watchdog')
    return marker.exists()


def test_the_watchdog_does_nothing_when_there_is_one_poller(box, tmp_path):
    assert not _watch(box, tmp_path, "111\\n")


def test_the_watchdog_clears_an_orphan_during_a_conflict(box, tmp_path):
    assert _watch(box, tmp_path, "111\\n222\\n")


def test_the_watchdog_never_touches_a_poller_a_live_session_owns(box, tmp_path):
    """That is somebody's working bridge — possibly another project's."""
    assert not _watch(box, tmp_path, "111\\n222\\n", verdict="owned")
