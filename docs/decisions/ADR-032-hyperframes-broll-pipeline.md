# ADR-032: Hyperframes-based Script-to-B-roll Pipeline

**Date:** 2026-05-25
**Status:** Accepted as amended by [ADR-050](ADR-050-video-production-line-brook-ownership.md)（2026-07-03 — §0.1 ownership 反轉：video production line 歸 Brook，機器遷移 `agents/brook/script_video/`，foundry 自 agent map 退場；其餘技術設計全數沿用。v2 原為 Draft 待 sign-off，實作已 ship，隨 ADR-050 裁決一併定案）
**Supersedes:** [ADR-015](ADR-015-script-driven-video-production.md)
**Related:** ADR-001（agent role）→ ADR-027（Brook narrow）/ ADR-014（RepurposeEngine — orthogonal）/ ADR-028（VAULT-LAYOUT）/ [ADR-033](ADR-033-thumbnail-generation-pipeline.md)（extends Hyperframes render layer to thumbnail stills, PR4 onward — see D10）

> **v1 → v2 change log**：v1 由 13-Q grill 凍結後跑 3-way panel (Codex GPT-5 + Gemini 2.5 Pro)。Panel verbatim audits 存在：
> - [`docs/research/2026-05-25-codex-adr032-audit.md`](../research/2026-05-25-codex-adr032-audit.md)
> - [`docs/research/2026-05-25-gemini-adr032-audit.md`](../research/2026-05-25-gemini-adr032-audit.md)
>
> v2 採納 11 項 panel push-back（見文末 §Panel Integration）。最大變動：
> 1. **新 agent `agents/foundry/`** 取代寄居 Brook（Gemini push-back，尊重 ADR-027 narrow Brook）
> 2. **Phase 1 UI 砍回 Tier 2**（無 inline player + polling，3 天估值守住；Tier 3 升級當 Phase 1.5）
> 3. **Mandarin normalization + LINE Seed TW @font-face 進 Phase 1**（不是 backlog）
> 4. **Anchor 改為 exact-copy mandatory + validator hard fail**（rapidfuzz 降級到 diagnostic）
> 5. **新 acceptance gate**：BigStat ~~MP4 hash snapshot~~ → **SSIM 視覺決定性測試**（2026-05-26 amend：byte hash 不可達成）+ DaVinci FCPXML import fixture
> 6. **Phase 1 dispatcher 只實作 Hyperframes**，reader/web-playwright workers schema-reserve 但 raise NotImplementedError（promote 進 main 才接通）
> 7. **Brand statement 重新 frame**：「talking head sacred」改為「保留原檔給 grade 用」
> 8. **單一 learning store**：edit_log 為主、examples 由 UI 一鍵 promote
> 9. **PR 估時上修**：9.5 天 → 13-17 天
> 10. **PR-1 範圍擴大**：promote BigStat 從 worktree 進 main + promote `web_highlight_record.py` 從 spike 進 main
> 11. **ADR-001 drift 修正**：本 ADR 不再說「Brook still Composer」；引用 ADR-027 narrow 後的 agent map，並 introduce 新 agent

---

## Context

### 為什麼重寫 ADR-015

ADR-015（2026-05-02）凍結了 Remotion + PyMuPDF + markdown DSL 的架構。三件事在這之後改變：

