# Robin — Knowledge Base ingest + KB read

Robin 吸收 source（article / paper / book / podcast）→ 抽 concept / entity → 寫 wiki page。
本 context 詞彙以 **source 從外部進 vault 的各種形態** + **ingest pipeline 中介物**為主軸。

## Language

### Zotero 整合（grill 中，2026-05-05+）

**Zotero item**:
Zotero 庫裡的一筆 metadata 紀錄（title / authors / DOI / publication），對應一篇 paper / 一個網頁 / 一份報告。一個 item 可掛多個 attachment。**修修閱讀的單位是 item，不是 attachment**。
_Avoid_: zotero entry, zotero record（用 item）

**Zotero attachment**:
掛在 Zotero item 底下的檔案：browser-extension 抓的 HTML snapshot、publisher PDF、supplementary PDF 等。Storage path 結構：`~/Zotero/storage/{itemKey}/{filename}`。
_Avoid_: zotero file（用 attachment）

**Snapshot**:
Zotero browser-extension 對網頁完整存檔的 HTML + `_assets/` 資料夾（圖、CSS、字型 verbatim）。**HTML 檔永遠留在 Zotero storage，不複製進 vault**。
_Avoid_: zotero archive, zotero capture（用 snapshot）

**Zotero sync**:
**One-shot import**：把 Zotero item 的 metadata + 選定 attachment 轉成 vault 內 MD 檔。**不是 live mirror** — sync 後 vault MD 跟 Zotero item 解耦，Zotero 端改 / 刪不會回沖 vault。**Sync agent 落 Zotero desktop 本機**（Mac / Windows）— 直接讀 `~/Zotero/zotero.sqlite`（copy 到 tmp 規避 lock），不上 VPS、不走 Web API。修修無 cloud sync，Zotero library 單機落點。
_Avoid_: zotero mirror, zotero replication（用 sync）

**Working MD**:
Sync 產出的 derived markdown，落 `Inbox/kb/{slug}.md`，**英文 only、LLM 未碰**。Frontmatter 帶 `zotero_item_key` + `zotero_attachment_path`（回指 Zotero storage）。Reader / translator / annotation / Robin ingest 全部 downstream consumer 都對它跑，不直接讀 Zotero。

**Bilingual sibling**:
Reader「翻譯」按鈕（PR #354）產出 `Inbox/kb/{slug}-bilingual.md`，含中英對照段。**原 `{slug}.md` 不變**，bilingual 是 sidecar。修修 annotate 落在 bilingual 上（`KB/Annotations/{slug}-bilingual.md`）。**翻譯引擎走自家 translator（`shared/translator.py` Claude Sonnet + 台灣繁中 glossary），不切沉浸式翻譯 / Pro。** 理由：(1) 單篇 paper 是短文無跨章節飄移，Pro Smart Context 邊際提升低；(2) PR #354 bilingual format 鎖在自家段落 blockquote 結構，ADR-017 annotation ref 對位也鎖在這格式；(3) 自家 user_terms 自動學習 glossary 控制權無可替代。Pro 訂閱是修修個人 EPUB 書翻譯軸的決策（PR #376 grill），跟 nakama Zotero pipeline decouple。
_Avoid_: 翻譯檔, translated md（用 bilingual sibling）

**Raw source page**:
Ingest 產出的兩檔之一，落 `KB/Wiki/Sources/{slug}.md`。**LLM 未碰過的英文原文**，從 Zotero `snapshot.html` re-extract（不 copy bilingual 強制保純）。**目的：來源未污染保證**，引用 / fact-check / 重翻譯都對它打。
_Avoid_: original page, source raw（用 raw source page）

**Annotated source page**:
Ingest 產出的兩檔之二，落 `KB/Wiki/Sources/{slug}--annotated.md`。**雙語對照 + 修修 annotation inline 編織**。**目的：把修修個人觀點 first-class 化進 KB**，concept extraction / 文獻科普寫稿都優先讀這個（看修修怎麼 frame 的）。
_Avoid_: annotation page, notes page（用 annotated source page）

