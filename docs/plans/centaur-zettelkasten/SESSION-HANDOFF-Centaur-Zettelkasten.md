# Session Handoff — Centaur Zettelkasten 設計討論

> **給接手的新 session / model**：這份讓你冷啟動就能接續，不必重讀整段對話。讀完這份 + 三份規格（§3 列路徑），你就有完整 context。語言用繁體中文（台灣）。
> **交接時間**：2026-06-11
> **狀態**：設計討論進行中，**尚未開始寫 code、尚未拆 task prompt**。

---

## 0. 一分鐘看懂現況

修修（使用者）在把一套「Centaur Zettelkasten（半人馬卡片盒）」人機協作知識系統的設計，**從一份在 Chat 裡產生、不反映真實 codebase 的交接文件**，逐輪討論收斂成「可以丟給 Claude Code 執行」的版本。

我們已經完成：① 釐清與真實 codebase 的對照、② 定案 Literature Note 統一格式、③ 定案 Ingest 端到端流程、④ 研究社群現況（含 Karpathy LLM Wiki）。**下一步**是把剩下的開放問題（§6）收斂，然後**拆成 N 系列 task prompt** 丟 Claude Code。

**互動方式**：修修要的是**多輪對話討論、一次推進一塊**，不要一次倒一大份計畫。每塊定案後才往下。先討論、後拍板、最後才產 task prompt。

---

## 1. 任務與目標

- **大目標**：在既有系統 Nakama（repo `E:\nakama`）+ Obsidian vault（`E:\Shosho LifeOS`）上，實作「人寫永久筆記 + AI 維護 Wiki」的卡片盒。
- **第一性原理（摩擦篩選）**：會產生新判斷的動作留給人（寫永久卡、定關係、選邊）；純損耗的動作交給 AI（搬運、摘要、抽實體、撈相關、記帳、標矛盾）。
- **這次 session 的具體任務**：把交接文件的抽象設計，grounded 到真實 code，逐塊討論成可執行規格 → 最後拆 task prompt。

---

## 2. 最重要的 context（沒讀到會做錯）

1. **`ADR-043-centaur-zettelkasten-permanent-layer.md` 已存在**（repo `docs/decisions/`，branch `docs/adr-043-centaur-zettelkasten`），是先前 panel-audited 的版本。**但修修的最新討論在關鍵點上推翻它**——
   - **被推翻的點（最重要）**：ADR-043 decision 3 要把 `KB/Wiki/Concepts/` 降級為候選（標 `status: candidate` + `#ai-draft`、停 Opus auto-merge）。**最新討論決定不降級**：`KB/Wiki/Concepts/` 維持 **LLM 自由寫 + 自由 merge 的平行權威層**（Karpathy 原意）。紅線只剩「AI 不寫 `KB/Permanent/`」。**凡 ADR-043 與這些規格衝突，以規格 + 最新討論為準。**
2. **greenfield**：`KB/Permanent/`、`KB/Fleeting/`、`KB/Wiki/MOCs/`、`KB/Literature/`、`KB/home.md` **都還不存在**；`KB/Wiki/Concepts/` 目前 **0 檔**（ADR-042 清理後）。
3. **Karpathy LLM Wiki（2026-04，gist `karpathy/442a6bf...`）是整套 `KB/Wiki/` 的源頭**：raw → LLM 編譯成 wiki（Sources/Concepts/Entities + index + log），ingest→整合→lint loop，**不用 embedding**（個人尺度靠 index 導航即可）。Centaur 的創新 = 在 Karpathy 的單層 AI wiki 上，**多加一層人寫的 Permanent**。

---

## 3. 已產出的 artifacts（都在 outputs 工作區，尚未進 repo/vault）

| 檔 | 內容 | 狀態 |
|---|---|---|
| `Literature-Note-統一規格書-v0.1.md` | 三路線統一文獻筆記：雙檔制、`type: literature` frontmatter、per-route body、退役舊 writer | 定案 v0.1 |
| `Ingest流程規格書-v0.1.md` | 按 Ingest 後七 Phase 端到端 + 永久卡一生 + §11.5 互動與複利 | 定案 v0.1（剛加完 §11.5） |
| `AI增強的卡片盒-社群現況綜述.md` | 社群研究長文（Karpathy、A-MEM、Smart Connections、Reor/Tana、ZK 論壇、Aeon） | 完成 |
| `Centaur-Zettelkasten-規格書.html` | 上面兩份規格的易讀 HTML（側欄導航 + 流程圖嵌入） | 完成，與 .md 同步 |

> ⚠️ **檔案同步 gotcha**：本工作區的 bash mount 對「反覆編輯的同一檔」會卡在舊快取（曾把 Ingest .md 卡在 15230 bytes 截斷）。file 工具（Read/Write/Edit）的視圖才是真相；用 bash 跑 build 前先確認 bash 看到的內容完整，必要時寫到新檔名再 build。

---

