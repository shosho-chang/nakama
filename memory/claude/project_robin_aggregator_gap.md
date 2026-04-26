---
name: Robin update logic 不是 aggregator — Step 3 待修；其餘兩 bug 已 fix
description: 三個 production bug 狀態 — #1 _update_wiki_page todo-append 待 Step 3 重寫、#2 broken pages migration script 已 merged 待修修 apply、#3 config env 順序已 fix
type: project
originSessionId: 27c0b340-d612-4f47-aba4-b4f3727267fd
---
2026-04-26 audit 列三個 production code 缺陷，Step 1 hygiene PR #164 解決 #2 + #3，#1 留 Step 3。

## 1. Robin `_update_wiki_page` 不是 aggregator（待 Step 3 fix）

- **檔案**：`agents/robin/ingest.py:472-510`
- **行為**：body 末尾 append `## 更新（{date}）` block + 「來源：[[stem]]」純文字
- **內容**：LLM 寫的 imperative todo（「應新增 X、應補充 Y」），**沒** merge 進 concept body 主體
- **結果**：concept page = 第一次寫的版本 + N 條 todo 補丁
- **證據**：`F:\Shosho LifeOS\KB\Wiki\Concepts\肌酸代謝.md` 末尾有 10 條 `## 更新（2026-04-13）` 全是 todo 句、page 主體永遠停留在第一次 ingest
- **fix path**：Step 3 implementation — 改呼叫 `kb_writer.upsert_concept_page(action=...)`，4 種 action（create / update_merge / update_conflict / noop）；ADR-011 §3.5 詳述

## 2. 既有 broken concept page（migration script 已 merged，待修修 apply）

- **狀態**：✅ A-11a fix（obsidian_writer width=10**9）+ A-11b migration script 已 merged（PR #164 `c2b529b`）
- **dry-run 找到 4 頁**（plan 原列 2 頁，多出 2 頁同 root cause）：
  - `ATP再合成.md` — recover source_refs[2]
  - `神經保護作用.md` — recover source_refs[0]
  - `肌酸代謝.md` — recover source_refs[3]
  - `膳食補充劑安全性.md` — recover source_refs[2]
- **共同 raw_suffix**：`Journal-of-the-International-Society-of-Sports-Nutrition.md`
- **修修要做**：`python -m scripts.migrate_broken_concept_frontmatter --vault "F:/Shosho LifeOS" --apply`（預設寫 `.bak` 同目錄）
- **根因**：`shared/obsidian_writer.write_page` 之前 `yaml.dump` 沒設 `width`，PyYAML default 80 char fold 把含字面 `---` 的長 source filename（如 `Foo---Bar.md`）從中間 fold，下一行開頭 `---` 被 yaml loader 當 document separator 切開

## 3. `shared/config.py` get_vault_path / get_db_path env 順序 bug（已 fix）

- **狀態**：✅ Fixed（PR #164）
- **修法**：`load_config()` 移到 `os.environ.get` 之前
- **副作用**：`.env` 設的 `VAULT_PATH` / `DB_PATH` 現在桌機 IDE 啟動的 process 拿得到（不必每次 `VAULT_PATH=... python` workaround）

## A-11c follow-up（low priority）

`shared/lifeos_writer.py:193` 同樣有 `yaml.dump` 沒設 `width=10**9` 的 bug class — 若 Nami 寫 Task / Project 含長 wikilink frontmatter（如 paper title），會踩同 corruption。Step 3 順手修。

## Why

#1 是 aggregator 哲學核心 violation，必須在 Step 3 重寫；#2 是 user-facing data corruption（Obsidian / yaml parser 對這 4 頁 frontmatter parse fail）；#3 影響桌機開發體驗。

## 完整 audit reference

- `docs/decisions/ADR-011-textbook-ingest-v2.md` — Step 2 ADR（含 §3.5 kb_writer 設計）
- `docs/plans/2026-04-26-ingest-v2-redesign-plan.md` — 完整 12 findings + 三步 sequencing
