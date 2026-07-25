# ADR-050: Video Production Line 歸 Brook + FCPXML 單一 Builder Seam

**Date:** 2026-07-03
**Status:** Accepted（2026-07-03 修修裁決 D1-A / D2-A / D3-A / D4-A，四題全數採納建議選項）
**Amends:** [ADR-032](ADR-032-hyperframes-broll-pipeline.md)（§0.1 ownership 反轉；pipeline 技術設計其餘全數沿用）/ [ADR-001](ADR-001-agent-role-assignments.md)（Foundry 條目改寫）/ [ADR-033](ADR-033-thumbnail-generation-pipeline.md)（`agents/foundry/` 路徑引用隨遷移更新）
**Disposes:** [ADR-015](ADR-015-script-driven-video-production.md) 殘留 code（`agents/brook/script_video/` Slice 1 骨架）+ stale issues #314–#317
**Related:** ADR-027（Brook narrow — 本 ADR 與其邊界的 cross-check 見 §Context）

---

## Context

### 觸發

2026-07-03 架構審計（improve-codebase-architecture skill）+ 修修明確表態：

> 「Foundry 會跑出來我之前就覺得很奇怪，這不是我預設中的。我還是希望把 Video Script Pipeline 給 Brook 來做。」

這與 ADR-032 §0.1（panel 採納 Gemini push-back、特意開 `agents/foundry/` 新 agent 以尊重 ADR-027 narrow Brook）正面衝突，需要正式修訂而非默默搬。

### 事實盤點（2026-07-03 實讀 code 驗證）

1. **兩套 FCPXML 1.10 emitter 並行、零共用**
   - `agents/brook/script_video/fcpxml_emitter.py`（191 行）— A-roll ripple-delete timeline（ADR-015 record-first 產物）
   - `agents/foundry/fcpxml_emitter.py`（243 行）— V1 talking head + lane-1 B-roll overlay（ADR-032 SRT-first 產物）
   - DaVinci quirk 知識已各自分岔：Brook 版有「`<asset-clip>` vs `<clip>`（FCP 拒收後者）」註記（`fcpxml_emitter.py:162-164`）+ deterministic sha256 UID（`:50-52`）；Foundry 版有「media-rep 必須 absolute `file://` URI，相對路徑被 DaVinci 靜默拒收」（`fcpxml_emitter.py:162-165`）+ `--fcpxml-version` 1.11/1.9 fallback（`:26-28`）+ content-addressed `b_roll_<hash>.mp4`（ADR-038 §D2，`:108-118`）。**修一邊另一邊不會跟上** — audit claim 屬實。

2. **兩 pipeline 共用 `data/script_video/<episode>/` 且會相撞**
   - 兩邊都寫 `out/episode.fcpxml`（brook `pipeline.py:73-75`；foundry `fcpxml_emitter.py:91`）— 同 episode 跑兩條會互相覆蓋，無任何 provenance 標記。
   - 輸入契約不同（brook 要 `script.md`；foundry 要 `episode.yaml` + `transcript.srt` + `storyboard.yaml`），目錄長相全靠人腦記。
   - 附帶 latent bug：foundry `_DATA_ROOT = Path("data/script_video")` 是 **cwd-relative**（`agents/foundry/pipeline.py:28`），brook 是 repo-root anchored（`agents/brook/script_video/pipeline.py:28-29`）— 從非 repo root 呼叫 foundry CLI 會寫到錯誤位置。
   - 現況只有一個 episode 目錄（`data/script_video/smoke-001`，brook smoke fixture），撞擊是潛在而非已發生。

3. **ADR-032 從未正式 sign-off** — Status 至今是「Draft v2（待修修最終 sign-off）」（`ADR-032:4`），但 Phase 1 五個 PR（#717/#720/#723/#724/#726）+ ADR-033 thumbnails（#737/#739）+ ADR-038 系列（#782/#783/#784/#800）已全數 merge。本修訂不是推翻 Accepted 決策，是**把一個 shipped-but-never-signed-off 的 Draft 修到 owner 認可的形狀再一次 sign-off**。

