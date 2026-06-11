# N522 — Centaur 每日回顧 daily job

> **上游文件（先讀）**：`docs/plans/centaur-zettelkasten/Centaur-Zettelkasten-規格-v0.2.md` §2 §5、`Centaur-Zettelkasten-Prompt規格-v0.1.md`（P-1、P-2 為本任務的 LLM prompt）。
> **依賴**：N520（indexer）、N521（Literature/annotation 讀取）。**worktree**：`E:\nakama-N522-daily-review-job`，branch `feat/n522-daily-review-job`。

## 1. 目標

scheduled job 產出每日回顧資料（候選卡 + fleeting 待處理 + 清掃項），與 UI 解耦。

## 2. 範圍

- 新 `agents/robin/daily_review.py`（cron，早上）：
  - `KB/Annotations/` delta 掃描（`created_at` 在昨日）
  - `KB/Fleeting/` `status: open` 掃描
  - 候選篩選 + 建議卡名：**P-1 prompt**（有 note 優先、強評價訊號置頂、純 highlight 排除、上限 7 條）
  - typed-edge 候選：FTS5 撈 top-k → **P-2 prompt** 判斷真關係 + 方向（supports/refutes/extends，每組上限 3，寧缺勿濫；internal_rationale 不外露）
  - 「之後再說」14 天過期歸檔
  - 每週清掃模式：stale seedling（>30 天）、孤兒卡（link graph 程式碼算，非 LLM）
  - `KB/log.md` append、Nami Slack 通知連結
- 結構化輸出（JSON），schema 與 N523 協定

## 3. 輸入

v0.2 §2（三迴圈）§5（每日回顧規格）；Prompt 規格 P-1/P-2 + §1 共同 system 前置（防注入、紅線）；N520 indexer API。

## 4. 輸出

job + 輸出 schema + schedule 設定 + 測試。

## 5. 驗收

- 以《卡片盒筆記》2026-06-10 真實 annotation 跑出候選清單，置頂含「必須重複三次」（08:43）與「這句是我想的，應該要記起來」（06:41）兩條
- P-2 輸出無「表面相似」誤判樣本（以財富階梯 wingate 案例做負面測試）
- 過期歸檔邏輯有單元測試

## 6. 邊界

不寫 `KB/Permanent/`（任何路徑）；不做 UI（N523）；P-10 LLM-judge 不做（fast-follow）；fleeting 捕捉不做（N526，測試先手建檔）。
