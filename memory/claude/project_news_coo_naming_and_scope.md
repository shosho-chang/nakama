---
name: News Coo 命名與職責切分
description: Browser extension 改名 News Coo（撤回 Den Den Mushi）；職責縮窄為純 extract+delivery，翻譯交給 Robin
type: project
---

撤回前一輪的 Den Den Mushi 命名（2026-05-10 同日內 pivot）。改用 **News Coo** 並把 scope 縮窄。

## 命名分層

| 層 | 名字 |
|---|---|
| Repo dir | `E:\news-coo` |
| Manifest `name` (Chrome store) | `News Coo` |
| Code identifier | `NewsCoo` |
| 對話 / commit / log | `News Coo` (3 syl 已短) 或 `news-coo` |

## 為什麼撤回 Den Den Mushi

Den Den Mushi 字面是「翻譯訊號 + 跨世界傳訊」雙重 metaphor。當 scope 含瀏覽器內翻譯時 fit；現 scope 縮窄到純 extraction + delivery，**翻譯不在 News Coo 職責內**，「翻譯訊號」metaphor 變 over-promise / 誤導。

News Coo（One Piece 海賊新聞遞送鳥）字面就是「把外面世界的內容遞送進來」，**純 delivery**，與職責 1:1 對應。

## 職責切分

**News Coo（新 browser extension）**：
- 瀏覽器按鈕 → Defuddle 抽取主內容 → 輸出乾淨 markdown + metadata
- POST 到 Nakama 後端新 endpoint（or 直寫 vault — 待 grill）
- **不做翻譯**、不做 bilingual format、不接 Reader
- 估 ~500 LOC, 1-2 天

**Robin（既有 agent，職責不變/小擴張）**：
- 既有：`/scrape-translate` (Trafilatura → translator → bilingual writer → Reader)
- 既有：`shared/translator.py` + `prompts/robin/translation_tw_glossary.yaml` 台灣術語
- 既有：`thousand_sunny/templates/robin/reader.html` 雙語 Reader
- **新加（獨立 PR）**：auto-translate trigger — 偵測 `Inbox/kb/*.md` 新檔（無 `-bilingual` sibling）→ 跑 translator → 寫 sibling
- Trigger 機制（Q8 待決）：sync via POST endpoint / async queue / 定期 polling / 手動按鈕

## 可獨立 ship

News Coo PR 不依賴 Robin 改動；Robin auto-translate trigger 也不 block News Coo。兩者可並行/任意順序 ship。

## How to apply

- 新 repo `E:\news-coo`，不要再用 `den-den-mushi` 命名
- 若已建 `den-den-mushi` worktree / branch / file → 全部撤回重建
- News Coo 範圍嚴守「extract + deliver」，**翻譯 / bilingual / glossary 任何議題不在 News Coo PR 內討論**
- 對應 PRD: `docs/prds/2026-05-10-toast-nakama-inbox-importer.md` 仍有效，但「Toast」字眼後續實作時改 News Coo（PRD 是 Codex 早上 grill prep，未鎖名）
