# Disaster Recovery — Nakama VPS 重建 / state.db 還原

**Scope:** 從 VPS 整台壞掉、state.db 損毀、或 secret compromise 復原 Nakama 服務的 step-by-step playbook。
**Owner:** 修修手動 ssh 執行；本檔的指令 copy-paste 友善。
**測試節奏:** 每半年做一次 drill（drill 步驟見 §6），記錄 wall-clock time 回饋本檔的 RTO 估值。

---

## 1. RPO / RTO 目標

| 故障情境 | RPO（資料遺失上限） | RTO（恢復時間目標） |
|---------|------:|------:|
| state.db 損毀（VPS 健在） | 24h | 30 分鐘 |
| VPS 整台失聯 / 重建 | 24h | 4 小時 |
| Secret compromise（key leak） | 0 | 2 小時 |
| Cloudflare Tunnel 中斷 | 0 | 1 小時（看 CF 回應） |

**24h RPO 來源**：state.db 每日 04:00 Asia/Taipei snapshot 到 R2（[`docs/capabilities/nakama-backup.md`](../capabilities/nakama-backup.md)）。
若需要 < 24h RPO，要走 ADR-006 §SPOF 的 litestream 升級路徑（Phase 2，目前未排）。

**24h RPO 對 approval_queue 影響最大**：approved 但 Usopp 還沒 publish 的 row 可能丟失。緩解：Usopp daemon 30 秒 poll，多數 approved 在分鐘內就 publish 完，實際暴露時間遠小於 24h。

---

## 2. Service / Secret / 資料 inventory

### 2.1 VPS 上的 service

| Service | Unit file | Repo path | 用途 |
|---------|-----------|-----------|------|
| `thousand-sunny` | `/etc/systemd/system/thousand-sunny.service` | `thousand-sunny.service` | FastAPI web (`:8000`) — Bridge / Brook / Robin / Zoro routers |
| `nakama-gateway` | 同上 | （由 systemd 包 `python -m gateway`） | Slack Socket Mode bot gateway |
| `nakama-usopp` | `/etc/systemd/system/nakama-usopp.service` | `nakama-usopp.service` | WP publisher daemon |
| `cloudflared` | 由 cloudflared package 安裝 | — | CF Tunnel `nakama.shosho.tw` → `127.0.0.1:8000` |

### 2.2 Cron jobs（root crontab）

| Schedule（Asia/Taipei） | Command | 用途 |
|------------------------:|---------|------|
| `0 4 * * *` | `python3 scripts/backup_nakama_state.py` | state.db daily snapshot → R2 `nakama-backup` |
| `30 5 * * *` | Robin pubmed digest | daily PubMed RSS digest |
| `0 1 * * 1` | Franky weekly report | 工程週報 |
| `*/5 * * * *` | Franky health probe | gateway + cron freshness |
| `0 5 * * *` | Zoro brainstorm scout | daily brainstorm topic prompt |

確認當前 crontab：`ssh nakama-vps 'crontab -l'`

### 2.3 Backed up

| 資料 | 位置 | 備份 | 還原來源 |
|-----|------|------|----------|
| `data/state.db` | VPS | ✅ R2 `nakama-backup/state/YYYY/MM/DD/state.db.gz` | R2 |
| `data/nakama.db` | VPS | ✅（即使 0 bytes） | R2 |
| `data/scimago_journals.csv` | VPS | ❌ | `python -m scripts.update_scimago` 重抓 |
| `data/google_oauth_credentials.json` | VPS | ❌ | GCP console download |
| `data/google_gmail_token.json` | VPS | ❌ | `python scripts/google_gmail_auth.py` 重跑 |
| `data/google_calendar_token.json` | VPS | ❌ | `python scripts/google_calendar_auth.py` 重跑 |
| `/home/nakama/.env` | VPS | ❌（手動本機保管） | 修修 1Password / 本機備份 |
| Repo code | VPS clone of GH | ✅ GH | `git clone` |

**不在備份內的東西要靠 GH repo + 修修本機 .env 手動保管 + secret-rotation runbook 的可重新發行 token**。

