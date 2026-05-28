#!/usr/bin/env bash
# VPS deploy: git pull + smart restart of affected systemd services.
#
# Solves a recurring problem: after `git pull` on VPS, the right services
# don't get restarted, so HTTP routes / handlers / daemons silently keep
# running stale code. Most recently /bridge/digests was 404 for 4 days
# because thousand-sunny was last restarted before the route landed.
#
# Path → service mapping:
#   thousand_sunny/                → thousand-sunny.service
#   gateway/                       → nakama-gateway.service
#   agents/usopp/                  → nakama-usopp.service
#   agents/<other>/, shared/       → thousand-sunny + nakama-gateway
#                                    (both import these)
#   requirements.txt               → all three (deps changed)
#   docs/, memory/, prompts/, tests/, .github/, *.md → no restart
#
# Usage (on VPS, as the nakama user with sudo):
#   cd /home/nakama
#   ./scripts/deploy_vps.sh                 # pull main + restart
#   ./scripts/deploy_vps.sh --dry-run       # show plan, don't act
#   ./scripts/deploy_vps.sh --force-all     # restart all three regardless
#
# Exit codes:
#   0  success (or dry-run)
#   1  not on main / dirty tree / git pull failed
#   2  pip install failed
#   3  one or more services failed to restart
#   4  post-restart healthz check failed

set -euo pipefail

DRY_RUN=0
FORCE_ALL=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force-all) FORCE_ALL=1 ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      exit 1
      ;;
  esac
done

cd "$(dirname "$0")/.."
REPO_ROOT=$(pwd)
echo "==> Repo: $REPO_ROOT"

# --- preflight ---
branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$branch" != "main" ]; then
  echo "ERROR: not on main (current: $branch). Refusing to deploy." >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERROR: working tree dirty. Stash or commit before deploying." >&2
  git status --short >&2
  exit 1
fi

OLD_SHA=$(git rev-parse HEAD)
echo "==> Current HEAD: $OLD_SHA"

# --- pull ---
echo "==> git fetch + pull --ff-only origin main"
git fetch --prune origin
git pull --ff-only origin main

NEW_SHA=$(git rev-parse HEAD)
echo "==> New HEAD:     $NEW_SHA"

if [ "$OLD_SHA" = "$NEW_SHA" ] && [ "$FORCE_ALL" -eq 0 ]; then
  echo "==> No new commits. Nothing to restart. (Use --force-all to override.)"
  exit 0
fi

# --- decide which services need restart ---
declare -A NEED=( [thousand-sunny]=0 [nakama-gateway]=0 [nakama-usopp]=0 )
PIP_INSTALL=0

if [ "$FORCE_ALL" -eq 1 ]; then
  echo "==> --force-all: restarting all services"
  NEED[thousand-sunny]=1
  NEED[nakama-gateway]=1
  NEED[nakama-usopp]=1
  PIP_INSTALL=1
  CHANGED_FILES="(--force-all)"
else
  CHANGED_FILES=$(git diff --name-only "$OLD_SHA" "$NEW_SHA")
  echo "==> Changed files:"
  echo "$CHANGED_FILES" | sed 's/^/    /'

  while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
      thousand_sunny/*)
        NEED[thousand-sunny]=1 ;;
      gateway/*)
        NEED[nakama-gateway]=1 ;;
      agents/usopp/*|agents/usopp.py)
        NEED[nakama-usopp]=1 ;;
      agents/*|shared/*)
        # imported by both web and slack gateway
        NEED[thousand-sunny]=1
        NEED[nakama-gateway]=1 ;;
      requirements.txt|requirements*.txt|pyproject.toml)
        PIP_INSTALL=1
        NEED[thousand-sunny]=1
        NEED[nakama-gateway]=1
        NEED[nakama-usopp]=1 ;;
      docs/*|memory/*|prompts/*|tests/*|.github/*|*.md|CONTEXT*.md|CLAUDE.md)
        : ;;  # no restart needed
      *)
        # unknown path — conservative: restart web (most common surface)
        NEED[thousand-sunny]=1 ;;
    esac
  done <<< "$CHANGED_FILES"
fi

plan=()
for svc in thousand-sunny nakama-gateway nakama-usopp; do
  if [ "${NEED[$svc]}" -eq 1 ]; then
    plan+=("$svc")
  fi
done

echo
echo "==> Plan:"
[ "$PIP_INSTALL" -eq 1 ] && echo "    - pip install -r requirements.txt"
if [ ${#plan[@]} -eq 0 ]; then
  echo "    (no service restarts needed)"
else
  for svc in "${plan[@]}"; do echo "    - systemctl restart $svc"; done
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo
  echo "==> Dry run; exiting without action."
  exit 0
fi

# --- pip install (if needed) ---
if [ "$PIP_INSTALL" -eq 1 ]; then
  echo
  echo "==> pip install -r requirements.txt"
  if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  if ! pip install -r requirements.txt; then
    echo "ERROR: pip install failed" >&2
    exit 2
  fi
fi

# --- restart services ---
fail=0
for svc in "${plan[@]}"; do
  echo
  echo "==> systemctl restart $svc"
  if sudo systemctl restart "$svc"; then
    sleep 2
    if sudo systemctl is-active --quiet "$svc"; then
      echo "    OK ($svc active)"
    else
      echo "    FAIL ($svc not active after restart)" >&2
      sudo journalctl -u "$svc" -n 20 --no-pager >&2 || true
      fail=1
    fi
  else
    echo "    FAIL (restart command exited non-zero)" >&2
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  exit 3
fi

# --- post-deploy healthz check ---
if [ "${NEED[thousand-sunny]}" -eq 1 ]; then
  echo
  echo "==> healthz check"
  # Hit the origin directly — going through nakama.shosho.tw from the VPS itself
  # triggers Cloudflare's bot challenge (VPS egress IP is flagged) and the
  # JS-challenge HTML comes back instead of the healthz JSON.
  if uptime=$(curl -fsS --max-time 10 http://127.0.0.1:8000/healthz | python3 -c 'import sys,json; print(json.load(sys.stdin)["uptime_seconds"])' 2>/dev/null); then
    echo "    uptime_seconds=$uptime (should be small)"
    if [ "$uptime" -gt 120 ]; then
      echo "    WARN: uptime > 120s — restart may not have taken effect" >&2
    fi
  else
    echo "    WARN: healthz check failed" >&2
    exit 4
  fi
fi

echo
echo "==> Deploy complete. ${OLD_SHA:0:7} → ${NEW_SHA:0:7}"
