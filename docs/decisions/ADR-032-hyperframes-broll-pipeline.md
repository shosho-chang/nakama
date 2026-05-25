# ADR-032: Hyperframes-based Script-to-B-roll Pipeline

**Date:** 2026-05-25
**Status:** Draft（pending multi-agent-panel review）
**Supersedes:** [ADR-015](ADR-015-script-driven-video-production.md)
**Related:** ADR-001（agent role）/ ADR-014（RepurposeEngine — orthogonal）/ ADR-028（VAULT-LAYOUT）

---

## Context

### 為什麼重寫 ADR-015

ADR-015（2026-05-02）凍結了 Remotion + PyMuPDF + markdown DSL 的架構。三件事在這之後改變：

1. **Hyperframes 上線（HeyGen open-source, v0.6.42）** — HTML/CSS/GSAP 寫合成、Chrome headless 用 `renderSeek` 確定性 capture、FFmpeg 編碼。比 Remotion 多了現成 catalog（apple-money-count / data-chart / 14 caption styles / 14 shader transitions / 13 transition showcases…）+ 內建 lint/inspect/snapshot pipeline。今天 2026-05-25 session 用它把 BigStat 從 zero 到 1080p mp4 ~30 分鐘走通。
2. **Reader+Playwright path 已驗證**（本 session 2026-05-25 內 ship）— `web_highlight_record.py` 通用工具：URL + 引用文字 → CDP screencast → 1080p mp4。對「書內引用」「網頁引用」這兩類 B-roll，比 PyMuPDF + bbox + DocumentQuote component **正確得多**（保留你 Robin Reader 原始閱讀體驗 + 真實 EPUB layout）。
3. **工作流順序重新釐清** — ADR-015 假設 record-first（先錄再 plan），但實際 workflow 是 **SRT-first**（已有 `/transcribe` skill 把 audio → 乾淨 SRT，這支 SRT 才是 broll pipeline 真正吃的 input）。Mistake removal 也獨立到 broll pipeline 之外。

### 本 ADR 的 grill 出處

本 ADR 從 2026-05-25 grill-with-docs session 凍結。13 個 Q 結論摘要見 §Decision。修修在 grill 內明確 delegate 多項決定，本 ADR 記錄 reasoning + alternatives 給未來 reader 看（避免「修修同意過」變成 unaccountable 決定）。

### 沿用的 ADR-015 invariants

- **Brook 仍是 Composer**（ADR-001）— pipeline orchestrator 寄居 `agents/brook/script_video/`
- **Talking head sacred** — DaVinci V1 軌道吃修修原檔，**永不 re-render**（color grade / 重剪不靠 broll pipeline）
- **Output = FCPXML 1.10**（DaVinci / Premiere / Final Cut 三家原生）— Phase 1 不直出 mp4
- **transcribe skill 重用** — 不改 transcribe，broll pipeline 接它 output
- **per-episode 自包含 `data/script_video/<episode-id>/`** — archive / 備份易

---

## Decision

### 0. 整體架構圖

