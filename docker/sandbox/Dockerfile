# Agent Babysitter sandbox image (Phase 3 / D7) — a long-lived Ubuntu box we exec
# Claude Code sessions into. The project lives in a DEDICATED host folder
# bind-mounted at /home/dev/workspace (the ONLY host path the container sees);
# everything else on the host stays invisible.
#
# Security posture (PLAN.md 5.6): non-root user `dev`, no secrets baked in
# (credentials are copied in at `abs sandbox create`, never here), default bridge
# network (so git clone / browsing works). Runs `tail -f /dev/null` as its main
# process so it stays alive to be exec'd into.

FROM ubuntu:24.04

# Match the container user's uid to the host user so bind-mounted files have clean
# ownership (default 1000; the CLI passes --build-arg UID=$(id -u)).
ARG UID=1000
ARG USERNAME=dev
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates jq ripgrep build-essential unzip \
      python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Node LTS (NodeSource) — needed for Claude Code's npm install.
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code, the official npm package. No credentials — those are copied in at
# `abs sandbox create` (D8), never baked into the image.
RUN npm install -g @anthropic-ai/claude-code

# The in-container launcher run by `docker exec … absd-session` (Phase 3.2).
COPY absd-session /usr/local/bin/absd-session
RUN chmod 0755 /usr/local/bin/absd-session

# --- ABS inside the box (v4) -------------------------------------------------
#
# The sandbox is meant to be a COMPLETE isolated ABS environment, not just a
# claude shell: `abs` on PATH, the status bar, the ABS remote controls, the Bash
# guard. All of that is abs.sh's own launch path, so abs.sh has to live in here.
#
# Split by rate of change: the image carries the SLOW parts (the venv + its
# deps, the directory skeleton, the PATH shim); the fast-moving CODE (abs.sh,
# absd/) is `docker cp`'d in at container start and again before every session
# (SandboxManager._install_abs_into_box). That way editing abs.sh on the host
# does not need an image rebuild, and a box can never run a stale launcher.
RUN mkdir -p /opt/abs/absd /opt/abs/agent_babysitter \
    && python3 -m venv /opt/abs/.venv \
    && /opt/abs/.venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/abs/.venv/bin/pip install --no-cache-dir aiohttp \
    && chmod -R 0755 /opt/abs

# `abs` on PATH. A thin shim rather than a symlink so a box whose code sync has
# not run yet fails with a sentence the operator can act on, instead of ENOENT.
COPY abs-shim /usr/local/bin/abs
RUN chmod 0755 /usr/local/bin/abs

# The restricted-assistant system prompt (Stage 3). Same file absd/prompts.py reads
# on the host — `absd-session --restricted` injects it via --append-system-prompt so
# the model refuses to build/run code and disclaims session control. Baking it into
# the image keeps the prompt out of the pane command's argv (no `ps` exposure) and
# single-sources it with the host.
COPY restricted-prompt.txt /usr/local/share/absd/restricted-prompt.txt
RUN chmod 0644 /usr/local/share/absd/restricted-prompt.txt

# ubuntu:24.04 ships a default `ubuntu` user at uid 1000; drop it so `dev` can take
# the requested uid cleanly, then create `dev`.
RUN userdel -r ubuntu 2>/dev/null || true; \
    useradd -m -u "${UID}" -s /bin/bash "${USERNAME}" \
    && mkdir -p /home/${USERNAME}/workspace \
    && chown -R "${UID}:${UID}" /home/${USERNAME}

USER ${USERNAME}

# Bun — the official Telegram plugin's MCP server hardcodes `"command": "bun"`, so
# the in-container `claude --channels plugin:telegram…` needs it to poll from inside
# the box (Phase 3.2). Installed into dev's home; no sudo.
RUN curl -fsSL https://bun.sh/install | bash
ENV PATH="/home/dev/.bun/bin:${PATH}"

WORKDIR /home/dev/workspace

# Stay alive so the sandbox can be exec'd into (docker exec … claude/bash).
CMD ["tail", "-f", "/dev/null"]
