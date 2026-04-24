# Usopp Publisher Daemon — VPS 部署

**Scope:** 把 `nakama-usopp` daemon 從本地跑起來 → 變成 VPS systemd service，解鎖 Slice C2 的 LiteSpeed Day 1 實測。
**Owner:** 修修手動 ssh 執行（桌機 / Mac 預先準備好材料）。
**執行條件:** Slice C1 (PR #97) 已 merge；桌機 repo 已 `git pull origin main`。
**時間預估:** 約 20 分鐘（含 `.env` diff + 首次 journal 觀察）。

---

## 為什麼需要獨立 systemd service

- `thousand-sunny`（FastAPI web） vs `nakama-gateway`（Slack Socket Mode） vs `nakama-usopp`（publisher poll loop）是三個**獨立工作負載**，掛一個不該帶倒其他。
- Usopp daemon 有 graceful shutdown（SIGTERM → 收完 current batch 再退），`TimeoutStopSec=60` 配合 signal handler，避免 abrupt kill 留下 stuck `claimed` row。
- 有獨立 service 後 `journalctl -u nakama-usopp -f` 可以單獨追 publisher log，不跟 web / gateway 混。

相關：[`feedback_vps_two_services.md`](../../memory/claude/feedback_vps_two_services.md)（變三個了，memory 待更新）。

---

## 前置檢查（桌機端，本機驗證）

執行前確認以下都已完成：

- [ ] Slice C1 PR #97 已 merge 到 main（`git log --oneline | grep 05d35a4`）
- [ ] 本機 `ruff check` + `ruff format --check` 綠
- [ ] 本機 `pytest` 綠（baseline 1035 passed）
- [ ] WP `bot_usopp` app password 已由修修產出並記錄在本機 `.env`（`WP_SHOSHO_APP_PASSWORD=...`）
- [ ] 本機驗證 `python -m agents.usopp` 可正常啟動一次 cycle（至少看到 `usopp daemon start worker_id=...` 就 Ctrl+C 停）

---

## 步驟

### 1. Push 新 code 到 VPS

```bash
ssh nakama-vps
cd /home/nakama
git pull origin main
# 確認 05d35a4 (或之後的 commit) 已進 main
git log --oneline -3
```

### 2. Diff `.env` key names，找出 VPS 缺的 USOPP_* 和 LITESPEED_PURGE_METHOD

**這一步強制做，別 scp 整份 `.env` — 會覆蓋掉 VPS-only keys**（[feedback_env_push_diff_before_overwrite.md](../../memory/claude/feedback_env_push_diff_before_overwrite.md)）。

桌機端：

```bash
# Extract key names (not values) from local + VPS
grep -oE '^[A-Z][A-Z0-9_]+=' .env | sort -u > /tmp/local_keys.txt
ssh nakama-vps "grep -oE '^[A-Z][A-Z0-9_]+=' /home/nakama/.env | sort -u" > /tmp/vps_keys.txt

# What VPS is MISSING that Slice C1 needs
comm -23 /tmp/local_keys.txt /tmp/vps_keys.txt | grep -E '^(USOPP_|LITESPEED_|WP_SHOSHO_|WP_FLEET_)'

# What would be LOST if you naively scp'd (run this to confirm you understand the risk)
comm -23 /tmp/vps_keys.txt /tmp/local_keys.txt
```

預期輸出（VPS 缺的 keys）應該包含以下**全部或部分**：

```
LITESPEED_PURGE_METHOD=
USOPP_BATCH_SIZE=
USOPP_POLL_INTERVAL_S=
USOPP_TARGET_SITE=
USOPP_WORKER_ID=
WP_SHOSHO_APP_PASSWORD=
WP_SHOSHO_BASE_URL=
WP_SHOSHO_USERNAME=
```

如果 VPS 已有 `WP_SHOSHO_*`（Slice A/B 時期加過），只需 append 其餘。

### 3. 備份 VPS `.env` + append 新 keys

```bash
# 在 VPS 上備份（強制 dated suffix）
ssh nakama-vps 'cp /home/nakama/.env /home/nakama/.env.bak.$(date +%Y%m%d_%H%M%S)'

# 在桌機，只 append Slice C1 新增的 keys（依上一步 diff 結果調整列名）
grep -E '^(USOPP_TARGET_SITE|USOPP_WORKER_ID|USOPP_POLL_INTERVAL_S|USOPP_BATCH_SIZE|LITESPEED_PURGE_METHOD)=' .env \
    | ssh nakama-vps 'cat >> /home/nakama/.env'

# 如果 WP_SHOSHO_* 也缺，單獨 append（含 app password，確認本機 .env 值正確後執行）
grep -E '^WP_SHOSHO_' .env | ssh nakama-vps 'cat >> /home/nakama/.env'
```

**注意：** `USOPP_WORKER_ID=` 空值是刻意的 — daemon 會 fallback 成 `usopp-<hostname>`，VPS 上自動變 `usopp-<vps-hostname>`，跟 Mac/桌機本機測試的 worker_id 不撞。要覆寫才手動填，否則留空即可。

確認 VPS `.env` 已含新 keys：

```bash
ssh nakama-vps 'grep -E "^(USOPP_|LITESPEED_|WP_SHOSHO_)" /home/nakama/.env'
```

### 4. 安裝 systemd unit

```bash
ssh nakama-vps
cp /home/nakama/nakama-usopp.service /etc/systemd/system/nakama-usopp.service
systemctl daemon-reload
systemctl enable nakama-usopp
```

**還不要 `systemctl start`** — 下一步先做 dry-run。

### 5. Dry-run：手動跑一輪 + Ctrl+C

```bash
ssh nakama-vps
cd /home/nakama
python3 -m agents.usopp
# 預期 log 第一行：
#   usopp daemon start worker_id=usopp-<hostname> poll_interval_s=30 batch=5 site=wp_shosho
# 等 30 秒看至少一次 cycle 不炸（approval_queue 空的話 cycle 什麼都不做，正常）
# Ctrl+C 停（驗證 SIGINT 會觸發 graceful shutdown，log 應出現 "usopp daemon shutdown requested"）
```

如果啟動瞬間就 crash（KeyError、ImportError、ConnectionError），回去檢查 `.env` 是否漏 keys。**dry-run 過了才繼續下一步。**

### 6. 啟動 systemd service + 觀察

```bash
ssh nakama-vps
systemctl start nakama-usopp
systemctl status nakama-usopp    # Active: active (running) 才算 ok
journalctl -u nakama-usopp -f    # 追 log，至少看 2 個 cycle（60 秒）
```

預期：

- `usopp daemon start worker_id=usopp-<hostname> poll_interval_s=30 batch=5 site=wp_shosho`
- `approval_queue` 空時，cycle log 只有 debug 級（`journalctl` 預設不顯示），不會一直刷
- 若有 claim 到 row，會看 `usopp daemon cycle processed=N worker_id=...`

### 7. 驗證 graceful shutdown

```bash
systemctl restart nakama-usopp
journalctl -u nakama-usopp -n 20
# 應看到：
#   usopp daemon shutdown requested (worker_id=...)
#   usopp daemon stopped worker_id=...
#   usopp daemon start worker_id=...
```

如果 `TimeoutStopSec=60` 內沒 graceful stop，systemd 會強殺 → journal 會顯示 `Killing process ... with signal SIGKILL`。出現這行代表 daemon 有 row 正在 publish 而且超時，需要查 `publish_jobs` 表看是不是 WP REST 卡住。

---

## 回滾步驟

若 daemon 在 production 跑炸了（WP 連爆、stuck claim、LiteSpeed purge hang 等）：

```bash
ssh nakama-vps
systemctl stop nakama-usopp
systemctl disable nakama-usopp

# 把 claim 到 'claimed' 但沒完成的 row 釋放回 'approved'（Franky reset_stale_claims 也會做，
# 但等 5 分鐘太久，手動跑一次）：
cd /home/nakama
python3 -c "from shared import approval_queue; print(approval_queue.reset_stale_claims(stale_minutes=0))"

# 確認沒有 'claimed' 孤兒：
sqlite3 /home/nakama/data/state.db "SELECT id, status, worker_id FROM approval_queue WHERE status='claimed';"
# 應回空集
```

回滾完成。重啟前先找出 root cause，別用「service 重啟」當 shortcut（[三條紅線](../../CLAUDE.md#三條紅線任何任務共同遵守) — 事實驅動）。

---

## 相關

- [ADR-005b](../decisions/ADR-005b-usopp-wp-publishing.md) — 正典設計
- [`nakama-usopp.service`](../../nakama-usopp.service) — systemd unit file
- [project_usopp_slice_c1_merged.md](../../memory/claude/project_usopp_slice_c1_merged.md) — Slice C1 產出清單
- [feedback_env_push_diff_before_overwrite.md](../../memory/claude/feedback_env_push_diff_before_overwrite.md) — 為什麼要 diff 不要 scp
- [feedback_vps_two_services.md](../../memory/claude/feedback_vps_two_services.md) — 多 service 部署原則（這次會變三個）
- [reference_vps_paths.md](../../memory/claude/reference_vps_paths.md) — VPS 路徑參考
