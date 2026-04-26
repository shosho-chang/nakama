---
name: Robin update logic 不是 aggregator + 已知 broken pages + vault path bug
description: 三個 production code 待修：Robin _update_wiki_page 是 todo-style append 不是 aggregator、ATP再合成/肌酸代謝 frontmatter 已壞、shared/config.py vault path env 順序 bug
type: project
originSessionId: 27c0b340-d612-4f47-aba4-b4f3727267fd
---
三個已知缺陷（待 ADR-010-v2 implementation 一波解決，2026-04-26 audit）：

## 1. Robin `_update_wiki_page` 不是 aggregator

- **檔案**：`agents/robin/ingest.py:472-510`
- **行為**：body 末尾 append `## 更新（{date}）` block + 「來源：[[stem]]」純文字
- **內容**：LLM 寫的 imperative todo（「應新增 X、應補充 Y」），**沒** merge 進 concept body 主體
- **結果**：concept page = 第一次寫的版本 + N 條 todo 補丁
- **證據**：`F:\Shosho LifeOS\KB\Wiki\Concepts\肌酸代謝.md` 末尾有 10 條 `## 更新（2026-04-13）` 全是 todo 句、page 主體永遠停留在第一次 ingest

## 2. 既有 broken concept page（frontmatter 已壞）

- **檔案**：
  - `F:\Shosho LifeOS\KB\Wiki\Concepts\ATP再合成.md:14-19`
  - `F:\Shosho LifeOS\KB\Wiki\Concepts\肌酸代謝.md:13-24`
- **症狀**：frontmatter 結尾 `---` 之後有「Journal-of-...md」純文字，再下一行 `---`，再 `confidence: medium / tags / related_pages`，再 `---`，雙包夾結構
- **根因**：`_update_wiki_page` 對含 unicode 長 filename 的 yaml.dump 換行解析錯誤
- **影響**：Obsidian / yaml parser 對這兩頁 frontmatter parse fail；retrieval 拿不到 metadata
- **fix**：A-11 migration script ~80 行 + 配合 #1 重寫（避免再踩）

## 3. `shared/config.py` get_vault_path / get_db_path env 順序 bug

- **檔案**：`shared/config.py:30-51`
- **現狀**：`os.environ.get("VAULT_PATH")` 先讀，`load_dotenv()` 後執行（在 load_config 第 21 行內）
- **結果**：`.env` 設的 `VAULT_PATH` 永遠拿不到（除非 OS shell 已 export）
- **影響範圍**：桌機 IDE 啟動的 process 一律走 yaml `vault_path: /home/Shosho LifeOS` fallback（VPS 路徑）；VPS 用 systemd Environment= 直接 export，沒踩到
- **`get_db_path` 同 bug**
- **fix**：把 `load_config()` 移到 `os.environ.get` 之前，6 行 + test
- **2026-04-25 textbook-ingest ch1 測試踩到**：用 inline `VAULT_PATH=... python` workaround，沒寫進 vault path 解析

**Why:** 三個都是 ADR-010-v2 implementation 路徑必須解決的硬傷，#2 是 user-facing data corruption 不能拖。

**How to apply:** 新 ingest 動工前先修 #1 update path 重寫，否則新 schema 寫進去依然會踩 #2 broken pattern；#3 修了之後桌機才能正確跑 ingest（不必每次 inline env var）。

## 完整 audit reference

- `docs/plans/2026-04-26-ingest-v2-redesign-plan.md`（完整 12 findings + 三步 sequencing）
- 2026-04-26 session sub-agent A audit report（Opus，conversation transcript）
