# ADR-035: Video Reader vertical + AV reader architecture

**Date:** 2026-05-27（v2）/ 2026-05-28（v3 §D6 reversal）
**Status:** Accepted (v3, post 2026-05-28 grill session — §D6 layout + annotation flow 反轉)

---

## Context

修修長期會看大量 YouTube 影片跟 podcast。需求 verbatim（2026-05-26 ADR-034 v2 grill 期間提出）：

> 我之前想要做一個以 Web UI 為底的 YouTube 影片閱讀器。我希望能一邊閱讀，一邊把我覺得重要的點畫下來。隨著我一邊觀看影片，底下會一邊即時將轉錄稿輸出，而我就會在我特別有認同或感覺的地方，在上面做 highlight 或是 annotation，跟我閱讀書本或文章的動作是一樣的。整部影片看完之後會有幾個輸出：(1) 完整的轉錄稿、(2) 我畫了哪些線以及註解、(3) 而這些如果我認為是有價值的話，我也希望把它納入到我的 Knowledge Base 裡面。這樣子以後我在寫文章的時候，很快就可以搜尋到我曾經看過哪個影片、哪一個人說了哪些事情。

這條 reader 是 ADR-034 v2 entity infrastructure 的 **first real caller** — Person/Organization Entity 在 promotion gate 跑的真正下游，目前 dry-run extractor 還未替換。

CONTEXT-MAP.md 已將 podcast 列在 Robin 職責（"article / paper / book / podcast"），YouTube video 為自然延伸。互動模型跟既有 EPUB / Inbox markdown reader 同 paradigm（scroll + highlight + annotation + 同步到 KB），但有兩個新軸：**timed media playback** 跟 **transcript follow-along sync**。

### 觸發此 ADR 的 grill（2026-05-27）

修修 `/grill-with-docs` 走完 7 題決策樹（記在 `.nakama/session_handoff_2026-05-27T03-00-00Z_youtube_reader_grill.md`）。本 ADR 凍結 grill 結論為 architecture lock-in 紀錄。

---

## Decision

### D1. Player 嵌在 Bridge 內，Robin surface 新增 video reader route

新 template `thousand_sunny/templates/robin/av_reader.html`，route `/robin/watchlist/{source_id}`。共享 Bridge 既有 chassis：HMAC cookie auth、design tokens、theme.js、promotion pipeline wiring、entity infrastructure。

未來 PWA 化（chassis-level）路徑相容：top-bottom stacked layout 在 portrait phone 唯一可行。

### D2. ASR 分階段：YouTube auto-captions（v1）→ Local Whisper（on-demand）

| Phase | Source | Transcript engine |
|---|---|---|
| Phase 1 | YouTube video | yt-dlp `--write-auto-sub`（zh-Hant / zh-CN / en 優先序） |
| Phase 2 (on demand) | 非 YouTube video / pure podcast | Local Whisper（`agents/brook/script_video/` 既有 ASR infra） |

雲端 ASR API 跳過 — single-user + 已有自家 GPU + VPS，long-term cost 不划算。

### D3. 不做自動 diarization；speaker 走 manual cast list + Person Entity

修修原文「**哪一個人說了什麼**」search use case 不靠 pyannote。

- Watchlist 匯入時宣告 cast list（host + guests，1-4 個 free-text name）
- Reader 內 annotation modal 上 speaker = **chip selector**（一 tap）
- 沒選 = `unspecified`（searchable filter）
- Cast 編輯入口：source metadata page (`/robin/watchlist/{source_id}/edit`)，typo / 漏列 / 後補 guest 都從這裡改
- 修修標的 speaker name 餵 Entity Extractor → Person EntityReviewItem → fast-track（confidence 高，自動 promote）
- Search 走 Person Entity backlinks，不靠 transcript 內 inline speaker tag

Bonus：yt-dlp 抓 YouTube description chapters 可當 cast seed suggestion（不 auto-bind 到 transcript timestamp，granularity 太粗）。

### D4. Watchlist ingestion = URL paste (v1) + vault markdown (fallback)

