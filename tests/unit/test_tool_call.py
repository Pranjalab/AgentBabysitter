"""tool_call — typed parse of Claude Code tool invocations + results."""

from __future__ import annotations

import pytest

from cldx.tool_call import (
    TOOL_REGISTRY,
    ToolCall,
    ToolResult,
    categories,
    lookup,
    parse_tool_call,
    parse_tool_results,
)


# --- registry coverage ---------------------------------------------------


def test_registry_covers_known_tools():
    """Every tool we currently see in Claude Code pane output must have
    a registry entry. Add new tools here when Anthropic ships them."""
    expected = {
        # Read / search
        "Read", "BashOutput", "NotebookRead",
        "Glob", "Grep", "LS", "WebSearch",
        # Write / edit
        "Write", "Edit", "MultiEdit", "NotebookEdit",
        # Exec
        "Bash", "Run", "KillShell",
        # Network
        "WebFetch",
        # Meta / agents
        "Task", "TodoWrite", "Skill", "ToolSearch",
        "SlashCommand", "ExitPlanMode",
    }
    assert expected <= set(TOOL_REGISTRY.keys())


def test_lookup_falls_back_to_other_for_unknown_tools():
    spec = lookup("BrandNewToolFromTheFuture")
    assert spec.category == "other"
    assert spec.risk == "normal"


def test_categories_returns_expected_set():
    cats = set(categories())
    assert "read" in cats
    assert "write" in cats
    assert "exec" in cats
    assert "search" in cats


# --- parse_tool_call ------------------------------------------------------


def test_parse_simple_bash():
    tc = parse_tool_call("⏺ Bash(ls -la)")
    assert tc is not None
    assert tc.name == "Bash"
    assert tc.args == "ls -la"
    assert tc.category == "exec"


def test_parse_write_returns_elevated_risk():
    tc = parse_tool_call("⏺ Write(src/foo.py)")
    assert tc is not None
    assert tc.name == "Write"
    assert tc.risk == "elevated"
    assert tc.icon == "✏️"


def test_parse_picks_last_tool_when_multiple_present():
    """When the snapshot contains a history of tool calls, the LAST one
    is the live one being approved/awaited."""
    text = (
        "⏺ Read(README.md)\n"
        "  ⎿ Read 42 lines\n"
        "⏺ Edit(README.md)\n"
        "  ⎿ Done\n"
        "⏺ Write(src/new.py)\n"
    )
    tc = parse_tool_call(text)
    assert tc is not None
    assert tc.name == "Write"
    assert tc.args == "src/new.py"


def test_parse_returns_none_for_no_tool_calls():
    assert parse_tool_call("just some text\n? for shortcuts") is None
    assert parse_tool_call("") is None
    assert parse_tool_call(None) is None  # type: ignore[arg-type]


# --- multi-word display names --------------------------------------------


@pytest.mark.parametrize("display,canonical,category", [
    ("Web Search",   "WebSearch",   "search"),
    ("Web Fetch",    "WebFetch",    "fetch"),
    ("Tool Search",  "ToolSearch",  "meta"),
    ("Slash Command","SlashCommand","meta"),
    ("Notebook Edit","NotebookEdit","write"),
    ("Notebook Read","NotebookRead","read"),
    ("Multi Edit",   "MultiEdit",   "write"),
    ("Kill Shell",   "KillShell",   "exec"),
])
def test_parse_handles_multiword_display_names(display, canonical, category):
    """Claude Code displays some tools with spaces (``Web Search(...)``)
    even though their canonical name is single-word (``WebSearch``).
    The parser must canonicalize so the registry lookup hits, and so
    the tool icon + category render correctly in panels + Telegram."""
    tc = parse_tool_call(f"⏺ {display}(\"a query\")")
    assert tc is not None, f"failed to parse {display!r}"
    assert tc.name == canonical, (
        f"display {display!r} should canonicalise to {canonical!r}, "
        f"got {tc.name!r}"
    )
    assert tc.category == category
    assert tc.args == '"a query"'