## 4. 已拍板的決定

**Literature Note（v0.1）**
- **雙檔制**：`KB/Annotations/{slug}.md`（機器 JSON，Reader 擁有、閱讀時動態更新，不動）+ `KB/Literature/{slug}.md`（人讀，ingest 當下 render 的快照）。
- 三路線**底層已統一**在 `AnnotationSetV3`（`shared/schemas/annotations.py`：Highlight/Annotation/Reflection）。缺的是「人讀那一份」。
- 統一 `type: literature` frontmatter（含 `source_kind`、`anchor_type`、`status: capturing→digested→mined`、`mined_concepts`、`annotations` 反指機器檔、`source_digest`）。
- body 依路線微調：書按章+CFI、文章+段落錨（**本輪補 `^p-N`，文章目前無位置錨**）、影片時間軸+講者（`t=` 錨點 code 已存在只是沒 render）。
- digest 的「🔗 KB 相關 + 👍/👎」升級為**三路共用**；退役 `book_notes_writer`/`book_digest_writer`（先 repoint consumer：Brook `context_bridge`、RCP、kb_search）。修修同意「Brook 寫作 context 還沒用，可直接砍」。
- 《財富階梯》當第一個 migration/pilot 樣本（已讀完：70 highlight + 8 annotation + 0 reflection，只有 digest.md）。

**Ingest 流程（v0.1）**
- 七 Phase：0 閱讀→（按 Ingest）1 Literature Note→2 LLM 編 Wiki（Karpathy loop）→3 提示座艙→🚧紅線🚧→4 人寫永久卡→5 AI 記帳善後→6 MOC（擠壓點才做）。
- 映照（人層 ↔ AI 層）四接點：① 共用 index ② Concept↔Permanent 雙向連結 ③ Entities 共用底料 ④ Literature 是來源鉸鏈。**優先規則：Permanent 是「修修怎麼想」的權威、檢索排最前**（fork 2）。
- **複利**＝兩條曲線：知識複利（wiki，要架構化）+「越來越懂你」複利（靠每次讀你的痕跡，**尤其永久卡=你的聲音**；**不自建偏好引擎**，記憶引擎交模型底層）。write-back 寫**蒸餾後的答案非原始對話**，帶 provenance，永不自動寫 Permanent。

**檢索規模（已釐清）**
- Karpathy 尺度：< ~100k token / ~150–200 頁，純上下文勝 RAG、不用 embedding。修修的 wiki 知識頁（Sources ~13.6 萬字元）+ index（~6k 字元）遠在門檻內。embedding 只有「對全部永久卡做 Connection Discovery」且累積到 ~200–500 張卡才需要（多年後）。**現在不用 embedding 是對的。**

**Smart Connections（決定）**：pilot 期用它當 Obsidian 內寫卡時的「相關卡發現側欄」（不重造輪子）；但它是 embedding-based、只給相似不給關係——**當人層的 authoring 輔助，不是 AI agent 的檢索骨幹**（骨幹仍是 wiki 連結 + kb_search FTS5）。長期的 Centaur 原生版是 `/draft-map`（LLM-over-corpus、帶 typed edges）。

---

## 5. 目前進度 + 下一步

- **剛完成**：把「§11.5 互動與複利」寫進 Ingest 規格 + 重生 HTML。
- **下一步（修修指定）**：本來要繼續推進，但修修要求**先把整段對話 handoff 到新 session 用新 model**——所以這份就是那個 handoff。
- **接手後該做的**：和修修確認 §6 的開放問題逐一定案；全部收斂後，**把兩份規格拆成 N 系列 task prompt**（見 §7 的 code 接點 + repo 既有的 `docs/task-prompts/N5xx-*.md` 慣例）丟 Claude Code。**先問修субuser、不要自己直接開拆。**

---

## 6. 待拍板的開放問題（接手後逐一問修修）

1. **處理座艙 pilot 形態**：純 Obsidian（手開 Literature + Smart Connections 側欄）vs 最小 CLI/側欄並排「Literature + Concept + nudge」。
2. **`🔗 KB 相關` 的 LLM-judge 過濾**：pilot 先做（擋雜訊）vs 先純 FTS5 快速上線。
3. **趁熱 vs 每週**：Phase 3 提示預設哪個。
4. **write-back 主動度**：確認式（建議）vs 自動。
5. **是否復活 `KB/Wiki/Outputs/`**（ADR-028 曾刪）當 query 寫回的家，還是只併進既有 Concept。
6. **MOC 位置/所有權**：`KB/home.md` + `KB/MOCs/`（人層、AI 撈骨架）——已傾向此，待最終確認（注意這與 ADR-043 原本放 `KB/Wiki/MOCs/` AI 全權不同）。
7. **拆 task prompt 的切法**：幾份、先後序（建議 pilot 走 route C 文章端到端）。

---

## 7. codebase 關鍵接點（grounded，拆 task prompt 用）

