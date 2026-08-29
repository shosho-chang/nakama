# Finished Cut Production / 林之晨 L04 — Claude Handoff

更新時間：2026-08-29（Asia/Taipei）

## 使用者目標

1. Long Highlight 從新的 **Finished Cut Production** 完整跑通，不再由 Legacy Creative DAG、`run_short_*` 或 Bridge 隱式 fallback。
2. 林之晨 L04 必須是新的 Release；Bridge 顯示完整 B-roll／title／transition timeline，並可逐事件回饋。
3. `supporting_title` 分類與所有新產生能力已退役；不能 alias 或升格成 Hero。
4. Fullscreen Transition 必須維持先前已核准的 B2 `transition_title_wide + paper_hand` 視覺語彙。
5. Bridge 左側播放器＋完整 Timeline 要有獨立縱向 scrollbar，不受右側 feedback pane 捲動牽連；不要橫向 scrollbar、ellipsis 或被裁切的播放器／sidebar。

## Workspace / Git

- Worktree：`E:\nakama\worktrees\lin-zhi-chen-e2e`
- Branch：`codex/lin-zhi-chen-e2e`
- HEAD：`86d47d8f45111c971ac04bd81cfb1d3bed156e91`
- 工作樹非常髒；新的 `finished_cut_production/` package、ADR-066、大量 tests 仍是 untracked，其他 Bridge/watcher/legacy caller migration 是 modified。
- **不要 reset、checkout 或覆蓋現有變更。先讀 `git status --short` 和逐檔 diff。**

## 今日已完成的 live L04 狀態

- Episode：`G:\Footages\20260805 林之晨`
- Cut：`long3-fresh-20260828-r4`
- Current manifest：`manifest-135e2060e0d612e2499658a1`
- Current Release：`release-af65a1d7a2ac611eb78be493`
- Release SHA-256：`7906be7a39f348d2be4cc146b6c02883145868cbf0680599181bd71ff951b135`
- Preview：`G:\Footages\20260805 林之晨\highlights\staging\finished-cut\4d516fa4c9efbd9d609c824d\preview.mp4`
- Preview SHA-256：`728520b58acb6d2bba27480d13f6b9c81c982a2e757908de14c8135422abb4aa`
- Resolve transaction：`resolve-368cb9c920c9c09473ce74e3`
- Release transaction receipt：`resolve-receipt-278cda152a55027950d9ff8c`
- Component count：15（8 B-roll、2 Hero、5 Fullscreen Transition；0 supporting title）
- Bridge 8128 current URL 回 HTTP 200。

### 五張新的 Fullscreen Transition assets

| event | Active Asset Store ref |
|---|---|
| `evt_chapter_k_shaped_future` | `asset-sha256:a534a14ee09d7c8446e7f440c004c015197fe5d8f6aca9c4930f0026d46f294d` |
| `evt_chapter_abundance` | `asset-sha256:431521afd16c9aada45e40c719b5cb7d0e8037052a0f5fdba34c1bbbe5ada952` |
| `evt_chapter_captivity` | `asset-sha256:c0a0f5661f00b0a35a34de465e384004d64d5b8567176d3f02a29a88e8458274` |
| `evt_chapter_deliberation` | `asset-sha256:a68cb48de7759b8d4c810ee18a1784635e7596d1593d197ba9646aee28949618` |
| `evt_chapter_self_actualization` | `asset-sha256:2aa5eaac317776cb9abd933257ef65799beb622d2ee200271ec491b46d18680d` |

抽幀證據：

- `E:\nakama\.cache\fullscreen-transition-audit\current-restored-49.png`
- `E:\nakama\.cache\fullscreen-transition-audit\current-restored-271.png`

## Fullscreen Transition 修正

### 根因

ADR-066 實作新 renderer 時，沒有沿用已定版的 `transition_title_wide.html`，而是在 `_long_visual_renderer.py` 重新寫了一張純米色、112px、無 kicker 的近似卡。程式把它誤稱 `canonical_paper_hand`。這是 architecture retirement 不當改變 visual language。

舊定版 contract：

- `.claude/skills/longform-cut/SKILL.md:186`
- `video/compositions/transition_title/compositions/transition_title_wide.html`

### 本次 code changes

- `agents/brook/script_video/finished_cut_production/_long_visual_renderer.py`
  - Fullscreen Transition recipe 改為 `fullscreen_transition:v4` / `style_name="paper_hand"`。
  - 恢復紙紋、章節 kicker、128px 大字、手繪短槓、等寬手繪底線與進場動畫。
- `agents/brook/script_video/finished_cut_production/_engine.py`
  - 新 chapter recipe identity 升到 v4，避免命中舊 v3 asset cache。
- `agents/brook/script_video/finished_cut_production/_hyperframes_renderer.py`
  - 保持 0.75s browser render 後延長至 3s，避免每張完整 browser render 超過 90 秒。
- Tests：
  - `tests/brook/script_video/test_finished_cut_long_visual_renderer.py`
  - `tests/brook/script_video/test_finished_cut_hyperframes_renderer.py`
- Episode-local pointer-last operation：
  - `.cache/finished-cut-production/lin-long3/restore_l04_fullscreen_transitions.py`

### 驗證

- Focused：16 passed / 3 explicit local smoke skipped。
- Real pinned HyperFrames + ffmpeg chapter render：1 passed / 56.36s。
- Current preview 49.0s 與 271.5s 抽幀：紙紋、章節、手繪線均可見。
- Bridge current URL：HTTP 200。

### 誠實限制