```
┌─────────────────────────────────────────────────────────────────────┐
│                       UPSTREAM (out of scope)                       │
│                                                                     │
│  raw video  →  mistake-cleanup  →  /transcribe  →  clean SRT        │
│  (修修錄)     (獨立工具，         (既有 nakama        (broll input)  │
│                Phase 2 評估)       skill)                            │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│             broll-pipeline (agents/brook/script_video/)             │
│                                                                     │
│  ┌────────────┐    ┌─────────────┐    ┌──────────────────────────┐  │
│  │  SRT 攤平  │ →  │   Planner   │ →  │     Beat aligner         │  │
│  │ (Python)   │    │   (LLM)     │    │   (Python, anchor→time)  │  │
│  │ flatten +  │    │ text_span + │    │  rapidfuzz substring     │  │
│  │ char↔time  │    │ creative    │    │  → timing + srt_line_ids │  │
│  └────────────┘    └─────────────┘    └──────────────────────────┘  │
│                                                  │                  │
│                                                  ▼                  │
│                                         storyboard.yaml             │
│                                                  │                  │
│              ┌───────────────────────────────────┴──────────────┐   │
│              ▼                                                  ▼   │
│   ┌────────────────────┐                       ┌──────────────────┐ │
│   │  Bridge UI Tier 3  │ ◀──────SSE────────────│  Render queue    │ │
│   │ Thousand Sunny     │                       │ (asyncio,        │ │
│   │  /script-video/    │                       │  concurrency=2)  │ │
│   │   <episode>        │                       │                  │ │
│   │  ─ table           │                       │  3-path dispatch │ │
│   │  ─ 3 actions/row   │                       │  ┌────────────┐  │ │
│   │  ─ inline player   │ ──approve──auto─────▶ │  │hyperframes │  │ │
│   │  ─ 2-layer status  │                       │  │            │  │ │
│   └────────────────────┘                       │  │reader-play │  │ │
│                                                │  │            │  │ │
│                                                │  │web-play    │  │ │
│                                                │  └────────────┘  │ │
│                                                └──────────────────┘ │
│                                                          │          │
│                                                          ▼          │
│                                              ┌─────────────────────┐│
│                                              │ FCPXML emitter      ││
│                                              │ V1: talking head    ││
│                                              │ V2: B-roll mp4 refs ││
│                                              └─────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
                                                          │
                                                          ▼
                                                 episode.fcpxml
                                                 + b_roll_NN.mp4 × N
                                                          │
                                                          ▼
                                                   DaVinci Resolve
                                                  （修修最終剪輯）
```

### 1. Render path = 3-way dispatcher（Q1）

不是 Hyperframes-only。每個 cutaway beat 帶一個 `render_target` 欄，dispatcher 依此分派：

| render_target | 何時用 | 實作 |
|---|---|---|
| `hyperframes` | 程式繪製的 component（BigStat、DataChart、Map、TransitionTitle、Caption…） | `video/compositions/` 下 fork from catalog block，sandbox per-episode |
| `reader-playwright` | 書內引用（用修修 Robin Reader 顯示原書，highlight 動畫畫線） | 沿用 `web_highlight_record.py` + 加 iframe recursion（Reader 用 foliate iframe）|
| `web-playwright` | 外部網頁引用（Wikipedia / 論文 abstract / 部落格段落 highlight） | 沿用同一支 `web_highlight_record.py` |

**none** 不是 path — 是 `broll_decision: none` 表「這 beat 不放 B-roll」。

**為什麼三條 sibling 不是統一 Hyperframes**：書/網頁引用本質是「保留原媒介 + 加 highlight 動畫」，Hyperframes 重畫 layout 會破壞「這引用真的來自 Robin 內這本書」的視覺契約。詳細決定見 `memory/claude/project_broll_dual_path_architecture.md`（PR #710 已 merge）。

### 2. 寄宿 = Brook Python canonical + thin skill escape hatch（Q2）

```
agents/brook/script_video/         # CANONICAL — pipeline 邏輯只在這
├── pipeline.py                    # 主 entry, asyncio orchestration
├── srt_flattener.py               # SRT → flat text + char↔time index
├── planner.py                     # LLM call (Anthropic SDK) → beats
├── beat_aligner.py                # rapidfuzz: text_span → timing + srt_line_ids
├── render_dispatcher.py           # 3-path 分派
├── render_workers/
│   ├── hyperframes_worker.py      # shell out `hyperframes render`
│   ├── reader_playwright_worker.py
│   └── web_playwright_worker.py
├── fcpxml_emitter.py              # FCPXML 1.10
├── layouts/                       # 命名 layout YAML（見 §3）
├── prompts/
│   └── broll_planner.md           # canonical prompt template（skill 也 load 同份）
├── STYLE.md                       # editorial rubric（grows over time）
├── guardrails.yaml                # allow/deny lists, machine-readable
├── examples/                      # few-shot library
│   ├── _index.yaml                # tag → file mapping
│   └── *.yaml                     # 個別 example
└── edit_log/
    └── <episode-id>.jsonl         # auto-capture re-plan feedback

.claude/skills/broll-planner/
└── SKILL.md                       # thin wrapper — load prompts/broll_planner.md
                                   # 對話內 patch 單一 beat 用，非 canonical
```

