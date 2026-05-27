
# 📋 Final Project Spec: Claude Code Tmux Bridge

## Project Name
`claude-tmux-bridge` (or your preferred name)

## Mission
Build a Python application that monitors a `tmux` pane running Claude Code, auto-responds to approval prompts based on configurable policies, and bridges to Telegram so users can remotely review and respond to Claude Code prompts when away from their laptop.

---

## 🎯 Core Use Cases

1. **Auto-approve safe operations** — User configures which Claude Code prompts can be auto-approved (e.g., file reads, safe commands) so they don't waste time clicking "yes"
2. **Block dangerous operations** — User configures which prompts are auto-denied (e.g., `rm -rf`, `sudo`)
3. **Remote interaction via Telegram** — When Claude Code needs human judgment, the app sends a Telegram message; the user replies on Telegram, and the reply is injected into Claude Code
4. **Manual injection** — User can send any message to the Telegram bot anytime, and it gets typed into the Claude Code session

---

## 🏗️ Architecture

### File Structure
```
claude-tmux-bridge/
├── main.py                       # CLI entry point + orchestrator
├── requirements.txt
├── README.md
├── config/
│   ├── policy.yml                # User-editable policy rules
│   └── telegram.env              # Bot token + chat ID (gitignored)
├── src/
│   ├── __init__.py
│   ├── tmux_monitor.py           # Polls tmux pane, detects changes
│   ├── tmux_controller.py        # Sends keys to tmux pane
│   ├── prompt_classifier.py      # Classifies what Claude is asking
│   ├── policy_engine.py          # Decides action based on policy.yml
│   ├── telegram_bridge.py        # Telegram bot (send + receive)
│   ├── session_store.py          # Logs all events to disk
│   └── session_picker.py         # Discovers/picks tmux session
├── sessions/                     # Auto-generated logs (jsonl)
└── .gitignore
```

---

## 🔧 Component Specifications

### 1. `session_picker.py` — Session Discovery
Supports all three discovery modes:

```python
def pick_session(cli_arg: str | None = None) -> str:
    """
    Priority:
    1. If --session CLI arg provided → use it
    2. If --auto-detect → find pane running 'claude' command
    3. Else → interactive picker (list all sessions/windows/panes)
    Returns: "session:window.pane" string
    """
```

Use `tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}'` to enumerate.

For auto-detect: filter where `pane_current_command` contains `claude` or `node`.

For interactive: use a simple numbered list, no fancy TUI library needed.

### 2. `tmux_monitor.py` — The Watcher

```python
class TmuxMonitor:
    def __init__(self, pane: str, poll_interval: float = 1.0):
        self.pane = pane
        self.poll_interval = poll_interval
        self.last_snapshot = ""
    
    def capture(self) -> str:
        """Run tmux capture-pane -p -t <pane> -S -200"""
    
    def strip_ansi(self, text: str) -> str:
        """Remove ANSI escape codes for clean pattern matching"""
    
    async def watch(self, on_change_callback):
        """Async loop. Calls callback(new_content, full_snapshot) on change."""
```

Key behaviors:
- Poll every 1 second
- Diff against previous snapshot
- Strip ANSI codes before sending to classifier
- Detect "stable state" — pane hasn't changed for N polls (Claude is waiting)

### 3. `prompt_classifier.py` — The Brain

```python
from enum import Enum

class PromptType(Enum):
    APPROVAL_YN = "approval_yn"           # "(y/n)" style
    APPROVAL_MENU = "approval_menu"       # Arrow-key "❯ Yes / No"
    TEXT_INPUT = "text_input"             # Free-form text expected
    RUNNING = "running"                   # Claude is working
    IDLE = "idle"                         # Nothing happening
    COMPLETE = "complete"                 # Task done

class ClassifiedPrompt:
    type: PromptType
    raw_text: str
    extracted_command: str | None         # e.g., "npm install"
    context: str                          # Last ~10 lines for user review

def classify(snapshot: str) -> ClassifiedPrompt:
    """
    Look at last 20 lines of the pane.
    Match against known Claude Code patterns:
    - "Do you want to proceed?"
    - "❯ 1. Yes  2. No"
    - "Run command: ..."
    - "│ Bash(...)" boxes
    Return classified prompt.
    """
```

Make patterns **configurable in policy.yml** so users can adjust if Claude Code's UI changes.

### 4. `policy_engine.py` — The Rule Engine

