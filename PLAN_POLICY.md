# Agent Babysitter — Policy Engine Implementation Plan

The policy engine is the make-or-break feature. Everything else — Telegram, LLM backends, multi-tool support — plugs into it. Get this right and adoption follows naturally.

---

## The Core Idea

A single `policy.yml` file that any developer can read and edit in under five minutes. The local LLM reads the same file and uses it as its decision constitution. No magic, no black-box behaviour — the policy is the contract between the human and the babysitter.

```yaml
version: "1.0"

babysitter:
  backend: ollama
  model: llama3.2
  endpoint: http://localhost:11434

notify:
  telegram:
    enabled: true

policy:
  profile: default

  profiles:
    default:
      approve:
        tools: [Read, Grep, Glob, LS, WebSearch]
        commands:
          - "git status"
          - "git log*"
          - "pytest*"
          - "npm test*"
          - "cargo test*"

      escalate:
        tools: [Write, Edit, Bash, MultiEdit]
        commands:
          - "git commit*"
          - "npm install*"
          - "pip install*"
          - "docker build*"
        wait_seconds: 5

      block:
        commands:
          - "rm -rf*"
          - "git push --force*"
          - "DROP TABLE*"
          - "mkfs*"
          - "dd if=*"
        paths:
          - "/etc/**"
          - "~/.ssh/**"
          - "~/.aws/**"
          - "~/.gnupg/**"

    yolo:
      extends: default
      escalate:
        wait_seconds: 2

    paranoid:
      extends: default
      approve:
        tools: [Read, Grep]
      escalate:
        tools: [Write, Edit, Bash, Glob, LS, WebSearch]
        wait_seconds: 10
```

---

## Phase 1 — Schema & Validation

**Goal:** A Pydantic model that parses, validates, and resolves a `policy.yml`. Bad configs fail loudly with a human-readable error, not a stack trace.

### Models to build

```
PolicySchema
  version: str
  babysitter: BabysitterConfig
  notify: NotifyConfig
  policy: PolicyRoot

BabysitterConfig
  backend: Literal["ollama", "lmstudio", "anthropic", "gemini", "openai", "disabled"]
  model: str
  endpoint: str | None
  api_key: str | None          # read from env if prefixed with $

NotifyConfig
  telegram: TelegramConfig | None

PolicyRoot
  profile: str                 # active profile name
  profiles: dict[str, Profile]

Profile
  extends: str | None          # inherit from another profile
  approve: RuleSet
  escalate: EscalateRuleSet
  block: BlockRuleSet

RuleSet
  tools: list[str]
  commands: list[str]          # glob patterns

EscalateRuleSet(RuleSet)
  wait_seconds: int = 5        # countdown before auto-escalate

BlockRuleSet(RuleSet)
  paths: list[str]             # glob path patterns — always hard block
```

### Profile inheritance resolution

When `paranoid` extends `default`, the resolver deep-merges:
- `approve.tools` → intersection (paranoid is stricter)
- `block.commands` → union (paranoid adds nothing but keeps all blocks)
- `escalate.wait_seconds` → child wins

Order: child → parent → built-in defaults. Never recurse more than 3 levels.

### Deliverables

- `abs/policy/schema.py` — Pydantic models
- `abs/policy/loader.py` — YAML loader + env-var expansion (`${VAR}`)
- `abs/policy/resolver.py` — profile inheritance resolver
- `tests/policy/test_schema.py`
- `tests/policy/test_resolver.py`

---

## Phase 2 — Pattern Matching Engine

**Goal:** Given a tool name + command string, return `APPROVE | ESCALATE | BLOCK` using only the policy file — no LLM involved yet. Fast, deterministic, testable.

### Matching rules (evaluated in order)

1. **Block check first** — if command matches any `block.commands` glob OR writes to a `block.paths` location → `BLOCK`. Unoverridable.
2. **Approve check** — if tool is in `approve.tools` AND command matches `approve.commands` → `APPROVE`.
3. **Escalate check** — if tool is in `escalate.tools` OR command matches `escalate.commands` → `ESCALATE`.
4. **Default** — `ESCALATE` (fail safe).

### Pattern types supported

| Syntax | Example | Meaning |
|---|---|---|
| Glob | `pytest*` | shell glob |
| Exact | `git status` | exact match |
| Path glob | `/etc/**` | fnmatch on file path arg |
| Regex | `~/regex/pattern` | explicit regex prefix |

### Deliverables

- `abs/policy/matcher.py` — `PolicyMatcher.match(tool, command, path) → Decision`
- `abs/policy/decision.py` — `Decision` dataclass (verdict + reason + matched_rule)
- `tests/policy/test_matcher.py` — 50+ cases covering all pattern types

---

## Phase 3 — LLM Babysitter Backend

**Goal:** When the pattern matcher returns `ESCALATE`, the local LLM gets a second opinion. It can upgrade to `APPROVE`, confirm `ESCALATE`, or downgrade to `BLOCK`. It cannot override a `BLOCK` from the pattern matcher.

### Abstract interface

```python
class BabysitterBackend(ABC):
    async def evaluate(self, context: EvalContext) -> LLMDecision:
        ...

@dataclass
class EvalContext:
    tool: str
    command: str
    args: dict
    policy_summary: str       # human-readable policy excerpt
    project_context: str      # recent git log + modified files
    history: list[Decision]   # last 5 decisions this session

@dataclass
class LLMDecision:
    verdict: Literal["APPROVE", "ESCALATE", "BLOCK"]
    reason: str               # shown in Telegram card
    confidence: float         # 0.0–1.0
```

### Prompt template (in policy.yml, overridable)

