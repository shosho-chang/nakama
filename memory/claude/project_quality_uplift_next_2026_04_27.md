---
name: Quality Uplift 下一輪起點（2026-04-27 接手）
description: 04-26 晚 +2 PR (5B-2 cron heartbeat / 5D external service probes)；下一步 5C 或 5B-3 或 6
type: project
created: 2026-04-26
originSessionId: 6966e360-f96e-4739-b4e8-44d3c16567d4
---
2026-04-26 晚 session：在 evening pickup（5B-2 cron instrument）基礎上又 close 5D。**取代過時的 `project_quality_uplift_next_2026_04_26_evening.md`**。

**Why:** 修修 auto mode + 授權 VPS deploy 後續做 5D。本 memo 是下一個對話的接手點。

**How to apply:** 開新對話讀完 `MEMORY.md` → 讀本 memo → 下一步預設 **Phase 5C** 或 **5B-3**（除非修修指定別的）。先確認 5D VPS deploy 做了沒（這 session 沒做）。

## 本 session 已完成（2 PR merged）

| PR | Merge | 內容 |
|---|---|---|
| #175 | `1b3df31` | **Phase 5B-2** instrument 5 cron with heartbeat（robin-pubmed / zoro-scout / franky-r2-verify / franky-news / franky-weekly）+ CRON_SCHEDULES 3 → 8 + drive-by `r2_backup_verify.py` env empty-string fix |
| #177 | `7309e9c` | **Phase 5D** external service auth probes（gsc / slack / gmail）+ /bridge/franky probe-row3 + ProbeTarget Literal 6 → 9 |

**Tests**：1854 → 1867（+13 from 5D；5B-2 內部已 +22 涵蓋）。

## VPS 部署狀態

- ✅ **5B-2 已部署**（修修授權 + 我 ssh nakama-vps git pull + restart 完）。確認 `CRON_SCHEDULES` 在 prod 是 8 entries、probe_cron_freshness 跑出來 5 個新 entry log INFO「registered but no heartbeat row yet」（正確：cron 還沒第一次 tick）。
- ❌ **5D 未部署** — 修修原本「授權 deploy + 接著下一步」的 deploy 是針對 5B-2，5D 是後續新工作。下次接手要先 deploy。

## 路上抓到 + 已自動修復 / 留 follow-up

1. **`r2_backup_verify.py` 兩條 module-level int/float coerce 用 `os.getenv(K, default)` 撞 .env empty-string** — 已併進 PR #175 修掉（drive-by）
2. **`probe_nakama_gateway` 同類 bug** — `NAKAMA_HEALTHZ_URL=` 空字串導致 `httpx.get("")` 炸 `UnsupportedProtocol`。**未修**，需小 PR：line `health_check.py:384` 改 `os.getenv("NAKAMA_HEALTHZ_URL") or DEFAULT_NAKAMA_HEALTHZ_URL`。即時補丁可改 VPS .env 補真值；durable fix 走 PR。同類詳見 `feedback_dotenv_empty_string_fallback.md`
3. **既知還沒修的 same-class bug**（grep 查到，下次 sweep 一次掃）：
   - `agents/usopp/__main__.py` USOPP_POLL_INTERVAL_S / USOPP_BATCH_SIZE
   - `shared/notifier.py` SMTP_PORT
   - `shared/multimodal_arbiter.py` GEMINI_MAX_WORKERS

## 待做

### Phase 5 剩 2 chunk

| 工作 | 規模 | Dep | 並行？ |
|---|:---:|---|---|
| **Phase 5B-3** anomaly daemon（cost / error rate / latency 3σ）| 2-3 天 | 5A latency 已有 | ❌ |
| **Phase 5C** FTS5 結構化 log search UI | 2 天 | none | ✅ |

### Phase 6-8

| 工作 | 規模 | Dep |
|---|:---:|---|
| **Phase 6** test coverage 補齊（thousand_sunny SSE / robin router → 80%）| 7 天 | none |
| **Phase 7** staging + feature flags | 8 天 | 修修決定 staging 機 |
| **Phase 8** CI/CD auto deploy + rollback | 4 天 | Phase 7 |

### 修修 manual

1. **VPS pull + restart for PR #177**：
   ```bash
   ssh nakama-vps 'cd /home/nakama && git pull && sudo systemctl restart thousand-sunny nakama-gateway'
   ```
2. 瀏覽器驗證：
   - `/bridge/franky` row 3 出現 3 個 card（Google Search Console / Slack · Franky bot / Gmail · Nami inbox）
   - 5 min 後（一個 franky-health tick）3 個 probe 都填 status
3. 看要不要修 `NAKAMA_HEALTHZ_URL` empty-string bug（小 PR 或補 .env）— 同時把 4 個 same-class bug 一起 sweep
4. 24h 後（明天）：`/bridge/franky` cron_freshness card 應該 `checked=8 stale_count=0`

## 推薦下一步序

5C 與 5B-3 各 2-3 天，並行 OK。**5C 較簡單（CRUD + FTS5 query），先做暖手**；**5B-3 有設計挑戰**（3σ statistical baseline 要從歷史 LLM 成本/error rate 算 + 抓 anomaly 的 dedup 策略）。

順序建議：
- **5C**（FTS5 log search） → **5B-3**（anomaly daemon） → 順手 sweep `dotenv empty-string` 4 個 known bug → **Phase 6 test coverage**

或先 sweep dotenv bugs（半天）解空集合 follow-up，再 5C / 5B-3。

## 不要自己決定的事

- Phase 7 staging — 規模大、要錢、要新 VPS，必先問
- 5C 還是 5B-3 哪個先 — 兩者 effort 相近、user value 不同（log search ergonomics vs anomaly auto-detect），看修修當下需求
- 順手 sweep 4 個 dotenv 同類 bug 是否要切獨立小 PR — 改動很小（每個 1 行 + comment），但跨 modules（usopp/notifier/multimodal_arbiter/franky），review 上算合理一個 PR

## 開始之前一定要先看

- 本 memo
- [project_quality_uplift_next_2026_04_26_evening.md](project_quality_uplift_next_2026_04_26_evening.md) — 上一輪 evening pickup（已過時，被本 memo 取代）
- [feedback_dotenv_empty_string_fallback.md](feedback_dotenv_empty_string_fallback.md) — 04-26 連兩 bug 才寫的 dotenv 空值 trap
- [feedback_probe_registry_verify_producer.md](feedback_probe_registry_verify_producer.md) — 5B-1 reviewer 抓到的 BLOCKER 教訓
- 原 9-phase plan：`docs/plans/quality-bar-uplift-2026-04-25.md`