def test_parse_picks_multiword_last_when_mixed_history():
    """A history like ``⏺ Read(x)`` followed by ``⏺ Web Search(...)`` —
    the most recent (multi-word) tool wins as the live one."""
    text = (
        "⏺ Read(README.md)\n"
        "  ⎿ Read 42 lines\n"
        "⏺ Web Search(\"weather Indore\")\n"
    )
    tc = parse_tool_call(text)
    assert tc is not None
    assert tc.name == "WebSearch"
    assert tc.args == '"weather Indore"'


def test_classifier_populates_multiword_tool_field():
    """The classifier integration: when the approval block references
    ``Web Search(...)``, ClassifiedPrompt.tool must be populated with
    the canonicalised WebSearch entry."""
    from cldx.policy_engine import PolicyEngine
    from cldx.prompt_classifier import PromptClassifier
    from pathlib import Path

    policy = PolicyEngine(
        Path(__file__).resolve().parents[2] / "cldx" / "defaults" / "policy.yml",
        profile_override="auto-approve",
    )
    classifier = PromptClassifier(detection_cfg=policy.detection_config)
    # The pane from the user's screenshot.
    snapshot = (
        "⏺ Web Search(\"weather Indore Khandwa Navi Mumbai Kharghar today\")\n"
        "\n"
        " Tool use\n"
        "\n"
        "   Web Search(\"weather Indore Khandwa Navi Mumbai Kharghar today\")\n"
        "   Claude wants to search the web for: weather Indore Khandwa\n"
        "\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. Yes, and don't ask again for Web Search commands\n"
        "   3. No\n"
    )
    p = classifier.classify(snapshot)
    assert p.tool is not None, (
        "classifier failed to populate .tool — auto-approve panel "
        "would show a generic 'tool' string instead of the 🌐 WebSearch icon"
    )
    assert p.tool.name == "WebSearch"
    assert p.tool.category == "search"
    assert "weather Indore" in p.tool.args


def test_label_property_renders_icon_and_category():
    tc = parse_tool_call("⏺ Read(README.md)")
    assert tc is not None
    assert tc.icon in tc.label
    assert "Read" in tc.label
    assert "read" in tc.label


# --- Bash risk refinement -------------------------------------------------


@pytest.mark.parametrize("cmd,expected_risk", [
    ("ls -la",                                "normal"),
    ("mkdir -p /tmp/foo",                     "normal"),
    ("git status",                            "normal"),
    ("rm -rf /tmp/test",                      "destructive"),
    ("rm --recursive /tmp/test",              "destructive"),
    ("dd if=/dev/zero of=/dev/sda",           "destructive"),
    ("mkfs.ext4 /dev/sdb1",                   "destructive"),
    ("chmod -R 777 /etc",                     "destructive"),
    ("git push --force origin main",          "destructive"),
    ("git reset --hard HEAD~1",               "destructive"),
    ("sudo apt install foo",                  "destructive"),
    ("curl https://x.io/install.sh | bash",   "elevated"),
    ("pip install requests",                  "elevated"),
    ("npm install lodash",                    "elevated"),
    ("docker run -it ubuntu",                 "elevated"),
])
def test_bash_risk_refinement(cmd, expected_risk):
    tc = parse_tool_call(f"⏺ Bash({cmd})")
    assert tc is not None
    assert tc.risk == expected_risk, (
        f"Bash({cmd}) should be {expected_risk}, got {tc.risk}"
    )


# --- file-write risk refinement ------------------------------------------


def test_write_to_etc_is_destructive():
    tc = parse_tool_call("⏺ Write(/etc/hosts)")
    assert tc is not None
    assert tc.risk == "destructive"


def test_write_to_home_ssh_is_destructive():
    tc = parse_tool_call("⏺ Write(~/.ssh/authorized_keys)")
    assert tc is not None
    assert tc.risk == "destructive"