```python
class PolicyDecision(Enum):
    AUTO_YES = "auto_yes"
    AUTO_NO = "auto_no"
    ESCALATE_TELEGRAM = "escalate_telegram"
    WAIT_LOCAL = "wait_local"  # Do nothing, let user respond manually

class PolicyEngine:
    def __init__(self, policy_path: str):
        self.config = load_yaml(policy_path)
        self.active_profile = self.config["active_profile"]
    
    def decide(self, prompt: ClassifiedPrompt) -> PolicyDecision:
        """
        Evaluation order:
        1. Check auto_deny patterns → AUTO_NO
        2. Check auto_approve patterns → AUTO_YES
        3. Check escalate patterns → ESCALATE_TELEGRAM
        4. Fall back to profile's default_action
        """
```

### 5. `tmux_controller.py` — The Responder

```python
class TmuxController:
    def __init__(self, pane: str):
        self.pane = pane
    
    def send_yes(self):
        """tmux send-keys -t <pane> 'y' Enter"""
    
    def send_no(self):
        """tmux send-keys -t <pane> 'n' Enter"""
    
    def send_text(self, text: str):
        """tmux send-keys -t <pane> '<text>' Enter"""
    
    def send_arrow_select(self, option: str):
        """For menu-style prompts: send arrow keys + Enter"""
    
    def send_raw_keys(self, keys: str):
        """For special keys like 'C-c', 'Escape'"""
```

### 6. `telegram_bridge.py` — The Remote Link

Use `python-telegram-bot` v21+ (async).

```python
class TelegramBridge:
    def __init__(self, token: str, chat_id: str, controller: TmuxController):
        self.bot = Application.builder().token(token).build()
        self.chat_id = chat_id
        self.controller = controller
        self.pending_prompt = None  # Track what we're waiting on
    
    async def notify_approval_needed(self, prompt: ClassifiedPrompt):
        """Send formatted message asking user to approve."""
        msg = f"🤖 Claude wants to:\n```\n{prompt.extracted_command}\n```\nReply: y / n / or custom text"
        await self.bot.bot.send_message(self.chat_id, msg, parse_mode="Markdown")
        self.pending_prompt = prompt
    
    async def notify_completion(self, summary: str):
        """Ping user when Claude finishes a long task."""
    
    async def handle_user_message(self, update, context):
        """
        Triggered by any incoming Telegram message.
        - If pending_prompt exists → use reply as response
        - Else → inject reply directly into pane (manual injection mode)
        """
```

### 7. `session_store.py` — The Audit Log

```python
class SessionStore:
    def __init__(self, session_name: str):
        self.path = f"sessions/{session_name}_{timestamp}.jsonl"
    
    def log_event(self, event_type: str, data: dict):
        """Append a JSON line. Event types: snapshot_change, prompt_detected, 
        decision_made, action_sent, telegram_sent, telegram_received"""
```

### 8. `main.py` — The Orchestrator

```python
async def main():
    args = parse_cli_args()
    pane = pick_session(args.session, args.auto_detect)
    
    monitor = TmuxMonitor(pane)
    controller = TmuxController(pane)
    classifier = PromptClassifier()
    policy = PolicyEngine("config/policy.yml")
    store = SessionStore(pane)
    telegram = TelegramBridge(...)
    
    async def on_pane_change(new_content, snapshot):
        store.log_event("snapshot_change", {"new": new_content})
        prompt = classifier.classify(snapshot)
        
        if prompt.type in [PromptType.RUNNING, PromptType.IDLE]:
            return
        
        if prompt.type == PromptType.COMPLETE:
            await telegram.notify_completion(snapshot)
            return
        
        decision = policy.decide(prompt)
        store.log_event("decision_made", {"decision": decision.value})
        
        if decision == PolicyDecision.AUTO_YES:
            controller.send_yes()
        elif decision == PolicyDecision.AUTO_NO:
            controller.send_no()
        elif decision == PolicyDecision.ESCALATE_TELEGRAM:
            await telegram.notify_approval_needed(prompt)
        # WAIT_LOCAL: do nothing
    
    # Run monitor + telegram bot concurrently
    await asyncio.gather(
        monitor.watch(on_pane_change),
        telegram.start_polling(),
    )

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 📄 `policy.yml` Schema

```yaml
active_profile: default