- **A. URL paste（v1 default）**：Bridge 上 input box → yt-dlp 抓 metadata + caption → 跳 cast 表單 → 寫 watchlist entry
- **C. Vault markdown fallback**：`Watchlist/youtube/*.md` with `youtube_url:` frontmatter，沿用 Inbox seam pattern（`reading_source_lister.py:264` InboxKey convention 擴展）
- B. YouTube playlist sync / D. Browser extension：延後到 P2+，不在 first slice

Fallback 情境（地區封鎖 / age gate / private）：UI 顯示「caption fetch 失敗，可上傳本機 mp4」入口，銜接 Phase 2 Local Whisper。

### D5. Transcript anchor schema bump

`EvidenceAnchorKind` enum 加 `"timestamp_range"`：

```python
EvidenceAnchorKind = Literal[
    "chapter_quote",
    "section_quote",
    "frontmatter_field",
    "external_ref",
    "timestamp_range",  # NEW (schema_version=2)
]
```

具體 anchor 形態：

```python
EvidenceAnchor(
    kind="timestamp_range",
    source_path="Watchlist/youtube/{video_id}/transcript.vtt",
    locator="t=123.4-145.7",  # seconds float, optional end → 單點 t=123.4
    excerpt="<選中 cues 的 text concat>",
    confidence=...,
)
```

- **Locator format**: seconds float（對齊 YouTube `?t=` URL 慣例 + HTML5 `<video>.currentTime`）
- **Anchor 粒度**: cue-boundary snap（click cue / shift-click range），不做 char-level free select
- **Schema bump**: 任何 manifest 含 `timestamp_range` anchor → `PromotionManifest.schema_version=2`（同 V12 invariant entity items 條件，可共用版本號）

### D6. Reader UI = 3-quadrant 桌面 layout，inline annotation pane，N-key two-mode keyboard

> **2026-05-28 amendment**：v2 原訂 top-bottom stacked + modal-based annotation flow 在 2026-05-28 grill session 被反轉。Modal 中斷閱讀流；stacked 在桌面寬螢幕浪費橫向空間。改為 3-quadrant inline pane + N-key 模式切換。**v1 desktop-only**；PWA / portrait phone 支援延後到後續 ADR（responsive collapse 不在 first slice）。

**Layout**（v1 desktop-only）：

```
┌──────────────────────────────────────────────────────────────┐
│ Header (existing reader.html chassis)                        │
├────────────────────────────────────┬─────────────────────────┤
│                                    │                         │
│         Video player               │                         │
│         (top-left quadrant)        │   Cue list              │
│         [Speed: 1.5x ▼]            │   (right column,        │
│                                    │    full height,         │
│                                    │    scroll-sync)         │
├────────────────────────────────────┤                         │
│                                    │   [00:00] cue text      │
│   Inline annotation pane           │   [00:03] cue (active)  │
│   (bottom-left quadrant)           │   [00:07] cue text      │
│                                    │   ...                   │
│   ★ highlight | note editor        │                         │
│   speaker chip | save              │                         │
│                                    │                         │
└────────────────────────────────────┴─────────────────────────┘
```

- **左上**：video player + speed control
- **左下**：inline annotation pane（不是 slide-up modal）— 永遠存在，editor 空閒時顯示最近一筆 annotation 或空 state
- **右**：cue list 全高，scroll-sync 跟 `currentTime` 對齊

**Share 範圍**：design tokens、theme.js、header 樣式、"同步到 KB" button + flow、annotation 寫回路徑（ADR-017 `KB/Annotations/{annotation_key}.md`）

**新寫**：`av_reader.html` template（3-quadrant grid）、`av-reader.css`、`av-reader.js`（player ↔ cue sync + pane mode controller）、cue list component、cast chip selector、inline annotation pane component

**Annotation flow（inline pane + N-key）**：

1. 影片播放 → cue 隨 player `currentTime` highlight active（YT IFrame Player API onStateChange + polling）
2. Click cue / shift-click range → 選取 anchor（player 不暫停）
3. `Ctrl+B` 或 click ★ → highlight only（state 1，不開 editor）
4. `N` → enter editor mode：暫停播放 + focus 移到 note textarea + 顯示 speaker chip selector
5. 寫 note → `Ctrl+Enter` save → POST → 寫 ADR-017 store → exit editor mode → 播放回到 user 按 N 之前的狀態（仍暫停，user 自己決定 Space 繼續）
6. `Esc` 離開 editor 不存（discard draft）

