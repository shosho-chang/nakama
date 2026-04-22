---
name: Multi-Model ADR Review — Meta Summary
description: 三份 ADR × 三家模型 = 9 份 review 的跨檔綜合分析與下一步建議
consolidation_date: 2026-04-22
scope: [ADR-005, ADR-006, ADR-007]
reviewer_models: [claude-sonnet-4-6, gemini-2.5-pro, grok-4]
---

# Multi-Model ADR Review — Meta Summary

讀這一份檔就夠了。後面要鑽細節再看 `ADR-00X--CONSOLIDATED.md`。

---

## 1. 三份 ADR 評分表

| ADR | Claude Sonnet 4.6 | Gemini 2.5 Pro | Grok 4 | 平均 |
|---|---|---|---|---|
| ADR-005 Publishing | 4/10 退回修改 | 3/10 退回重寫 | 6/10 修改後通過 | 4.3/10 |
| ADR-006 HITL Queue | 5/10 修改後通過 | 3/10 退回重寫 | 5/10 修改後通過 | 4.3/10 |
| ADR-007 Franky | 4/10 退回修改 | 3/10 退回重寫 | 5/10 修改後通過 | 4.0/10 |

**沒有任何一份拿到「直接通過」**。三家都認為骨架對但細節薄。

## 2. 模型個性觀察（對未來 multi-model panel 有用）

| 模型 | 傾向 | 適合當 |
|---|---|---|
| Gemini 2.5 Pro | 系統性最嚴厲（3/3 都 3/10 退回重寫），學院派、挑 schema/契約 | **吹哨者** — 專挑你懶得想的 edge case |
| Claude Sonnet 4.6 | 中間、細節最全（篇幅最長），平衡風險與實用 | **仲裁者** — 給結構化建議 |
| Grok 4 | 最樂觀（5-6/10），短而精煉，抓大局但漏 blocker | **啦啦隊 / 結論提煉** — 適合最後 phase 當 sanity check，不適合當獨立 review |

**使用建議**：未來 `shared/multi_model_panel.py` 做關鍵決策時，**三家都要跑**，Gemini 找問題、Claude 整理建議、Grok 給 go/no-go 提示。只跑一家必漏。

## 3. 跨三份 ADR 的共識 Blocker（Meta Pattern）

三家 × 三份 ADR 反覆出現的同類問題，必須系統性處理：

### 3.1 Schema / Contract 沒定死
| ADR | 具體問題 |
|---|---|
| ADR-005 | SEOPress API 契約脆弱，沒 fallback；Gutenberg block HTML 沒 validator |
| ADR-006 | payload JSON 沒 Pydantic schema，status FSM 未明確 |
| ADR-007 | `alert_state` schema 未定、state.db migration 路徑不明 |

**Meta 意義**：三份 ADR 共同缺「契約 first」思維。所有外部依賴與內部狀態都要有型別 + 版本 + 遷移計畫。

### 3.2 可觀測性 / 可測試性缺失
| ADR | 具體問題 |
|---|---|
| ADR-005 | 無 staging 測試策略，LLM 成本零估算 |
| ADR-006 | 無 race condition 測試，無審核延遲 SLO |
| ADR-007 | 無外部 uptime probe（VPS 掛 = Franky 掛 = 警報一起掛） |

**Meta 意義**：三份 ADR 都缺「如果壞了怎麼知道」。Phase 1 之前應**建立 observability baseline**（log aggregation + metric endpoint + test harness），而不是邊寫邊補。

### 3.3 Scope 太寬，該拆分
| ADR | 拆分建議 |
|---|---|
| ADR-005 | Gutenberg 生成 / Bricks template / SEOPress 可能要拆 |
| ADR-006 | HITL 核心 vs Obsidian 雙向同步該拆（ADR-006b） |
| ADR-007 | GSC / GA4 / Cloudflare 三個子系統應拆（ADR-008） |

**Meta 意義**：每份 ADR 都被指「想做太多」。這反映 Phase 1 計畫過度壓縮，應**把三份 ADR 拆成 5-6 份小 ADR**，逐個開工比整批推進風險低。

### 3.4 Single Point of Failure / 併發問題
| ADR | 具體問題 |
|---|---|
| ADR-005 | LiteSpeed cache 失效未設計，publish 失敗無 retry/idempotency |
| ADR-006 | `peek_approved` race condition（多 worker 重複發布） |
| ADR-007 | Franky 自監控盲點（自己就是 SPOF），無 alert deduplication |

**Meta 意義**：Nakama 需要一份通用的 **concurrency + idempotency + SPOF 原則文件**（可能是一份新 ADR 或 `docs/principles/reliability.md`），讓後續所有 ADR 都遵守，不用每份重新爭論。

## 4. 最 Blocking 的 Top 5 項目（跨三份 ADR 合併）

這是開 `feature/phase-1-infra` branch 前要解決的硬門檻：