skill 是 ergonomic surface，**邏輯 source of truth 永遠在 Python module**。

### 3. Talking head layout = FCPXML composite（Q3 + Q4）

#### 3a. Composite 在 DaVinci，不在 Hyperframes

`<ARollPip>` 之類「talking head 縮放 + B-roll 旁邊」layout，**用 FCPXML `adjust-transform` 在 DaVinci composite**，不進 Hyperframes：

```
V1: talking head 原檔（adjust-transform: scale 0.55, position -480 0 → 推左半）
V2: Hyperframes 渲染的 overlay mp4（1920×1080，右半畫東西、左半透明）
```

DaVinci 直接看到 split layout，**talking head 完全沒被 re-encode**，未來 color grade / 重剪不受影響。

#### 3b. Layout = 命名 YAML recipe（slot-decoupled from content）

`agents/brook/script_video/layouts/<name>.yaml`：

```yaml
# side_overlay_left.yaml
name: side_overlay_left
description: Talking head 縮左半，B-roll 落右半
slots:
  - id: aroll
    source: talking_head
    fcpxml_transform: { scale: 0.55, position_x: -480, position_y: 0 }
  - id: broll
    source: broll_render
    fcpxml_transform: { scale: 1.0, position_x: 0, position_y: 0 }
    broll_canvas: { x: 960, y: 0, w: 960, h: 1080 }   # 給 Hyperframes 知道右半才畫
```

Storyboard beat 只寫 `layout: side_overlay_left` + 各 slot 填什麼。**新 layout = 加 YAML file，emitter / planner / Hyperframes 都不動 code**。

**Phase 1 ship 5 個 seed layout**：

| Layout | 描述 |
|---|---|
| `full_aroll` | Talking head 全螢幕 |
| `full_broll` | B-roll 全螢幕（talking head 蓋掉） |
| `side_overlay_left` | Talking head 左 + B-roll 右 |
| `side_overlay_right` | 反向 |
| `pip_corner_br` | Talking head 縮右下角小窗（ADR-015 原 ARollPip） |

**但 Phase 1 實作只接通 `full_aroll` + `full_broll`** —— side overlay / PiP 在 Phase 1.5 補。

### 4. Feedback = 3-action hybrid + auto edit-log（Q5）

每個 beat 在 Bridge UI 上有三個 action：

| Action | UI | Brook 反應 | 寫 edit-log |
|---|---|---|---|
| **Approve** | 一鍵 | 標 `text_approved=true` → auto enqueue render | 否 |
| **Edit fields** | 展開 inline form（layout dropdown / component dropdown / params JSON） | 直接覆寫 storyboard.yaml，auto-approved | 否 |
| **Re-plan with note** | 展開 textarea | LLM 用 note + 該 beat context 重 plan，回新 proposal | **是** |

**第二層 visual approve** 走相同 3-action（render 完之後）：

- Approve → `visual_approved=true` → beat `finalized`
- Edit fields → 改 params → 只 re-render 該 beat（不重 plan）
- Re-plan with note → 重 plan + re-render，**寫 edit-log（高品質信號）**

**為什麼 Edit fields 不寫 edit-log**：純 mechanical 改 param 不是 taste 信號（target=11000 改 10000 沒泛化價值）。Re-plan with note 的自然語言才是 taste 信號。

### 5. Input contract = SRT-first（Q7）

