---
name: Nakama 三份通用原則
description: schemas / reliability / observability 三份 principles，所有 ADR 與新 code 援引，不用每次重新辯論
type: reference
tags: [principles, architecture, standards]
---

# Nakama 通用原則（2026-04-22 建立）

`docs/principles/` 下三份原則文件是 Nakama 的 baseline standards。新 ADR 與新 code **直接援引**，不要每份 ADR 重新辯論 idempotency 要不要做、schema 要不要 version、log 要不要 structured。

## 三份原則

| 檔案 | 涵蓋 |
|---|---|
| [`docs/principles/schemas.md`](../../docs/principles/schemas.md) | Pydantic schema、版本欄位、`extra="forbid"`、anti-corruption layer、enum/literal 優先、LLM structured output、ID 慣例 |
| [`docs/principles/reliability.md`](../../docs/principles/reliability.md) | Idempotency、atomic claim、SoT、SPOF 識別、retry+backoff、timeout、DLQ、crash safety |
| [`docs/principles/observability.md`](../../docs/principles/observability.md) | Structured log、operation_id、metrics、/healthz 契約、外部 probe、SLO、alert 三級、secrets 不入 log、LLM 成本觀察 |

## 核心硬規則（違反即不合格）

- **Schema 版本欄位**：任何持久化的 schema 必有 `schema_version: Literal[N]`
- **Atomic claim**：跨 worker queue 抓工作必須 atomic，禁止 read-then-write
- **Idempotency key**：所有寫操作必須有冪等 key
- **外部 uptime probe**：Franky 不能監控自己，必須用 UptimeRobot 類外部服務
- **Operation ID**：所有對外操作帶 `operation_id`，log 全程傳遞
- **Secrets 不入 log**：任何 observability 通道都不能看到 API key / token / password 明文

## How to apply

- 寫新 ADR 時：引用原則檔，不要複製內容
- 寫新 shared lib 時：檢查是否違反任何原則
- review PR 時：把三份原則當 checklist
- 未來更新：原則是活文件，發現新 pattern 或踩新坑時更新

## 歷史

2026-04-22 multi-model ADR review 揭露三份 ADR 都共享同一批問題（schema 薄、無 idempotency、無 observability）。決定把這些提升為 cross-cutting 原則，避免每份 ADR 都重新辯論。
