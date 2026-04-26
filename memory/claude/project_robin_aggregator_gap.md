---
name: Robin aggregator gap — 三 bug 全有 fix path（#1 PR #169 落地待 merge / #2 待 apply / #3 已 fix）
description: 三個 production bug 狀態 — #1 _update_wiki_page todo-append PR #169 已重寫；#2 broken pages migration script merged 待修修 apply；#3 config env 順序已 fix
type: project
originSessionId: 27c0b340-d612-4f47-aba4-b4f3727267fd
---
2026-04-26 audit 列三個 production code 缺陷，狀態：

## 1. Robin `_update_wiki_page` 不是 aggregator（PR #169 修法 push 完，待 ultrareview second pass + merge）

- **檔案**：`agents/robin/ingest.py`（PR #169 已重寫）
- **舊行為**：body 末尾 append `## 更新（{date}）` block + LLM imperative todo
- **PR #169 fix**：刪除 `_update_wiki_page`；新 `_execute_concept_action` 走 `kb_writer.upsert_concept_page(action=...)` 4 種 action（create / update_merge / update_conflict / noop）
- **狀態**：commit `26ec74f` (base) + `4d4ab4e` (ultrareview fixes 9 findings) push 完；CI 綠；branch `feat/ingest-v2-step3-schemas-kb-writer`
- **Ultrareview fixes 含**：critical prompt 目錄錯誤（runtime path → 從 dead `agents/robin/prompts/` 搬到 `prompts/robin/` + 5 categories）、slug validation、update_conflict idempotency、_ensure_h2_skeleton preserve non-canonical H2、noop strip legacy block + 5 nits
- **影響範圍**：lazy migrate 在第一次 update 時 trigger（v1 → v2 schema_version + derive mentioned_in from source_refs + strip legacy `## 更新` blocks）

## 2. 既有 broken concept page — ✅ 全 apply 完 2026-04-26

- **狀態**：✅ A-11a fix + A-11b migration + apply 全完成（PR #164 `c2b529b` merged + 4 頁 apply 2026-04-26）
- **apply 結果**：
  - `ATP再合成.md` — recovered source_refs[2] + merged keys [confidence, tags, related_pages]
  - `神經保護作用.md` — recovered source_refs[0]
  - `肌酸代謝.md` — recovered source_refs[3]
  - `膳食補充劑安全性.md` — recovered source_refs[2]
- **共同 raw_suffix**：`Journal-of-the-International-Society-of-Sports-Nutrition.md`
- **`.bak` 留同目錄**（`{name}.md.bak`）
- **根因**：`shared/obsidian_writer.write_page` 之前 `yaml.dump` 沒設 `width`，PyYAML default 80 char fold 把長 source filename 從中間 fold；下一行開頭 `---` 被 yaml loader 當 document separator

## 3. `shared/config.py` get_vault_path / get_db_path env 順序 bug（已 fix）

- **狀態**：✅ Fixed（PR #164）
- **修法**：`load_config()` 移到 `os.environ.get` 之前
- **副作用**：`.env` 設的 `VAULT_PATH` / `DB_PATH` 現在桌機 IDE 啟動的 process 拿得到

## A-11c follow-up（PR #169 順手修）

- **狀態**：✅ Fixed (PR #169)
- `shared/lifeos_writer.py:193` `yaml.dump` 加 `width=10**9`，含 long-wikilink regression test
- 同 A-11a / obsidian_writer 同 root cause class — Nami 寫 Task / Project 含長 wikilink frontmatter（如 paper title）不會踩 corruption

## Why

#1 是 aggregator 哲學核心 violation；#2 是 user-facing data corruption；#3 影響桌機開發體驗；A-11c 是同 bug class 預防。

## 完整 audit reference

- `docs/decisions/ADR-011-textbook-ingest-v2.md` — Step 2 ADR
- `docs/plans/2026-04-26-ingest-v2-redesign-plan.md` — 完整 12 findings + 三步 sequencing
- PR #164 / #165 / #169 — 三步落地紀錄