```
data/script_video/<episode-id>/
├── episode.yaml                 # metadata（title, target_duration, refs map）
├── raw_recording.mp4            # talking head（已 mistake-cleaned）
├── transcript.srt               # /transcribe 輸出
├── refs.yaml                    # OPTIONAL: quote disambiguation
└── storyboard.yaml              # planner 輸出 + UI 編輯
```

**SRT 已乾淨**（NG take 上游已砍）— broll pipeline 假設 input SRT 是真實時間軸 source of truth。Mistake removal（拍掌 marker / razor+ripple）是獨立工具，**out of scope**（Q8）。

`refs.yaml` 例：

```yaml
quotes:
  - text_anchor: "習慣是身分認同的形成"
    book: "原子習慣"
    page: 87
    book_slug_robin: "atomic-habits"   # 對應 Robin Reader EPUB
```

Brook 看到 storyboard beat 文本內有 `「習慣是身分認同的形成」`，先查 `refs.yaml`，找不到 fallback Robin KB（ADR-015 Q4-2 邏輯沿用 → 不靠 fuzzy match 隨機猜書）。

### 6. Beat 顆粒度 = LLM 分群 + Python 對齊（Q9）

**LLM 跟 SRT timing 完全解耦**。Planner LLM 看到的是攤平 prose（不知道 SRT 結構），輸出 anchor-based beat：

```yaml
- start_quote: "研究追蹤了 11,000"        # 8-12 字首
  end_quote: "...10 年下來發現的趨勢"      # 8-12 字尾
  layout: side_overlay_left
  broll: { render_target: hyperframes, component: bigstat,
           params: { target: 11000, label: "受試者", suffix: "人" } }
  ...
```

`beat_aligner.py` 用 rapidfuzz partial-ratio ≥ 0.85 把 anchor 對回攤平文本 → 算 timing + srt_line_ids。**對不到就 flag warning**（不 silent fail）。

**Beat 數量目標**：10 分鐘影片 ~15-25 個 beat（一個 idea unit = 一個 beat，不是一句一個）。Phase 1 不做拆/合 beat UI（Phase 1.5 補）。

### 7. UI = Thousand Sunny Bridge Tier 3（Q10）

`/script-video/<episode-id>` route。HTML 表格 + per-row 3 action + inline `<video>` player + SSE live progress。

**Phase 1 必做**：
- 表格 render storyboard.yaml
- 三個 action 按鈕 + endpoint
- Inline `<video>` 播 render 完的 mp4（file:// 或 streaming endpoint）
- SSE 推 render 狀態（draft / rendering / done / failed）
- 兩層 status chip：`📝 text_approved` `🎬 render_status` `✅ visual_approved`

**Phase 1 不做**：
- 拆/合 beat 按鈕（→ Phase 1.5）
- 多集 episode listing / history view（→ Phase 2）

### 8. Render trigger = approve auto-enqueue（Q11）

```
text_approved=true → 自動 enqueue render task
                  → render done → render_status=done → UI 跳出 inline player
                  → 看完 → visual_approved=true → beat finalized

任何時候 re-plan with note：
  → cancel in-flight render (kill subprocess)
  → 回 status=draft
  → LLM 重 plan
  → 拿新 proposal 再 approve → re-enqueue render
```

**Render concurrency = 2**（Hyperframes 一支 render ~50% CPU + GPU；Reader/Web Playwright 也吃 GPU 因為 CDP screencast）。Phase 1 用 asyncio.Queue，不用 Redis / Celery。

### 9. Style / brand 文件結構（Q12）