4. **頂層文件 drift 是修修 mental model 落差的直接根源**
   - `CONTENT-PIPELINE.md:59`（影片 channel 對照表）與 `:132`（Agents × Stages Brook 列）至今寫的是「Script-Driven Video pipeline（`agents/brook/script_video/`）」，**全文零提 foundry**。
   - `CONTEXT-MAP.md:23,33-34,64` 仍以 ADR-015 語彙描述 brook script_video ↔ video/ subproject 關係。
   - 即：repo 內同時存在兩套矛盾的 truth — ADR-001/ADR-032 說 video 歸 foundry；修修日常 anchor 的 CONTENT-PIPELINE.md 說歸 Brook。「Foundry 跑出來很奇怪」是文件系統性失職，不是修修記錯。

5. **`agents/brook/script_video/` 凍結在 ADR-015 Slice 1**（2026-05-03 PR #320 之後無實質開發）：Stage 1 ASR 永久回傳 `[]`（`pipeline.py:139-146`）、Stage 3/4 都是 stub（`:206-213`）。唯一有真實價值的資產是 **mistake removal（拍掌 marker 偵測 + ripple-delete）**，DaVinci import smoke 實測通過（2026-05-03 桌機）。ADR-015 的 Slice 2–5 issues（#314/#315/#316/#317）**至今 open**，且內容引用已被 ADR-032 廢棄的技術（Remotion components、PyMuPDF DocumentQuote、BGE-M3 fuzzy match）。

6. **`agents/foundry/` 是活躍開發線**：3,297 行 Python（vs brook script_video 1,064 行）+ Bridge router（`/foundry` prefix）+ `.claude/skills/foundry-replan/` + `shared/foundry_versions.py` + 完整測試。ADR-032 Phase 1 之後又長出 ADR-033 thumbnail workers 與 ADR-038 系列（beat editor / storyboard diff / export hash / silence-detection hint-beats）。

7. **ADR-038 文件缺失**（governance gap，本 ADR 順帶記錄、另開 task 處理）：4 個 merged PR 的 commit message 與 code 註解都引用「ADR-038 §D2」，但 `docs/decisions/` 內**不存在 ADR-038 檔案**。

8. **命名慣例**：nakama agent 全員取自 One Piece 船員（Robin / Nami / Zoro / Brook / Franky / Usopp / Sanji / Chopper），Foundry 是唯一破例 — 這是「不是我預設中的」的另一個成因。

### 與 ADR-027 紅線的 cross-check（feedback_adr_principle_conflict_check 紀律）

把 video line 交給 Brook **不觸犯** ADR-027 的紅線，理由：

- ADR-027 narrow 掉的是「LLM 從零生正文」（Stage 4 atomic content 必須人產）。Video production line 是 **Stage 5 製作**：吃修修已錄好的 talking head + 修修講出來的 SRT，LLM 只提案 B-roll beat（storyboard），且有兩層 HITL approve（text → visual）。不產完成句、段落、第一人稱正文。
- Panel 當時反對寄居的真正論點是 **monolith bloat**（Gemini：「forcing it into agents/brook/ risks bloating Brook into a monolith」），不是紅線。Bloat 用 sub-package 硬邊界緩解（見 D1）。
- Ownership 歸屬本質上是 owner 的 mental model / 主觀職責問題（[feedback_hitl_gate_serves_subjective_taste](../../memory/claude/feedback_hitl_gate_serves_subjective_taste.md)：品味與心智模型只有修修能驗收）— panel 可以對技術方案投票，不能替 owner 決定他想找誰做影片。
- 後續效應：Brook sub-responsibility 從 3 項變 4 項（Scaffold + Repurpose + SEO Audit + Video Production）。ADR-027 的 open follow-up「Brook scope 第三度收斂（SEO 歸屬檢討）」維持 open，本 ADR 不擴大也不解決它。

---

## Decision（2026-07-03 修修裁決；各題替代選項保留存查）

### D1 — Ownership：foundry 機器搬進 `agents/brook/script_video/`（✅ 裁決採納）