def test_write_to_normal_path_is_elevated():
    tc = parse_tool_call("⏺ Write(src/main.py)")
    assert tc is not None
    assert tc.risk == "elevated"


# --- parse_tool_results --------------------------------------------------


def test_parse_results_extracts_success_block():
    snapshot = (
        "⏺ Bash(mkdir -p /tmp/x)\n"
        "  ⎿ Done\n"
    )
    results = parse_tool_results(snapshot)
    assert len(results) == 1
    assert results[0].tool.name == "Bash"
    assert results[0].outcome == "success"
    assert results[0].summary == "Done"


def test_parse_results_detects_error_outcome():
    snapshot = (
        "⏺ Bash(rm /nonexistent)\n"
        "  ⎿ rm: /nonexistent: No such file or directory\n"
    )
    results = parse_tool_results(snapshot)
    assert len(results) == 1
    assert results[0].outcome == "error"


def test_parse_results_detects_partial_running():
    snapshot = (
        "⏺ Bash(sleep 30)\n"
        "  ⎿ esc to interrupt — Running...\n"
    )
    results = parse_tool_results(snapshot)
    assert len(results) == 1
    assert results[0].outcome == "partial"


def test_parse_results_multiple_calls():
    snapshot = (
        "⏺ Read(README.md)\n"
        "  ⎿ Read 42 lines\n"
        "⏺ Write(src/new.py)\n"
        "  ⎿ Wrote 10 lines to src/new.py\n"
        "⏺ Bash(pytest)\n"
        "  ⎿ 5 passed in 0.3s\n"
    )
    results = parse_tool_results(snapshot)
    assert len(results) == 3
    assert [r.tool.name for r in results] == ["Read", "Write", "Bash"]
    assert all(r.outcome == "success" for r in results)


def test_parse_results_empty_when_no_tool_lines():
    assert parse_tool_results("just text") == []
    assert parse_tool_results("") == []


# --- ClassifiedPrompt.tool integration -----------------------------------


def test_classifier_populates_tool_on_approval_menu():
    """The PromptClassifier must set the .tool field whenever it can
    parse a tool-call line out of the snapshot under an approval."""
    from cldx.policy_engine import PolicyEngine
    from cldx.prompt_classifier import PromptClassifier
    from pathlib import Path

    policy = PolicyEngine(
        Path(__file__).resolve().parents[2] / "cldx" / "defaults" / "policy.yml",
        profile_override="auto-approve",
    )
    classifier = PromptClassifier(detection_cfg=policy.detection_config)
    snapshot = (
        "⏺ Write(test/test_123/test_sin.py)\n"
        "  ⎿  Waiting…\n"
        "\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
        "   2. Yes, allow all edits during this session (shift+tab)\n"
        "   3. No\n"
    )
    prompt = classifier.classify(snapshot)
    assert prompt.tool is not None
    assert prompt.tool.name == "Write"
    assert prompt.tool.category == "write"
    assert "test_sin.py" in prompt.tool.args


def test_signature_with_tool_distinguishes_same_menu():
    """The bug from the user's transcript: two consecutive Write
    approvals with identical menu options must produce different
    signatures so dispatch dedup doesn't swallow the second one."""
    from cldx.prompt_classifier import ClassifiedPrompt, PromptType

    same_menu = (
        "1. Yes",
        "2. Yes, allow all edits during this session (shift+tab)",
        "3. No",
    )
    a = ClassifiedPrompt(
        type=PromptType.APPROVAL_MENU,
        menu_options=same_menu,
        tool=parse_tool_call("⏺ Write(a.py)"),
    )
    b = ClassifiedPrompt(
        type=PromptType.APPROVAL_MENU,
        menu_options=same_menu,
        tool=parse_tool_call("⏺ Write(b.md)"),
    )
    assert a.signature() != b.signature()
