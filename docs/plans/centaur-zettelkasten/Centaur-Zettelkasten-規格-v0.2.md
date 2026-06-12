# Centaur Zettelkasten 規格 v0.2 — 定案整合

> **文件地位**：整合 2026-06-11 與修修的討論定案。凡與 Literature Note 規格 v0.1、Ingest 流程規格 v0.1、ADR-043 衝突處，**以本文件為準**。v0.1 兩份規格仍是細節的 canonical（frontmatter 欄位、migration 步驟、code 接點），本文件只記錄修訂與新增。
> **UI 附件**：`centaur-kb-prototype-v2.html`（互動 prototype，作為 Web UI 的視覺與流程規格）。
> **日期**：2026-06-11

---

## 1. 定案總表

| # | 決定 | 取代 / 修訂 |
|---|------|------------|
| D-08 | **三迴圈架構**：每日迴圈、Ingest 迴圈、每週清掃（§2） | 修訂 Ingest v0.1 的「Phase 3 綁 ingest」 |
| D-09 | **Permanent frontmatter + typed edges 進 body**（§3） | 新增 |
| D-10 | **status 兩級**：seedling → evergreen，升級只能人做 | 修訂 ADR-043 的三級制 |
| D-11 | **Fleeting Note 格式**（§4）；Nami Slack bot 捕捉 | 新增 |
| D-12 | **每日回顧**（原「座艙」一詞退役）走 Web UI（§5–6） | 修訂 v0.1「pilot = Obsidian 側欄」 |
| D-13 | **連結選擇器兩層**：Robin judged 建議優先 + 全量搜尋兜底，永不裸列全量 | 新增 |
| D-14 | **紅線第五條**：Concept/Output 終端證據只能 cite Sources/Raw/Annotations（§7） | 新增（防 wiki 自我餵食） |
| D-15 | **Phase 5 鏡像降級**：AI 只沿人寫連結傳播；語意對應只建議不寫（§8） | 修訂 Ingest v0.1 Phase 5 |
| D-16 | **re-ingest 語意**：idempotent render，保留記帳欄（§9） | 新增 |
| D-17 | **Q2**：`🔗 KB 相關` pilot 先純 FTS5，LLM-judge 為 fast-follow | 拍板 |
| D-18 | **Q4**：write-back 確認式；log.md 一律自動記 | 拍板 |
| D-19 | **Q5**：復活 `KB/Wiki/Outputs/` | 拍板 |
| D-20 | **Q6**：MOC 在 `KB/MOCs/`（人層）；**`KB/home.md` 不做** | 修訂先前傾向（home 砍掉） |
| D-21 | **👍/👎 回饋 pilot 砍**，LLM-judge 上線時再回來當 few-shot | 修訂 Literature v0.1 D4 的一部分 |
| D-22 | **nudge 過期語意**：「之後再說」14 天未動自動歸檔；mined 是事實標記不是進度條 | 新增 |
| D-23 | **pilot 成功指標**：流程端到端跑通 + 讀書日有寫出 ≥1 卡；連結品質不計分 | 新增 |
| D-24 | **種子語料**：既有三本書 annotation 回灌 ingest | 新增 |

---

## 2. 三迴圈架構（取代 Phase 3 綁 ingest）

```
每日迴圈（早上，scheduled）       🤖 → 🧑
  掃 KB/Annotations/ 昨日 delta + KB/Fleeting/ status:open
  → 產生「每日回顧」（候選卡 + fleeting 待處理 + 相關既有卡）
  → 你三選一：開卡 / 略過 / 之後再說
  ※ 不等 Literature Note、不等讀完整本書

Ingest 迴圈（讀完按 Ingest）      🤖
  Phase 1 凍結 + render Literature Note
  Phase 2 LLM 編 Wiki（Karpathy loop，順序鎖不變）
  ※ Phase 3 從 ingest 尾巴移除，併入每日迴圈

每週清掃（週末）                  🤖 → 🧑
  漏網候選 + 放超過 30 天的 seedling + 孤兒卡 + 過期歸檔
```

