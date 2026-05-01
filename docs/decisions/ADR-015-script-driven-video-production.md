# ADR-015: Script-Driven Video Production Architecture

**Date:** 2026-05-02
**Status:** Accepted（PRD #310 approved 2026-05-02）

---

## Context

修修最高價值內容創作 workflow 自動化需求（2026-05-02 grill 凍結）：

- **INPUT** — 預寫好的中文逐字稿 + 修修照稿錄製的 A-roll talking head 影片
- **OUTPUT** — 半成品 DaVinci Resolve project（FCPXML），修修 import 後 timeline 微調 ≤30 分鐘 → export 上 YouTube
- **核心場景** — 書評 / 論文講解 / 健康主題（zh-TW 為主，論文常為 en）
- **頻率** — 一週 1-2 支
- **既有剪輯主力** — DaVinci Resolve

### Prior research（Claude Chat session 2026-05-01）

修修 5/1 跟 Claude Chat 對話得到的關鍵設計洞察：

1. **A-roll 音軌 = 時間軸脊椎** — 音軌完整連續，畫面是 N 個場景拼出來的可視層；錄影時一次錄完，視覺切換不需對口型
2. **Remotion** 為核心渲染引擎（React-based programmatic video）
3. **markdown DSL** 標記場景，每個段落是一個 scene type
4. **WhisperX 對齊驗證** 確保 narration 跟 script 一致
5. **CLI + Remotion Studio**（不寫 Web UI）
6. 五場景 component 起步 → 第一波建議擴 11 個 → 含 Vox / Kurzgesagt / Veritasium / Ali Abdaal 等頂級頻道交叉 reference
7. **引用視覺化** 拆兩模式：A 真實書頁 + 螢光筆動畫、B 風格化引文卡

研究品質紮實，但 Claude Chat 不知 nakama codebase 的既有 infra（transcribe / shared/pdf_parser / state.db / Robin KB / ADR-001 / ADR-014），grill 過程把上述設計跟 nakama 對齊，**凍結 7 個分岔**。

### 跟既有 ADR 的關係

- **ADR-001（agent role）** — 不衝突。Brook 仍 Composer，Brook orchestrator 寄居 `agents/brook/script_video/`
- **ADR-014（RepurposeEngine）** — 不擴展、不繼承。RepurposeEngine 是 SRT → 多 channel parallel fan-out；本 workflow 是 script + footage → 單支 mp4 sequential pipeline，**input/output shape 不同**，硬套會建概念債
- **ADR-013（Transcribe / WhisperX）** — 重用。Stage 1 ASR 直接呼叫既有 transcribe pipeline（WhisperX + word-level timestamps）

---

## Decision

凍結 7 個 grill 分岔 + 1 個架構反轉 + Phase 1 component 範圍。

### Q1 — 架構定位 = 獨立 video module + Brook orchestrator

```
video/                              # Node.js + Remotion subproject
├── package.json
├── src/
│   ├── scenes/                     # Remotion components (TSX)
│   │   ├── ARollFull.tsx
│   │   ├── ARollPip.tsx
│   │   ├── TransitionTitle.tsx
│   │   ├── DocumentQuote.tsx
│   │   ├── QuoteCard.tsx
│   │   └── BigStat.tsx
│   ├── parser/                     # markdown DSL parser
│   ├── renderer/                   # FCPXML 1.10 emitter
│   └── compositions/               # Remotion compositions
└── tsconfig.json

agents/brook/script_video/          # Python orchestrator
├── __init__.py
├── pipeline.py                     # 主 entry：parse → align → match → render → emit FCPXML
├── mistake_removal.py              # 拍掌 marker 偵測 + alignment fallback
├── pdf_quote.py                    # PyMuPDF + bbox + fuzzy match
├── embedding.py                    # BGE-M3 + sqlite-vec
├── srt_emitter.py                  # 中文 SRT 副產品
└── fcpxml_emitter.py               # FCPXML 1.10 schema

data/script_video/<episode-id>/
├── script.md                       # 修修寫的 markdown DSL
├── raw_recording.mp4               # 修修錄的 A-roll
├── refs/                           # 該集引用的 PDF（per-episode）
│   ├── atomic-habits.pdf
│   └── nature-2024-mind-body.pdf
├── aroll-audio.mp3                 # 自動抽出（由 ffmpeg）
├── aroll-video.mp4                 # 自動抽出（由 ffmpeg）
├── manifest.json                   # parser 輸出，給 Remotion 跟 FCPXML emitter 共用
└── out/
    ├── b_roll_001_motion.mp4       # Remotion render（個別 segment）
    ├── b_roll_002_pip.mp4
    ├── episode.fcpxml              # ★ DaVinci 用
    └── episode.srt                 # 中文字幕（YouTube 用）

data/script_video/_cache/embeddings/
└── <sha256>.npy                    # 跨集 PDF embedding cache
```