| 文件 | 位置 | 用途 |
|---|---|---|
| Brand tokens | `docs/design-system.md`（既有） | `--sho-*` tokens、LINE Seed TW、PANTONE 165 PC。**Planner load 這個，不複製** |
| Editorial rubric | `agents/brook/script_video/STYLE.md` | broll 編輯 rubric（do/don't）。**不放 `/memory/`**（pipeline asset ≠ agent identity） |
| Guardrails | `agents/brook/script_video/guardrails.yaml` | allow/deny machine-readable |
| Few-shot | `agents/brook/script_video/examples/` | tag-indexed retrieval（Phase 1 用 `_index.yaml` tag filter；embedding Phase 2 看是否需要） |
| Edit log | `agents/brook/script_video/edit_log/<episode>.jsonl` | auto-capture re-plan note + before/after diff |

**`STYLE.md` vs `memory/claude/feedback_*.md` 邊界**：
- 進 STYLE：「BigStat 別用在 < 100 的數字（沒視覺衝擊）」← broll 特定
- 進 memory feedback：「Claude commit message 不要 emoji」← 跨任務行為

### 10. 學習 loop = 解釋實情（非 fine-tuning）

模型本身不學習。**所謂 learning = 累積 explicit, version-controlled corpus 餵 prompt 用**。具體：

- `edit_log/*.jsonl` — auto-capture re-plan 的 before/after + user_note
- `examples/` — 從 edit_log 提煉的 worked examples（含 negative）
- `STYLE.md` — 從 edit_log 提煉的 do/don't 規則

每次 planner 跑：
1. 載入 `docs/design-system.md` brand context
2. 載入 `STYLE.md`（整份）
3. 載入 `guardrails.yaml`（整份）
4. 載入 `examples/_index.yaml` → 依當前 beat tag 抓 top-3 examples 塞 prompt

Phase 1 corpus 空，planner cold-start 跑 built-in rubric。修修每集跑完，edit_log 累 5-20 條，第二集開始 examples 有東西。

---

## Acceptance Criteria（Phase 1）

- [ ] 跑 `python -m agents.brook.script_video --episode test-fixture-001` 從 SRT + raw_recording 開到 storyboard.yaml，全程無錯
- [ ] Bridge UI `/script-video/test-fixture-001` 渲染表格，三個 action 按鈕都能觸發對應 endpoint
- [ ] Approve 一個 BigStat beat → SSE 推進度 → 完成後 inline `<video>` 可播 mp4
- [ ] Re-plan with note 觸發 LLM 重 plan，storyboard.yaml 換 proposal，edit_log 多一條
- [ ] FCPXML 1.10 emit 後，DaVinci Resolve 開檔正確：V1 talking head 整段 + V2 B-roll mp4 在對應時間段 + transform 數值套對
- [ ] 修修真實一集（10-15min 含 3-5 個 BigStat beat）走通 end-to-end，產出 .fcpxml + 個別 .mp4

---

## Phase 1 PR slicing

| PR | 範圍 | 估時 | 依賴 |
|---|---|---|---|
| **PR-1** | ADR-032 + `agents/brook/script_video/` scaffold（pipeline.py 殼 + STYLE.md seed + guardrails.yaml seed + 5 個 layout YAML + episode dir layout convention） | 1 天 | — |
| **PR-2** | SRT flattener + char↔time index + beat_aligner + rapidfuzz + unit test | 1.5 天 | PR-1 |
| **PR-3** | planner.py（LLM call + prompt template `prompts/broll_planner.md`）+ storyboard.yaml schema + integration test on fixture | 2 天 | PR-2 |
| **PR-4** | render_dispatcher + hyperframes_worker（接 BigStat from `video/compositions/bigstat/`）+ FCPXML 1.10 emitter + dry-run（無 UI） | 2 天 | PR-3 |
| **PR-5** | Thousand Sunny Bridge UI Tier 3（table + 3 actions + SSE + inline player + 2-layer status）+ endpoint wiring + edit_log writer | 3 天 | PR-4 |

合計 ~9.5 天 agent 工時。

---

## Phase 1.5 / Phase 2 backlog