紅線位置不變：兩個人工迴圈（每日、每週）的「寫卡」動作即 Phase 4；Phase 5 善後在你存檔後觸發。

---

## 3. Permanent Note 規格

**檔名 = 一句宣告句**（即卡片標題與 ID；Obsidian 改名自動更新連結）。

```yaml
---
type: permanent
status: seedling          # seedling → evergreen；升級只能人做
author: human
created: 2026-06-11      # AI 預填
modified: 2026-06-11     # AI 維護
source_refs:             # AI 回填（多來源 list，帶錨點）
  - "[[Literature/卡片盒筆記]] ^cfi-6-26-106"
aliases: []              # AI 維護（跨語檢索）
---

（正文：一卡一概念，用自己的話。🚧 AI 不代筆）

支持:: [[好系統讓你不需要意志力]] — 因為系統把消耗意志力的環節先自動化
反駁:: [[寫作產出靠的是紀律]] — 它假設人人都是村上春樹
延伸:: [[Hell yeah or no]] — 同一原則從執行延伸到選擇
```

- **typed edges 在 body**，用 Dataview inline field（`支持::` / `反駁::` / `延伸::`），連結 + 理由同一行。**理由是人的判斷，不可省略提示**（UI placeholder 引導）。
- **方向定死**：一律「本卡 → 對方」。反向由 Obsidian backlinks / kb_indexer 免費取得，人不寫、AI 不鏡像寫入。
- **frontmatter 全部是 AI 記帳區**，人寫卡時不碰；唯一寫入口 `update_permanent_bookkeeping()`（只允許 `source_refs` / `modified` / `aliases` key）。
- **不放**：tags、confidence、Zettel ID、`related:` 無型別欄位。
- **Template**：`Templates/tpl-permanent.md`（人層檔案，由修修建立；Web UI 開卡時由後端組裝同樣結構）。

---

## 4. Fleeting Note 規格

`KB/Fleeting/{timestamp}-{前幾字}.md`，Nami Slack bot 寫入：

```yaml
---
type: fleeting
created: 2026-06-11T08:32:00
via: slack               # slack | mobile | obsidian
status: open             # open → processed（AI 在你處理後翻轉）
---
（原話一字不動）
```

生命週期：捕捉 → 隔天早上出現在每日回顧 →（開卡 / 併入既有卡 / 丟掉）→ AI 翻 `status: processed` 並把原檔送**回收桶**（PowerShell recycle bin，不用 `rm`）。寫入權限：人 + Nami 寫入；AI 只翻 status 與善後，不改字。

---

## 5. 每日回顧（Daily Review）規格

- **資料產出**：daily job（scheduled，早上）。掃描範圍：① `KB/Annotations/` 昨日新增條目 → 候選卡（附 AI 建議卡名 + why + Robin judged 相關卡）；② `KB/Fleeting/` status:open；③ 每週清掃日加入 stale seedling / 孤兒。
- **候選篩選**：有 note 的 annotation 優先；強評價訊號（「要記起來」「太重要」「必須重複三次」等）置頂。純 highlight 預設不進候選（雜訊控制），可日後調。
- **動作三選一**：開卡 / 略過（不再出現）/ 之後再說（14 天未動自動歸檔，無罪惡感佇列）。
- **開卡預填**：卡名（AI 建議、可改）、source_refs 錨點、Robin judged 的 typed-edge 候選 chips（分方向）；**正文永遠空白**。存檔擋空正文。
- **連結選擇器（D-13）**：每個關係組顯示「✦ Robin 建議」chips（已判斷相關性與方向，理由留人）+「搜尋全部卡片」兜底。
- **存檔後（Phase 5）**：回填 Literature `mined_concepts` + status、處理 fleeting status、更新 `KB/index.md`、`KB/log.md` append。
- Slack 通知：Nami ping 每日回顧連結。

---

## 6. Web UI 架構