# Detection patterns (so users can update if Claude Code UI changes)
detection:
  approval_yn_patterns:
    - "\\(y/n\\)"
    - "\\? \\[Y/n\\]"
  approval_menu_patterns:
    - "❯.*Yes"
    - "1\\. Yes.*2\\. No"
  completion_patterns:
    - "✓ Done"
    - "Task complete"

profiles:
  default:
    # Patterns matched against the command/context Claude is asking about
    auto_approve:
      - "^Read file"
      - "^List directory"
      - "^Run: (ls|cat|pwd|echo|grep|find)"
    
    auto_deny:
      - "rm -rf"
      - "sudo"
      - "chmod 777"
      - "DROP TABLE"
      - "git push --force"
    
    escalate_to_telegram:
      - "^Run: npm install"
      - "^Run: git push"
      - "^Edit file"
      - "^Create file"
    
    default_action: escalate_telegram  # If nothing matches

  yolo:
    # Trust mode — auto-approve everything except truly dangerous
    auto_deny:
      - "rm -rf /"
    default_action: auto_yes

  restricted:
    # Everything goes to Telegram for review
    auto_deny:
      - "rm -rf"
      - "sudo"
    default_action: escalate_telegram

  paranoid:
    # Deny most things, escalate the rest
    auto_deny:
      - "rm"
      - "sudo"
      - "chmod"
      - "chown"
      - "mv"
      - "> "  # Output redirection
    default_action: escalate_telegram

telegram:
  enabled: true
  notify_on_completion: true
  approval_timeout_seconds: 600  # If user doesn't reply in 10min → auto-deny
  timeout_action: auto_no
  allow_manual_injection: true   # User can send msgs anytime
```

---

## 🚀 CLI Interface

```bash
# Auto-detect Claude Code session
python main.py --auto-detect

# Specify session manually
python main.py --session mywork:0.0

# Interactive picker
python main.py

# With specific policy profile (overrides yml)
python main.py --profile yolo

# Disable Telegram for this run
python main.py --no-telegram
```

---

## 📦 Dependencies (`requirements.txt`)

```
python-telegram-bot>=21.0
pyyaml>=6.0
python-dotenv>=1.0
libtmux>=0.35  # Cleaner than raw subprocess (optional)
rich>=13.0     # Pretty console output
```

---

## 🛠️ Development Phases

**Phase 1 — Local monitoring & auto-response (no Telegram)**
- Build: `session_picker`, `tmux_monitor`, `prompt_classifier`, `policy_engine`, `tmux_controller`, `main`
- Test: Manually run Claude Code in tmux, verify auto yes/no works
- Acceptance: Auto-approves `ls` commands, auto-denies `rm -rf`, prints escalations to console

**Phase 2 — Telegram integration**
- Build: `telegram_bridge`
- Test: Run Claude Code, get Telegram message, reply, see it injected
- Acceptance: Round-trip works end-to-end

**Phase 3 — Robustness**
- Add: `session_store`, error handling, reconnect logic, timeout handling
- Polish: Logging, README, sample policy.yml

---

## ⚠️ Edge Cases to Handle

1. Tmux pane closes mid-session → graceful shutdown + Telegram notification
2. Telegram bot loses connection → retry with backoff
3. Two prompts detected back-to-back before first is answered → queue them
4. Claude Code uses arrow-key menus instead of y/n → handle both with `send_arrow_select`
5. ANSI escape codes break pattern matching → strip them first
6. User sends Telegram message when no prompt is pending → use "manual injection" mode
7. Same pane state read twice (no actual change) → diff check prevents duplicate action
8. Approval timeout — what if user is asleep? → configurable timeout_action

---

## 🔐 Setup Instructions (include in README)

1. Install tmux: `sudo apt install tmux` (Linux) / `brew install tmux` (Mac)
2. Create Telegram bot:
   - Message `@BotFather` on Telegram → `/newbot` → get token
   - Message your new bot → visit `https://api.telegram.org/bot<TOKEN>/getUpdates` → find your chat_id
3. Create `config/telegram.env`:
   ```
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```
4. Install deps: `pip install -r requirements.txt`
5. Start Claude Code in tmux: `tmux new -s claude-work` → run `claude`
6. In another terminal: `python main.py --auto-detect`

---

## ✅ Success Criteria

- User can leave laptop, get Telegram notifications, respond, and Claude Code continues seamlessly
- Safe commands (read, list) never bother the user
- Dangerous commands (`rm -rf`) are always blocked
- All decisions are logged for audit
- Setup takes <10 minutes for a new user