### Reader-side（既有，PR #354 凍結）

**Bilingual reader**:
`reader.html` 雙語 toggle 機制 — 一份 MD 含中英對照段，UI 切換顯示。**不是兩個檔**。
_Avoid_: 雙語 reader pair（用 bilingual reader）

## Relationships

- 一個 **Zotero item** → 一個 **Working MD**（A 方案凍結，Q1）
- 一個 **Zotero item** 可有多個 **attachment**，sync 挑主：
  - **HTML snapshot 在 → 走 HTML path**（Trafilatura → MD + `_assets/` 複製）
  - **HTML snapshot 不在、PDF 在 → 走 PDF path**（pymupdf4llm → MD + 圖檔抽到 `_assets/`，既有 PR #71 module reuse）
  - **兩個都不在（純 metadata item）→ sync fail**，inbox 寫 placeholder `status=no_attachment`
  - **同時有 HTML + PDF → HTML 贏**（Q1 凍結）；圖表完整度若不夠，修修回 Zotero 開 PDF 看（fallback 不在 nakama 內處理）
- **Snapshot** HTML 永遠留 Zotero storage；**`_assets/` 圖檔複製到 `KB/Attachments/zotero/{slug}/_assets/`** — 讓 vault 自包含、跨機器可看（Q2+Q3 凍結）
- **Working MD** 在 `Inbox/kb/{slug}.md`（英文 only），圖路徑寫成 vault 相對：`Attachments/zotero/{slug}/_assets/fig1.png`
- **Working MD** → 修修按「翻譯」→ 產 **Bilingual sibling** `{slug}-bilingual.md`（原檔不動，frontmatter copy）
- **Bilingual sibling** → 修修 annotate → annotations 落 `KB/Annotations/{slug}.md`（**raw + bilingual 共用同 annotation slug**，ADR-017 既有 title-based derivation 天然 merge — 不需要 amend ADR）
- **Ingest 時 fan out 兩檔**進 `KB/Wiki/Sources/`：
  - **Raw source page** `{slug}.md`：從 `snapshot.html` re-extract，純英文原文，LLM 未碰
  - **Annotated source page** `{slug}--annotated.md`：bilingual + annotations 編織，修修觀點 first-class
- **Sync 觸發**：修修 Zotero 右鍵 Copy Link → 貼 nakama Inbox UI（`zotero://select/library/items/{itemKey}`）→ sync 一篇。仿既有 URL ingest UX；Inbox form 擴成「URL 或 zotero:// link」共用 dispatcher
- **Sync 落地平台**：Zotero desktop 那台（修修主用 Windows，偶 Mac）；storage path 走 config（Win `%USERPROFILE%\Zotero\` / Mac `~/Zotero/`）

## Example dialogue

> **Dev**：「同 item 第二次 sync 怎麼處理？修修在 Zotero 改了 metadata，要不要回沖？」
> **修修**：「**sync 是 one-shot**，第二次預期 skip（已存在）。回沖會碰到 annotation merge 問題，不是 MVP scope。」
> **Dev**：「那 attachment 路徑變了呢？例如修修把那篇 paper 從 collection A 搬到 B。」
> **Dev**：「Zotero `itemKey` 永久不變，`storage/{itemKey}/` path 也穩定 — collection 搬移不影響 storage path，sync 不需要追。」

## Re-sync 行為（MVP 默認）

修修第二次貼**同 item 的 `zotero://` link** → **skip 不覆寫**，return existing inbox path。仿既有 `inbox_writer.find_existing_for_url()` 對 `original_url` reverse-lookup 的 short-circuit 模式（這次比對的是 frontmatter `zotero_item_key`）。Force-update / 重抓 metadata 等 Phase 2 加 explicit「re-sync」UI 按鈕。

## Annotation / Reflection 寫稿流程詞彙（2026-05-07 panel-revised v2）