- **Vault 是唯一真相，Web UI 只是 renderer**。Obsidian 並存（graph、Smart Connections 照用）。
- 服務掛 Thousand Sunny；視覺照 `docs/design-system.md`（`--sho-*`）。
- **IA（六 view，照 prototype v2）**：每日回顧／人層：Permanent、Literature、MOC／AI 層：Wiki（唯讀）、系統（index/log/紅線）。`home.md` 不做。
- **紅線在 API 層**：寫 `KB/Permanent/` 正文的 endpoint 只有一個（`POST /kb/api/permanent`，human-authoring surface 專用，寫入帶 `author: human`）。agent 的 promotion target resolver 永遠解析不到 `KB/Permanent/`。tripwire 測試斷言之。
- 用語：「每日回顧」（不用「座艙」）。導航不混中英贅字。

---

## 7. 演算法紅線（v2，五條 — 寫進兩邊 CLAUDE.md + tripwire）

1. AI 絕不寫 `KB/Permanent/` 正文與 status；唯一入口 `update_permanent_bookkeeping()`（白名單 key）。
2. 每個事實宣稱附 citation，溯源回 raw / annotation 錨點。
3. Concept 可寫可 merge，但不冒充永久卡（provenance 分離，`author` 欄必填）。
4. ingest 不建 MOC — MOC 等人的擠壓點。
5. **Concept / Output 的終端證據只能是 Sources / Raw / Annotations，不得以另一個 Concept / Output 作為事實來源**（防 citation laundering / wiki 自我餵食）。

> 同 PR 必須更新 LifeOS `CLAUDE.md` 權限表（新增 `KB/Permanent/` = 🔒 human-only body、`KB/Fleeting/` = 人+Nami、`KB/Literature/` = 🤖 render、`KB/MOCs/` = 🟡 marker convention）與 `docs/VAULT-LAYOUT.md`。

---

## 8. Phase 5 善後（修訂）

AI 在你存檔後只做：① 回填 Literature `mined_concepts` / `status: mined`；② 回填永久卡記帳欄；③ 更新 index；④ log append。
**鏡像連結降級**：AI 只沿**你親手寫的連結鏈**傳播 backlink 記帳（你引了 Literature → 沿 Literature→Source 補）；「這張卡對應哪個 Concept」屬語意判斷，AI 只能在 Concept 頁加 defer 標記**建議**，不得代你建立 Permanent 側的連結。

---

## 9. re-ingest 語意（D-16）

書分多天讀完、ingest 後又繼續讀：允許 re-ingest。Literature Note 的 re-render 必須 **idempotent**：保留 frontmatter 記帳欄（`mined_concepts`、`status`）與既有「✓ 已開卡」標記；只更新劃線內容區。實作上 render 區與記帳區用 marker 分隔。pilot 期若實作成本過高，退而求其次：re-ingest 前先讀舊檔記帳欄、render 後寫回。

---

## 10. MOC 維護規格

- 觸發：mental squeeze point（找不到了才建），或 AI lint 建議（「主題 X 已 N 張卡互鏈密集且無 MOC」）— 建不建永遠是人決定。
- 結構：人寫分組標題與「為什麼放這」；AI 維護 `%%agent-robin-unfiled%%` marker section（未歸位卡）+ 孤兒標記。
- MOC 索引頁（Web UI）：每主題顯示 已歸位數 / 未歸位數 / 孤兒數 / updated（人編輯日，AI 更新不算）。

---

## 11. Pilot 範圍與指標

- **先行**：route C（文章）端到端 + 書的每日迴圈（《卡片盒筆記》為真實場景）。B 路 Concept 等 N519、E 路後補。
- **成功指標（D-23）**：流程端到端跑通；讀書日有寫出 ≥1 張卡。連結品質不計分（冷啟動）。
- **種子語料（D-24）**：財富階梯、發現我的多重職涯組合、讓你的思緒平靜下來安然入睡 三本回灌。
- **砍掉**：👍/👎 回饋（D-21）、home.md（D-20）、LLM-judge（D-17，fast-follow）。

---

## 12. 拆分概覽（詳見 task prompts 文件）

N520 永久層地基 → N521 Literature writer 統一 → N522 每日回顧 daily job → N523 每日回顧 Web UI → N524 route C ingest 接線 → （後置）N525 其餘 view、N526 Nami fleeting capture。