**Capture frame 從 v1 移除（defer to Phase 2+ candidate）**：

YouTube IFrame Player API 不暴露 video element；`<canvas>.drawImage(iframe)` 因 cross-origin policy taint canvas、`toDataURL` / `toBlob` 拋 SecurityError。要做 frame capture 必須走 yt-dlp 抽 stream URL 換 `<video>` element — 違反 ToS（D8 / Negative 已聲明不可做），或走 server-side ffmpeg 抽 frame（額外 infra 重投資）。Health/Wellness slide/diagram 視覺 anchor 需求承認存在，但靠 `[mm:ss]` deep link + 修修自己回 YT 看那一段已足堪用。重評時機：Phase 2 Local Whisper 上線後若同步抽 audio stream，可順便決定要不要連 frame 一起抽。

**Keyboard mode-switch table**：

| Mode | Trigger | Keys active |
|---|---|---|
| **Player mode**（default） | 進入頁面 / `Esc` from editor | `Space` play/pause、`J / L` ±10s、`Ctrl+B` highlight only、`N` → enter editor |
| **Editor mode** | `N` from player | `Ctrl+Enter` save、`Esc` discard + back to player、textarea 接管文字輸入（`Space` 不再 play/pause） |

進 editor mode 一律暫停播放（避免邊打字邊跑 cue）。離開 editor mode 不自動續播（user 自決）。

**Playback speed control（v1 必需）**：0.75 / 1.0 / 1.25 / **1.5 (default)** / 1.75 / 2.0。修修認真吸收 podcast 預設走 1.5×。

### D7. Promote 單位三階段 incremental

| Phase | ReviewItem | KB 寫回 | Use case |
|---|---|---|---|
| **Phase 1 (first slice)** | `SourcePageReviewItem` | `KB/Sources/youtube_{video_id}.md` — frontmatter + annotation list with `[mm:ss] speaker` heading + excerpt blockquote + note + YT timestamp deep link | "我曾經看過哪個影片" |
| **Phase 2** | `EntityReviewItem` (Person) | `KB/Wiki/Entities/People/{name}.md` — speaker chip + annotation 內 mentioned Person 抽取，fast-track auto-promote | "哪個人在哪些影片講過 X"（**ADR-034 entity infra first real caller**） |
| **Phase 3** | `ConceptReviewItem` | `KB/Wiki/Concepts/{concept}.md` — 跨影片同 concept annotation 聚合 | "睡眠跟失智的關係，所有來源說過什麼" |

順序原則：A 必先（沒 source page，C/B 無 anchor）→ C 在 B 前（speaker chip 是 structured metadata，抽取近乎 trivial；concept 要 LLM call cost 高）→ B 視 Phase 1+2 用過實際 friction 決定要不要做。

Source page schema:

```markdown
---
type: source
source_kind: youtube_video
video_id: dQw4w9WgXcQ
title: ...
channel: ...
cast: [host, guest_a]
duration_s: 3600
url: https://youtube.com/watch?v=dQw4w9WgXcQ
---

# Title

## Annotations

### [00:23] host
> excerpt cue text

修修的 note。

[Watch on YT](https://youtube.com/watch?v=dQw4w9WgXcQ&t=23)

### [05:47] guest_a
...
```

### D8. Unified AV reader（design level），audio-only on demand only

`SourceKind` 加 `youtube_video`（v1）+ `podcast`（on demand）。同一 `av_reader.html` template，內部 conditional render `<video>` vs `<audio>` element。

但實際 ingestion：修修認真吸收 podcast 的動作 = 找 YouTube video 版本 + 1.5× 速度，pure-audio path 不在 active roadmap。Phase 2 ASR + audio ingestion 只在「真的遇到沒 video 版的 podcast 想存」時補。

Route 命名不滲入 kind：`/robin/watchlist/{source_id}`，不是 `/robin/video/` 或 `/robin/podcast/`。

### D9. Annotation primitives = 2-state model

Annotation 只有兩個合法 state，編碼在 UI primitive：