- **Reader / 存註解**：book `thousand_sunny/routers/books.py`（`GET /robin/books/{id}`、`POST …/annotations`、`…/ingest-request`→`shared/book_queue.py`）；article/video `thousand_sunny/routers/robin.py`（`/robin/read`、`POST /robin/save-annotations`；`/robin/watchlist/{id}`、`POST …/annotation`）。
- **註解儲存**：`shared/annotation_store.py`（`KB/Annotations/{slug}.md`，JSON-in-frontmatter）；schema `shared/schemas/annotations.py`（V3）。
- **Ingest/promotion**：`agents/robin/ingest.py`（`IngestPipeline.ingest`，文章 source page + `upsert_concept_page`）；`shared/promotion_review_service.py`、`shared/promotion_commit.py`、`shared/promotion_renderer.py`；`thousand_sunny/promotion_wiring.py`（**`llm` 模式 main 仍 `raise` = dry-run；真 extractor 在未 merge 的 `feat/n519-llm-promotion-extractor`**）。
- **舊 book writer（要退役）**：`agents/robin/book_notes_writer.py`、`book_digest_writer.py`。consumer（要 repoint 到 `KB/Literature/`）：`agents/brook/context_bridge.py:138-145`、`agents/robin/reading_context_package.py:17-19`、`agents/robin/kb_search.py:178`。producer 要改：`agents/robin/annotation_merger.py`。
- **Wiki 寫入**：`shared/kb_writer.py`（`upsert_concept_page` Opus diff-merge、`write_source_page`）。
- **索引/檢索**：`shared/kb_indexer.py`（目前索引 `{Sources,Concepts,Entities}`+Annotations，**需新增 `KB/Permanent/` + 排序提前**）；`agents/robin/kb_search.py`（FTS5/BM25）。
- **永久層（新建）**：`KB/Permanent/`、窄寫入口 `update_permanent_bookkeeping()`（只記帳型 key）、author-provenance 欄 + tripwire 測試（斷言 promotion target 永不回 Permanent）。
- **影片時間錨**：`shared/video_source_map_builder.py`、`robin.py` `_format_t_locator`（`t=<start>-<end>` 已存在）。

---

## 8. 真實 vault 現況（`E:\Shosho LifeOS`，2026-06）

- `KB/`：Annotations、Attachments、Raw、Wiki、index.md、log.md。
- `KB/Wiki/`：Sources（15 檔）、Concepts（**0**）、Entities（**0**）、Digests（89 檔日報，~48 萬字元——是 feed 不是知識語料）。
- `KB/Annotations/`：3 檔（財富階梯、發現我的多重職涯組合、讓你的思緒平靜下來安然入睡）。
- 不存在：`KB/Permanent/`、`KB/Fleeting/`、`KB/Wiki/MOCs/`、`KB/Literature/`、`KB/home.md`。
- canonical vault 結構：`E:\nakama\docs\VAULT-LAYOUT.md`（ADR-028）。

---

## 9. 工作慣例（務必遵守）

- **worktree 紀律**：`E:\nakama` 是 control plane，**不直接寫檔/commit**。任何寫檔開 sibling worktree。所以本次所有產出放在 Cowork outputs 工作區，**未進 repo/vault**——之後要落地需修修指定位置（`docs/plans/` 或 sibling worktree）。
- **刪檔**：repo/vault 禁 `rm`，用 PowerShell 回收桶（見 repo CLAUDE.md）。
- **語言**：頁面內容繁中、frontmatter key 英文、專有名詞保留原文。
- **vault 寫入紅線**：`Journals/{Daily,Quarterly,Yearly}` 禁寫；`KB/Raw/` 不改 body；`KB/index.md` 同步、`KB/log.md` append-only。
- **user 偏好**：盡量精簡直接、少廢話；**一次推進一塊、多輪討論**；重大決定先問再做。
- **檔案同步 gotcha**：見 §3 ⚠️。

---

## 10. 關鍵來源文件（repo 內，接手可深讀）

- `docs/decisions/ADR-043-centaur-zettelkasten-permanent-layer.md`（注意：concept-降級點已被推翻）
- `docs/VAULT-LAYOUT.md`、`docs/decisions/ADR-017`（annotation）、`ADR-019`（two-file source）、`ADR-024`（promotion+RCP）、`ADR-042`（KB 輕量化/移除 dense vector）。
- `agents/robin/CONTEXT.md` §Centaur Zettelkasten（已內建詞彙與紅線）。
- 原始交接文件：使用者上傳的 `Centaur_Zettelkasten_交接文件.md`（抽象設計，檔名路徑為示意）。
- Karpathy LLM Wiki gist：`https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f`。
- 三份規格 + 綜述：見 §3（outputs 工作區）。

---

*Handoff 結束。接手後第一步：跟修修確認要先處理 §6 哪個開放問題，或直接進「拆 task prompt」的討論。記得——先討論、一次一塊、別自己暴衝。*