### 2.4 Secret 清單（rotation 流程見 [`secret-rotation.md`](secret-rotation.md)）

VPS `.env` 內的 secret 分四類：
1. **LLM API keys**（Anthropic / xAI / Gemini / Firecrawl / PubMed / YouTube / Twitter / Unpaywall）
2. **Slack apps × 3**（NAMI / SANJI / ZORO bot+app token）
3. **Google OAuth**（Gmail token + Calendar token + sa JSON）
4. **WordPress app passwords**（shosho + fleet）
5. **R2 credentials**（read + write）
6. **Web auth**（WEB_PASSWORD + WEB_SECRET）
7. **SMTP**（notification）

---

## 3. 情境 A：state.db 損毀（VPS 健在）

**徵兆**：thousand-sunny 啟動失敗、`/bridge` 503、journal 出現 `sqlite3.DatabaseError: database disk image is malformed`。

### 步驟（預計 30 分鐘）

```bash
# 1. 停所有 read/write state.db 的 service
ssh nakama-vps 'systemctl stop thousand-sunny nakama-gateway nakama-usopp'

# 2. 備份壞掉的檔（forensic 用，不要 rm）
ssh nakama-vps 'mv /home/nakama/data/state.db /home/nakama/data/state.db.corrupt.$(date +%Y%m%d_%H%M%S)'

# 3. 從本機跑 restore script（dry-run 先）
cd /Users/shosho/Documents/nakama  # 或桌機 repo
python scripts/restore_from_r2.py list --db state | head -10
python scripts/restore_from_r2.py restore --db state    # dry-run 預設，下載到 /tmp 驗證 schema

# 4. 確認 dry-run 報告 OK，再 ssh 進 VPS 執行真正 restore
ssh nakama-vps
cd /home/nakama
python3 scripts/restore_from_r2.py restore --db state --apply
# script 會自動 backup pre-existing 為 state.db.pre-restore.{timestamp}（即使你已經 mv 走，再加一層保險）

# 5. chown 確保 service 能讀
chown root:root /home/nakama/data/state.db

# 6. 啟動 + smoke
systemctl start thousand-sunny nakama-gateway nakama-usopp
journalctl -u thousand-sunny -n 30 --no-pager
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/healthz   # 預期 200
```

### Smoke checks（restore 後逐項過）

| 檢查 | 指令 | 通過條件 |
|------|------|---------|
| Web 起來 | `curl http://127.0.0.1:8000/healthz` | 200 |
| Bridge memory 頁能列 | 開瀏覽器 `nakama.shosho.tw/bridge/memory` | 看到 agent rail + 記憶條目 |
| approval_queue 表完整 | `sqlite3 data/state.db 'SELECT COUNT(*), status FROM approval_queue GROUP BY status'` | 看到 status 統計 |
| Slack bot 響應 | Slack DM Nami `hello` | 回話 |
| Usopp daemon | `systemctl status nakama-usopp` | active running |

---

## 4. 情境 B：VPS 整台失聯 / 重建

**徵兆**：ssh 失敗 / Vultr console 看 instance 死、 / `nakama.shosho.tw` 502 持續。

### 步驟（預計 4 小時）

#### B-1. 確認 VPS 真的死（5 min）

```bash
ssh nakama-vps        # connection refused / timeout
# 在 Vultr console 確認 server state；嘗試 hard reboot；reboot 後再 ssh 一次
# 確認 root cause：硬碟壞 / 網卡壞 / 平台問題；如果只是 OS hang，reboot 可能解決
```

如果 reboot 救得回來 → 走情境 A（state.db 可能在過程中損毀）。
如果救不回來 → 繼續下面。

#### B-2. 開新 Vultr instance（30 min）

- Region：與舊機相同（Asia / Tokyo or Singapore）
- OS：Ubuntu 24.04 LTS（與舊機一致）
- Spec：2 vCPU / 4GB RAM（[`reference_infra_xcloud_vultr.md`](../../memory/claude/reference_infra_xcloud_vultr.md)）
- 開好後拿到新 IP

#### B-3. 基礎安裝（30 min）