| State | Trigger | Persisted shape | Use case |
|---|---|---|---|
| **1. Highlight only** | ★ button / `Ctrl+B`（player mode） | anchor + 空 `note` field（or omitted） | 「這段重要，但我沒話想說 / 還沒時間寫」 |
| **2. Highlight + note** | `N` → enter editor → write → `Ctrl+Enter` | anchor + 非空 `note` field | 「這段重要，且我對它有想法」 |

**Note-without-highlight 不是合法 state**：所有 note 都 anchored 到 cue selection。不存在 free-floating note。修修要寫整部影片的總結請開獨立 Wiki 頁面 / Journal，不在 reader 內。

實作意涵：

- Editor mode 進入時 anchor 必須已選定（不能無 selection 開 editor）；若 user 按 N 但沒選 cue，UI behavior = silently noop 或 toast 提示「請先 click cue」
- 升級路徑：state 1 → state 2 = 對該 annotation 再按 N 進 editor 補 note，save 後 state 自動升級
- 降級路徑：state 2 → state 1 = 清空 note field save，treated as state 1
- 沒有 ★+note 同步操作捷徑（state 1 跟 state 2 是兩個動作，刻意 keep 簡單；避免 v2 modal flow 那種「Highlight only / Save」雙按鈕決策摩擦）

跟 ADR-017 annotation store 相容：`note` field 既有 optional 屬性已足夠表達 2-state，不需要新增 schema 欄位。

---

## Considered Options

### Rejected: 獨立 web app（React + videojs / react-player）

更成熟的 player ecosystem，但代價是 auth/design/deploy 全部 fork — 對 first slice 過重，違反「不過度設計」紀律。若未來 reader 真要抽出，現在 Bridge 落地會是好的 reference impl。

### Rejected: Obsidian plugin

脫離 Bridge promotion pipeline → 違背「reader 是 entity infrastructure 的 first real caller」這條 loop-close。Obsidian 內 video 播放體驗受限。

### Rejected: Cloud ASR API（Deepgram / Whisper API / AssemblyAI）

對 single-user 系統來說 long-term cost 比一次性 GPU 投資高；修修 self-host VPS + 本機都有 GPU，infra 已備。除非未來 latency 成 bottleneck，否則沒理由付雲端 mark-up。

### Rejected (v1): Auto diarization (pyannote)

v1 不做 — GPU job 工程量大；manual cast list + chip selector 對 single-user / podcast interview small-N cast 已堪用。

**未來重評為 suggestion layer**（panel review 2026-05-27 反方）：pyannote 不必當 "source of truth" 跟 manual override 二選一 — 可以當「pre-populate Speaker 1/2 chip」的 suggestion engine，user 一次性 map 到真名（Speaker 1 → "Andrew Huberman"），把 per-annotation 摩擦降到零。若 Phase 1 用一陣子發現 manual chip tapping 真的累，這條 path 是 candidate PR。不視為 ML 跟 manual 的競爭，是兩者協作。

### Rejected: Horizontal two-pane layout（player 左 / transcript 右）

Desktop 寬螢幕舒服，但 portrait phone 完全壞掉 — 跟 PWA 化路徑衝突。

**Revisited 2026-05-28, decision reversed.** §D6 v3 採 3-quadrant（horizontal-derived）layout。Rationale：(1) 修修實際使用 100% 桌面 — PWA portrait 是「未來想做」不是 first slice 必需；(2) v2 top-bottom stacked 在 24"/27" 螢幕橫向空間嚴重浪費（cue list 被擠到下半部窄欄）；(3) responsive collapse（desktop 3-quadrant → mobile stacked）是 CSS-only 工作，可在 PWA ADR 一起處理，不需要為了「未來可能 mobile」放棄當下桌面 UX。原 PWA-conflict 論點承認當時 over-weighted 假設性 portrait use case。

### Rejected: Floating mini-player + 全螢幕 transcript

YouTube/Twitch 級 UX，state machine 複雜，first slice 不背 floating player state。

### Rejected: Char-level free-select 在 transcript text 上