1. **Hyperframes 上線（HeyGen open-source, v0.6.42）** — HTML/CSS/GSAP 寫合成、Chrome headless 用 `renderSeek` 確定性 capture、FFmpeg 編碼。比 Remotion 多了現成 catalog（apple-money-count / data-chart / 14 caption styles / 14 shader transitions / 13 transition showcases…）+ 內建 lint/inspect/snapshot pipeline。2026-05-25 session **在 sibling worktree spike** 用它把 BigStat 從 zero 到 1080p mp4 ~30 分鐘走通（spike 路徑 `E:\nakama-hyperframes-bigstat\video\compositions\bigstat\`，**未 merge 進 main**）。
2. **Reader+Playwright path 已 spike 驗證**（2026-05-25 session 內 spike，**未 merge 進 main**）— `web_highlight_record.py` 通用工具：URL + 引用文字 → CDP screencast → 1080p mp4。spike 路徑 `E:\nakama-reader-record-spike\scripts\web_highlight_record.py`。對「書內引用」「網頁引用」這兩類 B-roll，比 PyMuPDF + bbox + DocumentQuote component 在概念上更乾淨（保留 Robin Reader 原始閱讀體驗 + 真實 EPUB layout），但 **promotion 進 main + Robin URL scheme 定義 + iframe recursion 都尚未完成**。PR #710（merged）寫的是 decision memory，不是 implementation。
3. **工作流順序重新釐清** — ADR-015 假設 record-first（先錄再 plan），但實際 workflow 是 **SRT-first**（已有 `/transcribe` skill 把 audio → 乾淨 SRT，這支 SRT 才是 broll pipeline 真正吃的 input）。Mistake removal 也獨立到 broll pipeline 之外。

### Agent map 現況（ADR-001 → ADR-027 後）

ADR-001 把 agent role 凍結，但 ADR-027（2026-05）把 Brook 從「Composer（all-in-one 內容組裝）」narrow 到 **「Scaffold + Repurpose + SEO Audit」三件事**。本 ADR 因此 **不寄居 Brook**，改 introduce 新 agent。詳見 §0.1。

### 本 ADR 的 grill + panel 出處

- **Grill**：2026-05-25 grill-with-docs session 13 個 Q 凍結（[memory/claude/MEMORY.md](../../memory/claude/MEMORY.md) 之外）
- **Panel**：2026-05-25 multi-agent-panel skill 跑 3-way audit（Claude draft → Codex 6-section audit → Gemini 6-section audit → Claude integrate）。整合矩陣見 §Panel Integration

### 沿用的 ADR-015 invariants

- **Composer 角色仍是 nakama agent map 一部分**（ADR-001 + ADR-027）— pipeline 不寄居 Brook，但仍尊重「nakama agent 各司其職」原則
- **保留原檔給 grade 用** — DaVinci V1 軌道吃修修原檔，broll pipeline **永不主動 re-encode talking head**。注意：DaVinci 在最終 export 階段套 LUT / grade 時會 re-encode；本 ADR 的 invariant 是「pipeline 不破壞 grade latitude」，**不是「talking head 永不被 encode」**（v1 措辭錯，v2 修正）
- **Output = FCPXML 1.10**（DaVinci / Premiere / Final Cut 三家原生）— Phase 1 不直出 mp4；版本選定見 §Risk「FCPXML 版本」
- **transcribe skill 重用** — 不改 transcribe，broll pipeline 接它 output
- **per-episode 自包含 `data/script_video/<episode-id>/`** — archive / 備份易

---

## Decision

### 0.1 新 agent `agents/foundry/`（取代寄居 Brook）

> **⚠️ Superseded by ADR-050（2026-07-03）**：修修裁決 video production line 歸 Brook — 本節機器整樹遷移 `agents/brook/script_video/`、foundry 自 agent map 退場、Bridge route 改 `/brook/video`。當年 panel 反對寄居的 monolith 顧慮以 sub-package 硬邊界緩解。本節以下保留為歷史脈絡。

ADR-027 narrow Brook 後，本 pipeline 的特性 — multi-worker render queue、realtime UI（Bridge route）、依賴 Hyperframes + Playwright 兩個重 stack、跨 episode learning corpus — 已**遠超 Brook 的 Scaffold/Repurpose/SEO Audit 範疇**。Gemini panel 直接點出：「forcing it into agents/brook/ risks bloating Brook into a monolith」。

**Decision**：introduce 新 agent **`foundry`**（取「鑄造廠」意，video production line）。

```
agents/foundry/                       # NEW — Video B-roll pipeline 專屬 agent
├── __init__.py
├── pipeline.py                       # 主 entry, asyncio orchestration
├── srt_flattener.py                  # SRT → flat text + char↔time index
├── chinese_normalizer.py             # NEW — Mandarin pre-processing layer（Phase 1）
├── planner.py                        # LLM call (Anthropic SDK) → beats
├── beat_aligner.py                   # exact substring match primary, rapidfuzz diagnostic only
├── render_dispatcher.py              # 3-path dispatch（Phase 1 只接通 hyperframes）
├── render_workers/
│   ├── hyperframes_worker.py         # ★ Phase 1
│   ├── reader_playwright_worker.py   # Phase 1.5 — raise NotImplementedError
│   └── web_playwright_worker.py      # Phase 1.5 — raise NotImplementedError
├── fcpxml_emitter.py                 # FCPXML 1.10（待 DaVinci 實測驗證版本）
├── layouts/                          # 命名 layout YAML
│   ├── full_aroll.yaml
│   ├── full_broll.yaml
│   ├── side_overlay_left.yaml        # Phase 1.5 才接
│   ├── side_overlay_right.yaml       # Phase 1.5
│   └── pip_corner_br.yaml            # Phase 1.5
├── prompts/
│   └── broll_planner.md              # canonical prompt template
├── STYLE.md                          # editorial rubric
├── guardrails.yaml                   # allow/deny lists
├── examples/                         # few-shot library（Phase 1 不載入，corpus ≥ 5 才啟用）
│   └── _index.yaml
└── edit_log/                         # ★ single learning store — UI 一鍵 promote 進 examples/
    └── <episode-id>.jsonl

