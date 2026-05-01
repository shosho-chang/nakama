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

# Sandcastle bind-mounts the git worktree at /home/agent/workspace at container start.
ENTRYPOINT ["sleep", "infinity"]