Video 的自然 unit 是 cue（2-5 秒 utterance），char-level 是過度精度；跨 cue boundary 要 reconstruct char offset → 解析跟 excerpt 邊界都麻煩。Cue-snap UI 極簡且 video player sync 自然對齊。

### Rejected: Cue index 作為 locator（`cue=5-7`）

WebVTT-native 但跨 caption variant（re-transcribe）就 break — 不耐久。Seconds float `t=` 跟 caption source decouple。

### Rejected: Browser extension ingestion (Option D)

最順手但要另起 build pipeline、跨 browser 維護成本高，URL paste 已涵蓋同樣 use case。

### Rejected: YouTube playlist sync（Option B）為 first slice

長期 high-value（auto-diff + 修修已有的 watching 行為直接餵進系統），但需要修修 maintain dedicated playlist + OAuth or unlisted feed 解析 — 不是 first slice 必需。P2/P3 候選。

### Rejected: 拆成獨立 video reader / podcast reader 兩個 vertical

90% overlap（互動模型、cue list、annotation、promote 路徑完全一致），10% 差別只在 source ingestion + 是否跑 ASR + media element。拆兩套 = 重複 code + UX 不一致風險。

---

## Consequences

### Positive

- ADR-034 v2 entity infrastructure 取得 first real caller（Person Entity 走 Phase 2 promotion）
- Bridge chassis（auth / design / pipeline）零 fork，PWA-ready
- Watchlist 擴展 Inbox seam，不另起 source ingestion 概念
- Reader UI 從文字 reader 自然演化（top-bottom stacked = scroll 主軸 + sticky media band）
- 三階段 promote 路徑跟 entity infra fast-track 機制天然對齊（修修標 speaker → confidence 高 → auto-promote）

### Negative / 風險

- **Schema bump (`schema_version=2`)** — manifest format 對 video evidence 不向下相容；既有 schema_version=1 manifest 仍 valid（只是不能含 timestamp_range anchor）。需要 migration 邏輯如未來想合併。
- **YouTube ToS / yt-dlp 風險** — yt-dlp 抓 caption 是 grey area（YouTube ToS §III.J 禁止 download content without permission），YouTube API 改動可能 break。Phase 2 Local Whisper 路徑需要 yt-dlp 拿 audio stream，灰色程度更高。**Single-user / personal 使用脈絡可接受，不可商業化 / 不可 distribute**。Fallback：UI 顯示 caption fetch 失敗 → 接 Local Whisper（Phase 2 dep）。
- **Auto-caption quality 變異** — 中文 auto-caption 標點稀疏 / 偶有錯字。修修自評：值得記下來的內容 **~98% 是英文**，中文 source 體驗差不在 v1 critical path。若未來中文需求變高再補 hybrid auto-detect ASR（panel review 2026-05-27 G1 deferred）。Escape hatch：UI re-transcribe with Whisper 按鈕（Phase 2 ship 時上）。
- **Cast list manual maintenance** — 修修每部 video 要填 cast；對 podcast interview 大宗 use case 是 small N（host + 1-2 guest）可接受，但若大量短 video 會累。觀察 Phase 1 實際使用再決定要不要加 cast inference seed。
- **Annotation key for video（ADR-017）** — 現有 annotation_key 規則對 video 的具體 mapping 待 PR1 定（候選：`youtube_{video_id}_t{start_ms}_{end_ms}`）。

### Neutral

- Foundry agent（ADR-032 script-driven video pipeline）跟本 ADR 不衝突 — Foundry 是 **output**（修修產 video），本 ADR 是 **input**（修修消費 video）。雙向都用 ASR 但 use case 互不依賴。
- Brook script_video 既有 Whisper infra（`agents/brook/script_video/`）為 Phase 2 reader ASR 可複用 — 共享 model checkpoint + GPU 排隊邏輯。

---

## Implementation sequencing

### PR1: Schema + watchlist ingestion + AV reader skeleton

- `EvidenceAnchorKind` 加 `"timestamp_range"`，`PromotionManifest.schema_version` 邏輯擴展
- `SourceKind` 加 `youtube_video`
- `Watchlist/youtube/{video_id}/` 目錄結構 + `manifest.json` schema
- yt-dlp ingestion route + cast 表單
- `av_reader.html` template + cue list + player band + speed control
- 不含 promotion / annotation save flow（純 read-only viewing）

