# Sandcastle templates (nakama-customized)

These four files are the **canonical, version-controlled** templates for setting up Sandcastle on a fresh machine. They replace the `templates/` directory referenced in older runbook prose (which existed only on 修修's Windows desktop and never crossed machines).

## What's in here

| File | Customization vs upstream sandcastle defaults |
|---|---|
| `Dockerfile` | Adds `python3` + `python3-pip` + `python3-venv` + `PIP_USER=1` + `PIP_BREAK_SYSTEM_PACKAGES=1` (Debian Bookworm PEP 668) |
| `main.mts` | `imageName: "sandcastle:nakama"` + explicit `env: { ANTHROPIC_API_KEY, GH_TOKEN }` (sibling-dir layout) + `maxIterations: 5` + `copyToWorktree: []` (Python project, no node_modules) + Python pip install hook |
| `prompt.md` | Replaces the upstream RALPH preset: nakama-specific orientation (CLAUDE.md / `docs/agents/*` / `docs/decisions/`), Conventional Commits format, no-push-no-PR rule (sandcastle merge-to-head handles branching), nakama escalation flow |
| `.env.example` | Same as upstream — two keys: `ANTHROPIC_API_KEY` + `GH_TOKEN` |

## Setup on a new machine

See `../sandcastle.md` for the full runbook. Quick version:

```bash
# Sibling directory to nakama/
mkdir -p ~/Documents/sandcastle-test && cd ~/Documents/sandcastle-test
npm init -y
npm install --save-dev @ai-hero/sandcastle tsx

mkdir -p .sandcastle
cp ~/Documents/nakama/docs/runbooks/sandcastle-templates/{Dockerfile,main.mts,prompt.md,.env.example} .sandcastle/
cp .sandcastle/.env.example .sandcastle/.env
# Fill .sandcastle/.env with real ANTHROPIC_API_KEY + GH_TOKEN
printf '.env\nlogs/\nworktrees/\nnode_modules/\n' > .sandcastle/.gitignore

# One-time image build (~5 min)
docker build -t sandcastle:nakama -f .sandcastle/Dockerfile .sandcastle
```

## When to update these templates

- A round of trials reveals a new pitfall → update `main.mts` or `prompt.md`, commit, push.
- New base image / Claude CLI breaking change → update `Dockerfile`, rebuild image on every machine.
- New project rule in `CLAUDE.md` that the agent should obey → update `prompt.md` Rules section.

Keep customizations here, not in `~/Documents/sandcastle-test/.sandcastle/` directly. That way every machine `cp` from the same canonical source.