.claude/skills/foundry-replan/
└── SKILL.md                          # thin wrapper — 對話內 patch 單一 beat 用
```

Brook 仍可在某 stage 呼叫 foundry（例如 ADR-014 RepurposeEngine 產 long-form script 後遞給 foundry 做 B-roll）— **invoke pattern，非 host pattern**。

**ADR-001 amendment 同步出**：agent map 增 `foundry` 條目，scope 限定「script + SRT → DaVinci-importable broll timeline」。

### 1. Render path = 3-way dispatcher schema, Phase 1 only Hyperframes 接通

每個 cutaway beat 帶 `render_target` 欄。Phase 1 schema-reserve 三個值，**實作只接 hyperframes**：

| render_target | 何時用 | Phase 1 實作 |
|---|---|---|
| `hyperframes` | 程式繪製的 component | ✅ 接通（BigStat from `video/compositions/bigstat/`） |
| `reader-playwright` | 書內引用 | ❌ Worker raise NotImplementedError；Phase 1.5 promote `web_highlight_record.py` + 定義 Robin URL scheme 後接通 |
| `web-playwright` | 外部網頁引用 | ❌ 同上 |

`broll_decision: none` 表「這 beat 不放 B-roll」，不是 path 之一。

**為什麼三條 sibling 不是統一 Hyperframes**：書/網頁引用本質是「保留原媒介 + 加 highlight 動畫」，Hyperframes 重畫 layout 會破壞「這引用真的來自 Robin 內這本書」的視覺契約。**詳細決定見 `memory/claude/project_broll_dual_path_architecture.md`（PR #710 已 merge 為 decision memory，不是 implementation）。**

### 2. 寄宿 — 已移至 §0.1

### 3. Talking head layout = FCPXML composite（沿用 invariant 但措辭修正）

#### 3a. Composite 在 DaVinci，不在 Hyperframes

`<ARollPip>` 之類「talking head 縮放 + B-roll 旁邊」layout，**用 FCPXML `adjust-transform` 在 DaVinci composite**，不進 Hyperframes：

```
V1: talking head 原檔（FCPXML adjust-transform 套縮放/位置 → DaVinci 看到 split layout）
V2: Hyperframes 渲染的 overlay mp4（1920×1080，broll_canvas 限制只在右半畫）
```

**「保留原檔給 grade 用」**（v2 修正）— DaVinci 在 final export 套 LUT / grade 時當然 re-encode；本架構 invariant 是 broll pipeline 不主動破壞 grade latitude，**不是「永不 encode」**。

#### 3b. ⚠️ FCPXML adjust-transform 單位 — 待 DaVinci 實測驗證

> Panel 強烈警告：Apple FCPXML `adjust-transform position` 不是 pixels，是相對 frame-height units（例如 `position="10 0"` ≠ 10px）。v1 寫的 `position_x: -480` 大概率錯。**Phase 1 acceptance gate：必先實測通過 DaVinci import fixture 才能 ship 任何 side layout。**

#### 3c. Layout = 命名 YAML recipe（slot-decoupled from content）

`agents/foundry/layouts/<name>.yaml`：

```yaml
# side_overlay_left.yaml (Phase 1.5 才實作 — Phase 1 只 seed YAML)
name: side_overlay_left
description: Talking head 縮左半，B-roll 落右半
slots:
  - id: aroll
    source: talking_head
    fcpxml_transform: <PENDING DAVINCI IMPORT FIXTURE>  # 不寫具體值待實測
  - id: broll
    source: broll_render
    fcpxml_transform: <PENDING>
    broll_canvas: { x: 960, y: 0, w: 960, h: 1080 }   # 給 Hyperframes 知道右半才畫
```

新 layout = 加 YAML file，emitter / planner / Hyperframes 都不動 code。

**Phase 1 ship 5 個 seed layout YAML，但只實作 `full_aroll` + `full_broll`**（無 transform 需求 → 不卡 DaVinci 實測）。side overlay / PiP Phase 1.5 補 + 同 PR 補 DaVinci import fixture。

### 4. Feedback = 3-action hybrid + auto edit-log + batch actions（v2 新增）

每個 beat 在 Bridge UI 上有三個 action：

| Action | UI | foundry 反應 | 寫 edit-log |
|---|---|---|---|
| **Approve** | 一鍵 | 標 `text_approved=true` → auto enqueue render | 否 |
| **Edit fields** | 展開 inline form | 直接覆寫 storyboard.yaml，auto-approved | 否 |
| **Re-plan with note** | 展開 textarea | LLM 用 note + 該 beat context 重 plan | **是** |

**v2 新增 batch actions**（panel 點出 solo user 不該 N 個 beat 各按一次）：

- `Approve All Text Drafts` — 批次標 `text_approved=true`，逐筆 enqueue render
- `Render All Approved` — 一鍵啟動所有 text_approved 但未 render 的 beat
- `Finalize All Passing Renders` — 看完所有 render，未明確拒絕的都 `visual_approved=true`

**第二層 visual approve** 走相同 3-action（render 完之後）：

- Approve / Edit fields / Re-plan with note 跟第一層同樣語意
- Re-plan with note **第二層**特別寫 edit-log（高品質信號，看到實際畫面才改）

**為什麼 Edit fields 不寫 edit-log**：純 mechanical 改 param 不是 taste 信號。

### 5. Input contract = SRT-first（含 Mandarin 正規化 — v2 提到 Phase 1）

```
data/script_video/<episode-id>/
├── episode.yaml                 # metadata（title, target_duration, refs map）
├── raw_recording.mp4            # talking head（已 mistake-cleaned）
├── transcript.srt               # /transcribe 輸出
├── refs.yaml                    # OPTIONAL: quote disambiguation
└── storyboard.yaml              # planner 輸出 + UI 編輯
```

**SRT 已乾淨**（NG take 上游已砍）— foundry 假設 input SRT 是真實時間軸 source of truth。Mistake removal **out of scope**（獨立工具，Q8 凍結）。

### 5b. Mandarin 文字正規化 layer（v2 新增 — Phase 1 必做）

> Panel（Gemini）強指出：anchor 對齊跟 LLM 抽取在中文有獨特挑戰，v1 完全沒寫。

`agents/foundry/chinese_normalizer.py` Phase 1 必含：

1. **標點正規化** — 全形 `，。？！「」` → 統一 canonical form；半形混合處理
2. **數字正規化** — `一萬一千` / `11000` / `11,000` / `一·一萬` → 統一 canonical（建議用 `cn2an` 或等價）。**這層處理過後 LLM 跟 aligner 看到的是統一格式**
3. **SRT cue 跨句合併** — 不是 naive `join(" ")`，要看句末標點 `。？！` 才斷句，否則 cue 之間以 `　`（全形空白）連接保留 prosody hint
4. **Quote bracket 強制** — Planner prompt 顯式要求台灣標準 `「」`；refs.yaml lookup 只認 `「」`，validator reject `"` / `"` / `''`

**這支 module 是 `srt_flattener` 跟 `planner` 之間的 hard boundary**。Phase 1 PR-2 必含。

### 5c. refs.yaml schema

```yaml
quotes:
  - text_anchor: "習慣是身分認同的形成"     # 正規化後字串
    book: "原子習慣"
    page: 87
    book_slug_robin: "atomic-habits"     # Phase 1.5 才用（Robin URL scheme 定義後）
