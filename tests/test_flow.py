"""Pure ABS START flow helpers (absd/flow.py) — no poller, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from absd import flow


# ---- D6 folder-name jail -----------------------------------------------------


@pytest.mark.parametrize(
    "name,ok",
    [
        ("web", True),
        ("my-project_2.0", True),
        ("a", True),
        ("A1._-", True),
        ("x" * 64, True),
        ("x" * 65, False),  # over 64
        ("", False),
        ("   ", False),
        (".", False),
        ("..", False),
        ("../evil", False),  # path separator + traversal
        ("/abs/path", False),  # absolute / separator
        ("a/b", False),  # separator
        ("a\\b", False),  # backslash separator
        ("space name", False),  # space
        ("café", False),  # unicode
        ("na\x00me", False),  # NUL
        ("-ok", True),  # leading hyphen is allowed by the class
    ],
)
def test_validate_folder_name_matrix(name: str, ok: bool) -> None:
    got, msg = flow.validate_folder_name(name)
    assert got is ok
    if not ok:
        assert msg  # a rejection always explains itself


# ---- structural path jail (belt-and-suspenders over the regex) ---------------


def test_safe_join_under_root_valid(tmp_path: Path) -> None:
    target = flow.safe_join_under_root(tmp_path, "web")
    assert target == (tmp_path / "web").resolve()


def test_safe_join_under_root_rejects_escape(tmp_path: Path) -> None:
    # These never pass validate_folder_name, but the join is the second gate.
    assert flow.safe_join_under_root(tmp_path, "..") is None
    assert flow.safe_join_under_root(tmp_path, "../sibling") is None
    assert flow.safe_join_under_root(tmp_path, "a/b") is None


# ---- project enumeration -----------------------------------------------------


def test_enumerate_registered_then_children_then_newfolder(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    (root / ".hidden").mkdir()  # skipped
    reg_dir = tmp_path / "elsewhere" / "reg"
    reg_dir.mkdir(parents=True)

    options = flow.enumerate_project_options(
        [(str(reg_dir), "reg")], root
    )
    kinds = [(o.kind, o.label) for o in options]
    # registered first, then sorted workspace children, then the sentinel; hidden
    # dirs excluded.
    assert kinds[0] == ("project", "reg")
    labels = [o.label for o in options if o.kind == "project"]
    assert "alpha" in labels and "beta" in labels and ".hidden" not in labels
    assert options[-1].kind == "newfolder"


def test_enumerate_no_root_has_no_newfolder(tmp_path: Path) -> None:
    reg_dir = tmp_path / "reg"
    reg_dir.mkdir()
    options = flow.enumerate_project_options([(str(reg_dir), "reg")], None)
    assert [o.kind for o in options] == ["project"]  # no newfolder without a root


def test_enumerate_dedups_and_drops_missing(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    shared = root / "shared"
    shared.mkdir(parents=True)
    missing = tmp_path / "gone"
    options = flow.enumerate_project_options(
        [(str(shared), "shared"), (str(missing), "gone")], root
    )
    project_paths = [o.path for o in options if o.kind == "project"]
    # 'shared' registered AND a workspace child, but appears once; 'gone' dropped.
    assert project_paths.count(str(shared.resolve())) == 1
    assert str(missing) not in project_paths


# ---- choice parsing (callback + numbered fallback) ---------------------------


def _opts() -> list[flow.ProjectOption]:
    return [
        flow.ProjectOption(kind="project", label="one", path="/p/one"),
        flow.ProjectOption(kind="project", label="two", path="/p/two"),
        flow.ProjectOption(kind="newfolder", label="➕ New folder", path=None),
    ]


def test_choose_project_callback() -> None:
    opts = _opts()
    assert flow.choose_project("as:p:0", "", opts) is opts[0]
    assert flow.choose_project("as:p:1", "", opts) is opts[1]
    assert flow.choose_project("as:nf", "", opts) is opts[2]
    assert flow.choose_project("as:p:9", "", opts) is None  # out of range
    assert flow.choose_project("garbage", "", opts) is None


def test_choose_project_numbered_fallback() -> None:
    opts = _opts()
    assert flow.choose_project(None, "1", opts) is opts[0]
    assert flow.choose_project(None, "3", opts) is opts[2]
    assert flow.choose_project(None, "0", opts) is None
    assert flow.choose_project(None, "4", opts) is None
    assert flow.choose_project(None, "two", opts) is None  # not a number


def test_choose_mode() -> None:
    assert flow.choose_mode(flow.CB_MODE_NORMAL, "") == flow.MODE_NORMAL
    assert flow.choose_mode(flow.CB_MODE_AWAY, "") == flow.MODE_AWAY
    assert flow.choose_mode(None, "1") == flow.MODE_NORMAL
    assert flow.choose_mode(None, "2") == flow.MODE_AWAY
    assert flow.choose_mode(None, "3") is None
    assert flow.choose_mode("nope", "") is None


# ---- keyboards ---------------------------------------------------------------


def test_project_keyboard_shape() -> None:
    opts = _opts()
    kb = flow.build_project_keyboard(opts)
    rows = kb["inline_keyboard"]
    assert rows[0][0]["callback_data"] == "as:p:0"
    assert rows[2][0]["callback_data"] == flow.CB_NEWFOLDER
    # every callback data is well under Telegram's 64-byte limit
    for row in rows:
        assert len(row[0]["callback_data"].encode()) <= 64


def test_mode_keyboard_shape() -> None:
    kb = flow.build_mode_keyboard()
    datas = [b[0]["callback_data"] for b in kb["inline_keyboard"]]
    assert datas == [flow.CB_MODE_NORMAL, flow.CB_MODE_AWAY]


# ---- launcher argv (4.2) -----------------------------------------------------


def test_build_launcher_argv() -> None:
    argv = flow.build_launcher_argv("/opt/abs.sh", "work", away=False)
    assert argv == ["bash", "/opt/abs.sh", "--profile", "work", "--daemon-start"]
    argv_away = flow.build_launcher_argv("/opt/abs.sh", "work", away=True)
    assert argv_away[-1] == "--away"


def test_build_launcher_argv_resume_appends_continue() -> None:
    argv = flow.build_launcher_argv("/opt/abs.sh", "work", away=False, resume=True)
    assert argv[-1] == "--continue"
    assert argv[:5] == ["bash", "/opt/abs.sh", "--profile", "work", "--daemon-start"]
    both = flow.build_launcher_argv("/opt/abs.sh", "work", away=True, resume=True)
    assert "--away" in both and both[-1] == "--continue"


def test_build_launcher_argv_initial_prompt_one_element() -> None:
    tricky = 'say "hi"\nline2 🎉'
    argv = flow.build_launcher_argv(
        "/opt/abs.sh", "work", away=False, resume=True, initial_prompt=tricky
    )
    assert argv[-2] == "--prompt"
    assert argv[-1] == tricky  # exact, ONE element (quotes/newline/emoji intact)
    assert "--continue" in argv
    # no prompt when None
    assert "--prompt" not in flow.build_launcher_argv("/opt/abs.sh", "w", away=False)


def test_build_offline_prompt() -> None:
    p = flow.build_offline_prompt(["a", "b"])
    assert p == flow.OFFLINE_PROMPT_PREFIX + "\na\nb"


def test_parse_pool_choice() -> None:
    # callbacks
    assert flow.parse_pool_choice(flow.CB_POOL_ALL, "", 3) == "all"
    assert flow.parse_pool_choice(flow.CB_POOL_SKIP, "", 3) == "skip"
    assert flow.parse_pool_choice("garbage", "", 3) is None
    # text
    assert flow.parse_pool_choice(None, "send all", 3) == "all"
    assert flow.parse_pool_choice(None, "all", 3) == "all"
    assert flow.parse_pool_choice(None, "skip", 3) == "skip"
    assert flow.parse_pool_choice(None, "send 1,3", 3) == [1, 3]
    assert flow.parse_pool_choice(None, "1, 3", 3) == [1, 3]
    assert flow.parse_pool_choice(None, "send 3,1,3", 3) == [1, 3]  # dedup + sort
    assert flow.parse_pool_choice(None, "send 9", 3) is None  # out of range → none
    assert flow.parse_pool_choice(None, "nope", 3) is None
    assert flow.parse_pool_choice(None, "", 3) is None


def test_pool_keyboard_and_render() -> None:
    kb = flow.build_pool_keyboard()
    datas = [b["callback_data"] for b in kb["inline_keyboard"][0]]
    assert datas == [flow.CB_POOL_ALL, flow.CB_POOL_SKIP]
    out = flow.render_pool_selection(["hello", "x" * 100])
    assert "2 pooled message(s) waiting" in out
    assert "1. hello" in out
    assert "…" in out  # long line truncated to 60


# ---- resume-first recents screen (Step 2.2) ----------------------------------


from datetime import datetime, timezone  # noqa: E402


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def test_humanize_age_coarse() -> None:
    now = _dt("2026-07-23T15:00:00Z")
    assert flow.humanize_age("2026-07-23T14:59:30Z", now) == "just now"
    assert flow.humanize_age("2026-07-23T14:48:00Z", now) == "12m"
    assert flow.humanize_age("2026-07-23T12:00:00Z", now) == "3h"
    assert flow.humanize_age("2026-07-18T15:00:00Z", now) == "5d"
    assert flow.humanize_age("garbage", now) == ""


class _Rec:
    def __init__(self, label: str, started_at: str = "") -> None:
        self.label = label
        self.started_at = started_at


def test_recents_keyboard_and_menu() -> None:
    recents = [_Rec("llm"), _Rec("web"), _Rec("api"), _Rec("extra")]
    kb = flow.build_recents_keyboard(recents)
    rows = kb["inline_keyboard"]
    # up to RECENTS_SHOWN resume buttons + a New session row
    assert len(rows) == flow.RECENTS_SHOWN + 1
    assert rows[0][0]["callback_data"] == "as:r:0"
    assert rows[-1][0]["callback_data"] == flow.CB_NEW_SESSION
    assert "Resume llm" in rows[0][0]["text"]
    menu = flow.render_recents_menu(recents)
    assert "1. ▶ Resume llm" in menu
    assert f"{flow.RECENTS_SHOWN + 1}. 🆕 New session" in menu


def test_choose_recent_callback_and_text() -> None:
    # callback
    assert flow.choose_recent("as:r:0", "", 3) == 0
    assert flow.choose_recent("as:r:2", "", 3) == 2
    assert flow.choose_recent("as:r:9", "", 3) is None
    assert flow.choose_recent(flow.CB_NEW_SESSION, "", 3) == "new"
    # numbered fallback (1..count = recents, count+1 = new)
    assert flow.choose_recent(None, "1", 3) == 0
    assert flow.choose_recent(None, "3", 3) == 2
    assert flow.choose_recent(None, "4", 3) == "new"
    assert flow.choose_recent(None, "5", 3) is None
    assert flow.choose_recent(None, "nope", 3) is None