| # | 問題 | 來自 | 負責 ADR 修訂 |
|---|---|---|---|
| 1 | 建立 `shared/gutenberg_builder.py` + validator（避免 LLM 產破碎 HTML） | ADR-005 3/3 | ADR-005 |
| 2 | approval_queue 改 atomic `claim_approved_drafts`（防 race） | ADR-006 3/3 | ADR-006 |
| 3 | 加 Franky 外部 uptime probe（UptimeRobot / Better Uptime） | ADR-007 3/3 | ADR-007 |
| 4 | 定義所有 payload 的 Pydantic schema + 版本欄位 | 跨三份 | 新建 `shared/schemas/` 目錄 |
| 5 | VPS baseline 壓測（cron 跑起來後 CPU/RAM 實測，不用估算） | ADR-007 3/3 | ADR-007 |

## 5. 建議的 ADR 修訂路徑

**立即拆分**（降低 Phase 1 風險）：

```
ADR-005 Publishing
   ├── ADR-005a: Brook → Gutenberg HTML pipeline（含 validator）
   ├── ADR-005b: Usopp → WP REST + SEOPress
   └── ADR-005c: Bricks template 維護（僅 docs，無 code）

ADR-006 HITL Approval
   ├── ADR-006 (瘦身版): Bridge /drafts 核心 queue（Phase 1）
   └── ADR-006b: Obsidian 雙向同步（獨立調研，Phase 2）

ADR-007 Franky
   ├── ADR-007 (瘦身版): VPS 健康 + R2 備份驗證 + Slack bot（Phase 1）
   └── ADR-008: SEO 觀測中心（GSC + GA4 + Cloudflare，Phase 2）
```

**新增 cross-cutting 原則**：

- `docs/principles/schemas.md` — Pydantic schema 慣例、版本欄位、migration
- `docs/principles/reliability.md` — idempotency、atomic claim、SPOF 辨識原則
- `docs/principles/observability.md` — logging、metrics、外部 probe、SLO

## 6. 開工 Gate 最新版（取代原 checkpoint）

**Phase 1 開工前必做**：

- [ ] ADR-005 修訂（補 Gutenberg validator、SEOPress 降級策略、LLM 成本估算）→ 或拆成 005a/b/c
- [ ] ADR-006 修訂（atomic claim、payload schema、拆 Obsidian 出去）
- [ ] ADR-007 修訂（外部 probe、alert dedup、拆 ADR-008）
- [ ] 新增 `docs/principles/` 三份原則文件
- [ ] VPS baseline 壓測（跑個 24 小時 cron 模擬看 CPU/RAM）

**可以 Phase 1 中期再補**（不 block 開工）：

- [ ] Franky alert dedup 實作
- [ ] observability 完整 dashboard
- [ ] staging 環境（可以先用「publish 到 draft 不發佈」當暫代）

## 7. 修修的下一步（兩條路選一）

### 路徑 A（推薦）：先修 ADR 再開工
**時間成本**：半天到一天  
**做法**：
1. 我按 §5 把三份 ADR 拆成 5-6 份小 ADR（1-2 小時）
2. 你快速 review 拆分結果 + 原則文件（30-60 分鐘）
3. 再跑一輪 multi-model review（~$10 + 5 分鐘），確認 Tier A blocker 都解了
4. 開 `feature/phase-1-infra` branch

**優點**：起點乾淨，後續不用邊寫邊回頭改 ADR。  
**缺點**：多花半天。

### 路徑 B：帶 blocker 開工，邊寫邊補
**時間成本**：立即開工  
**做法**：開 branch 開始寫 code，遇到 blocker 才回頭修 ADR。

**優點**：有動能。  
**缺點**：三家 review 都指出「契約先於實作」— 先寫 code 再補 schema 等於在沒地基上蓋房子；Phase 1 中後期可能大重構。

**我的仲裁**：**走路徑 A**。三家共識夠強（9/9 reviews 都指出細節薄），這不是「哪家模型多疑」的問題，是 ADR 真的缺契約。花半天修 ADR 比花一週重構 code 便宜。

## 8. 檔案清單

```
docs/decisions/multi-model-review/
├── _META-SUMMARY.md                              # 本檔
├── ADR-005--CONSOLIDATED.md                      # ADR-005 三家合併
├── ADR-006--CONSOLIDATED.md                      # ADR-006 三家合併
├── ADR-007--CONSOLIDATED.md                      # ADR-007 三家合併
└── （9 份原始 review，命名格式 ADR-XXX--{model}.md）
```

## 9. 本次多模型審查的成本 / 價值評估

- **成本**：9 次 API call，估 $3-5（Claude Sonnet + Grok 4 + Gemini 2.5 Pro），5 分鐘並行
- **產出**：9 份原始 review + 3 份 consolidated + 1 份 meta = 13 份分析文件
- **發現**：5 個 Tier A blocker、3 個系統性 meta pattern、建議 5-6 份 ADR 拆分
- **對比單模型**：如果只跑 Grok（最樂觀），會得到「三份 ADR 都 5-6/10 修改後通過」的結論，直接開工 → Phase 1 中後期踩上述 5 個 blocker；如果只跑 Gemini，會得到「三份都退回重寫」→ 可能過度保守延遲開工。**三家 triangulate 才抓到「要修但不用重寫」這個準確位置**。

結論：multi-model panel 對 ADR-level 決策**值得做**，但對 style extraction 這類偏美感判斷的任務價值有限（之前的判斷正確）。建議把這個工具形式化成 `shared/multi_model_panel.py`（Phase 2）。
