# ADR-005c: Bricks Template Maintenance

**Date:** 2026-04-22
**Status:** Accepted
**Phase:** Phase 1（純 docs，無 code 自動化）
**Supersedes section of:** [ADR-005](ADR-005-publishing-infrastructure.md)

---

## Context

shosho.tw 用 `bricks-child` 1.1 theme（parent = Bricks），部落格內文走 Gutenberg（由 [ADR-005a](ADR-005a-brook-gutenberg-pipeline.md) 產出），而站體 layout / header / footer / archive / single post template 則由 Bricks 編輯器管理。

原 ADR-005 曾考慮「Claude Design → Bricks AI Studio / MCP」的自動化橋接。Multi-model review 三家（Claude / Gemini / Grok）一致回饋：

- **Template 改版頻率極低**（年度級，非每週）
- **Bricks 官方無穩定 MCP 或 CLI import API**
- **人工流程 3 分鐘可搞定**（Claude Design 產 HTML/CSS → Bricks 編輯器「Raw HTML」element 貼上 → 微調）
- **造輪子風險 > 收益**（Bricks editor 有自家資料結構，逆向工程成本高）

Review 建議 Phase 1 把 Bricks 自動化從風險欄下架，降級到 Phase 2+ 預覽事項。本 ADR 正式把 Bricks template 維護定位為**人工流程**，並把流程寫清楚以免日後再次誤入自動化泥坑。

## Decision

### 1. 人工維護流程（正式 runbook）

```
1. 視覺探索
   └─ Claude Design（claude.ai/design）或 Figma
      ├─ 輸入 design-system.md tokens（顏色、字型、間距）
      └─ 迭代到修修滿意
2. 匯出
   ├─ Claude Design：「交付套件 → Claude Code」handoff
   └─ 產出：HTML + CSS（Tailwind 或原生 CSS）
3. 進 Bricks
   ├─ 方案 A：Bricks 的「Raw HTML」element 直貼（小區塊）
   ├─ 方案 B：Bricks 原生 element 重建（大改版，~30 分鐘）
   └─ 方案 C：註冊 Bricks custom element（含 PHP，僅在複數頁面復用時考慮）
4. 微調
   ├─ 在 Bricks editor 的 Preview 模式 tweak spacing / responsive breakpoint
   └─ 跨裝置實測（desktop / tablet / mobile）
5. 發布 template
   └─ Bricks 「Save template」→ Assign to condition（e.g. Archive: book-review category）
```

### 2. 觸發時機（什麼時候需要改 template）

| 時機 | 頻率 | 範例 |
|---|---|---|
| 新 category 上線 | 視需求 | 新開 `productivity-science` archive 頁 |
| 年度改版 | 1 次/年 | Header / hero / footer 整體更新 |
| Design system 升級 | 1-2 次/年 | tokens 改版後同步 template |
| 個別頁面特製 | 零星 | Landing page、活動頁 |
| A/B 測試 | 實驗才做 | 不在本 runbook 範圍 |

**不觸發**：單篇文章排版特製（走 Gutenberg，不改 template）。

### 3. 角色分工

| 角色 | 負責 |
|---|---|
| 修修 | 主 driver：決策、Bricks editor 實際操作、發布 template |
| Claude（副駕） | 產視覺稿、Tailwind → Bricks 對應元素的 translation、tokens consistency check |
| Usopp / Brook / Robin | **無相關責任**（agent 不碰 Bricks） |

Claude 的 handoff 產出固定走 `docs/design-system.md` tokens，不可硬寫色碼 / 字型（見 CLAUDE.md §美學要求）。

### 4. 現況盤點（2026-04-22）

- Bricks parent theme + `bricks-child` 1.1
- 站內 template 目前由修修手動維護
- Bricks AI Studio **未安裝**；即使安裝，Phase 1 也不走那條路
- 已有 192 篇文章不受 template 維護影響（Gutenberg 內文獨立）

### 5. 未來路徑（Phase 3+，觀望）

若以下**至少兩項**同時成立，重新評估自動化：

- [ ] Bricks 官方發布穩定 MCP 或 CLI import API
- [ ] Nakama 有 > 10 個 active WP 站（規模效益顯現）
- [ ] Template 改版頻率 > 每月 1 次（實際有痛點）

在此之前，**一律人工**。

## Consequences

### 正面
- Phase 1 scope 明確縮小，review §2 blocker「Bricks AI Studio 風險」從關鍵路徑移除
- 不耗資源逆向工程 Bricks 資料結構
- 人工流程文字化 → 下次改版不用重新思考

### 風險
- 無。人工流程已驗證可行（修修過往就是這麼做）。

### SPOF / Idempotency / Schema / SLO

本 ADR 不是 service，不涉及運行時系統。

- **SPOF**：N/A（無自動化系統可掛）
- **Idempotency**：N/A（人工操作，可直接在 Bricks editor undo）
- **Schema**：N/A（無跨 agent 資料流）
- **SLO**：N/A（非 service；若硬要定，「修修改完 template 到上線 < 30 分鐘」屬個人效率指標不屬系統指標）

### 不做的事

- 不開發 Claude Design → Bricks 的 MCP bridge
- 不逆向 Bricks 資料庫結構做程式化寫入
- 不為 Bricks template 版控設 git workflow（Bricks 原生 export JSON 可存 `bricks-templates/` 做備份，**非自動化**）
- 不把 template 維護納入任何 agent 的職責

## 開工 Checklist

純 docs ADR，無開工項。以下是「未來改 template 時的 reminder」：

- [ ] 改版前先讀 `docs/design-system.md` 確認 tokens 未變
- [ ] Claude Design 產出經 accessibility check（AAA body / AA secondary / keyboard nav）
- [ ] 改完 Bricks 原生 export 一份 JSON 存 `bricks-templates/YYYY-MM-DD-{slug}.json` 備份
- [ ] 前台實測三台裝置（desktop / tablet / mobile）
- [ ] `prefers-reduced-motion` 路徑驗證（若有 animation）

## Notes

- 本 ADR 拆自 ADR-005，回應 multi-model review §4 分歧 2（Bricks AI Studio 風險嚴重度三家共識偏高估）+ §6 Tier C（從 Phase 1 移除）
- 一開始就 `Accepted`，因為是「decide not to build」的決定，不需再 proposed 討論
- 2026-04-22 提出
