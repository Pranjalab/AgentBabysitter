"""herdr-specific tests for the herdr engine backend (PLAN.md Step 1.2).

Mirror of ``test_engine_tmux.py``: only the genuinely herdr-shaped surface lives
here — the pure JSON-parsing helpers (run everywhere, no herdr needed), the
liveness predicate, and the herdr attach-command string. The cross-engine
lifecycle behaviour is in ``test_engine_shared.py``.

The JSON fixtures below are verbatim shapes captured from herdr 0.7.5 during the
Step 1.2 empirical probes (recorded in ``docs/v3/herdr-recipes.md``).
"""

from __future__ import annotations

from absd.engines import HerdrEngine
from absd.engines.base import Engine
from absd.engines.herdr import (
    HerdrSession,
    PaneRef,
    ProcessInfo,
    command_running,
    first_pane,
    launched_pid,
    pane_id_from_create,
    parse_process_info,
    parse_session_list,
    session_running,
)
import pytest
from absd.engines.herdr import HerdrError

# --- verbatim herdr 0.7.5 JSON samples ------------------------------------- #

_SESSION_LIST = (
    '{"sessions":['
    '{"default":true,"name":"default","running":false,'
    '"session_dir":"/home/u/.config/herdr",'
    '"socket_path":"/home/u/.config/herdr/herdr.sock"},'
    '{"default":false,"name":"abs-work","running":true,'
    '"session_dir":"/home/u/.config/herdr/sessions/abs-work",'
    '"socket_path":"/home/u/.config/herdr/sessions/abs-work/herdr.sock"}'
    ']}'
)

_WORKSPACE_CREATE = (
    '{"id":"cli:workspace:create","result":{'
    '"root_pane":{"agent_status":"unknown","cwd":"/proj","pane_id":"w1:p1",'
    '"tab_id":"w1:t1","workspace_id":"w1"},'
    '"tab":{"tab_id":"w1:t1"},"type":"workspace_created",'
    '"workspace":{"workspace_id":"w1"}}}'
)

_PANE_LIST = (
    '{"id":"cli:pane:list","result":{"panes":['
    '{"agent_status":"unknown","cwd":"/proj","focused":true,"pane_id":"w1:p1",'
    '"tab_id":"w1:t1","workspace_id":"w1"}],"type":"pane_list"}}'
)

# A launched command holds the foreground: fg_pgid != shell_pid.
_PROC_ALIVE = (
    '{"id":"cli:pane:process_info","result":{"process_info":{'
    '"foreground_process_group_id":729709,'
    '"foreground_processes":['
    '{"argv":["bash","fake-claude"],"cmdline":"bash fake-claude","cwd":"/proj",'
    '"name":"bash","pid":729709},'
    '{"argv":["sleep","3600"],"cmdline":"sleep 3600","cwd":"/proj",'
    '"name":"sleep","pid":729719}],'
    '"pane_id":"w1:p1","shell_pid":727737},"type":"pane_process_info"}}'
)

# The command has exited: the idle shell reclaimed the foreground (fg_pgid == shell_pid).
_PROC_IDLE = (
    '{"id":"cli:pane:process_info","result":{"process_info":{'
    '"foreground_process_group_id":727737,'
    '"foreground_processes":['
    '{"argv":["/bin/bash"],"cmdline":"/bin/bash","cwd":"/proj",'
    '"name":"bash","pid":727737}],'
    '"pane_id":"w1:p1","shell_pid":727737},"type":"pane_process_info"}}'
)


# --------------------------------------------------------------------------- #
# session list
# --------------------------------------------------------------------------- #


def test_parse_session_list_basic() -> None:
    sessions = parse_session_list(_SESSION_LIST)
    assert sessions == [
        HerdrSession(
            name="default",
            running=False,
            socket_path="/home/u/.config/herdr/herdr.sock",
        ),
        HerdrSession(
            name="abs-work",
            running=True,
            socket_path="/home/u/.config/herdr/sessions/abs-work/herdr.sock",
        ),
    ]


def test_parse_session_list_empty_and_malformed() -> None:
    assert parse_session_list("") == []
    assert parse_session_list("not json") == []
    assert parse_session_list('{"sessions":null}') == []
    # a row without a name is skipped
    assert parse_session_list('{"sessions":[{"running":true}]}') == []


def test_session_running_predicate() -> None:
    assert session_running(_SESSION_LIST, "abs-work") is True
    assert session_running(_SESSION_LIST, "default") is False  # present but not running
    assert session_running(_SESSION_LIST, "abs-nope") is False


# --------------------------------------------------------------------------- #
# workspace create -> pane id
# --------------------------------------------------------------------------- #


def test_pane_id_from_create_ok() -> None:
    assert pane_id_from_create(_WORKSPACE_CREATE) == "w1:p1"


def test_pane_id_from_create_bad_shape_raises() -> None:
    with pytest.raises(HerdrError):
        pane_id_from_create('{"result":{"nope":1}}')
    with pytest.raises(HerdrError):
        pane_id_from_create("garbage")


# --------------------------------------------------------------------------- #
# pane list -> first pane
# --------------------------------------------------------------------------- #


def test_first_pane_ok() -> None:
    assert first_pane(_PANE_LIST) == PaneRef(pane_id="w1:p1", cwd="/proj")


def test_first_pane_empty_or_malformed() -> None:
    assert first_pane('{"result":{"panes":[]}}') is None
    assert first_pane("") is None
    assert first_pane('{"result":{}}') is None


# --------------------------------------------------------------------------- #
# process-info -> liveness
# --------------------------------------------------------------------------- #


def test_parse_process_info_alive_shape() -> None:
    info = parse_process_info(_PROC_ALIVE)
    assert info.shell_pid == 727737
    assert info.fg_pgid == 729709
    assert info.foreground_pids == (729709, 729719)


def test_parse_process_info_missing_fields() -> None:
    info = parse_process_info("")
    assert info == ProcessInfo(pane_id=None, shell_pid=None, fg_pgid=None)


def test_command_running_true_when_job_foreground() -> None:
    assert command_running(parse_process_info(_PROC_ALIVE)) is True


def test_command_running_false_when_idle_shell() -> None:
    # fg_pgid == shell_pid: the launched command exited, shell reclaimed foreground.
    assert command_running(parse_process_info(_PROC_IDLE)) is False


def test_command_running_false_when_no_info() -> None:
    assert command_running(ProcessInfo(pane_id=None, shell_pid=None, fg_pgid=None)) is False


def test_launched_pid_is_fg_pgid_while_running_else_none() -> None:
    assert launched_pid(parse_process_info(_PROC_ALIVE)) == 729709
    assert launched_pid(parse_process_info(_PROC_IDLE)) is None


# --------------------------------------------------------------------------- #
# attach command (herdr-only, per recipes) + protocol
# --------------------------------------------------------------------------- #


def test_attach_command_exact_string() -> None:
    eng = HerdrEngine()
    assert eng.attach_command("work") == "herdr session attach abs-work"


def test_attach_command_custom_prefix() -> None:
    eng = HerdrEngine(session_prefix="abs-test-xyz-")
    assert eng.attach_command("p1") == "herdr session attach abs-test-xyz-p1"


def test_herdr_engine_satisfies_protocol() -> None:
    assert isinstance(HerdrEngine(), Engine)
