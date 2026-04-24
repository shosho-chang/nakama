---
name: Python dep 加 requirements.txt 時要同步 pyproject.toml
description: Nakama 雙份 manifest 並存，CI 只讀 pyproject.toml；只改 requirements.txt 會讓 CI ModuleNotFoundError
type: feedback
---

加 Python dep 時 `requirements.txt` + `pyproject.toml` 兩份**都要同步**更新，不能只改一份。

**Why:** 2026-04-24 PR #107（Zoro Slice C1 Reddit→Trends hotfix）加 `trendspy` 只進了 `requirements.txt`，漏了 `pyproject.toml`。CI workflow 跑 `pip install -e ".[dev]"`（讀 pyproject.toml），本機開發則兩份都讀，所以本機能跑、CI 直接 `ModuleNotFoundError: No module named 'trendspy'` × 6 tests。主幹 CI 從 PR #107 merge 當下就紅燈，PR #110/#112 試圖 merge 才被 blocked，才發現主幹早就 broken 了。最終 PR #113 補 pyproject.toml 3 行修完。

**How to apply:**

1. 加新 dep 時，`sed` 加 `requirements.txt` 後**立刻**同步到 `pyproject.toml` 的 `[project].dependencies`（或 `[project.optional-dependencies].dev`，看用途）
2. 版本 pin 用同一個 lower bound（例如 requirements.txt 寫 `trendspy>=0.1.6`，pyproject.toml 也寫 `trendspy>=0.1.6`）
3. Local sanity：commit 前跑 `pip install -e ".[dev]"` 到乾淨 venv 驗證 CI 能跑起測試
4. 檢查脚本 idea：`tests/test_dep_manifest_sync.py` 讀兩份 manifest，diff 頂層 package name set，不一致就 fail — 避免下次再踩。暫未實作，踩第三次再說
5. **類似雙份 manifest 風險**：`.env.example` vs 實際 VPS `.env`（見 `feedback_vps_env_drift_check.md`）、`config/*.yaml` vs code default，遇到「一份靜態宣告 + 一份運行時實際讀取」的 pattern 就警覺 drift 風險
