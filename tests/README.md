# Tests

This directory holds the full test suite for claudex — both the
tests covering Phase 1 (what's already shipped) and **skeleton files
for every upcoming phase** so each new feature has a place to drop its
tests as it's built.

## Quick start

```bash
# Install dev deps (pytest + pytest-asyncio):
pip install -r requirements-dev.txt

# Run everything (Phase 1 tests pass, Phase 2+ are skipped):
pytest

# Just the unit tests:
pytest tests/unit/

# Just the integration tests:
pytest tests/integration/

# Show what's skipped per phase (sanity check the roadmap):
pytest --collect-only -q
```

## Layout

```
tests/
├── conftest.py                       # shared fixtures: snapshot(), policy, classifier
├── fixtures/
│   ├── snapshots/                    # captured Claude Code pane states
│   │   ├── safe_ls_yn.txt
│   │   ├── dangerous_rm_menu.txt
│   │   ├── edit_menu.txt
│   │   ├── running.txt
│   │   ├── idle.txt
│   │   └── complete.txt
│   └── policies/                     # alternate policies for tests
├── unit/
│   ├── test_prompt_classifier.py     # Phase 1 ✓
│   ├── test_policy_engine.py         # Phase 1 ✓
│   ├── test_tmux_monitor.py          # Phase 1 ✓
│   ├── test_tmux_controller.py       # Phase 1 ✓
│   ├── test_session_picker.py        # Phase 1 ✓
│   ├── test_session_store.py         # Phase 2 (skipped)
│   ├── test_wait_bar.py              # Phase 3 (skipped)
│   ├── test_destructive_ops.py       # Phase 3 (skipped)
│   ├── test_startup.py               # Phase 4 (skipped)
│   ├── test_memory.py                # Phase 5 (skipped)
│   ├── test_agent.py                 # Phase 6 (skipped)
│   ├── test_summarizer.py            # Phase 6 (skipped)
│   └── test_telegram_bridge.py       # Phase 7 (skipped)
└── integration/
    └── test_pipeline.py              # Phase 1 ✓ end-to-end snapshot→decision
```

## Phase markers

Every skeleton test is tagged with `pytestmark = pytest.mark.phaseN` so
you can iterate per-phase as you implement:

```bash
# Run all Phase 1 tests:
pytest -m "not phase2 and not phase3 and not phase4 and not phase5 and not phase6 and not phase7"

# Just see what'll be needed for Phase 3:
pytest -m phase3 --collect-only
```

## Adding a new test

1. **Fixture**: if you need a new snapshot, drop a `.txt` file in
   `tests/fixtures/snapshots/` and reference it as `snapshot("name")`
   in your test.
2. **Async tests**: just write `async def test_...` — `asyncio_mode = auto`
   in `pytest.ini` picks them up automatically.
3. **Subprocess mocking**: see `tests/unit/test_tmux_controller.py` for
   the `captured_calls` fixture pattern (monkeypatches
   `asyncio.create_subprocess_exec`).

## Pre-commit workflow

Run `pytest` before every commit. CI will eventually run the same
command; Phase 2+ test files don't fail the build because they're
skipped, but they do count toward `--collect-only` so the test plan
stays visible.