**Phase 1.5（~1 週）**：
- Reader + Web Playwright workers 接 dispatcher（用 `web_highlight_record.py`）
- 加 `side_overlay_left/right` + `pip_corner_br` layout 實作
- 加 TransitionTitle component（Hyperframes `caption-kinetic-slam` + `transitions-cover`）
- 拆/合 beat UI
- Edit-log examples retrieval（tag filter）真用上

**Phase 2（無時程）**：
- DataChart / Map / Caption / Transition components
- LINE Seed TW @font-face capture
- Embedding-based examples retrieval（如 corpus > 100）
- Multi-episode listing + history view
- Mistake-cleanup 獨立 skill（如真要自動化）
- Phase 2 升級條件 — 修修發現「進 DaVinci 從沒真的改什麼」→ 加 `--direct-mp4` flag 跳 FCPXML

---

## Consequences

### 立即影響

1. **新模組 `agents/brook/script_video/`** — Python orchestrator + LLM planner + render workers
2. **新依賴** — `rapidfuzz`（beat alignment）+ `PyYAML`（storyboard schema）+ `anthropic` SDK 升級到支援 streaming（既有）
3. **既有 `video/compositions/bigstat/` 被 dispatcher 接管**（之前是 ad-hoc 試水，Phase 1 變成 first-class component）
4. **新 Thousand Sunny route `/script-video/<episode-id>`** + endpoint set
5. **新 `data/script_video/` 樹結構** — per-episode + edit_log + examples
6. **新 CLI** — `python -m agents.brook.script_video --episode <id>`
7. **新 skill `.claude/skills/broll-planner/SKILL.md`** — thin wrapper 對話內 patch beat 用
8. **PR #710 memory（B-roll dual-path）成為 ADR-032 的 prior decision** — 已 merge，本 ADR formalize

### 對既有 ADR 的影響

| ADR | 影響 |
|---|---|
| **ADR-015** | **Superseded**。Remotion 不裝，PyMuPDF DocumentQuote 不做，markdown DSL 不做 |
| ADR-001（agent role） | 無。Brook 仍 Composer |
| ADR-013（transcribe）| 無。SRT 上游，本 ADR 接 output |
| ADR-014（RepurposeEngine）| 無。orthogonal |
| ADR-028（VAULT-LAYOUT）| 無，但 `data/script_video/` 為新增 sibling 樹，不在 vault scope |

### 風險

| 風險 | 機率 | mitigation |
|---|---|---|
| Hyperframes catalog block 跟 nakama design tokens 衝突大 → BigStat 級客製需逐個 fork | 中 | 已驗證 BigStat fork from `apple-money-count` 走得通；token 集中在 `docs/design-system.md` 唯一源 |
| LLM 自然分群 beat 太細 / 太粗，user 常需手動拆合 | 中 | Phase 1.5 拆合 UI；Phase 1 用 1-2 集找出 prompt 該怎麼 tune |
| `beat_aligner` rapidfuzz 對不到 LLM anchor（LLM 改字／加標點） | 中 | partial-ratio 0.85 + normalize 數字/標點/空白；對不到 flag warning 不靜默 |
| Tier 3 UI 工程量超預算（~3 天估值樂觀） | 中 | UI 不靠原生 framework，純 HTMX + SSE，跟既有 thousand_sunny 工具鏈一致；超時 fallback Tier 2 |
| Render concurrency=2 在 Phase 1 不夠（多 beat 等 queue） | 低 | Phase 1 BigStat 9s/支，~25 個 beat 串行 ~4 分鐘可接受 |
| Hyperframes Studio preview / hyperframes lint warning 在 CI 跑不過 | 低 | Phase 1 不上 CI 跑 hyperframes；只跑 pytest unit test |
| Reader iframe recursion 在 foliate-js 上 `web_highlight_record.py` 失效 | 中 | Phase 1.5 才做 Reader path；先驗證再 schedule |

### 不變項

