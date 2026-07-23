# Manual test — the restricted assistant (`abs restricted`)

Prereq: Docker; the daemon installed + running; an existing paired `default` profile
(so the pairing PIN relays to your phone); a phone with Telegram; a throwaway bot from
@BotFather ready to pair.

> One-time: Stage 3 needs the **v3** image (bakes in the restricted prompt + extends
> `absd-session`). Rebuild it once:
> ```sh
> abs sandbox build --rebuild
> ```

## 1. Create the restricted assistant

```sh
abs restricted create assistant
```

Expected, in order:
- Creates a dedicated **no-credentials** sandbox `assistant` ("no host credentials
  copied — Claude Code logs in separately inside this box").
- BotFather walkthrough → asks **`Bot token:`** (hidden). Paste your throwaway bot's
  token → **`Authenticated as @…`**.
- A pairing PIN, also sent to your phone via your `default` bot. Open the NEW bot, tap
  **Start**, send it the PIN → **`Paired`**, a "restricted assistant paired ✅"
  confirmation, and the `/` menu registered.
- Ends with: profile `assistant` (Haiku, sandbox `assistant`, no host creds) and the
  next step — `abs restricted login assistant`. It does NOT launch yet.

## 2. Log Claude in inside the box (one time)

```sh
abs restricted login assistant
```

- Drops you into an interactive `claude` INSIDE the box. Complete the login
  (device-code/browser) as the operator, then `/exit`.
- Within a few seconds the daemon brings the assistant online on its own (keep-alive).
  Confirm:
  ```sh
  abs daemon status                          # 'assistant: restricted-live'
  journalctl --user -u absd -n 30            # 'restricted session (re)launched in box assistant'
  ```

## 3. It answers everyday questions (on Haiku)

From the **restricted bot's** Telegram chat:
- Ask *"what's the weather in Tokyo right now?"* or *"summarize https://example.com"* or
  *"what's 18% of 240?"* → it answers (web lookups where it has a tool, calculations,
  general Q&A). Fast/cheap — it's Haiku.
- Ask it to *"make a note: buy milk, eggs, bread"* → it writes a notes/Markdown file in
  its workspace. That's allowed (not project code).

## 4. It REFUSES to build code

From the restricted bot, ask *"write me a python script that scrapes a website"* (or
*"build a small sorting algorithm in C"*). Expected:
- It refuses with, verbatim: **"This is a restricted assistant — ask the operator to
  upgrade your profile to build projects."** (optionally offering the non-code help it
  can give). No code is produced.

## 5. Session control is operator-only

- From the restricted bot, send **`ABS START`** or **`ABS EXIT`** → while the session is
  live, the in-box assistant declines (session control isn't its to give). It never
  launches or ends a session.
- (Optional, to see the daemon-side refusal) Stop it first: `abs restricted stop
  assistant`, then from the bot send `ABS START` → the daemon replies with the
  control-refusal / offline note; it does NOT start a normal session. Resume with `abs
  restricted start assistant`.

## 6. Login-needed path (optional)

- `docker exec -it absd-sbx-assistant claude /logout` (or remove
  `/home/dev/.claude/.credentials.json` in the box) to simulate an expired login.
- Within a few relaunch attempts the daemon gives up and DMs the operator's chat:
  **"🔐 Restricted assistant 'assistant' needs login: run `abs restricted login
  assistant` …"** — exactly once (no spam).
- Run `abs restricted login assistant`, re-auth, `/exit` → it comes back on its own.

## 7. Operator controls

```sh
abs restricted list                 # shows: assistant  sandbox=assistant  model=haiku
abs restricted stop assistant       # pause keep-alive + stop the box
abs restricted start assistant      # resume
abs restricted destroy assistant    # remove the sandbox + profile
```

- After `destroy`, `abs daemon status` drops the profile within ~60s (rescan). Delete
  the bot in @BotFather if it was a throwaway.

## Containment spot-check (optional, reassuring)

The restricted box holds NO host credentials:
```sh
docker exec absd-sbx-assistant test -e /home/dev/.claude/.credentials.json; echo $?
# 1 (absent) BEFORE you run `abs restricted login`; after login it's the box's OWN creds,
# never a copy of your host ~/.claude.
docker exec absd-sbx-assistant ls /home/dev        # no copy of your host home
```
