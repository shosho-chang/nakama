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
#   ./scripts/deploy_vps.sh --dry-run       # fetch + show plan；**不 pull、不動 HEAD**
#   ./scripts/deploy_vps.sh --force-all     # restart all three regardless
#
# --dry-run 是唯讀的（只 fetch），跑完再跑正式 deploy 會正常重啟。舊版 dry-run
# 會 pull，導致接著跑正式版時被判定「無新 commit → 不用重啟」，VPS 停在
# 新檔案 + 舊 process（2026-07-30 實際踩到）。
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

# --- fetch, then pull only when actually deploying ---
#
# ⚠️ --dry-run 絕對不可以 pull。舊版無條件 pull、只在最後跳過 restart，結果：
# 先跑 dry-run 看計畫 → 再跑正式 deploy → 正式那次看到 OLD_SHA == NEW_SHA →
# 「No new commits. Nothing to restart.」→ **服務永遠不重啟**，VPS 停在
# 「新檔案 + 舊 process」——正是本 script 當初被寫出來要防的那個事故
# （2026-05-28 /bridge/digests 4 天 404）。2026-07-30 實際踩到並靠 --force-all
# 補救。照 usage 註解「先 dry-run 看看」的直覺操作就會中。
echo "==> git fetch origin"
git fetch --prune origin
NEW_SHA=$(git rev-parse FETCH_HEAD)

if [ "$OLD_SHA" = "$NEW_SHA" ] && [ "$FORCE_ALL" -eq 0 ]; then
  echo "==> No new commits. Nothing to restart. (Use --force-all to override.)"
  exit 0
fi

echo "==> Incoming: $OLD_SHA → $NEW_SHA"
if [ "$OLD_SHA" != "$NEW_SHA" ]; then
  git log --oneline "$OLD_SHA..$NEW_SHA" | sed 's/^/    /'
fi

if [ "$DRY_RUN" -eq 0 ]; then
  echo "==> git pull --ff-only origin main"
  git pull --ff-only origin main
  PULLED_SHA=$(git rev-parse HEAD)
  if [ "$PULLED_SHA" != "$NEW_SHA" ]; then
    echo "ERROR: pull 後 HEAD ($PULLED_SHA) 與 fetch 目標 ($NEW_SHA) 不符 — 中止。" >&2
    exit 1
  fi
else
  echo "==> Dry run: 不 pull（HEAD 保持 $OLD_SHA），以下計畫依 fetch 的內容推算"
fi

# --- decide which services need restart ---
declare -A NEED=( [thousand-sunny]=0 [nakama-gateway]=0 [nakama-usopp]=0 )
# Always run pip install — pip is a fast no-op when everything is already
# installed (~2s), and this catches the case where a past commit added a
# dep that the VPS never picked up (2026-05-28 incident: bleach was added
# earlier, VPS never ran pip install, next restart crashed with ModuleNotFoundError).
PIP_INSTALL=1

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
        # PIP_INSTALL is already 1 unconditionally; deps change → restart everything
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
  echo "==> Dry run; exiting without action. (HEAD 未動，重跑不帶 --dry-run 才會實際 deploy)"
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