- `transcribe` skill SKILL.md / 觸發詞 / pipeline 完全不動
- Robin KB 結構不動（只 read metadata 對應 book slug）
- ADR-001（agent role） / ADR-014（RepurposeEngine）全套 unchanged
- 修修既有 DaVinci project template / preset / 字型 / 配色全照舊（FCPXML 只標 clips + transform，不強加 styles）
- `docs/design-system.md` 唯一 brand source of truth

---

## Alternatives Considered

| Alternative | 為何沒選 |
|---|---|
| **Remotion**（ADR-015 原選） | TypeScript 框架成熟但 catalog 空（要從零寫 BigStat 跟 14 個 caption），不如 Hyperframes 起手快 |
| **PyMuPDF + bbox DocumentQuote**（ADR-015 原選） | 失去 Robin Reader 視覺契約；Phase 1 已驗證 Reader+Playwright 更乾淨 |
| **Markdown DSL input**（ADR-015 原 Q1） | 要修修學新 syntax；本 session Q6 凍結 SRT-first + Brook 自判，markers 只在 quote disambiguation 用 |
| **Record-first（先錄再 plan）** | 創意 review 卡在錄影；本 session Q7 凍結 SRT-first → plan 跟錄影解耦 |
| **Hyperframes-baked layout（talking head 進 Hyperframes 一起合成）** | 破 ADR-015 invariant「talking head sacred」。本 session Q3 凍結 FCPXML composite |
| **Batch render（all-approve-then-render）** | Tier 3 inline player 派不上用場；本 session Q11 凍結 approve auto-enqueue |
| **Single approve（不分文字 / 影片兩層）** | 失去「文字 review 早攔截」 + 「視覺 review 後再修」兩個價值。本 session Q11 凍結 2-layer |
| **DESIGN.md 獨立 brand 文件** | 跟 `docs/design-system.md` drift。本 session Q12 凍結唯一源 |
| **STYLE.md / edit_log 進 `/memory/`** | 混淆 agent identity feedback vs pipeline asset。本 session Q12 凍結 module-local |

---

## Open Questions（不阻擋落地）

1. **broll-planner skill SKILL.md 觸發詞** — 是 `/broll-planner` 還是 `/broll-replan`？Phase 1 後決定
2. **edit_log retention policy** — 永久保留 or per-episode 砍舊？Phase 2 看修修一年累積量再決定
3. **Examples retrieval embedding switch** — 何時從 tag filter 升 embedding？預設 corpus > 100 + 修修報告「tag retrieval 抓錯太多」才升
4. **Streaming render（hyperframes streaming-encode）** — Phase 1 用 batch encode，Phase 2 評估 streaming
5. **Caption sync 用 SRT 還是 word-level JSON** — Phase 2 做 caption 動畫時再決定（SRT sentence-level 對 BigStat / DocumentQuote 足夠）

---

## References

- [`memory/claude/project_broll_dual_path_architecture.md`](../../memory/claude/project_broll_dual_path_architecture.md) — PR #710，3-path 決定
- [`memory/claude/feedback_cdp_screencast_over_recordvideo.md`](../../memory/claude/feedback_cdp_screencast_over_recordvideo.md) — PR #710，CDP screencast 選擇
- [Hyperframes v0.6.42 docs](https://hyperframes.mintlify.app/llms.txt)
- [`video/compositions/bigstat/`](../../video/compositions/bigstat/) — Phase 1 第一個 component 試水（已 ship 9s 1080p mp4）
- ADR-015 supersedes 標記後仍 readable，記錄為何選 Remotion → 為何改 Hyperframes 的脈絡
- 2026-05-25 grill-with-docs session 13 個 Q：Q1 dispatch / Q2 寄宿 / Q3 composite / Q4 layout YAML / Q5 feedback / Q6 input model / Q7 SRT-first / Q8 mistake removal scope / Q9 LLM/Python 分工 / Q10 UI Tier / Q11 render trigger / Q12 style 文件結構 / Q13 phasing