**理由**：

1. Remotion 是 Node.js stack，跟 nakama Python 主 repo 用 process boundary 切開
2. Brook composer 角色仍合理（script + B-roll 標記是 compose specialist）
3. 不扭曲 ADR-014 fan-out 概念（不套 `Stage1Extractor` + `ChannelRenderer` protocol）
4. 觀測 / cost tracking / Bridge UI surface 自動入網（agent 子模組繼承 `shared/anthropic_client`）
5. 未來 `/bridge/script_video/<run_id>` review surface 直接接 thousand_sunny router

### 架構反轉 — Remotion 不 render 整支影片

Claude Chat 原始 vision 是 Remotion compose 整支片成 mp4。Q7 凍結 DaVinci 接管 final composite 後，**架構反轉**：

> Remotion 只 render B-roll segments 為個別 mp4 clips，A-roll 由 DaVinci 直接吃原檔在 V1 軌道，FCPXML 把 B-roll mp4 reference 進 V2-V4。

**Render 速度快很多**（單一 segment ≈ 5-15 秒，不是整片 10 分鐘），且最終剪輯品質完全在修修掌控。

### Q3 — Mistake removal = Marker-based α 為主 + Alignment-based β fallback

**錄影習慣（修修確認）**：整段重唸（從段落開頭重錄），唸錯時拍兩下手 marker。

**主路徑（α）**：
- WhisperX 對 raw recording 跑 word-level transcription
- 偵測拍掌 audio spike（連續兩下 < 0.3s 間隔，>3kHz burst > calibrated threshold）
- 砍 marker 之前最近的整段（從段落開頭到 marker 前 0.5s）
- 在 FCPXML V1 軌道上 razor cut + ripple delete

**Fallback（β）**：
- 對未標 marker 的 segment 做 sequence alignment（WhisperX words ↔ script words，Needleman-Wunsch DP）
- 找出 high-confidence 重複段落（同一句話連續講兩次，第二次相似度 >0.92）
- **不直接切**，標 marker 給修修 review（log 印「候選漏網重複」清單）

**工程量比純 alignment 路線少 ~5-8 倍**：marker 偵測是 audio threshold + token match 兩個訊號，不需 fuzzy alignment 全套複雜度。

### Q7 — Output 路徑 = DaVinci timeline (FCPXML 1.10)

**Phase 1 不直出 mp4**。輸出 FCPXML 1.10（DaVinci / Premiere / Final Cut 三家 NLE 都原生支援）。

**Timeline 結構**：

```
V1: A-roll（連續軌，已套 razor cut + ripple delete）
V2-V4: B-roll segments（每個 component render 出來的個別 mp4）
A1: A-roll 原音
A2: 背景音樂 placeholder（DaVinci 內手動選）
```

**Phase 2 升級條件** — 當修修發現「進 DaVinci 從沒真的改什麼」時，加 `--direct-mp4` flag 跳過 FCPXML、Remotion compose 整片。Phase 2 不擋路。

### Q4-1 — PDF library = per-episode refs/ + 全局 embedding cache

每集 PDF 在 `data/script_video/<episode-id>/refs/`，自包含、archive / 備份容易。

**Embedding cache 跨集共享** `data/script_video/_cache/embeddings/<sha256>.npy`：同一本 PDF 不同集第二次用直接讀 cache，不重 embed（一本書 embed 約 10-30 秒）。

### Q4-2 — Robin metadata 接（越自動越好）

修修在 markdown 寫 `[quote source="原子習慣" page=87]`，程式自動：