```

Phase 1 dispatcher 不接 reader-playwright → `book_slug_robin` 純 metadata，**不真執行 lookup**。

### 6. Beat alignment = LLM exact-copy + Python validator hard fail（v2 大改）

> Panel（Codex + Gemini 一致 push-back）：v1 的 `rapidfuzz partial_ratio >= 0.85` 是錯誤的 primary 路徑。LLM 改字會穿過 fuzzy match。

**v2 重新設計**：

1. **Planner prompt 改 contract**：「你必須從附帶的 normalized transcript 中**完全複製貼上**作為 anchor，**禁止改字、改標點、改格式**」
2. **Planner LLM 輸出**：每 beat 帶 `start_quote` / `end_quote`，**仍是 8-12 字 anchor**（節省 token）
3. **`beat_aligner.py` 主路徑**：deterministic substring search（`str.find()`）；**找不到 → hard fail，回 LLM 重試該 beat（最多 3 次）**，仍對不到就 escalate human
4. **rapidfuzz 降級**：只在 **`--diagnostic-fuzzy` flag** 下啟用，列「相似但對不到的候選」供 debug 使用，**永不當 production fallback**

正規化過後的文本是 stable canonical form，LLM 拒絕 exact-copy 是 prompt bug 或 model 行為錯，**應該 fail loud 而非 silently fuzzy 通過**。

```python
# beat_aligner.py 核心
def align_beat(beat, flat_text, char_to_time):
    start_idx = flat_text.find(beat['start_quote'])
    end_idx = flat_text.find(beat['end_quote'], start_idx)
    if start_idx == -1 or end_idx == -1:
        raise AnchorNotFoundError(beat, flat_text)  # hard fail
    timing = {
        'start': char_to_time[start_idx],
        'duration': char_to_time[end_idx + len(beat['end_quote'])] - char_to_time[start_idx],
    }
    return timing
```

### 7. UI = Thousand Sunny Bridge **Tier 2**（v2 改）

> Panel 一致：Tier 3 三天估時是 fantasy。Tier 2 是 Phase 1 sweet spot。

`/foundry/<episode-id>` route（Brook 改名 → foundry）。

**Phase 1 Tier 2 必做**：
- 表格 render storyboard.yaml
- 三個 action 按鈕 + batch actions（Approve All Text / Render All Approved / Finalize All Passing）
- Render 狀態用 **polling**（每 2-3 秒 GET status endpoint），不上 SSE
- 兩層 status chip：`📝 text_approved` `🎬 render_status` `✅ visual_approved`
- **沒有 inline `<video>` player** — render 完的 mp4 用 file:// 連結（修修自己用系統 player 開）

**Phase 1.5 升 Tier 3**：
- SSE 換掉 polling
- Inline `<video>` 嵌入每 row（含 streaming endpoint + CORS）
- 拆/合 beat 按鈕

**Phase 2 評估 Hyperframes Studio iframe** — 如果 Studio 已能 preview composition + 接 props，可能直接 embed iframe 取代部分 inline player 工。Phase 1 不評估。

### 8. Render trigger = approve auto-enqueue + batch actions（v2 合併）

```
text_approved=true → 自動 enqueue render task
                  → render done → render_status=done → polling 更新 UI
                  → 看完 → visual_approved=true → beat finalized

batch action「Approve All Text」 → bulk set text_approved + bulk enqueue
batch action「Render All Approved」 → re-trigger 已 approved 但 render_status=pending 的
batch action「Finalize All Passing」 → bulk visual_approved（修修可逐筆 override 個別）

任何時候 re-plan with note：
  → cancel in-flight render (kill subprocess)
  → 回 status=draft
  → LLM 重 plan
