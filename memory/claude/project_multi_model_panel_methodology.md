---
name: Multi-Model Panel 方法論（2026-04-22 實證有效）
description: ADR / 策略決策用三家獨立 review 的 pattern，各家定位、觸發條件、成本、下一步工具化
type: project
tags: [methodology, multi-model, adr-review]
---

# Multi-Model Panel Methodology

2026-04-22 session 實證的方法論：關鍵決策用 **Claude Sonnet + Gemini + Grok** 三家獨立 review，triangulate 出真正的問題。

## 三家模型個性（實測確認）

| 模型 | 定位 | 特徵 |
|---|---|---|
| **Gemini 2.5 Pro** 🕵️ 吹哨者 | 系統性最嚴厲、學院派 | 專挑 schema / 契約 / edge case；回應相對短但狠；Round 1 平均給 3/10 |
| **Claude Sonnet 4.6** ⚖️ 仲裁者 | 中間、篇幅最長、最具體 | 平衡風險與實用；會給具體修法建議；適合當彙整基礎 |
| **Grok 4** 📣 啦啦隊 | 最樂觀、短而精煉 | 抓大局會漏 blocker；Round 1 平均 5-6/10；不適合單獨決策 |

## 何時用 Multi-Model Panel

**適合**：
- ADR / 架構決策（lock-in 成本高）
- 關鍵 SEO / 編輯決策（ground truth 重要）
- Franky 告警 false positive 過濾（誤判成本高）
- Brook 產稿前的事實 / 倫理 check

**不適合**：
- Style extraction（偏美感、答案非 binary）
- 例行 Brook compose（成本不值）
- 已有明確 ground truth 的驗證任務

## 使用模式

### Round 1：獨立 review
- 各模型收到**相同 input**、**相同 review prompt**
- **不互相參考**（獨立性是價值來源）
- 輸出結構化（分數、blocker、alternative、consequence）

### Round 2：blocker verification（修訂後）
- 提供「原始 blocker list + 修訂後的版本」
- 問：解了嗎？新引入什麼問題？
- 輸出 go / no-go

## 成本參考（2026-04-22 實測）

| 批次 | Call 數 | 耗時 | 成本估 |
|---|---|---|---|
| Round 1（3 ADR × 3 模型）| 9 | ~3 分鐘並行 | ~$3-5 |
| Round 2 驗證（4 ADR × 3 模型）| 12 | ~2 分鐘並行 | ~$3-5 |

## 實作暫態

- **目前**：`scripts/adr_multi_model_review.py` + `scripts/adr_blocker_verification.py`（one-shot script，硬編 ADR 清單）
- **Phase 2**：形式化為 `shared/multi_model_panel.py`
  - 輸入：document path、review framework（prompt template）、provider list
  - 輸出：per-model review files + consolidated synthesis
  - 支援 verification mode（傳 previous review 當參考）
  - 自動計算共識度（3/3 vs 2/3 vs 1/3）

## How to apply

- 遇到 ADR 或重大架構決策：先跑 Round 1，再決定修改方向
- 單模型 review 不等於 triangulation：有時候單家漏掉的東西是致命的
- Gemini 太嚴也別緊張：它的嚴厲常是 signal（真的有結構問題）
- Grok 說 go 不等於 go：再看 Claude 與 Gemini

## 相關檔案

- 2026-04-22 實證範例：`docs/decisions/multi-model-review/_META-SUMMARY.md`
- Round 1 scripts：`scripts/adr_multi_model_review.py`
- Round 2 scripts：`scripts/adr_blocker_verification.py`