1. 查 Robin KB（`KB/Sources/<book-slug>.md` 的 frontmatter）找對應 book_id / title / author / pdf_path
2. 找到 → 自動補 metadata 進 manifest
3. 找不到（書未 ingest）→ markdown 寫 explicit metadata（`book="..." author="..." source_pdf="..."`）

**Robin 接的範圍**：metadata 重用，**不接 chunk text**——chunk text 已脫離 PDF page coord，bbox 計算還是要回原 PDF。

### Q4-3 — Embedding = BGE-M3 本地（cross-lingual） + Qwen3-Embedding swap 接口預留

**Phase 1 選 BGE-M3**（vs OpenAI text-embedding-3-large / Voyage voyage-3 / **Qwen3-Embedding-0.6B**）：

1. **跨語言對齊** — 中文 narration ↔ 英文論文段落 native support，這是 BGE-M3 取名 M3（Multi-lingual / Multi-functional / Multi-granular）的招牌能力
2. **中文 SOTA**（FlagEmbedding 2024 paper）— OpenAI / Voyage 中文側都較弱
3. **本地跑 + 0 月費** — 修修 5070 Ti 16GB 跑得動，~600MB model 一次性下載
4. **Latency 不重要** — fuzzy match 是 batch task（每集 1 次 build），不在 user-facing path
5. **一次 embed cached** 之後查 100 次 = 0 cost
6. **Ecosystem 成熟度** — vs Qwen3-Embedding 較新（2025-2026），HF / RAG middleware 整合廣

**Qwen3-Embedding-0.6B 為 Phase 2 swap 候選**（2026-05-02 調研發現）：
- MMTEB benchmark **+7.9% relative improvement vs BGE-M3**（Aryan Kumar 2026 Medium 比較）
- Apache-2.0 license + 600M 模型大小相近
- nakama 端設計：embedding.py 走 abstract interface，未來 swap Qwen3 不動 caller code
- 觸發條件（Phase 2）：「BGE-M3 fuzzy top-1 hit rate < 80% + Qwen3 在等量真實 fixture > 90%」即 swap

### Q4-4 — Quote 索引 = state.db + sqlite-vec

加 3 個 table（schema 在 plan）：

- `video_quote_sources` — 書 / 論文 metadata
- `video_quote_chunks` (sqlite-vec virtual table) — chunk text + embedding + bbox
- `video_quotes` — 跨集引用追蹤（episode × quote × source × chunk）

**選 sqlite-vec**（vs ChromaDB / 純檔案 .npy）：

1. nakama state.db 是 single source of truth（cost tracking / approval queue / FSM 都在這）
2. sqlite-vec 是 sqlite 原生 extension，0 額外 daemon
3. 跨集查詢「這本書你引用過幾次」一個 SQL join

### 副產品 — 中文 SRT（不另跑 LLM 翻譯）

修修澄清：英文論文 highlight 場景下，**中文字幕不需另翻譯**——他的 markdown narration 全程是中文，從 mistake-removal 後的乾淨 timeline 就能直接生成 SRT（中文 narration 詞 ↔ A-roll 詞級時間戳對應）。

**好處**：
- 翻譯品質 100%（修修本人講的話 vs LLM 翻譯）
- 0 翻譯成本
- 整支片 YouTube 字幕順便有了（SEO + 可及性 bonus）

### Q5 — Phase 1 component 範圍 = 6 個

**Phase 1 必做**：

| # | Component | 為什麼必做 |
|---|---|---|
| 1 | `<ARollFull>` | 基底 |
| 2 | `<TransitionTitle>` | 章節切換 |
| 3 | `<ARollPip>` | 投影片 + PiP，最常用 |
| 4 | `<DocumentQuote>` (模式 A) | 書頁 highlight，最高 ROI |
| 5 | `<QuoteCard>` (模式 B) | 風格化引文卡 |
| 6 | `<BigStat>` | 巨大數字 |

**Phase 1 不做**（Phase 2 backlog）：

- `<BookHero>` 書封 3D 動畫
- `<ARollSplit>` 列表跑
- `<MotionGraphics>` generic
- `<Beat>` 純視覺沉默
- `<Recap>` 結尾總結
- `<EndScreen>` YT end card
- **角色動畫**（character mascot）— 等 character asset library ready

---

## Consequences

