---
name: 收工 — 2026-05-03 PR #320 ship + Usopp deploy + sandcastle sync
description: PR #320 Slice 1 squash merged bb5d54d；Usopp #270 busy_timeout fix 上 VPS（30s active 10:54:08 MainPID 702145）等明天 cron 觀察；sandcastle templates 三檔 sync + image v4 rebuild + 4 dev tools 驗證；Docker AutoStart=true；settings.json deny rule ssh+systemctl 砍掉
type: project
created: 2026-05-03
---

修修 2026-05-03 上午（從 Mac 回桌機）三件並行 ship：PR #320 DaVinci smoke 通過 → squash merge；Usopp VPS deploy；sandcastle templates 桌機 sync。

## 三件戰績

### 1. PR #320 Script-Driven Video Slice 1 ship

DaVinci import smoke 桌機跑通（fixture 限制 timeline 短但 schema 認得 + ripple delete + 30fps exact）→ squash merge `bb5d54d` → close issue #313 → delete remote branch → Slice 2 #314 unblocked。

詳見 [project_script_video_phase2a.md](project_script_video_phase2a.md)。

### 2. Usopp #270 busy_timeout fix 上 VPS

PR #307 sandcastle 寫的 fix（`shared/state.py:26` `PRAGMA busy_timeout` 5s→30s）已 merge 進 main，但 VPS 還跑舊 code。今天 deploy：

```bash
ssh nakama-vps "cd /home/nakama && git pull origin main && systemctl restart nakama-usopp"
```

驗證結果：
- ✅ git pull 拉到 `ce6a859`（含 PR #307）
- ✅ `shared/state.py:26` `PRAGMA busy_timeout=30000`
- ✅ service active, MainPID=702145, restart `2026-05-03 10:54:08 CST`
- ✅ daemon log: `usopp daemon start worker_id=usopp-shoshotw poll_interval_s=30 batch=5 site=wp_shosho`

**Acceptance 進度**：
- #1 ✅ code 不再 raise（PR #307 保證）
- #2 ⏳ end-to-end publish in cron 時段 — 等修修實際 SEO 中控台 audit→export→publish 觸發
- #3 ⏳ 連 3 天 0 traceback — 從 5/4 開始觀察到 5/6

journal 顯示 Apr 29-May 03 06:30 一連串 `database is locked` traceback（fix deploy 前的舊行為），10:54:08 restart 後新行為從那時開始。明天（5/4）05:30/06:30 cron 時段是新 fix 首次驗證。

### 3. Sandcastle templates 桌機 sync + image rebuild

`E:\sandcastle-test\.sandcastle\` 三檔跟 canonical 差距大（trial #1 setup 留下的舊版 vs PR #306+#318+#319 整理過的最新版）：

| 檔 | 差異行 | 內容 |
|---|---|---|
| Dockerfile | 109 | + libxml2-utils + chmod fix + tsc + nakama deps pre-install |
| main.mts | 115 | rewrite (cwd / timeout 600s / Mac UID dodge / hooks 改) |
| prompt.md | 153 | rewrite (CLAUDE.md / triage-labels / domain.md orientation) |

直接 cp 三檔 + `docker build -t sandcastle:nakama -f .sandcastle/Dockerfile .sandcastle`：

驗 image v4 dev tools 預裝：
- pytest 9.0.3 ✓
- ruff 0.15.12 ✓
- xmllint libxml 20914 ✓
- tsc 6.0.3 ✓

Image sha256:8ee78c63... 完成。下次 sandcastle round 跑用新 image。

### 4. Docker Desktop AutoStart=true

`C:\Users\Shosho\AppData\Roaming\Docker\settings-store.json` 改：
```diff
-  "AutoStart": false,
+  "AutoStart": true,
```

下次桌機開機自動啟動 Docker Desktop daemon（不影響當下 session）。

### 5. settings.json deny rule 砍

修修 2026-05-03 砍掉 `.claude/settings.json` 兩條 deny rule（為了讓我能 ssh VPS 跑 Usopp deploy）：
```diff
-      "Bash(systemctl *)",
-      "Bash(ssh *)",
```

詳見教訓 [feedback_settings_deny_rule_blocks_explicit_auth.md](feedback_settings_deny_rule_blocks_explicit_auth.md)。

## 雙視窗共用 working tree（dual_window_worktree 違例 + 安全收尾）

修修 2026-05-03 上午同時：
- 視窗 A（我）：PR #320 ship + Usopp deploy + sandcastle sync
- 視窗 B：教科書 ingest 軸線（ch1 重 ingest + ch4 完成 + vault E:/F: 教訓）

兩視窗共用 `E:/nakama` working tree（沒開 git worktree）。**安全收尾關鍵 = A 全 server-side 操作（沒留 working tree 改動），B 留 working tree dirty**。給 B 視窗清對話前先切 main 的指令（stash → checkout → pull → branch -D → stash pop → 解 conflict），B 自己跑完 PR #323 ship → main `ce6a859`。

教訓：dual-window 還是該開 git worktree（feedback_dual_window_worktree.md hold），這次運氣好沒 collide（A 沒 working tree 改動 + B 改 memory 不踩 A）。

## 修修要做的（acceptance gate 等待）

| 項 | 何時 | 動作 |
|---|---|---|
| Usopp #270 acceptance #2 | 任意 cron 時段（5:30/6:30） | 跑一次 SEO 中控台 audit→export→publish 端到端 |
| Usopp #270 acceptance #3 | 5/4 + 5/5 + 5/6 早上 | 跟我說一聲，我 ssh 看 journalctl 確認 0 traceback |
| Slice 2 #314 dispatch | 隨時 | 開 Claude Design 視覺探索 6 components → 「交付套件 → Claude Code」handoff |

## 文件 / artifacts 索引

- PR #320 squash commit `bb5d54d`
- DaVinci smoke runbook：[docs/runbooks/2026-05-02-davinci-import-smoke.md](../../docs/runbooks/2026-05-02-davinci-import-smoke.md)
- Usopp deploy runbook：[docs/runbooks/deploy-usopp-vps.md](../../docs/runbooks/deploy-usopp-vps.md)
- Sandcastle templates canonical：[docs/runbooks/sandcastle-templates/](../../docs/runbooks/sandcastle-templates/)
- Sandcastle runbook：[docs/runbooks/sandcastle.md](../../docs/runbooks/sandcastle.md)

## 相關記憶 cross-ref

- [project_script_video_phase2a.md](project_script_video_phase2a.md) — Slice 1 ship 詳細
- [reference_sandcastle.md](reference_sandcastle.md) — sandcastle 工具背景 + Mac 6 gotcha
- [feedback_settings_deny_rule_blocks_explicit_auth.md](feedback_settings_deny_rule_blocks_explicit_auth.md) — settings deny 優先教訓
- [feedback_dual_window_worktree.md](feedback_dual_window_worktree.md) — dual-window 該開 worktree（這次違例）
- [project_session_2026_05_02_mac_sandcastle.md](project_session_2026_05_02_mac_sandcastle.md) — 前一晚 Mac sandcastle setup（templates 凍結來源）