目前 v4 是 self-contained visual restoration，不是舊 Envato `paper-texture.mp4` 的 byte-identical motion background：

- 紙紋改為 inline procedural texture。
- 為避免每張完整 3 秒 HyperFrames 超時，只有進場動畫；最後畫面延長到 3 秒，沒有舊版 title 上滑退場。

使用者要求的是「樣式改回來」，目前畫面已恢復主要定版語彙；但長期 single source of truth 應直接讓新 renderer 使用 canonical `transition_title_wide` 和受 Active Asset Store 管理的 paper texture，而不是再維護第二份近似 HTML。

## Supporting Title 退役

- 新 Finished Cut Production 的 Director/DP worker schema、active projection vocabulary、derived renderer、policy、persistence、Resolve lane 已移除 `supporting_title`。
- Historical sealed Release parser 僅保留唯讀相容，不可產生新 revision authority。
- L04 原本五張 supporting title 已轉成 intentional A-roll，current Resolve V5 為 0。
- Episode-local operation：`.cache/finished-cut-production/lin-long3/suppress_l04_supporting_titles.py`
- 注意：舊 tests 尚有 supporting-title fixtures；production source 已退役，但 full suite 仍需清理 retired-behaviour tests。

## 新 Finished Cut Production 已完成的大項

- ADR：`docs/decisions/ADR-066-finished-cut-production-and-legacy-retirement.md`
- 新 package：`agents/brook/script_video/finished_cut_production/`
- Authority：ApprovedCut → Director AcceptedStage → DP AcceptedStage → derived assets → visual AcceptedStage → MaterializationPlan。
- Core-owned Visual Placement、Pre-release Event Correction、durable semantic dispatch recovery。
- Active Asset Store / neutral worker catalog 分離。
- Pinned HyperFrames、Long title/person-inset render、DaVinci Resolve exact UID transaction、Candidate/Release/v3 current pointer-last。
- Bridge read adapter：`thousand_sunny/adapters/finished_cut_review.py`
- CLI：`scripts/run_finished_cut_production.py`
- Bridge launcher：`scripts/start_bridge_8128.ps1`
- Watcher 與 Bridge router 已大幅改寫成 Finished Cut Production caller，但尚未整理 commit／完成 Legacy Creative DAG physical deletion。

## 重要架構債務

1. **Current Release 可重建 authority 尚未閉合**：
   - `runs/authority.json` 仍是原本 20-component plan。
   - supporting suppression 與 transition v4 replacement 由 episode-local deterministic operations 產生新 plan／Release。
   - current Release + committed transaction 是 canonical，但不能只靠 original run authority 重建同一 current plan。
   - 下一步應把 mechanical component suppression/replacement 收成正式 Targeted Revision 或 Release Amendment authority，不能長期依賴 `.cache` scripts。
2. **Canonical paper_hand 有兩份實作**：
   - 真正核准版：`transition_title_wide.html`。
   - 新流程目前另寫 `_paper_hand_chapter_document`。
   - 應消除雙寫，讓 Finished Cut Production 直接吃 canonical component/template + Active Store paper texture。
3. **工作樹未 commit**：不要一次 mega commit。至少拆成：
   - ADR/domain docs；
   - Finished Cut core/asset/renderer；
   - Resolve/materialization/release；
   - Bridge/watcher cutover；
   - UI；
   - tests/legacy retirement。
4. **舊碼仍在 repo**：production callers 已部分切斷，但 ADR-066 的 physical retirement/deletion gate 尚未真正完成。

## Bridge UI 待處理

使用者最新明確要求：

- 左側播放器＋Full Timeline 是一個獨立的 vertical scroll region。
- 右側 feedback pane 有自己的 vertical scrollbar，兩側互不牽動。
- 不需要 timeline 的 horizontal scrollbar；要讓完整時間範圍在容器內 fit。
- 左側 lane labels 不可 ellipsis，播放器／sidebar 不可被裁掉。
- 保持播放器 currentTime 與 timeline playhead 同步。

相關檔案：

- `thousand_sunny/templates/bridge/finished_review.html`
- `thousand_sunny/routers/highlight_review.py`
- `tests/test_finished_review.py`
- `tests/test_finished_cut_review_adapter.py`

不要只跑 DOM test；必須在 8128 用實際 viewport 截圖驗證。

## Claude 接手優先順序

1. 先讀本 handoff、ADR-066、Brook `CONTEXT.md`，再看 `git status`；禁止 reset。
2. 用 current Release ID / SHA 確認 Bridge 仍指 `release-af65a1d7a2ac611eb78be493`，不要重跑 Director/DP。
3. 修 Bridge 雙欄獨立縱向捲動與 timeline fit-width；實機截圖驗收。
4. 把 Fullscreen Transition v4 收斂到 canonical template + Active Store paper texture；保留 recipe version bump，局部替換五張即可。
5. 把 L04 的兩個 episode-local mechanical operations 收成正式、可重建的 Release authority；不要手改 run state。
6. 清理 supporting-title stale fixtures，跑 focused Finished Cut + Bridge tests。
7. 分 commit；再完成 Legacy Creative DAG caller scan、archive/deletion gate。

## 不要做

- 不要重跑整個 L04 semantic pipeline。
- 不要把 historical sealed Release 當新 authority。
- 不要恢復 `supporting_title`，也不要 alias 成 Hero。
- 不要讓 Bridge fallback 到 historical manifest。
- 不要在 current Resolve timeline 原地編輯；一律 duplicate/apply/preview/commit/pointer-last。
- 不要刪 G: 歷史資料，直到 ADR-066 archive/retention gate 明確通過。
