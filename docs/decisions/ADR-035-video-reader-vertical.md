# ADR-035: Video Reader vertical + AV reader architecture

**Date:** 2026-05-27
**Status:** Proposed

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
- Cast 可在 source metadata page 改正
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

### D6. Reader UI = top-bottom stacked，share Bridge chassis

**Layout**：

```
┌──────────────────────────────────────┐
│ Header (existing reader.html chassis)│
├──────────────────────────────────────┤
│                                       │
│        Sticky player band            │
│        (~40vh on desktop)            │
│        [Speed: 1.5x ▼]               │
│                                       │
├──────────────────────────────────────┤
│  [00:00] cue text ...                │
│  [00:03] cue text ... (active)       │
│  [00:07] cue text ...                │
│  ...                                  │
│  (transcript cue list, scroll)       │
│                                       │
└──────────────────────────────────────┘
```

**Share 範圍**：design tokens、theme.js、header 樣式、"同步到 KB" button + flow、annotation 寫回路徑（ADR-017 `KB/Annotations/{annotation_key}.md`）、annotation modal 抽 `_annotation_modal.html` partial 共用

**新寫**：`av_reader.html` template、`av-reader.css`、`av-reader.js`（player ↔ cue sync controller）、cue list component、cast chip selector

**Annotation flow**：

1. 影片播放 → cue 隨 `<video>.currentTime` highlight active
2. Space 暫停 → click cue / shift-click range
3. Bottom slide-up annotation modal：speaker chip + note + [Highlight only / Save]
4. Save → POST → 寫 ADR-017 annotation store
5. Space 繼續播放

**Keyboard parity（跟 reader.html）**：
- `Space` = play/pause
- `J / L` = ±10s seek (YouTube convention)
- `Ctrl+B` = highlight 不開 note
- `Ctrl+Shift+C` = highlight + 開 annotation modal

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

---

## Considered Options

### Rejected: 獨立 web app（React + videojs / react-player）

更成熟的 player ecosystem，但代價是 auth/design/deploy 全部 fork — 對 first slice 過重，違反「不過度設計」紀律。若未來 reader 真要抽出，現在 Bridge 落地會是好的 reference impl。

### Rejected: Obsidian plugin

脫離 Bridge promotion pipeline → 違背「reader 是 entity infrastructure 的 first real caller」這條 loop-close。Obsidian 內 video 播放體驗受限。

### Rejected: Cloud ASR API（Deepgram / Whisper API / AssemblyAI）

對 single-user 系統來說 long-term cost 比一次性 GPU 投資高；修修 self-host VPS + 本機都有 GPU，infra 已備。除非未來 latency 成 bottleneck，否則沒理由付雲端 mark-up。

### Rejected: Auto diarization (pyannote)

中文 podcast 質量不穩；GPU job 工程量大；而修修自己在 annotation 上標的 speaker name 就是 canonical label，比 ML 抽到 "speaker_1" 再人工 reconcile 準。Manual override 在這個用例 strictly dominates ML。Escape hatch 留著：未來真的 highlight 量大到 manual 不及，再補 pyannote pre-fill chip default（manual override win）。

### Rejected: Horizontal two-pane layout（player 左 / transcript 右）

Desktop 寬螢幕舒服，但 portrait phone 完全壞掉 — 跟 PWA 化路徑衝突。

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
- **YouTube ToS / yt-dlp 風險** — yt-dlp 抓 caption 是 grey area，YouTube API 改動可能 break。Fallback：UI 顯示 caption fetch 失敗 → 接 Local Whisper（Phase 2 dep）。
- **Auto-caption quality 變異** — 中文 auto-caption 標點稀疏 / 偶有錯字；修修主要 use case 是英文 talk/podcast，quality 普遍 OK，但中文 source 體驗會差。Escape hatch：UI re-transcribe with Whisper 按鈕。
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

- Annotation key naming convention for video（ADR-017 ext）— PR1 解
- Player band 跟 transcript 視覺重心配比（Claude Design 視覺探索階段）— PR1 出視覺前 finalize
- "Highlight only" vs "Save with note" 兩按鈕 vs 一按鈕 + 空 note 等價 — PR2 grill
- YouTube embed iframe 還是 `<video>` + 抽 stream URL（後者違反 ToS）— PR1 確認；preferred 是 iframe + YouTube IFrame Player API（合規）
- Cue 沒有句子等級切分時的 readability 處理（YT auto-caption 是 2-3 字一段，閱讀不流暢） — PR1 後觀察

---

## Status / next steps

- Status: **Proposed**
- Next: `multi-agent-panel` review (Codex + Gemini audit) before PR1 ships
- Trigger justification: architectural lock-in（schema bump、reader UI architecture、Watchlist ingestion seam）+ numerical claims（Whisper 0.1× realtime、playback rate 1.5× default）+ strong rejection of multiple valid alternatives（horizontal layout / cloud ASR / char-select / pyannote）

After panel review + integration → flip to **Accepted** → PR1 implementation begins.