**選項 A（採納）**：foundry 全部機器（planner / beat_aligner / render_dispatcher / render_workers（含 thumbnail）/ fcpxml_emitter / beat_editor / replan_agent / silence_detection / schemas / layouts / prompts / edit_log）**物理搬進 `agents/brook/script_video/`**，取代該路徑下的 ADR-015 Slice 1 骨架（處置見 D3）。

- 路徑名沿用 `script_video` — 這正是 CONTENT-PIPELINE.md 與修修口中「Video Script Pipeline」的名字；文件與心智模型零改動成本。
- **Foundry 作為 agent 退場**（ADR-001 條目移除），「video production line」記為 Brook 的第 4 個 sub-responsibility。
- Sub-package 保持硬邊界（自己的 README / CONTEXT.md / tests 樹），緩解 panel 的 monolith 顧慮 — Brook 其他模組不得 import 其內部，只准走 CLI / pipeline 頂層 API（call-not-host 內化成 package 邊界）。
- Bridge route `/foundry/*` 改 `/brook/video/*`（留 301 redirect 一版）；CLI 改 `python -m agents.brook.script_video`；skill `foundry-replan` 改名 `brook-replan-beat`（觸發詞遷移）。
- ADR-033 thumbnail pipeline 的 `agents/foundry/` 路徑引用同步 amend。

替代選項：
- **B（掛名不搬）**：文件宣告 Brook 為 owner、code 留 `agents/foundry/`（foundry 降格為「Brook 的生產線廠房」）。省一個 mechanical PR，但路徑≠owner 的失調永久存在 — 正是這次 drift 的成因，不建議。
- **C（維持獨立 foundry agent）**：只補文件同步。尊重 panel 原判，但違背修修明確表態，僅列存查。

### D2 — FCPXML 統一成單一 builder seam：`shared/fcpxml/`（✅ 裁決採納）