```bash
ssh root@<NEW_IP>

# 系統套件
apt update && apt upgrade -y
apt install -y python3.12 python3.12-venv python3-pip git sqlite3 cron

# 建 nakama user 結構（沿用舊機慣例：repo + data 都在 /home/nakama/）
mkdir -p /home/nakama/data
mkdir -p /var/log/nakama

# Clone repo
cd /home/nakama
git clone https://github.com/shosho-chang/nakama.git .
pip install --break-system-packages -e .   # 或 venv 看當下慣例
```

#### B-4. 還原 secret + state.db（45 min）

```bash
# 1. 從 1Password / 修修本機把 .env 上來（用 scp 從修修桌機）
# 修修桌機端：
scp .env root@<NEW_IP>:/home/nakama/.env
chmod 600 /home/nakama/.env

# 2. Google OAuth tokens（無法從 R2 還原，要重 push 修修本機 cache 或重跑 OAuth flow）
scp data/google_oauth_credentials.json root@<NEW_IP>:/home/nakama/data/
scp data/google_gmail_token.json root@<NEW_IP>:/home/nakama/data/
scp data/google_calendar_token.json root@<NEW_IP>:/home/nakama/data/
scp data/scimago_journals.csv root@<NEW_IP>:/home/nakama/data/   # 或新機跑 update_scimago.py

# 3. 從 R2 還原 state.db
ssh root@<NEW_IP>
cd /home/nakama
python3 scripts/restore_from_r2.py restore --db state --apply
python3 scripts/restore_from_r2.py restore --db nakama --apply   # 即使 0 bytes 也還原，保 layout
```

#### B-5. systemd + cron 安裝（30 min）

```bash
# systemd units
cp /home/nakama/thousand-sunny.service /etc/systemd/system/
cp /home/nakama/nakama-usopp.service /etc/systemd/system/

# nakama-gateway 沒有獨立 unit file（目前由 thousand-sunny 內含 or 自寫一份）— 確認當下狀態
ls /etc/systemd/system/nakama-*.service

systemctl daemon-reload
systemctl enable thousand-sunny nakama-usopp

# Crontab（從本檔 §2.2 抄回去）
crontab -e
# 貼入 §2.2 的所有 entries

# Cloudflared
# 走 https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/
# 用同一個 tunnel ID 但新 Connector，因為舊 connector ID 在舊 VPS 上
# 修修可能要在 Cloudflare Zero Trust dashboard 重新生 token
```

#### B-6. 啟動 + 完整 smoke（30 min）

```bash
systemctl start thousand-sunny nakama-usopp
systemctl status thousand-sunny nakama-usopp

# 各項 smoke（同 §3 的清單）+ 額外 DNS：
curl -s https://nakama.shosho.tw/healthz       # 200 才代表 CF Tunnel 通了
```

#### B-7. 收尾

- 把舊 VPS 在 Vultr console 砍掉（避免 monthly charge）
- 把新 IP 寫進 `~/.ssh/config` 的 `nakama-vps` alias
- 在 vault `Incidents/` 開 incident 文件記 timeline / RTO 實測 / 教訓
- Memory：`reference_vps_paths.md` 如果有 IP 紀錄要更新

---

## 5. 情境 C：Secret compromise

**徵兆**：API quota 暴增、VPS journal 出現非預期 origin 的 token 使用、GitHub secret scan alert。

### 立即動作（10 分鐘內）

```bash
# 1. 暫停所有對外發送（防擴散）
ssh nakama-vps 'systemctl stop nakama-usopp nakama-gateway'
# thousand-sunny 可以繼續跑（只是 web，不發外）

# 2. 在外部 console 立即 revoke 該 key（順序按敏感度）：
#    - Anthropic console: revoke ANTHROPIC_API_KEY
#    - Slack admin: rotate bot token (NAMI/SANJI/ZORO)
#    - WordPress: 改 bot user 密碼 + 重發 app password
#    - Cloudflare R2: revoke API token
#    - Google: revoke OAuth credentials in cloud console
#    - GH: revoke any leaked PAT
```

### 完整 rotation：見 [`secret-rotation.md`](secret-rotation.md) per-secret 流程