> v1（早上 grill）的「File 1 / File 2 雙檔」+「Brook 寫進 Project 頁面」+「sequential HITL」三個方案被 Codex+Gemini panel 翻案。下面是 ADR-021 v2 凍結的詞彙。

**Annotation set**（W3C 風 v3 schema）:
單檔 canonical at `KB/Annotations/{slug}.md`，**沒有 derived view 在 vault 內**。每個 item 同時帶 target（位置）+ body（內容）— 不再分「位置檔」跟「內容檔」。三型：HighlightV3 / AnnotationV3 / ReflectionV3。
_Avoid_: File 1 / File 2（v1 詞彙，已棄）；annotated source page（ADR-017 v2 詞彙，已棄）

**Reflection**:
v3 schema 內的 `ReflectionV3` type — 章節級 / 整本級的長思考，不綁特定 span。code 內既有 `CommentV2` 升 v3 改名 `ReflectionV3`，留 alias。
_Avoid_: comment（對 user 講都用 reflection）

**Evidence pool**:
Brook synthesize 的中間產出之一 — 對 Project 主題做 KB 廣搜後選出的 evidence 清單，每條附 chunk_text + hit_reason + source slug。**存 server-side store `data/brook_synthesize/{slug}.json`，不寫進 vault**。Web UI evidence panel 的資料源。
_Avoid_: 引用清單, search results

**Outline draft**:
Brook synthesize 的另一個中間產出 — title 候選 / 段落小標 / 每段引用哪些 evidence。跟 evidence pool 一起在**同一次 Brook call** 產出（unified synthesize），不是兩段。存同 server-side store。
_Avoid_: structure, plan

**Outline final**:
修修 Web UI in-context review 後 finalize 的 outline — Brook 依 reject/move 動作 regenerate 過、修修 Web UI 認可的版本。修修 Step 5 寫稿照這個寫。Project 頁面內由修修手動取用（複製 / 重寫，不 auto-write）。

**Server-side synthesize store**:
Brook synthesize 輸出的 single source of truth — `data/brook_synthesize/{project_slug}.json`，VPS 存。**Vault 完全看不到**。Web UI route `/projects/{slug}` 讀寫此 store。

## Workflow（Line 3 文獻科普 Stage 4 — ADR-021 v2 凍結）

```
Step 1 修修開 Project 頁面 + 定主題（Obsidian 手寫）
Step 2 Zoro keyword research（agent backend → Project frontmatter）
Step 3 Brook unified synthesize（agent backend）：
  廣搜 → evidence pool + draft outline（一次產，綁定每段引用哪條 evidence）
  → 寫 server-side synthesize store
Step 4 修修 Web UI 內 in-context review：
  - 對著 outline 結構看 evidence
  - reject / 移段 動作（顆粒度：「這條 evidence 從第 3 段拿掉」）
  - finalize → Brook regenerate outline（廣搜結果 cached，不重撈）
Step 5 修修右螢幕 Obsidian 寫稿、左螢幕 Web UI viewer 渲染 outline final + evidence
```

## 跟 ADR-022 multilingual embedding 的 dependency

ADR-021 v2 Brook 廣搜 cross-lingual 命中靠 ADR-022 切 BGE-M3 為全 KB 預設。**ADR-022 必須先 ship**。過渡期 Brook 走 multi-query（繁中 + 英文 keywords 兩 query 合併），ADR-022 ship 後改回 single-query。

## Flagged ambiguities

- 既有 `book_digest_writer.py` / `book_notes_writer.py` / `annotation_merger.py` 的角色 — ADR-021 v2 不再讓它們參與 retrieve canonical 路徑，但保留為 optional view；實作時評估是否棄用
- Robin CONTEXT 上方 Zotero language section 隨 PR #451 過期 — 獨立 cleanup（不在本 ADR scope）
- BGE-M3 對繁中 query 命中質量（ADR-022 mini-bench 任務）