### 立即影響

1. **新 subproject `video/`**（Node.js + Remotion + TypeScript）— `package.json` / `tsconfig.json` / 6 component / parser / FCPXML emitter
2. **新 Python 模組 `agents/brook/script_video/`** — pipeline / mistake_removal / pdf_quote / embedding / srt_emitter / fcpxml_emitter
3. **新依賴** — `FlagEmbedding` (BGE-M3) + `sqlite-vec` Python binding 加進 `requirements.txt` + `pyproject.toml`
4. **新 state.db schema** — 3 個 table（migration 同 PR 落地）
5. **新 `data/script_video/` 樹**（per-episode + cache）
6. **新 CLI** — `python -m agents.brook.script_video --episode <id>`
7. **transcribe skill 重用** — 不改 transcribe，只在 pipeline 第一階段呼叫

### 對既有 ADR 的影響

| ADR | 影響 |
|---|---|
| ADR-001（agent role） | **無**。Brook 仍 Composer |
| ADR-013（transcribe）| **無**。重用 WhisperX pipeline |
| ADR-014（RepurposeEngine）| **無**。本 ADR 是 sibling 不是 extension |
| ADR-006（HITL approval queue）| **無**（Phase 1）。Phase 2 若做 Bridge UI review surface 才接 |

### 工程量估（Phase 1）

依 plan slice 拆分 5 個 PR：

| Slice | 重點 | 估時 |
|---|---|---|
| 1. 骨幹 | DSL parser + WhisperX align + Mistake removal + FCPXML 生成 | ~3 天 |
| 2. 場景 | 6 個 Remotion component + Studio preview | ~2 天 |
| 3. 引用 PDF | `shared/pdf_parser` 整合 + bbox + DocumentQuote 渲染 | ~2 天 |
| 4. Embedding | BGE-M3 setup + state.db sqlite-vec schema + fuzzy match pipeline | ~2 天 |
| 5. 端到端 | 一支真實 dry-run 影片 vs 人工剪比對 | ~1 天 |

合計 ~10 工程天（agent 工時，不是 wall-clock）。修修 wall-clock 上 sandcastle / Mac 副機 AFK 跑可顯著壓縮。

### 風險

| 風險 | 機率 | mitigation |
|---|---|---|
| Remotion render B-roll segment 速度不如預期 | 低 | Remotion 官方 examples 證實單 segment 15s 完成；批次平行 |
| FCPXML 1.10 schema 在 DaVinci 解析有 quirk（如 lane offset 不對齊） | 中 | 早期 dry-run 驗 + DaVinci forum 已知 quirk 清單預讀 |
| 拍掌 marker 偵測在環境噪音下失效 | 中 | calibrate threshold per-episode + 漏網的 alignment β fallback 補位 |
| BGE-M3 cross-lingual 對某些英文論文 jargon 表現飄 | 低 | top-3 候選 + `match_index` 手動指定 |
| Robin metadata 命名衝突（同名書、不同版本） | 中 | source_id 加 disambiguator（年份 / 譯者）+ Robin 端 frontmatter 規範 |
| sqlite-vec extension 在某 Python wheel 安裝失敗 | 低 | 文件記錄安裝步驟，CI 環境已知 |
| 修修拍掌新習慣養成失敗 | 低 | β fallback 補位；β 標出來修修 DaVinci 30s 處理 |

### 不變項

- `transcribe` skill SKILL.md / 觸發詞 / pipeline 完全不動
- `shared/pdf_parser.py`（pymupdf4llm）不動
- Robin KB 結構不動（只 read metadata）
- ADR-001 / ADR-014 全套 unchanged
- 修修既有 DaVinci project template / preset / 字型 / 配色全照舊（FCPXML 只標 clips、不強加 styles）

---

## Alternatives Considered

**A. 套 ADR-014 RepurposeEngine 為 Line 4** — 拒絕。RepurposeEngine 是 fan-out parallel pattern（Stage1Extractor → multiple ChannelRenderer），本 workflow 是 sequential pipeline（parse → align → fuzzy match → render → composite），硬套會強加 stage1 schema + parallel 概念債。本 ADR 為 sibling 而非 extension。