恢復後 smoke 同 §3。

---

## 6. Drill 演練 protocol（每半年一次）

**目標**：驗證本 runbook 還能用 + 實測 RTO。

**做法**（不影響 production）：

### 6.1 半量級 drill（30 min，建議每半年）

只演練「state.db 從 R2 還原到一個臨時 path 並驗證 schema」：

```bash
# 1. 在桌機（or Mac）開乾淨工作目錄
mkdir -p /tmp/dr-drill-$(date +%Y%m%d)
cd /tmp/dr-drill-$(date +%Y%m%d)

# 2. 從 R2 拉最新 state snapshot
cd /Users/shosho/Documents/nakama
python scripts/restore_from_r2.py list --db state --limit 5
python scripts/restore_from_r2.py restore --db state --target /tmp/dr-drill-$(date +%Y%m%d)/state.db

# 3. 驗證可讀 + schema 對：
sqlite3 /tmp/dr-drill-*/state.db '.schema agent_memory'
sqlite3 /tmp/dr-drill-*/state.db 'SELECT COUNT(*) FROM agent_memory'
sqlite3 /tmp/dr-drill-*/state.db 'SELECT COUNT(*), status FROM approval_queue GROUP BY status'

# 4. 在 vault `Incidents/drill-YYYY-MM-DD-state-restore.md` 記下：
#    - wall-clock time
#    - 哪一步卡住
#    - 哪些 doc 過期
```

### 6.2 全量級 drill（4h，建議每年一次）

按情境 B 完整跑一次到備援 VPS（可開 Vultr $5/月最低 spec 一台跑完當天砍掉）：

- 走完 §4 全 7 步
- 不切 DNS（drill VPS 用 `<IP>:8000` 直連 smoke）
- 演練完砍 VPS
- vault `Incidents/drill-YYYY-MM-DD-full-rebuild.md` 記 timeline + RTO 實測

### Drill checklist 模板

```markdown
# DR Drill — YYYY-MM-DD

**Type:** half / full
**Operator:** 修修
**Started:** HH:MM
**Ended:** HH:MM
**Wall-clock RTO:** XX min

## Steps that worked

- [ ] step 1
- [ ] step 2

## Steps that broke / 文件過期

| 步驟 | 觀察到的問題 | 建議改 runbook 哪段 |
|------|--------------|---------------------|
| ... | ... | ... |

## Action items

- [ ] update runbook §X
- [ ] add cron / monitoring for Y

## Measured RTO vs target

- Target: XX min
- Actual: YY min
- Verdict: pass / fail
```

---

## 7. 已知缺口（improvement backlog）

| 缺口 | Plan phase | 嚴重度 |
|------|-----------|-------|
| RPO 24h 對 approval_queue 偏大 | ADR-006 Phase 2 litestream | mid |
| Google OAuth tokens 沒備份 | 修修個人 1Password backup | low（可重 OAuth） |
| `.env` 沒備份（手動保管） | quality-uplift Phase 9 / 個人 1Password | mid |
| `cloudflared` connector ID 沒文件化 | 補 setup runbook（CF 端） | low |
| 新 VPS 重建沒自動化（IaC） | 沒排，commodity 1 機 stack 不必 | low |

---

## 相關

- [`docs/capabilities/nakama-backup.md`](../capabilities/nakama-backup.md) — backup 機制詳述
- [`scripts/restore_from_r2.py`](../../scripts/restore_from_r2.py) — restore 工具（list / restore / dry-run / apply）
- [`secret-rotation.md`](secret-rotation.md) — per-secret rotation 流程
- [`docs/plans/quality-bar-uplift-2026-04-25.md`](../plans/quality-bar-uplift-2026-04-25.md) — DR 屬 Phase 1
- [`reference_vps_paths.md`](../../memory/claude/reference_vps_paths.md) — VPS 路徑
- [`reference_vps_ssh.md`](../../memory/claude/reference_vps_ssh.md) — VPS ssh alias
- [`reference_infra_xcloud_vultr.md`](../../memory/claude/reference_infra_xcloud_vultr.md) — VPS 規格 / xCloud setup
