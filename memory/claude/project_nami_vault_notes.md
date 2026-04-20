---
name: Nami vault 筆記工具
description: Nami 新增 3 個 vault note tools + 路徑規則模組，已完成但尚未 VPS 部署
type: project
---

**Nami vault note 能力（2026-04-20，本機完成，待 VPS 部署）**

## 已完成

- `shared/vault_rules.py` — 集中路徑規則：`VaultRuleViolation`、`assert_nami_can_write`、`assert_nami_can_read`，含 path traversal 保護與 prefix 精確比對
- 3 個 Nami tools：`write_vault_note` / `read_vault_note` / `list_vault_notes`
- `prompts/nami/agent_system.md` 加「Vault Notes（秘書筆記）」區塊
- 76 tests pass（19 vault_rules unit tests + 7 gateway handler integration tests）

## 路徑規則

- **寫入白名單**：`Nami/Notes/` 只有這一個
- **讀取白名單**：`Nami/Notes/`、`Projects/`、`TaskNotes/Tasks/`
- 禁止：`Journals/`、`KB/`、任何含 `..` 的路徑、絕對路徑

## 設計分工

- **記憶系統**（`shared/agent_memory.py`）：Nami 從對話學到的事，不對外顯示
- **vault note**（這個功能）：交付物（sales kit、會議摘要），使用者可見

## 待辦

- VPS 部署（`git pull` + `systemctl restart nakama-gateway`）
- 告知修修可以請 Nami 整理 Gmail Sent 報價記錄到 `Nami/Notes/sales-kit-*.md`

**Why:** 修修問 Nami 能不能幫整理 sales kit，觸發了這個設計。
**How to apply:** Nami 現在可以把交付物存進 vault，秘書能力完整一圈。
