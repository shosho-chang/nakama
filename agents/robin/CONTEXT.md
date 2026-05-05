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

## Flagged ambiguities

（grill 2026-05-05 後 — 暫無 unresolved 詞彙衝突）