**選項 A（採納）**：抽 `shared/fcpxml/builder.py`（確定性函式庫，符合 skill 三層架構「確定性函式 → shared/*.py」）：

- 共同 model：`TimelineDoc(format, fps, version)` / `Asset(path, duration, has_audio, …)` / `Clip(ref, offset, start, duration, lane)`。
- 所有 DaVinci quirk 知識**集中一處**：absolute `file://` media-rep、`<asset-clip>` not `<clip>`、deterministic UID、rational `N/30s` duration、NDF、`--fcpxml-version` 1.10/1.11/1.9 fallback、DOCTYPE 寫法。
- 兩種 timeline 形狀都是 builder 的 adapter：**ripple-delete 形**（cleanup stage 用，若 D3 保留）與 **overlay 形**（B-roll storyboard 用）。
- Golden fixture tests 搬到 builder 層；既有兩邊 emitter tests 改打 adapter。

替代選項：
- **B（builder 收進 Brook video package 內）**：若 D3 裁決整包退役 record-first、caller 只剩一個，可不進 shared/。但 thumbnail / 未來 repurpose 影片化都可能要 FCPXML，shared/ 是便宜保險。
- **C（不統一，只 cross-port quirk 註記）**：最小工，但 audit 指出的 drift 結構原封不動，不建議。

### D3 — Workflow 收斂：單一 pipeline、mistake removal refit 成選配前置 stage（✅ 裁決採納）

**選項 A（採納）**：**只留一條 workflow**，record-first 的唯一真實資產（拍掌 marker mistake removal）refit 成選配 `cleanup` stage：

```
raw_recording.mp4
  → [選配] cleanup（拍掌 marker 偵測 → ripple-delete FCPXML or 清乾淨的 mp4）
  → /transcribe（SRT）
  → plan（storyboard）→ render → emit        ← ADR-032 機器原封不動
```

- 退役清單：markdown DSL + `video/src/parser/` TS parser（storyboard.yaml 已取代 DSL）、brook 版 `fcpxml_emitter.py`（被 D2 builder 取代）、`manifest.py` / `srt_emitter.py` / 舊 `pipeline.py` 五階段殼。`mistake_removal.py` + 其測試存活、掛進新 pipeline 當 `cleanup` subcommand。
- 關閉 stale issues #314/#315/#316/#317（留 comment 指向本 ADR）。
- ADR-032 的「mistake-cleanup out of scope」invariant 改寫為「cleanup 是同一條 line 的選配前置 stage，非平行 pipeline」。
- SRT timing 與 cleanup 後 mp4 的一致性沿用 ADR-032 既有 sanity check（SRT 末 timestamp vs mp4 duration 差 ≥1s → warn）。

替代選項：
- **B（record-first 整包退役）**：若修修已放棄拍掌 marker 錄影習慣，`mistake_removal.py` 一併刪除，pipeline 只剩 SRT-first。**這是修修的使用習慣事實，只有他能答**。
- **C（兩條並行保留 + provenance）**：維持現狀加標記。audit 前提（quirk 修一邊另一邊不跟）原封不動，不建議。

### D4 — Episode 目錄 provenance：episode.yaml 標記 + CLI 啟動驗證（✅ 裁決採納）

**選項 A（採納）**：

- `episode.yaml` 增 `pipeline:`（D3 選 C 時標 `record-cleanup` / `broll-storyboard`）或 `stages:`（D3 選 A/B 時記錄 `[cleanup, transcribe, plan, render, emit]` 各 stage 完成時間）。
- 兩個（或單一）CLI 啟動時驗證：episode 目錄若屬另一條 pipeline / 缺 `episode.yaml` → fail loud 附建立指引，不靜默混寫。
- `out/` 產物檔名帶 stage 前綴避免互相覆蓋（若 D3 收斂單線則只剩一個 `episode.fcpxml`，自然無撞）。
- 順手修 foundry `_DATA_ROOT` cwd-relative bug（改 repo-root anchored，跟 brook 版同式）。

替代選項：**B（分目錄物理隔離）**`data/script_video/` vs `data/broll/` — 若 D3 收斂單線就沒必要；**C（只修 out/ 檔名衝突）** — 最小工，不留 machine-readable 記錄。

---

## Consequences（依裁決 D1-A / D2-A / D3-A / D4-A）

1. `agents/foundry/` 整樹遷移至 `agents/brook/script_video/`，foundry 自 agent map 除名；git history 可 `git log --follow` 追。
2. 新 `shared/fcpxml/`（builder + 兩 adapter + golden tests）。
3. `agents/brook/script_video/` 舊 Slice 1 骨架退役，`mistake_removal.py` 存活為 `cleanup` stage。
4. Bridge `/foundry/*` → `/brook/video/*`（301 一版）；`.claude/skills/foundry-replan` → `brook-replan-beat`；`shared/foundry_versions.py` 更名或註記。
5. 文件同步（**與 code 同 PR**，Vault 寫入規則同精神）：CONTENT-PIPELINE.md（Stage 5 Brook 列）、CONTEXT-MAP.md（§video / §brook 關係）、ARCHITECTURE.md、ADR-001 / ADR-032 / ADR-015 / ADR-033 status 與 amendment 註記、`thousand_sunny` inventory/architecture templates。
6. Issues #314–#317 關閉。
7. `memory/claude/` 內 `project_foundry_agent.md` / `project_foundry_phase1_complete.md` 補 supersede 註記。

### 受影響 ADR 一覽

| ADR | 影響 |
|---|---|
| ADR-032 | §0.1 被本 ADR 取代；其餘技術設計（SRT-first、exact-copy anchor、Hyperframes、Tier 2 UI、layouts YAML、edit_log）**全數沿用**。Status 隨本 ADR sign-off 一併定為 Accepted (as amended by ADR-050) |
| ADR-001 | Foundry 條目移除；Brook 條目 amend 加第 4 sub-responsibility「Video Production Line（ADR-050）」 |
| ADR-015 | Status 補「殘留 code 已由 ADR-050 處置（mistake removal 存活為 cleanup stage，其餘退役）」 |
| ADR-033 | `agents/foundry/` 路徑引用 amend 為新路徑；決策內容不變 |
| ADR-027 | **不動**。紅線 cross-check 見 §Context；「第三度收斂」follow-up 維持 open |
| ADR-038（缺檔） | 另開 task 補寫；補寫時直接用新路徑 |

### 風險

| 風險 | 機率 | mitigation |
|---|---|---|
| 搬遷 PR 面積大（3.3k LOC + router + skill + templates）夾帶行為改動 | 中 | 搬遷 PR **純 mechanical**（只改路徑與 import，禁止趁機重構）；tests 全綠 + Bridge UI golden path 手走（feedback_ui_browser_verification_before_merge） |
| emitter 統一時兩邊 quirk 語意互斥（e.g. UID 策略不同影響 DaVinci re-import 識別） | 低-中 | builder 層保留策略參數；DaVinci import fixture 兩形狀各跑一次（沿用 ADR-032 acceptance gate） |
| `/foundry` 路由改名斷修修書籤 / bridge nav | 低 | 301 redirect 保留一版 + bridge nav 同 PR 更新 |
| cleanup stage 與 SRT timing 錯位（錄完先 cleanup 再 transcribe 的順序沒被遵守） | 中 | pipeline 驗證順序 + 既有 duration sanity check；README 寫清楚順序契約 |
| 修訂本身重蹈「shipped but never signed off」 | — | 本 ADR **必須拿到修修 explicit sign-off 才動工**（D1–D4 裁決即 sign-off 前置） |

---

## Alternatives Considered

| Alternative | 為何沒選 |
|---|---|
| 維持 foundry 獨立 agent、只補文件（D1-C） | Owner 明確表態要 Brook；ownership 是主觀職責，panel 技術判斷不能替 owner 拍板 |
| Brook 掛名、code 留原地（D1-B） | 路徑≠owner 的失調正是本次 drift 成因；便宜但把病根留著 |
| 兩套 emitter 各自為政 + 註記互相參照（D2-C） | Audit 指出的 quirk drift 結構不變；DaVinci quirk 是實測換來的知識，散兩處必再分岔 |
| 兩條 workflow 並行 + provenance（D3-C） | 1,064 行凍結骨架的維護稅 + 心智負擔，換不到任何 record-first 獨有價值（唯一價值 mistake removal 可 refit） |
| 另開新 agent 名（One Piece 命名）承接 video line | 又一次引入修修沒預期的名字；Brook 已是修修 mental model 的答案 |

---

## Open Questions（不阻擋 sign-off）

1. `shared/foundry_versions.py` 的 `EXPORT_VERSION` 更名時要不要順勢 bump（強制全 episode cache flush）？— 實施 PR 內決定
2. cleanup stage 輸出形狀：ripple-delete FCPXML（進 DaVinci 手動 export 乾淨檔）vs 直接 ffmpeg 剪出乾淨 mp4？— 待 D3 裁決後、實施前跟修修確認一次錄影工作流
3. 本修訂要不要跑 panel？建議：**輕量單模型 audit**（Codex `gpt-5` + medium reasoning，per feedback_codex_medium_reasoning_for_long_audit）只審 D2 builder 設計與遷移計畫；D1 ownership 是 owner 主觀裁決，panel 無置喙餘地

---

## References

- 2026-07-03 架構審計 session（improve-codebase-architecture skill）— 觸發本 ADR
- ADR-032 §0.1 + §Panel Integration（foundry 決策原始出處；Status Draft v2 從未 sign-off）
- `memory/claude/project_foundry_phase1_complete.md` / `project_foundry_agent.md` / `project_broll_dual_path_architecture.md`
- `memory/claude/project_script_video_phase2a.md`（ADR-015 Slice 1 ship 紀錄 + DaVinci smoke）
- `memory/claude/feedback_hitl_gate_serves_subjective_taste.md`（主觀職責歸 owner）
- `memory/claude/feedback_adr_principle_conflict_check.md`（本 ADR §Context cross-check 之依據）
- 實施計畫：[docs/plans/2026-07-03-video-line-consolidation-plan.md](../plans/2026-07-03-video-line-consolidation-plan.md)
