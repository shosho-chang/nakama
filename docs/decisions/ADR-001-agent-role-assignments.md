# ADR-001: Agent Role Assignments

**Date:** 2026-04-09
**Status:** Accepted

---

## Context

Nakama 在 v0.1.0 初始設計時，7 個 Agent 的職責是概略草擬的，尚未對齊實際的工作流程需求。隨著對內容創作者業務流程的理解加深，需要重新對齊每個 Agent 的職責邊界。

初始設計（v0.1.0）的問題：
- Usopp 被定義為「Community Monitor」，但發布工作（WordPress、YouTube、Fluent CRM）需要一個獨立角色
- Sanji 被定義為「Producer（選題/大綱）」，但社群營運（Fluent Community）是一個全職工作，需要獨立角色
- Franky 被定義為「Repurpose（SEO/改寫）」，但系統維護（套件更新、漏洞掃描、健康檢查）也需要獨立角色
- Brook 被定義為「Publish」，但發布已交給 Usopp，Brook 應專注於內容重組（同一內容轉換為不同平台格式）

## Decision

重新分配 Agent 職責如下：

| Agent | 舊職責 | 新職責 |
|-------|--------|--------|
| Robin | Knowledge Base | Knowledge Base（不變）|
| Nami | Secretary | Secretary（不變）|
| Zoro | Scout | Scout（不變）|
| Usopp | Community Monitor | **Publisher**：發布至 WordPress、YouTube、社群媒體；電子報管理（Fluent CRM）|
| Sanji | Producer（選題/大綱）| **Community Manager**：Fluent Community 社群營運、成員互動、活動策劃 |
| Franky | Repurpose（SEO/改寫）| **System Maintenance**：套件更新、CVE 掃描、API key 驗證、系統健康檢查 |
| Brook | Publish | **Composer**：內容重組，將文章/影片腳本轉換為各平台格式（Blog/YouTube/IG/Newsletter）|

## Consequences

- 所有 Agent 的職責邊界更清晰，避免重疊
- Usopp 承擔發布責任，需 Owner 核准後才觸發（human-in-the-loop）
- Brook 的 Composer 角色定位為「格式轉換器」，輸入來自 Owner 提供的原始內容
- Franky 的系統維護角色讓基礎設施健康有明確負責人
- 原「Repurpose」和「SEO」功能暫時沒有 Agent 負責，未來可能由 Brook 擴展承擔，或另立 Agent

## Notes

- 此決策在 2026-04-09 與 Owner 討論後確認
- README.md 與 ARCHITECTURE.md 應依此更新
