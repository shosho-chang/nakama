# Script-Driven Video Production — Phase 1 Implementation Plan

**Date:** 2026-05-02
**Status:** Final（PRD #310 approved 2026-05-02 / ADR-015 Accepted）
**PRD:** [#310](https://github.com/shosho-chang/nakama/issues/310)
**ADR:** [docs/decisions/ADR-015-script-driven-video-production.md](../decisions/ADR-015-script-driven-video-production.md)
**Owner:** Brook（Composer agent）+ video subproject（Node.js / Remotion）
**Workflow context:** Phase 0 grill closed 2026-05-02 — 7 design 分岔全凍結

---

## 1. Background

修修最高價值 content creation workflow 的自動化專案。Phase 0 grill 凍結 7 個設計分岔（Q1 架構 / Q3 mistake removal / Q4-1/2/3/4 引用基礎設施 / Q5 Phase 1 component 範圍 / Q7 output 路徑），結論寫進 ADR-015。Phase 1 PRD（#310）涵蓋：

- 5-stage pipeline（Mistake Removal → DSL Parsing → Quote Visualization → B-roll Render → FCPXML Emit）
- 6 個 Phase 1 component（ARollFull / TransitionTitle / ARollPip / DocumentQuote / QuoteCard / BigStat）
- 雙模式引用視覺化（Mode A 真實書頁 + 螢光筆 / Mode B 風格化引文卡）
- DaVinci FCPXML 1.10 timeline 為輸出（修修微調 ≤30 分鐘）

---

## 2. 技術選型調研（2026-05-02）

按 [feedback_dev_workflow](../../memory/claude/feedback_dev_workflow.md)「技術選型要上網調研」執行 5 條 query 綜合社群 / 部落格 / 官方文件，重要發現：

### 2.1 Remotion 4.x

- **狀態**：Production-ready，2026 主流；React-based programmatic video，frame-by-frame render
- **Performance tips**：
  - `--concurrency` flag 用 `npx remotion benchmark` 找最優值
  - `useMemo()` / `useCallback()` cache expensive computation
  - 注意 GPU-accelerated CSS（box-shadow / text-shadow / gradient / filter）在無 GPU host 會 bottleneck
  - WebM vp8/vp9 編碼極慢，nakama 走 H.264 mp4 不踩雷
- **Cloud rendering（Lambda / Cloud Run）**：Phase 3+ 才考慮（Phase 1 本機 render 已足夠）
- **Anthropic 自家 Remotion Agent Skills 已存在**——可參考其 prompt patterns
- **trade-off vs After Effects template**：AE 不易程式化、需訂閱、無 hot-reload；Remotion BSD-style license + npm 生態 + TypeScript native，明確選 Remotion
- **產業趨勢**：2027 預測 45% motion graphics 來自 AI-assisted code generation——nakama 走在這條路上是 tailwind

### 2.2 sqlite-vec

- **版本**：v0.1.0 stable release（Aug 2024），pure C，pypi 有 Python binding
- **Production readiness**：作者 roadmap 是「v1.0 maintenance mode in next year」，目前 v0.x 已被 LangChain / SQLite Cloud / Turso integrated
- **Capabilities**：brute-force vector search + quantization；對 nakama 規模（< 1000 books × 500 chunks）綽綽有餘
- **trade-off vs ChromaDB**：sqlite-vec 是 sqlite extension（0 daemon），ChromaDB 需獨立 process。nakama state.db 已是 single source of truth，不引入第二 daemon
- **trade-off vs sqlite-vector** (sqliteai/sqlite-vector)：後者 newer fork，作者不同；sqlite-vec 採用面廣、文件多

### 2.3 BGE-M3 vs Qwen3-Embedding（**新調研發現**）

| Model | 中文 SOTA | Cross-lingual zh-en | 模型大小 | License | Ecosystem |
|---|---|---|---|---|---|
| **BGE-M3** | 強（2024 SOTA） | ✅ 招牌 | 568M | MIT | 成熟，HF / RAG 生態廣 |
| **Qwen3-Embedding-0.6B** | **+7.9% MMTEB** | ✅ | 600M | Apache-2.0 | 較新（2025-2026），成長中 |

**新資訊**：Qwen3-Embedding 在 MMTEB benchmark 全面 +7.9% 超越 BGE-M3（Aryan Kumar 2026 比較）。

**選型決定**：**先 BGE-M3，Phase 2 預留 Qwen3-Embedding swap 接口**。理由：

1. BGE-M3 ecosystem 成熟，新踩坑風險小
2. Cross-lingual zh-en 是 BGE-M3 招牌能力，纯中文場景的 7.9% 差距對 nakama use case 影響相對小
3. Qwen3 較新，長期穩定性 / 中文 long-document 能力尚未廣泛驗證
4. **設計接口時 abstract embedding model layer**，未來 swap Qwen3-Embedding 不動 caller code（記憶 [feedback_open_source_ready](../../memory/claude/feedback_open_source_ready.md) 原則）
5. 此調研結果記憶留存，Phase 2 視 BGE-M3 在 nakama 真實工況品質決定是否升級

### 2.4 DaVinci Resolve FCPXML 支援

- **DaVinci Resolve 18+** 支援 FCPXML 1.9 / 1.10 import
- **1.11**：仍有 import quirk，社群報告
- **1.12**：DaVinci Studio **不支援** opening
- **跨家生態現況**：「latest FCP exports 1.13；DaVinci Resolve 20 不支援」——FCP 端要走「Previous Version」export 才能進 DaVinci

**選型決定**：**FCPXML 1.10**（confirmed conservative 選擇）。Phase 1 不嘗試 1.11/1.12。

### 2.5 PyMuPDF4llm（已 import）

- **版本**：1.27+ 已在 nakama requirements.txt
- **核心 API**：
  - `page.get_text("words", extract_words=True)` → `(x0, y0, x1, y1, word, ...)` tuple list
  - `page.search_for(text)` → `[Rect]` for exact match
  - `pymupdf.recover_quad()` 從 line/span dict 還原 quad（multi-line bbox）
  - `page.add_highlight_annot(rect)` 加 highlight annotation（直接寫 PDF；對我們場景是 reference，不需真寫 PDF）
- **新功能（2026 update）**：rectangle containment perf 改善 + `to_markdown(page_chunks=True)`
- **不需 GPU**——CPU 跑就快，不跟 BGE-M3 搶 GPU

---

## 3. 架構詳細設計

依 ADR-015 凍結結構，落地細節：

### 3.1 目錄結構（最終）

```
nakama/
├── video/                              # 新 Node.js subproject
│   ├── package.json
│   ├── tsconfig.json
│   ├── remotion.config.ts
│   ├── src/
│   │   ├── Root.tsx                   # Remotion compositions root
│   │   ├── scenes/
│   │   │   ├── ARollFull.tsx
│   │   │   ├── ARollPip.tsx
│   │   │   ├── TransitionTitle.tsx
│   │   │   ├── DocumentQuote.tsx       # Mode A
│   │   │   ├── QuoteCard.tsx           # Mode B
│   │   │   └── BigStat.tsx
│   │   ├── parser/
│   │   │   ├── parse.ts               # markdown DSL → Manifest
│   │   │   ├── validate.ts            # schema + 時間軸驗證
│   │   │   └── types.ts               # Manifest TypeScript types
│   │   ├── compositions/
│   │   │   └── BRollSegment.tsx       # 單個 B-roll segment composition
│   │   └── render/
│   │       └── render-segment.ts      # Remotion CLI wrapper
│   └── tests/
│       └── parser.test.ts
│
├── agents/brook/script_video/          # 新 Python orchestrator
│   ├── __init__.py
│   ├── __main__.py                    # CLI entry
│   ├── pipeline.py                    # 5-stage 主編排器
│   ├── mistake_removal.py             # 拍掌 marker + alignment fallback
│   ├── pdf_quote.py                   # PyMuPDF + bbox + fuzzy match
│   ├── embedding.py                   # BGE-M3 wrapper（abstract interface）
│   ├── srt_emitter.py                 # 中文 SRT 副產品
│   ├── fcpxml_emitter.py              # FCPXML 1.10 schema
│   ├── robin_metadata.py              # Robin KB metadata adapter
│   ├── manifest.py                    # Manifest dataclass + JSON schema
│   └── cuts.py                        # CutPoint dataclass
│
├── shared/
│   └── (reuse: pdf_parser.py, anthropic_client.py)
│
├── data/script_video/
│   ├── <episode-id>/
│   │   ├── script.md
│   │   ├── raw_recording.mp4
│   │   ├── refs/                      # per-episode PDF
│   │   ├── aroll-audio.mp3            # ffmpeg 抽出
│   │   ├── aroll-video.mp4            # ffmpeg 抽出
│   │   ├── manifest.json
│   │   └── out/
│   │       ├── b_roll_*.mp4
│   │       ├── episode.fcpxml
│   │       └── episode.srt
│   └── _cache/
│       └── embeddings/
│           └── <sha256>.npy
│
└── tests/
    └── brook/script_video/
        ├── test_mistake_removal.py
        ├── test_pdf_quote.py
        ├── test_embedding.py
        ├── test_srt_emitter.py
        └── test_fcpxml_emitter.py
```

### 3.2 Manifest JSON Schema（Python ↔ TypeScript shared）

```typescript
// video/src/parser/types.ts
type Manifest = {
  episodeId: string;
  fps: 30;
  totalFrames: number;
  scenes: Scene[];
  arollAudio: string;       // path to aroll-audio.mp3
  arollVideo: string;       // path to aroll-video.mp4
  cuts: CutPoint[];         // mistake removal 結果
};

type Scene =
  | ARollFullScene
  | ARollPipScene
  | TransitionTitleScene
  | DocumentQuoteScene
  | QuoteCardScene
  | BigStatScene;

type SceneBase = {
  id: string;
  startFrame: number;       // 在 final timeline 的 frame
  durationFrames: number;
};

type ARollFullScene = SceneBase & {
  type: 'aroll-full';
  arollStartSec: number;    // A-roll 音檔的秒數
};

type ARollPipScene = SceneBase & {
  type: 'aroll-pip';
  arollStartSec: number;
  slide: SlideData;
  pipPosition: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
};

type DocumentQuoteScene = SceneBase & {
  type: 'document-quote';
  pageImagePath: string;    // 渲染好的書頁 PNG
  imageWidth: number;
  imageHeight: number;
  highlights: BBox[];       // bbox 陣列（multi-line）
  variant: 'highlighter-sweep' | 'ken-burns' | 'spotlight';
  citation: { title: string; page: number; author?: string };
};

type CutPoint = {
  type: 'razor' | 'ripple-delete';
  startFrame: number;
  endFrame: number;
  reason: 'marker' | 'alignment-detected';
  confidence: number;       // 0..1
};
```

Python 端用 Pydantic / dataclasses 鏡像 schema，validate via JSON Schema export。

### 3.3 5-Stage Pipeline（Python 編排）

```python
# agents/brook/script_video/pipeline.py
def run(episode_id: str) -> EpisodeResult:
    paths = resolve_paths(episode_id)

    # Stage 0: 預處理（從 raw_recording.mp4 抽 audio + video stream）
    extract_audio_video_streams(paths)

    # Stage 1: WhisperX ASR + Mistake Removal
    whisperx_words = run_transcribe_skill(paths.aroll_audio)
    cuts = mistake_removal.clean(
        audio=paths.aroll_audio,
        whisperx_words=whisperx_words,
        script_words=parse_script_words(paths.script),
    )

    # Stage 2: DSL Parser → Manifest
    manifest = invoke_node_parser(paths.script, cuts)

    # Stage 3: Quote Visualization（per [quote] directive）
    for quote_directive in manifest.quote_directives:
        if quote_directive.mode == 'document':
            metadata = robin_metadata.lookup(quote_directive.source)
            pdf_path = paths.refs / metadata.pdf_filename
            match = pdf_quote.find(pdf_path, quote_directive.text, quote_directive.page_hint)
            scene = DocumentQuoteScene(
                pageImagePath=render_pdf_page(pdf_path, match.page),
                highlights=match.bboxes,
                variant=quote_directive.variant,
                citation=metadata.to_citation(match.page),
            )
            manifest.attach_scene(quote_directive.id, scene)

    # Stage 4: Remotion render B-roll segments
    invoke_node_renderer(manifest, paths.out_dir)

    # Stage 5: FCPXML + SRT emit
    fcpxml_emitter.emit(manifest, paths.out_dir / 'episode.fcpxml')
    srt_emitter.emit(manifest, paths.out_dir / 'episode.srt')

    return EpisodeResult(...)
```

---

## 4. Vertical Slice 拆分（5 slice）

每 slice 是 tracer-bullet vertical slice（跨 schema / code / test，可 demo / 可 ship）。實際 issue 拆分由 Phase 2b `/to-issues` skill quiz 修修確認。

### Slice 1: 骨幹 — DSL parser + WhisperX align + Mistake removal + FCPXML 生成

**端到端 demo 目標**：寫一個只含 `[aroll-full]` directive 的最小 script.md，跑 pipeline，吐出可 import DaVinci 的 FCPXML（V1 一條 A-roll，無 B-roll），DaVinci import 成功 + timeline 對齊。

**範圍**：

- `video/src/parser/parse.ts`（先支援 `[aroll-full]` 一個 directive）
- `agents/brook/script_video/mistake_removal.py`（marker primary，alignment fallback 留 stub）
- `agents/brook/script_video/fcpxml_emitter.py`（FCPXML 1.10 minimal schema）
- `agents/brook/script_video/pipeline.py`（編排骨架）
- `agents/brook/script_video/__main__.py`（CLI: `python -m agents.brook.script_video --episode <id>`）
- 測試：`test_mistake_removal.py` + `test_fcpxml_emitter.py`（部分）

**Acceptance**：
- [ ] CLI 可呼叫
- [ ] 一個 fixture audio（內含已知拍掌位置）→ 正確 cut points
- [ ] FCPXML 通過 `xmllint --schema` 驗證
- [ ] DaVinci import smoke：建一個 dummy script + dummy mp4，跑 pipeline，DaVinci 開檔不報錯
- [ ] CI 全綠（pytest + ruff + tsc）

**Type**：HITL（首次跑通需修修在 DaVinci 驗 import）  
**Blocked by**：無（first slice）

### Slice 2: 6 場景 components — Remotion 實作 + Studio preview

**端到端 demo 目標**：6 個 Remotion components 各自有 Studio preview 可看，渲染到單個 mp4 segment 成功。

**範圍**：

- `video/src/scenes/ARollFull.tsx`
- `video/src/scenes/TransitionTitle.tsx`
- `video/src/scenes/ARollPip.tsx`
- `video/src/scenes/DocumentQuote.tsx`（用 stub data，PDF 整合 in Slice 3）
- `video/src/scenes/QuoteCard.tsx`
- `video/src/scenes/BigStat.tsx`
- `video/src/compositions/BRollSegment.tsx`（dispatcher composition）
- `video/src/render/render-segment.ts`
- `video/package.json` + `tsconfig.json` + `remotion.config.ts`
- DSL parser 擴 5 個 directive（`[aroll-pip]` / `[transition]` / `[quote]` / `[big-stat]`）
- Pipeline 在 Stage 4 invoke Node.js renderer

**Acceptance**：
- [ ] `npx remotion studio` 可開，6 個 component 可 preview
- [ ] `npx remotion render BRollSegment <props>` 出 mp4
- [ ] DSL parser test 5 個 directive 都通
- [ ] 視覺 baseline screenshot in `video/tests/snapshots/`（但 snapshot test 不算 unit test 強驗）

**Type**：HITL（component 美學需修修確認，遵 [feedback_aesthetic_first_class](../../memory/claude/feedback_aesthetic_first_class.md)）  
**Blocked by**：Slice 1

### Slice 3: 引用 PDF — PyMuPDF integration + bbox + DocumentQuote 渲染

**端到端 demo 目標**：寫一個含 `[quote source="<test-book>" page=87 mode=highlight-sweep]` 的 script，跑 pipeline，吐出含書頁 + highlight 動畫的 mp4 segment + 進 FCPXML V2 軌道。

**範圍**：

- `agents/brook/script_video/pdf_quote.py`（PyMuPDF wrapper）
  - `find_quote_in_pdf(pdf_path, quote_text, page_hint)` 三層匹配（exact → fuzzy（Slice 4 接 embedding） → bbox 計算）
  - **Slice 3 只做 exact match + page_hint required**，fuzzy 走 Slice 4
- `agents/brook/script_video/manifest.py` 擴 DocumentQuoteScene
- `video/src/scenes/DocumentQuote.tsx` 渲染：3 變體（highlighter-sweep / ken-burns / spotlight）
- 測試：`test_pdf_quote.py`（exact match + bbox 計算）

**Acceptance**：
- [ ] PDF fixture（已知 quote + page）→ 正確 bbox
- [ ] DocumentQuote 渲染 3 變體可選
- [ ] 端到端：`[quote ... page=87 mode=highlight-sweep]` → 出 highlight 動畫 mp4 segment
- [ ] DaVinci import smoke：FCPXML V2 軌道有 quote segment

**Type**：HITL（highlight 動畫品質需修修確認）  
**Blocked by**：Slice 2

### Slice 4: Embedding — BGE-M3 + sqlite-vec + cross-lingual fuzzy match

**端到端 demo 目標**：`[quote ... auto]`（不指定 page）→ 程式自動找到 PDF 對應段落 + bbox + 渲染。

**範圍**：

- `agents/brook/script_video/embedding.py`（BGE-M3 wrapper，abstract interface）
- state.db migration：3 新 table（`video_quote_sources` / `video_quote_chunks` sqlite-vec / `video_quotes`）
- `agents/brook/script_video/pdf_quote.py` 擴 fuzzy match（embed + cosine top-K + alignment 二次驗證）
- `agents/brook/script_video/robin_metadata.py`（Robin KB metadata read-only adapter）
- 跨集 embedding cache `data/script_video/_cache/embeddings/`
- requirements.txt + pyproject.toml 加 `FlagEmbedding` + `sqlite-vec`
- 測試：`test_embedding.py`（embed + sqlite-vec round-trip + cache hit）+ `test_pdf_quote.py` 擴 cross-lingual fixture

**Acceptance**：
- [ ] BGE-M3 模型載入 + embed 中文 / 英文 / 中英混 sample → vector 形狀 (N, 1024)
- [ ] sqlite-vec round-trip：insert + search top-K 排序正確
- [ ] Cache hit：同 PDF 第二次 build 從 `_cache/embeddings/` 讀
- [ ] Cross-lingual fixture：中文 narration「60 歲身心運動睡眠提升」→ 英文論文 abstract / conclusion 段落 top-1 match
- [ ] Robin KB 已 ingest 書 → metadata 自動補

**Type**：AFK（純技術整合，無 UX 判斷）  
**Blocked by**：Slice 3

### Slice 5: 端到端 dry-run + 整合驗證

**端到端 demo 目標**：修修挑一支已剪好的舊片（可作 ground truth），寫對應 markdown DSL，跑 pipeline → DaVinci import → 30 分鐘微調 → 對照人工剪片差異。

**範圍**：

- 修修提供 dry-run 候選舊片 1 支
- 寫對應 markdown DSL（修修主導）
- 跑 pipeline 端到端
- DaVinci 開檔驗證
- 寫 dry-run 報告 `docs/research/2026-MM-DD-script-video-dry-run.md`
  - 統計各 stage wall-clock
  - 比對人工剪 vs 自動剪差異
  - 列出漏網 cut / mismatch quote / B-roll 對齊偏差
  - 評估 Phase 1 ship 可行性
- 視結果決定是否 hardening + 開 Phase 2 backlog issue

**Acceptance**：
- [ ] 端到端跑通無 crash
- [ ] DaVinci import 無 schema error
- [ ] 修修微調 ≤ 30 分鐘
- [ ] 產出品質可上 YT
- [ ] Dry-run 報告寫完進 docs/research/

**Type**：HITL（修修主導 dry-run + 驗收）  
**Blocked by**：Slice 4

---

## 5. 工程量估時

| Slice | Type | 工程天估時（agent） | 修修參與 |
|---|---|---|---|
| 1. 骨幹 | HITL | ~3 | DaVinci import smoke 確認 |
| 2. 6 場景 | HITL | ~2 | 6 個 component 美學 review |
| 3. 引用 PDF | HITL | ~2 | highlight 動畫 3 變體 review |
| 4. Embedding | AFK | ~2 | — |
| 5. 端到端 | HITL | ~1 | dry-run 主導 + 驗收 |

合計 **~10 工程天**（agent 工時）。

Sandcastle AFK 並行可顯著壓縮 wall-clock（Slice 4 是純 AFK，可獨立並行；其他 slice HITL 但 multi-agent review 替代 ultrareview）。

---

## 6. 風險登記簿

| ID | 風險 | 機率 | 影響 | Mitigation |
|---|---|---|---|---|
| R1 | Remotion render B-roll segment 速度慢 | 低 | 中 | 官方 examples 證實單 segment 5-15s；批次平行 |
| R2 | FCPXML 1.10 在 DaVinci Resolve 20 仍有 quirk | 中 | 高 | Slice 1 做 import smoke；社群 quirk 清單預讀；retreat 路徑 = 1.9 fallback |
| R3 | 拍掌 marker 偵測在環境噪音下失效 | 中 | 中 | calibrate threshold per-episode + alignment fallback 補位（Slice 1+ ） |
| R4 | BGE-M3 cross-lingual 對某 jargon 飄 | 低 | 中 | top-3 候選 + `match_index` 手動指定 + Phase 2 swap Qwen3-Embedding 接口已留 |
| R5 | Robin metadata 命名衝突（同名書、不同版本） | 中 | 低 | source_id 加 disambiguator（年份 / 譯者）+ Robin frontmatter 規範 |
| R6 | sqlite-vec extension 在某 Python wheel 安裝失敗 | 低 | 中 | runbook 文件記錄安裝步驟 + CI 環境驗 |
| R7 | 修修拍掌新習慣養成失敗 | 低 | 低 | β fallback 標 review marker，DaVinci 30s 補殺 |
| R8 | Node.js + Python dual-stack dep drift | 中 | 低 | sandcastle Dockerfile 明示 base image + npm install + pip install 兩套 |
| R9 | Remotion components 美學是 AI slop | 中 | 高 | 遵 `feedback_aesthetic_first_class`，Slice 2 修修 review；高品質 reference Vox / Kurzgesagt screen-grab |
| R10 | DaVinci import 後 timeline lane offset 飄 | 中 | 中 | Slice 1 + 5 dry-run 早期暴露；FCPXML lane offset 慣例查官方 spec |

---

## 7. Open Questions（不阻擋 Phase 2b 拆 issue）

- **Q1**：FCPXML 1.10 vs 1.11 — 1.10 凍結（社群 quirk 報告少）。Phase 2 視 DaVinci 升版後新 demand 評估
- **Q2**：拍掌 audio threshold — Phase 1 假設修修錄音環境穩定（同 mic）一次性 calibrate；Phase 2 加 auto-calibrate
- **Q3**：BGE-M3 model 一次性下載要不要走 Robin 既有 model cache？— Phase 1 各模組獨立 cache（HF default 路徑），Phase 2 評估統一
- **Q4**：Phase 0 dry-run 用哪一支舊片？— Slice 5 由修修挑
- **Q5**：character animation Phase 2 component 命名 — 等 asset library ready
- **Q6**：Phase 2 swap Qwen3-Embedding 觸發條件 — 暫定「BGE-M3 fuzzy top-1 hit rate < 80% + Qwen3 在等量真實 fixture 上 > 90%」即 swap

---

## 8. 不變項（Phase 1 不動）

- ADR-001 / ADR-013 / ADR-014 全套
- transcribe skill SKILL.md / 觸發詞 / pipeline
- shared/pdf_parser.py（pymupdf4llm）
- Robin KB 結構（read-only metadata 接口）
- 修修 DaVinci project template / preset / 字型 / 配色（FCPXML 只標 clips、不強加 styles）
- nakama state.db 既有 schema（純 add table，無 schema migration on existing tables）

---

## 9. References

### nakama internal
- PRD #310 [Phase 1 PRD](https://github.com/shosho-chang/nakama/issues/310)
- ADR-015 [Script-Driven Video Production Architecture](../decisions/ADR-015-script-driven-video-production.md)
- ADR-014 [RepurposeEngine Plug-in Interface](../decisions/ADR-014-repurpose-engine-plugin-interface.md)（sibling，不擴展）
- ADR-013 [Transcribe Engine](../decisions/ADR-013-transcribe-engine-reconsideration.md)（重用）
- ADR-001 [Agent Role Assignments](../decisions/ADR-001-agent-role-assignments.md)
- transcribe skill：[.claude/skills/transcribe/SKILL.md](../../.claude/skills/transcribe/SKILL.md)
- shared/pdf_parser.py — pymupdf4llm 重用點
- [feedback_aesthetic_first_class](../../memory/claude/feedback_aesthetic_first_class.md)
- [feedback_quality_over_speed_cost](../../memory/claude/feedback_quality_over_speed_cost.md)
- [feedback_minimize_manual_friction](../../memory/claude/feedback_minimize_manual_friction.md)
- [feedback_no_handoff_to_user_mid_work](../../memory/claude/feedback_no_handoff_to_user_mid_work.md)
- [feedback_open_source_ready](../../memory/claude/feedback_open_source_ready.md)

### External（2026-05-02 調研）
- Remotion: https://remotion.dev/ + [Performance Tips](https://www.remotion.dev/docs/performance)
- Anthropic Remotion Agent Skills（reference for prompt patterns）
- sqlite-vec: https://github.com/asg017/sqlite-vec + [v0.1.0 stable release blog](https://alexgarcia.xyz/blog/2024/sqlite-vec-stable-release/)
- BGE-M3 paper: M3-Embedding (FlagEmbedding 2024) + https://huggingface.co/BAAI/bge-m3
- Qwen3-Embedding 比較：[Aryan Kumar 2026 Medium](https://medium.com/@mrAryanKumar/comparative-analysis-of-qwen-3-and-bge-m3-embedding-models-for-multilingual-information-retrieval-72c0e6895413)
- DaVinci FCPXML 支援：[Blackmagic forum](https://forum.blackmagicdesign.com/viewtopic.php?f=21&t=151297)
- PyMuPDF4LLM: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/

### Claude Chat session（2026-05-01）— 外部研究 reference
- A-roll 音軌脊椎架構
- 五場景設計 → 第一波 11 個 component 推薦
- Vox / Kurzgesagt / Veritasium / Ali Abdaal / Johnny Harris 跨頻道 design pattern
- 引用視覺化兩模式 + 跨集引用追蹤 leverage