```

**Render concurrency = 1 (Phase 1)**（v2 改保守 — panel 指出 Hyperframes worker 內含多 Chrome process + FFmpeg + 可能 Playwright，concurrency=2 是樂觀估計。先 = 1，measure 後再 raise）。**Phase 1 ≤ 25 個 beat × 10s/beat ≈ 4-5 分鐘串行**，可接受。

**GPU semaphore** — 未來 hyperframes / playwright 混合 dispatch 時需要 cross-worker semaphore，**留 Phase 1.5 上來**。

### 9. Style / brand 文件結構 — single-store learning（v2 簡化）

| 文件 | 位置 | 用途 |
|---|---|---|
| Brand tokens | `docs/design-system.md`（既有） | `--sho-*` tokens、LINE Seed TW、PANTONE 165 PC。Planner load 這個，**不複製** |
| Editorial rubric | `agents/foundry/STYLE.md` | broll 編輯 rubric（do/don't） |
| Guardrails | `agents/foundry/guardrails.yaml` | allow/deny machine-readable |
| Few-shot | `agents/foundry/examples/` | **Phase 1 不載入** — corpus ≥ 5 才啟用 retrieval |
| Edit log | `agents/foundry/edit_log/<episode>.jsonl` | **★ single source of truth for learning** — UI 提供「promote to example」一鍵操作 |

> Panel（Gemini）正確指出：solo user 不會維護三個 store。v2 改為「edit_log 是主，example 由 UI 一鍵升級」。STYLE.md 仍存在但變成「定期人類手動 review edit_log 後提煉的固化規則」，不是並行 store。

### 9b. LINE Seed TW 字型 — Phase 1 必處理

> Panel（Gemini）指出：Hyperframes headless Chrome 預設無 LINE Seed TW → fallback Noto Sans TC（不同字寬 / kerning，破壞 brand）。

**Phase 1 PR-4 必含**：
1. 下載 LINE Seed TW woff2 進 `video/assets/fonts/`
2. 每個 Hyperframes composition `<head>` 加 `@font-face` 指向 local woff2
3. Hyperframes `lint` 通過（不再 warn `font_family_without_font_face`）

---

## Acceptance Criteria（Phase 1，v2 強化）

### Functional

- [ ] 跑 `python -m agents.foundry --episode test-fixture-001` 從 SRT + raw_recording 開到 storyboard.yaml，全程無錯
- [ ] Bridge UI `/foundry/test-fixture-001` 渲染表格，三個 action + 三個 batch action 都能觸發對應 endpoint
- [ ] Approve 一個 BigStat beat → polling 顯示 rendering → done 後 file:// 連結可開 mp4
- [ ] Re-plan with note 觸發 LLM 重 plan，storyboard.yaml 換 proposal，edit_log 多一條
- [ ] 修修真實一集（10-15min 含 3-5 個 BigStat beat）走通 end-to-end，產出 .fcpxml + 個別 .mp4

### Determinism / Visual

> **Amended 2026-05-26**: 原訂「MP4 byte hash 一致」實測**不可達成** — Hyperframes 0.6.42 H.264 encoder 多執行緒非決定性，default mode 與 `--docker` mode 都產生不同 byte/stream/pixel hash。但兩次 render 之間 **SSIM ≥ 0.9997**（empirical, 2026-05-26 Windows host）。Acceptance 改為**視覺決定性 SSIM ≥ 0.99**，並保留 byte 等級為 `xfail` 試金石以追蹤 upstream 修補。

- [x] **BigStat visual determinism test** — 同 input 兩次 render，FFmpeg SSIM ≥ 0.99（floor）／實測 ≥ 0.9997
- [x] **BigStat structural determinism** — 同 input 兩次 render，frame count + duration + 解析度完全一致
- [ ] **Mandarin normalization regression** — 9 個 test fixture（全形/半形標點、4 種數字格式、`「」`、SRT cue 跨句）pytest 全綠

### FCPXML / DaVinci 兼容

- [ ] **DaVinci import fixture** — 寫一支極簡 FCPXML（含 V1 talking head + V2 BigStat mp4 + 1 個 adjust-transform），手動 import DaVinci Resolve 修修現用版本，確認：
  - clip 出現在正確 timeline 位置
  - transform 視覺效果符合預期
  - 無 schema error 警告
- [ ] **FCPXML version 確認** — Phase 1 試 1.10 → 失敗則 fallback 1.11 或 1.9。emitter 加 `--fcpxml-version` flag

### Mandarin / Font

- [ ] LINE Seed TW @font-face 加入 BigStat composition；Hyperframes `lint` 無 font warning
- [ ] BigStat render 視覺檢查：中文字使用 LINE Seed TW，不 fallback

---

## Phase 1 PR slicing（v2 上修）

| PR | 範圍 | v1 估時 | **v2 估時** | 依賴 |
|---|---|---|---|---|
| **PR-1** | ADR-032 + `agents/foundry/` scaffold + 5 layout YAML + STYLE.md/guardrails.yaml seed + episode dir convention + **promote BigStat from spike worktree** + **promote `web_highlight_record.py` from spike** + ADR-001 amendment (add foundry) | 1d | **2d** | — |
| **PR-2** | SRT flattener + **chinese_normalizer.py + 9 test fixtures** + char↔time index + beat_aligner (exact-copy primary) + AnchorNotFoundError + unit test | 1.5d | **2.5d** | PR-1 |
| **PR-3** | planner.py + prompt template + storyboard.yaml schema + integration test on fixture | 2d | **2d** | PR-2 |
| **PR-4** | render_dispatcher（hyperframes only）+ hyperframes_worker（接 BigStat）+ **LINE Seed TW @font-face** + **BigStat hash snapshot test** + FCPXML 1.10 emitter + **DaVinci import fixture (blocking)** + reader/web-playwright workers stub raise NotImplementedError | 2d | **4d** | PR-3 |
| **PR-5** | Thousand Sunny Bridge UI Tier 2（table + 3 actions + 3 batch actions + polling status + 2-layer status chip）+ endpoint wiring + edit_log writer + edit_log → example promote UI | 3d | **3d** | PR-4 |

**v1 合計**：9.5 天 → **v2 合計：13.5 天**

> Codex 認為 PR-4 可能 4-6 天、PR-5 5-7 天。v2 取保守中位。Tier 2 守住 PR-5 3 天的關鍵是「無 SSE / 無 inline player / 無拆合 beat」— 三個都砍了。

---

## Phase 1.5 / Phase 2 backlog

**Phase 1.5（~1 週）**：
- **Reader + Web Playwright workers 真接通**（Robin URL scheme 定義 + iframe recursion）
- 加 `side_overlay_left/right` + `pip_corner_br` layout 實作（同 PR 補 DaVinci transform fixture）
- 加 TransitionTitle component（Hyperframes `caption-kinetic-slam` + `transitions-cover`）
- UI 升 Tier 3：SSE + inline `<video>` + 拆/合 beat
- Edit_log examples 累 ≥ 5 → 啟用 tag-filter retrieval
- GPU semaphore（cross-worker resource cap）

**Phase 2（無時程）**：
- DataChart / Map / Caption / Transition components
- Embedding-based examples retrieval（如 corpus > 100）
- Multi-episode listing + history view
- Mistake-cleanup 獨立 skill（如真要自動化拍掌 marker）
- Hyperframes Studio iframe embedding 評估
- `--direct-mp4` flag 跳 FCPXML（修修發現「進 DaVinci 從沒真的改什麼」時啟用）

---

## Consequences

### 立即影響

1. **新 agent `agents/foundry/`** — 新 Python 模組樹（規模 ~10 個 file）
2. **新依賴** — `rapidfuzz` (diagnostic only) + `PyYAML` + `cn2an`（中文數字正規化）+ `anthropic` SDK（既有）
3. **既有 `video/compositions/bigstat/`** — Phase 1 PR-1 從 worktree promote 進 main，**成為 first-class component**
4. **`web_highlight_record.py`** — Phase 1 PR-1 從 spike sibling 目錄 promote 進 main（`agents/foundry/lib/web_highlight_record.py` 或 `shared/web_highlight_record.py`，PR-1 內決定）
5. **新 Thousand Sunny route `/foundry/<episode-id>`**
6. **新 `data/script_video/` 樹結構** — per-episode + edit_log
7. **新 CLI** — `python -m agents.foundry --episode <id>`
8. **新 skill `.claude/skills/foundry-replan/SKILL.md`** — 對話內 patch 用
9. **ADR-001 amendment** — 加 foundry 條目
10. **PR #710 memory** 是 decision precedent，但 implementation 沒進 main —— v2 不再聲稱「shipped」

### 對既有 ADR 的影響

| ADR | 影響 |
|---|---|
| **ADR-015** | **Superseded** |
| **ADR-001** | **Amend** — agent map 加 foundry 條目 |
| ADR-027（Brook narrow） | 無變動。本 ADR 尊重 ADR-027 的 narrow，新建 foundry |
| ADR-013（transcribe）| 無 |
| ADR-014（RepurposeEngine）| 無。可能未來在 Brook fan-out script 後 invoke foundry，但 contract 由 Brook 拉動，foundry 不知道誰呼叫 |
| ADR-028（VAULT-LAYOUT）| 無 |

### 風險（v2 增補）

| 風險 | 機率 | mitigation |
|---|---|---|
| Hyperframes catalog block 跟 nakama design tokens 衝突大 → BigStat 級客製需逐個 fork | 中 | 已 spike 驗證 BigStat fork OK；token 集中 `docs/design-system.md` |
| LLM 自然分群 beat 太細 / 太粗 | 中 | Phase 1.5 拆合 UI；Phase 1 用 1-2 集找出 prompt 該怎麼 tune |
| **LLM 拒絕 exact-copy anchor**（改字、加標點） | 中 | exact-copy 必須 + AnchorNotFoundError hard fail + LLM 最多 3 次 retry → escalate human |
| **Mandarin 正規化未覆蓋 corner case**（粵語拼音、注音、罕用全形數字） | 中 | 9 test fixture 起手，發現缺再補；fail loud（normalizer 不認的 raise） |
| **FCPXML 1.10 在 DaVinci Resolve 修修版本 import 失敗**（Apple 規範 vs DaVinci 實作有偏） | 中 | Phase 1 PR-4 必過 DaVinci import fixture；不通則 emitter 加 `--fcpxml-version` flag 試 1.11 / 1.9 |
| **FCPXML adjust-transform 單位錯**（v1 寫的 pixel 大概率非 frame-height units） | 高 | Phase 1 不 emit transform；Phase 1.5 補 side overlay 時用 fixture 確定單位才 ship |
| **Hyperframes determinism 跨 Chrome 版本** | 中 | ~~BigStat hash snapshot test~~ → SSIM 視覺決定性測試（byte 等級不可達成，2026-05-26 amend）+ pin Chrome 版本（hyperframes browser 自管） |
| **Hyperframes v0.6.42 → v0.7+ breaking changes** | 低-中 | `video/package.json` 強 pin `"hyperframes": "0.6.42"`；CI 加 install audit |
| **LINE Seed TW @font-face capture 失敗** | 低 | spike 已知 Noto Sans TC fallback 視覺接近；fail loud + dev 手動 verify |
| **Robin Reader URL scheme 未定義導致 Phase 1.5 卡住** | 中 | Phase 1.5 第一 PR 必先定義 scheme + write fixture |
| **修修錄影沒做 mistake-cleanup → SRT timing vs raw mp4 duration 不一致** | 中 | foundry pipeline 開頭加 sanity check：SRT 最後 timestamp vs mp4 duration 差 ≥ 1s → warn |
| **Render concurrency=1 太慢** | 低 | Phase 1 BigStat ~10s/支，25 beat 串行 ~4 分鐘可接受；measure 後 Phase 1.5 raise + GPU semaphore |
| **Tier 2 UI（無 inline player）讓 visual review 麻煩** | 中 | 是預期 trade-off。Phase 1.5 升 Tier 3 補 |

### 不變項

- `transcribe` skill SKILL.md / 觸發詞 / pipeline 完全不動
- Robin KB 結構不動（Phase 1 只 read metadata；Phase 1.5 才用 URL scheme 真載書）
- ADR-014（RepurposeEngine）不動
- 修修既有 DaVinci project template / preset / 字型 / 配色全照舊（FCPXML 只標 clips + transform，不強加 styles）
- `docs/design-system.md` 唯一 brand source of truth

---

## Alternatives Considered

| Alternative | 為何沒選 |
|---|---|
| **Remotion**（ADR-015 原選） | catalog 空（要從零寫），不如 Hyperframes 起手快 |
| **PyMuPDF + bbox DocumentQuote**（ADR-015 原選） | 失去 Robin Reader 視覺契約；Reader+Playwright 更乾淨 |
| **Markdown DSL input**（ADR-015 原 Q1） | 要修修學新 syntax；SRT-first + Brook 自判 |
| **Record-first** | 創意 review 卡在錄影；SRT-first 解耦 |
| **Hyperframes-baked layout** | 破 invariant「保留原檔給 grade 用」 |
| **Batch render（all-approve-then-render only）** | Phase 1 Tier 2 已加 batch action，per-beat auto-render 仍是 default |
| **Single approve（不分文字/影片兩層）** | 失去早攔截 + 視覺修正兩個價值 |
| **DESIGN.md 獨立 brand 文件** | 跟 `docs/design-system.md` drift |
| **STYLE.md / edit_log 進 `/memory/`** | 混淆 agent identity feedback vs pipeline asset |
| **寄居 Brook（v1 選）** | ADR-027 narrow 後 Brook 不該再擴張；v2 改 foundry |
| **rapidfuzz primary（v1 選）** | LLM 改字穿過 fuzzy；v2 改 exact-copy + hard fail |
| **Tier 3 UI Phase 1（v1 選）** | 3 天估時 fantasy；v2 砍 Tier 2 + Phase 1.5 升 |
| **WebVTT 取代 SRT input** | `/transcribe` 出 SRT 是既定 contract；雙格式 maintain 成本不值 |
| **整合 Edit fields + Re-plan into single "modify"** | 兩 action 的 taste signal 性質不同，混合損失 learning |
| **Hyperframes Studio iframe Phase 1** | 待 auth/file access 評估；Phase 2 |
| **LLM-generated DSL as internal IR** | YAML cleaner long-term；多一層 translation 無必要 |
| **examples cold-start retrieval Phase 1** | corpus 空 = 純浪費 prompt token |

---

## Open Questions（不阻擋落地）

1. **foundry-replan skill 觸發詞** — Phase 1 後決定（`/foundry-replan` vs `/foundry-fix-beat`）
2. **edit_log retention policy** — Phase 2 看修修一年累積量再決定
3. **Examples retrieval embedding switch** — corpus > 100 + tag retrieval 出問題才升
4. **Streaming render**（hyperframes streaming-encode）— Phase 2 評估
5. **Caption sync 用 SRT 還是 word-level JSON** — Phase 2 caption 動畫時決定
6. **FCPXML 1.10 vs 1.11 vs 1.9** — Phase 1 PR-4 DaVinci fixture 結果決定 default

---

## Panel Integration（v2 新增 — 3-way audit 採納記錄）

3-way panel：Claude draft → Codex (GPT-5) audit → Gemini 2.5 Pro audit → Claude integrate。整合矩陣：

| Topic | Claude v1 | Codex | Gemini | Pattern | v2 Resolution |
|---|---|---|---|---|---|
| Code grounding（BigStat / web_highlight_record.py 在 main） | shipped | False，in spike | （無 disagreement） | 2-of-3 against v1 | **採納** — v2 改字眼「spike validated」+ PR-1 promote 進 main |
| ADR-001 drift（Brook 仍 Composer） | Yes | False，已 ADR-027 narrow | （未提） | Codex 獨家 | **採納** — v2 改寄居為 new `agents/foundry/` |
| Pipeline 寄居 Brook vs 新 agent | Brook | Brook OK | **新 agent foundry** | 1-of-3 dissent | **採納 Gemini** — 尊重 ADR-027 spirit |
| rapidfuzz 0.85 primary | Yes | False，exact-copy primary | 同 + Chinese 更難 | Universal against v1 | **採納** — exact-copy + hard fail，fuzzy 降 diagnostic |
| UI Tier 3 Phase 1 3 天 | Yes | False，5-7d 或砍 Tier 2 | 同 + 加 batch | Universal against v1 | **採納** — 砍 Tier 2，加 batch actions |
| Mandarin 正規化 | 未提 | 未提 | 必做 Phase 1 | Gemini 獨家 | **採納** — chinese_normalizer.py 進 PR-2 |
| LINE Seed TW @font-face | Phase 2 | 未提 | 必做 Phase 1 | Gemini 獨家 | **採納** — 進 PR-4 |
| FCPXML transform 單位 | pixel-like | Apple 規範非 pixel | 同 | Universal | **採納** — v2 加 DaVinci import fixture acceptance gate |
| Cold-start examples retrieval | Yes | No | No | Universal against v1 | **採納** — Phase 1 不載入，corpus ≥ 5 才啟 |
| 2-layer approve UX | per-beat only | 加 batch | 加 batch + 簡化 | Universal | **採納 batch actions** |
| 3-store learning corpus | Yes | 未強烈反對 | Will atrophy → single store | Gemini 獨家 | **採納** — edit_log 主，example UI promote |
| Talking head 永不 re-render | Yes | 未質疑 | DaVinci 出片仍 encode → misframe | Gemini 獨家 | **採納** — v2 改「保留原檔給 grade 用」 |
| FCPXML 1.10 預設安全 | 預設 | 未質疑 | DaVinci 兼容性歷史不穩 | Gemini 獨家 | **採納** — emitter 加 version flag，PR-4 fixture 驗 |
| Render concurrency=2 | Yes | 太樂觀 | 同 + GPU semaphore needed | Universal against v1 | **採納** — Phase 1 改 = 1，semaphore Phase 1.5 |
| Robin URL scheme | 未提 | 未提 | Phase 1 feasibility 問題 | Gemini 獨家 | **採納** — Phase 1.5 第一 PR 先定義 |
| Hyperframes 版本 pin | 未提 | 未提 | 必 pin + 視覺 regression test | Gemini 獨家 | **採納** — package.json 強 pin + hash snapshot |
| Mistake-cleanup 假設 | upstream done | 同 | brittle, need duration sanity | Gemini 獨家 | **採納** — pipeline 開頭加 SRT vs mp4 duration check |
| WebVTT 替代 SRT | 未評估 | 應評估 | 未提 | Codex 獨家 | **Reject** — `/transcribe` contract 既定 |
| Edit fields + Re-plan 合併 | 分離 | 合併 | 未提 | Codex 獨家 | **Reject** — taste signal 性質不同 |
| Hyperframes Studio iframe | 未評估 | 應評估 | 未提 | Codex 獨家 | **Defer Phase 2** |
| Pre-segment in transcribe | 未評估 | 應評估 | 未提 | Codex 獨家 | **Defer** — `/transcribe` skill 自有 contract，本 ADR 不擴 |
| LLM-generated DSL IR | 未評估 | 應評估 | 未提 | Codex 獨家 | **Reject** — YAML cleaner |

**統計**：採納 17 項；reject 4 項；defer 2 項。

---

## References

- [`docs/research/2026-05-25-codex-adr032-audit.md`](../research/2026-05-25-codex-adr032-audit.md) — Codex GPT-5 6-section audit verbatim
- [`docs/research/2026-05-25-gemini-adr032-audit.md`](../research/2026-05-25-gemini-adr032-audit.md) — Gemini 2.5 Pro 6-section audit verbatim
- [`memory/claude/project_broll_dual_path_architecture.md`](../../memory/claude/project_broll_dual_path_architecture.md) — PR #710，3-path decision memory
- [`memory/claude/feedback_cdp_screencast_over_recordvideo.md`](../../memory/claude/feedback_cdp_screencast_over_recordvideo.md) — CDP screencast 選擇
- [Hyperframes v0.6.42 docs](https://hyperframes.mintlify.app/llms.txt)
- ADR-015 (Superseded) — 保留 Remotion → Hyperframes 改變脈絡
- ADR-027 (Brook narrow) — foundry decision 的前置
- 2026-05-25 grill-with-docs session 13 Qs + multi-agent-panel skill 3-way audit