**B. 直出 mp4 上 YT（no DaVinci）** — Phase 1 拒絕。100% 自動化的 quality bar 要求 mistake removal + B-roll 對齊接近完美，工程量爆炸，失敗模式劣（一支 B-roll 對齊錯了只能整段重 render）。Phase 2 升級條件 = 修修進 DaVinci「從沒真改什麼」時加 `--direct-mp4` flag。

**C. 純文字對齊（不接 BGE-M3）** — 拒絕。修修引文跟原文字面差異普遍（標點 / 斷句 / 簡繁 / 潤飾 / 漏字），exact match 必失敗。中英 cross-lingual 也是必要。BGE-M3 本地 0 月費 SOTA 是最佳選擇。

**D. ChromaDB 取代 sqlite-vec** — 拒絕。多一個 daemon、跟 nakama 既有 state.db 哲學分裂。sqlite-vec 是 sqlite 原生 extension，無 daemon、Python `sqlite3` 直接用，足夠用。

**E. After Effects template 取代 Remotion** — 拒絕。AE template 不易程式化、無 React component reusability、無 hot reload preview，且授權成本（AE 訂閱）。Remotion 是專為 programmatic video 設計，TypeScript / React 生態。

**F. 新 agent（Sniper / Apoo）取代 Brook 子模組** — 拒絕。本 workflow 不需獨立 Slack bot、不需 cross-agent event 互動、跟其他 agent 沒事件耦合，沒理由動 ADR-001。

**G. 純 standalone scripts/（不在 agent 體系）** — 拒絕。脫離 agent 觀測 / cost tracking / Bridge UI surface，未來想加 review UI 要從零接。

---

## Open Questions（不阻擋落地）

- **Q1**：FCPXML 1.10 vs 1.11 — 1.11 是 macOS Sonoma+ Final Cut Pro 11.0+ 才支援，DaVinci 較保守仍用 1.10。先採 1.10，1.11 是未來事
- **Q2**：拍掌 audio threshold 是否要 per-microphone calibrate？— Phase 1 假設修修錄音環境穩定（同一支 mic）一次性 calibrate；Phase 2 加 auto-calibrate
- **Q3**：BGE-M3 model 一次性下載要不要走 Robin 既有 model cache 路徑？— Phase 1 各模組獨立 cache（HF default 路徑），Phase 2 評估統一
- **Q4**：Phase 0 dry-run 用哪一支舊片？— plan 中決定，會在 Slice 5 拍板
- **Q5**：character animation Phase 2 component 命名（`<CharacterScene>` vs `<Mascot>` vs `<Persona>`）— 等 asset library ready 再凍結

---

## References

- **PRD**: [#310 Phase 1 PRD](https://github.com/shosho-chang/nakama/issues/310)（approved 2026-05-02）
- **Plan**: [docs/plans/2026-05-02-script-driven-video-production.md](../plans/2026-05-02-script-driven-video-production.md)（含完整技術選型調研、5 slice 拆分、風險登記簿）
- ADR-001 — agent role assignments（Brook = Composer）
- ADR-013 — transcribe 引擎 WhisperX
- ADR-014 — RepurposeEngine plug-in interface（this ADR is sibling）
- `shared/pdf_parser.py` — pymupdf4llm 重用點
- `agents/robin/` — Robin KB metadata source（textbook ingest v2 frontmatter）
- [feedback_minimize_manual_friction.md](../../memory/claude/feedback_minimize_manual_friction.md) — DaVinci import friction 是修修可接受的（既有工作流）
- [feedback_quality_over_speed_cost.md](../../memory/claude/feedback_quality_over_speed_cost.md) — BGE-M3 + DaVinci 路線跟 quality > speed > cost 對齊
- [feedback_no_handoff_to_user_mid_work.md](../../memory/claude/feedback_no_handoff_to_user_mid_work.md) — grill 完→實作做完→修修最後驗收
- Claude Chat session 2026-05-01 — Remotion architecture + 場景 vocabulary research（外部研究 reference）
- BGE-M3 paper：M3-Embedding（FlagEmbedding 2024）
- Remotion docs — https://remotion.dev/
- FCPXML 1.10 spec — Apple Developer
- sqlite-vec — https://github.com/asg017/sqlite-vec
- PyMuPDF — https://pymupdf.readthedocs.io/
