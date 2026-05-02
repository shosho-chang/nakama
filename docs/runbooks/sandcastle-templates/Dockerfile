FROM node:22-bookworm

# Install system dependencies (+ python3 — nakama is Python)
RUN apt-get update && apt-get install -y \
  git \
  curl \
  jq \
  python3 \
  python3-pip \
  python3-venv \
  && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
  && apt-get update && apt-get install -y gh \
  && rm -rf /var/lib/apt/lists/*

# Rename the base image's "node" user (UID 1000) to "agent" so bind-mounts map cleanly.
RUN usermod -d /home/agent -m -l agent node
USER agent

# Install Claude Code CLI (user-local install)
RUN curl -fsSL https://claude.ai/install.sh | bash

# Claude CLI + pip user binaries on PATH
ENV PATH="/home/agent/.local/bin:$PATH"

# pip user install (sandbox already isolated; no venv needed).
# PIP_BREAK_SYSTEM_PACKAGES required by Debian Bookworm PEP 668.
ENV PIP_USER=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /home/agent

# Make /home/agent world-writable so sandcastle can write `.gitconfig` (and other lifecycle
# artifacts) under any host UID. Sandcastle runs the container with `--user $hostUid:$hostGid`
# (see src/sandboxes/docker.ts), but image-side `/home/agent` is owned by UID 1000.
# On macOS, host UID is typically 501-503 (NOT 1000), so the running user can't write into
# its own home dir → `git config --global --add safe.directory ...` fails with "Permission denied".
# Linux desktops with UID 1000 dodge this; Windows Docker Desktop maps differently and dodges it too.
USER root
RUN chmod -R 0777 /home/agent
USER agent

# Pre-install dev tools (pytest / ruff / hypothesis / pytest-cov) so agent doesn't
# self-install on every iteration. Saves ~10-15s wall per trial. These are NOT in
# nakama's requirements.txt (CI uses pyproject.toml [project.optional-dependencies.dev]),
# so the per-iter `pip install -r requirements.txt` hook misses them.
RUN python3 -m pip install --user pytest ruff hypothesis pytest-cov

# Re-chmod /home/agent/.local after pip install — files written above are owned UID 1000,
# but sandcastle runs container with --user $hostUid (Mac UID 501-503), so the per-iter
# `pip install -r requirements.txt` hook fails permission denied on transitive dep updates
# (e.g. readability-lxml → sgmllib.py rewrite). Earlier line-45 chmod runs before pip install,
# so dev-tool .local tree misses the 0777 sweep.
USER root
RUN chmod -R 0777 /home/agent/.local
USER agent

# Sandcastle bind-mounts the git worktree at /home/agent/workspace at container start.
ENTRYPOINT ["sleep", "infinity"]