### PR2: Annotation flow + ADR-017 integration

- Annotation modal partial（從 reader.html 抽 / 新寫）
- Cue-boundary snap selection
- Cast chip selector
- POST annotation → ADR-017 store
- Keyboard shortcuts

### PR3: Promote Phase 1 — SourcePage

- `KB/Sources/youtube_{video_id}.md` 寫回 schema
- Promotion review surface for video source（沿用 ADR-034 promotion_review UI，可能需要 entity arm 平行的 video arm）
- E2E: watchlist → annotate → promote → KB

### PR4: Promote Phase 2 — Person Entity（**closes ADR-034 loop**）

- Speaker chip → EntityCandidate
- Annotation note Person mention 抽取（LLM call，replaces DryRunEntityExtractor）
- EntityReviewItem fast-track integration

### PR5+ (deferred): Phase 3 Concept、playlist sync、Local Whisper、podcast audio-only

---

## Open questions

- ~~Annotation key naming convention for video（ADR-017 ext）— PR1 解~~ **Closed 2026-05-28**：`youtube_{video_id}` per PR1b resolver。每個 video 一個 annotation file，match 既有 book / article single-file-per-source pattern。Per-annotation 唯一 id 在 file 內以 heading + timestamp 區分。
- Player band 跟 transcript 視覺重心配比（Claude Design 視覺探索階段）— PR1 出視覺前 finalize（**v3 update**：改為 3-quadrant 比例調整 — video quadrant 跟 annotation pane 上下分割比、cue list 右欄寬度）
- "Highlight only" vs "Save with note" 兩按鈕 vs 一按鈕 + 空 note 等價 — **Closed by §D9**：2-state primitive（highlight only / highlight + note）已 lock
- YouTube embed iframe 還是 `<video>` + 抽 stream URL（後者違反 ToS）— PR1 確認；preferred 是 iframe + YouTube IFrame Player API（合規）
- ~~Cue 沒有句子等級切分時的 readability 處理（YT auto-caption 是 2-3 字一段，閱讀不流暢）~~ **Closed by PR #785**：sentence coalesce shipped — cue list render 階段把 2-3 字 fragment 聚合成句子等級 segment，selection unit 改 sentence-snap，underlying anchor 仍 seconds float。Panel review G8 風險點解掉。
- ~~Capture frame 的儲存 / 顯示細節~~ **Removed from v1 scope**（見 §D6 amendment）— YT IFrame cross-origin 讓 `<canvas>.drawImage(iframe)` 不可行，defer 到 Phase 2+ candidate（若走 server-side ffmpeg 或 Local Whisper 同步抽 stream 時再評估）。

---

## Status / next steps

- Status: **Accepted (v2)**
- Panel review: 2-way (Claude + Gemini) ran 2026-05-27. Codex 第三方 audit dispatch 卡在 stdin EOF（CLI deprecated flag 觸發 stdin fallback），改走 2-way。Audit verbatim 落於 `docs/research/2026-05-27-gemini-video-reader-adr-035-audit.md`
- Integration: Capture Frame button (G2 adopt) / Diarization rejection 改 suggestion-layer-future-consideration (G3 adopt) / Cast edit UI (G6 adopt) / ToS strengthened acknowledgement (G9 adopt) / Hybrid ASR (G1) 跟 Whisper-button-Phase-1 (G4) 因修修自評 ~98% 內容為英文而 drop / Horizontal responsive layout (G7) reject for PWA path / Cue-vs-sentence (G8) defer to PR1 observation
- **2026-05-28 v3 reversal**：§D6 layout 改 3-quadrant + inline annotation pane + N-key two-mode keyboard。Capture Frame (G2) 從 v1 移除（YT IFrame cross-origin 不可行，defer Phase 2+）。Horizontal layout (G7) 反轉採用（desktop-only v1，PWA responsive 延後）。Cue-vs-sentence (G8) closed by PR #785（sentence coalesce shipped）。Annotation primitives 鎖 2-state（§D9）。
- Next: PR1 (schema bump + watchlist ingestion + av_reader skeleton) implementation begins