```
You are an AI babysitter reviewing an action by an AI coding agent.

PROJECT CONTEXT:
{project_context}

POLICY (abridged):
{policy_summary}

PROPOSED ACTION:
Tool: {tool}
Command: {command}
Args: {args}

RECENT DECISIONS:
{history}

Respond with exactly one line:
APPROVE | ESCALATE | BLOCK — <brief reason under 20 words>
```

### Backends to build

| Backend | Priority | Notes |
|---|---|---|
| Ollama | P0 | Local, free, works offline |
| LM Studio | P0 | Local, free, OpenAI-compatible API |
| Disabled | P0 | Skip LLM, use pattern-match only |
| Anthropic | P1 | Claude Haiku for cloud option |
| Google Gemini | P1 | Free tier |
| OpenAI | P2 | Drop-in via openai SDK |

### Deliverables

- `abs/backends/__init__.py` — `BackendFactory.create(config) → BabysitterBackend`
- `abs/backends/ollama.py`
- `abs/backends/lmstudio.py`
- `abs/backends/anthropic.py`
- `abs/backends/gemini.py`
- `abs/backends/disabled.py`
- `tests/backends/` — mocked HTTP tests for each

---

## Phase 4 — Agent Adapter Layer

**Goal:** `policy.yml` works the same whether the AI agent is Claude Code, Gemini CLI, or Codex. Each adapter exposes the same `AgentAdapter` interface.

### Abstract interface

```python
class AgentAdapter(ABC):
    async def capture(self) -> PaneSnapshot:
        ...

    async def send(self, text: str) -> None:
        ...

    async def detect_approval_request(self, snapshot: PaneSnapshot) -> ApprovalRequest | None:
        ...
```

### Adapters to build

| Adapter | Detection strategy |
|---|---|
| `ClaudeCodeAdapter` | Existing pattern classifier (already works) |
| `GeminiCLIAdapter` | Detect Gemini's `Allow this action? [y/n]` prompts |
| `CodexAdapter` | Detect Codex terminal approval patterns |
| `GenericTmuxAdapter` | Configurable prompt pattern via `policy.yml` |

For each new adapter, the config in `policy.yml`:

```yaml
agents:
  - type: gemini-cli
    pane: auto-detect        # or explicit tmux pane id
    approval_pattern: "Allow this action"
    yes_key: "y"
    no_key: "n"
```

### Deliverables

- `abs/adapters/__init__.py`
- `abs/adapters/claude_code.py` (refactored from existing)
- `abs/adapters/gemini_cli.py`
- `abs/adapters/codex.py`
- `abs/adapters/generic_tmux.py`
- `tests/adapters/`

---

## Phase 5 — Lifecycle Monitor

**Goal:** The babysitter tracks project lifecycle phase (planning → implementing → testing → deploying) and adjusts its strictness accordingly. A `DROP TABLE` in a migration during deploy gets a very different response than in a dev scratch file.

### Lifecycle phases

```yaml
lifecycle:
  planning:
    approve_extra: [WebSearch, WebFetch]
    escalate_extra: []
  implementing:
    approve_extra: []
    escalate_extra: []        # use profile defaults
  testing:
    approve_extra: [Bash]     # broader bash approval during test runs
    escalate_extra: []
  deploying:
    approve_extra: []
    escalate_extra: [Read]    # even reads get a second look during deploy
    block_extra:
      - "git push --force*"
      - "DROP*"
```

### Phase detection (automatic)

- `planning` — recent git commits include `plan`, `spike`, `rfc`, `design`; no code files changed yet
- `implementing` — code files being written/edited
- `testing` — test runner commands detected (`pytest`, `npm test`, `cargo test`)
- `deploying` — deploy commands detected (`kubectl`, `terraform apply`, `helm upgrade`, `docker push`)

### Deliverables

- `abs/lifecycle/detector.py`
- `abs/lifecycle/context.py` — project context builder (git log + diff summary)
- `tests/lifecycle/`

---

## Phase 6 — Telegram Integration (enhanced)

The existing Telegram bridge works. Phase 6 adds policy-awareness to the cards.

### Enhanced escalation card

```
🤔 Agent Babysitter — Needs Your Call
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 Agent: Claude Code
🔧 Tool: Bash
📋 Command: npm install express
📁 Project: my-api (implementing)

📊 Policy verdict: ESCALATE
🧠 Babysitter says: "Package install — verify you want this dependency"
⚖️  Confidence: 78%

Reply:
  y — approve
  n — block
  ! — block + add to permanent blocklist
  ? — ask babysitter for more context
```

### Deliverables

- `abs/telegram/cards.py` — updated card templates
- `abs/telegram/commands.py` — new `/policy`, `/lifecycle`, `/why` commands

---

## Execution Order

| Phase | Depends on | Estimated effort |
|---|---|---|
| 1 — Schema | nothing | 2 days |
| 2 — Matcher | Phase 1 | 2 days |
| 3 — LLM Backend | Phase 1 | 3 days |
| 4 — Adapters | Phase 2 | 3 days |
| 5 — Lifecycle | Phase 3, 4 | 2 days |
| 6 — Telegram | Phase 3, 5 | 1 day |

**Total: ~13 days** for a complete, tested policy engine.

---

## What "done" looks like

A developer creates this `policy.yml`:

```yaml
version: "1.0"
babysitter:
  backend: ollama
  model: llama3.2
notify:
  telegram:
    enabled: true
policy:
  profile: default
```

Runs `abs`, points it at their Claude Code / Gemini CLI / Codex pane, and walks away. The babysitter handles everything routine, buzzes their phone for anything interesting, and nothing dangerous happens while they're at the gym.

That is the definition of done.
