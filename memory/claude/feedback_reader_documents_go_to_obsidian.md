---
name: feedback_reader_documents_go_to_obsidian
description: 給修修「閱讀＋修改」的文件一律放 Obsidian vault 的對應資料夾，不要只留在 episode 資料夾
metadata:
  type: feedback
---

**要修修讀、要他在上面改的文件，落點是 Obsidian vault，不是 `G:\Footages\<episode>\`。**

podcast 的話是 `AgentOutputs/interviews/<YYYY-MM-DD>-<來賓>/`，編號接續資料夾內既有的
`01–05`（訪前研究）往下編，依產出順序。既有實例：蘇予昕那集是 `06-title-brainstorm.md`、
`07-選段報告.md`；呂冠緯那集是 `06-字幕勘誤單.md`、`07-剪輯清單.md`。

⚠️ 資料夾日期是**訪談日**，episode 資料夾是**發布日**，兩者不同
（`20260721 呂冠緯` ↔ `2026-07-17-呂冠緯`）。用 `shared/vault_interviews.py` 對應，不要自己算。

**Why**：2026-09-11 修修原話——「你把字幕勘誤單移到 Obsidian 的 folder 裡面，我用 Obsidian 來
閱讀、修改比較方便。以後這類型的文件，可以也都放在 Obsidian，相關的資料夾裡。」
他的工作面是 Obsidian：有連結、有搜尋、手機上也讀得到。丟在 episode 資料夾等於要他開檔案總管
找一個 markdown，他不會去。

**怎麼分辨「這類型」**：問這份文件是**給人讀的**還是**給機器讀的**。
- 進 vault：勘誤單、剪輯清單、選段報告、標題腦力激盪、訪前研究——他會逐行看、會改、會回頭查
- 留 episode 資料夾：`run_log.md`、manifest、receipt、`*.json` 契約檔、SRT——那些是產線證據與
  機器輸入，vault 放它們只會變雜訊

**How to apply**：
- 產出這類文件時**直接寫進 vault**，不要先寫 episode 資料夾再搬。
- 兩邊都留會漂——他改 vault 那份、我讀 episode 那份，然後對不起來。以 vault 為 canonical。
- 新增或改動 agent 寫入 vault 的路徑，同一個 PR 要更新 `docs/VAULT-LAYOUT.md`（CLAUDE.md 明訂，
  reviewer 會抓）。`AgentOutputs/interviews/` 就是 2026-09-11 才補進那份文件的——在用很久了，但沒人寫。
- 相關：[[feedback_pipeline_anchored_planning]]
