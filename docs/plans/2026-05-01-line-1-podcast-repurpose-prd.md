# PRD: Line 1 Podcast Repurpose — 訪談轉錄稿三 channel 自動重製

> Drafted from `/grill-with-docs` session 2026-05-01。
> 待 file 成 GitHub issue（label: `enhancement`, `needs-triage`）。
> Grilling decision table 在本 doc 末「Grilling Decisions」段落。

---

## Problem Statement

修修是 health & wellness / longevity 內容創作者，每集 podcast 訪談錄音（約 1 小時）後，要把訪談轉成三個 channel 的內容：

- **Blog 人物專訪**（部落格，~2-3K 字，敘事體 + 受訪者引言）
- **Facebook 短文**（社群媒體，300-500 字，hook-driven）
- **Instagram Carousel**（5-10 張卡的圖文序列，起承轉合視覺節奏）

目前這個流程是純手動的：修修要逐字看 SRT 校正稿，自己組織人物敘事寫 blog，手動縮短做 FB post，手動拆段做 IG 文字 — 每集多花一兩個小時的重複勞動，且容易在第三 channel 失神導致品質下滑。三條內容生產線（[`project_three_content_lines.md`](../../memory/claude/project_three_content_lines.md)）2026-04-30 凍結時，**Line 1 是修修最緊急的功能**。

修修訂下兩條最高指導原則必須在解決方案內貫徹：

- **盡量減少手動操作**（[`feedback_minimize_manual_friction.md`](../../memory/claude/feedback_minimize_manual_friction.md)） — 每個手動步驟 = 摩擦力
- **品質為第一優先**（[`feedback_quality_over_speed_cost.md`](../../memory/claude/feedback_quality_over_speed_cost.md)） — 不求速度也不求省錢

## Solution

一條 SRT-driven repurpose pipeline，整合 transcribe + Brook compose + Bridge UI review + Usopp publish 既有基建：

1. **Diarization 自動化**：WhisperX 內建 diarization 重啟（PR #273 反向 cherry-pick），輸出 SRT 含 `[SPEAKER_00]` / `[SPEAKER_01]` label
2. **校正人工 gate**：修修在 VSCode 校正 SRT（含 diarization 微調），既有工序不變
3. **CLI 觸發**：`run_repurpose.py <srt> --host 張修修 --guest 朱為民醫師`
4. **Brook 跑 two-stage pipeline**：
   - **Stage 1**（Sonnet 4.6）：從 diarized SRT 萃取結構化 JSON — 8 段 narrative arc + 金句候選 + title 候選 + episode_type
   - **Stage 2**（Sonnet 4.6 × N）：三 channel 並行 fan-out render，各吃 Stage 1 JSON + 對應 style profile
5. **Artifact 落 staging folder**：`blog.md` + 4 篇 `fb-{tonal}.md` + `ig-cards.json`
6. **Bridge UI 3-panel review surface**：`/bridge/repurpose/<id>` edit-in-place + per-channel approve（PR #140 mutation pattern reuse）
7. **Auto-publish blog**：approve blog → Usopp 既有 publisher 推 WordPress draft（不直接 publish 到 live，HITL approval queue ADR-006 不繞過）
8. **Manual paste FB / IG**（Phase 1 限制）：approve FB/IG → mark done + clipboard copy + 修修在 Meta Business Suite attach 圖片並發布

未來 Phase 2 升 Bridge UI button 替代 CLI、Phase 3 補 Usopp FB/IG adapter 朝 full auto。

修修最高指導原則的具體落地：

- **減少手動**：CLI 一個 command 取代逐個 channel 手寫；Bridge UI 一個 surface 取代多檔切換；blog auto-publish 取代 copy-paste WP；IG episode_type 自動 routing 取代修修每集挑卡數
- **品質第一**：Stage 1 + Stage 2 全 Sonnet 4.6（不降 Haiku）；two-stage 架構（Stage 1 萃取共享、Stage 2 渲染分流）取代 single-shot；FB 4 tonal variants 給品味 leverage；IG episode_type routing 對應 Chris Do AIDA 框架
- **scope 砍除前看下游**：PR #273 砍 diarization 的決策反轉 — Line 1 三 channel 都需要 speaker label

## User Stories

1. As 修修，I want a single CLI command after校正 SRT，so that 我不用為每個 channel 各跑一次工具或寫 prompt。
2. As 修修，I want 訪談 SRT auto-diarized with speaker labels，so that 我不用手動標註誰是訪問者誰是受訪者。
3. As 修修，I want 在校正 SRT 同道工序內微調 diarization 標錯，so that 不增加新工序。
4. As 修修，I want 透過 CLI args 帶 host / guest 真名，so that blog 人物專訪能直接寫「朱為民醫師說」而非「Speaker 01 說」。
5. As 修修，I want 所有三 channel artifact 並排 review，so that 我能在一個 surface 看到全貌不切檔案。
6. As 修修，I want 4 個 tonal variants 的 FB post（輕鬆風趣 / 感性 / 嚴肅 / 一般），so that 我能挑符合該集受訪者氣場的 voice。
7. As 修修，I want IG carousel 的卡數結構依 episode 類型自動選擇（5/7/10），so that 我不用每集判斷該用哪種 carousel 模板。
8. As 修修，I want edit-in-place 修改 channel artifact 文字，so that 微改字眼不浪費 LLM call。
9. As 修修，I want approve 過的 blog 自動推 WordPress draft，so that 我不用手動 copy-paste 進 WP editor。
10. As 修修，I want FB / IG artifact 是 markdown / JSON 格式可直接 copy 進 Meta Business Suite，so that Phase 1 沒 Usopp adapter 我也能順暢發布。
11. As 修修，I want 三 channel 共享同一份 Stage 1 萃取的金句候選，so that 三 channel voice 一致不互相矛盾。
12. As 修修，I want Stage 1 同時產出 ≥3 個 SEO + CTR 取向的 title 候選 + meta description，so that 我能直接挑 blog 標題不另外腦力激盪。
13. As 修修，I want Stage 1 + Stage 2 全用 Sonnet 4.6，so that 品味敏感的 voice 不被便宜模型拖品質。
14. As 修修，I want repurpose engine 抽象成 plug-in 介面，so that 之後 Line 2（讀書心得）/ Line 3（文獻科普）能 plug-in 不用重蓋 orchestration。
15. As 修修，I want 明確的 Beta / Production / Done 三層 acceptance，so that 我知道 Line 1 ship 的標準在哪。
16. As 修修，I want WhisperX diarization 在 transcribe pipeline 重啟，so that Line 1 立刻 unblock 不卡 ASR scope 邊界。
17. As 修修，I want LLM cost trade-offs 透明，so that 我能在 quality vs cost 之間隨時切換（雖然當前是品質優先）。
18. As 修修，I want 每個 channel 的 style profile 可獨立迭代，so that 我未來累積更多 FB / IG 樣本後能 upgrade profile 不影響其他 channel。
19. As 修修，I want CLI 當 Phase 1 trigger、Bridge UI button 留 Phase 2，so that 我能 ship fast 然後迭代 UI。
20. As 修修，I want Bridge UI surface 支援 Stage 1 JSON 預覽，so that 我能在 Phase 2 直接看金句候選 / title 候選並手動 override。
21. As Brook（compose agent），I need 統一的 style profile loader 讀 people.md / fb-post.md / ig-carousel.md，so that 三 channel 各自取對應 profile 不混。
22. As Brook，I need diarized SRT 含 `[SPEAKER_XX]` label 加上 CLI 提供的 host/guest 名字 mapping，so that 我能正確 attribute 引用受訪者真名。
23. As Brook，I need Stage 1 JSON schema 驗證（必填欄位 / 金句候選 ≥1 / title 候選 ≥3），so that downstream renderer 不會因缺欄位失敗。
24. As Brook，I need IG renderer 的 episode_type → 卡數模板 routing 表是顯式 mapping（不是 LLM 自由判斷），so that 輸出結構穩定可預測 / 可 debug。
25. As Brook，I need FB renderer 的 4 tonal directives 是顯式 prompt parameter（不是 LLM 自由發揮），so that 4 個 variant 不會 degenerate 成相似品。
26. As Usopp（publisher agent），I need Brook 產的 blog.md 帶完整 frontmatter（title / meta_description / category / tags），so that WordPress draft 一次就位不需後續補 metadata。
27. As Usopp，I need approval gate（ADR-006 approval_queue）依然成立 — blog approve 只進 WP draft 不直接 publish live，so that publish-time HITL 維持。
28. As 修修，I want 三條 PR 並行可開（Slice 1 diarization / Slice 5 FB profile / Slice 6 IG profile）不互鎖，so that 多 worktree 並行加速 ship。
29. As 修修，I want 每個 channel artifact 落地 path 是 `data/repurpose/<YYYY-MM-DD>-<slug>/`，so that 同一集多 channel 集中、跨集易於 sweep / archive。
30. As 修修，I want PRD 明文排除「第四 channel YouTube 主題剪輯影片」、「Line 2 / Line 3 接入」、「FB follow-up posts」、「Bridge UI 校正 surface」、「Usopp FB/IG adapter」、「FB/IG 圖片 attach 流程」，so that 第一輪 ship 不被 future-work 蔓延拖垮。

## Implementation Decisions

### 架構

兩階段 LLM pipeline + 三 channel 並行 fan-out：

```
SRT (校正完, diarized)
    ↓
Stage 1 (Sonnet 4.6) — Line1Extractor
    ↓
Structured JSON
    ├─ 開場鉤子候選 [3-5]
    ├─ 身份速寫 / 起點 / 轉折 / 重生 / 現在 / 結尾留白
    ├─ 金句候選 [≥5 受訪者原話]
    ├─ Title 候選 [≥3 SEO + CTR]
    ├─ meta_description
    └─ episode_type ∈ {narrative_journey, myth_busting, framework, listicle}
    ↓
Stage 2 fan-out parallel (Sonnet 4.6 × 6 calls)
    ├─ BlogRenderer (people.md profile) → blog.md
    ├─ FBRenderer × 4 tonal variants → fb-{light,emotional,serious,neutral}.md
    └─ IGRenderer (episode_type → 5/5/7/10 卡) → ig-cards.json
    ↓
Bridge UI review surface → per-channel approve
    ↓
Blog → Usopp WP draft；FB/IG → mark done + clipboard
```

### 主要模組（deep modules）

- **`RepurposeEngine`**（Line-agnostic）— `run(source_input, episode_metadata) → ChannelArtifacts`，封裝 orchestration / parallelism / retry / cost tracking / 路徑慣例
- **`Line1Extractor`**（Stage 1）— `extract(srt, host, guest) → Stage1JSON`，封裝 Sonnet 4.6 prompt 構造 + JSON schema validation + retry on schema mismatch
- **`BlogRenderer`** / **`FBRenderer`** / **`IGRenderer`**（Stage 2 三個）— 共用 `ChannelRenderer` 介面 `render(stage1, profile, options) → ChannelArtifact`，FBRenderer 多 tonal arg、IGRenderer 內含 episode_type routing
- **`run_repurpose.py`**（CLI orchestrator）— argparse + Engine 組裝 + I/O
- **`thousand_sunny/routers/repurpose.py`** + 對應 templates — Bridge UI surface（PR #140 mutation pattern）
- **WhisperX diarization restore** — `shared/transcriber.py` 把 PR #273 砍掉的 `_get_align_model` / `_get_diarize_pipeline` / `assign_word_speakers` / `--no-diarization` flag / `[SPEAKER_XX]` 輸出 從 git history 拉回

`RepurposeEngine` 的 plug-in 介面為 Line 2/3 預留 — 各 line 提供自己的 `Stage1Extractor` + `ChannelRenderer` dict，engine 共享 orchestration / I/O / Bridge UI panel renderer。Stage 1 schema 跨 line 不共用（各 line 敘事骨架本質不同）。

### 設計凍結（grill 拍板）

- **Speaker 標示**：CLI args `--host`（default `張修修`） + `--guest`（無 fallback LLM 推 + log warning）
- **Stage 1 / Stage 2 模型**：Sonnet 4.6（呼應品質優先原則；不降 Haiku 直到 batch / deterministic 場景）
- **FB tonal variants**：4 軸 `light` / `emotional` / `serious` / `neutral`（修修品味選擇例外允許多 variants pick-one）
- **IG episode_type → 卡數 mapping**：
  - `narrative_journey` → 5 卡金句結構（Bait → Why → 核心引述 → 反差 → CTA）
  - `myth_busting` → 7 卡 Myth-Fact（Hook → Myth → Fact → 機制 1 → 機制 2 → 行動處方 → CTA）
  - `framework` → 5 卡 framework（Bait → Setup → 4 原則 → CTA）
  - `listicle` → 10 卡 listicle（Hook → Setup → 7 points → mid-CTA save → Land CTA）
- **Output paths**：`data/repurpose/<YYYY-MM-DD>-<slug>/{stage1.json, blog.md, fb-{tonal}.md, ig-cards.json}`
- **Trigger**：CLI 第一輪、Bridge UI button 留 Phase 2
- **Review surface**：Bridge UI 3-panel + edit-in-place + per-channel approve
- **Publish**：blog → Usopp WP draft auto；FB/IG → manual paste 第一輪
- **Diarization**：WhisperX 內建（PR #273 反轉，[`ADR-013`](../decisions/ADR-013-transcribe-engine-reconsideration.md) amendment 待落）
- **品質 baseline**：Beta / Production / Done 三層 acceptance（見 Further Notes）

### Style profile authoring（Slice 5/6）

Light-weight extraction 路線（呼應 Q2 拍板）：

- **FB profile** — 修修提供 5-10 篇正例（按 4 tonal axes 各 1-2 篇分類）+ 3-5 篇反例 + voice 自述；Brook 抽 hook patterns / sentence rhythm / 句尾節奏 / hashtag 風格 → `agents/brook/style-profiles/fb-post.md` 含 4 sub-tonal sections + few-shot
- **IG profile** — 修修提供 3-5 個喜歡的 reference carousel + 1-2 anti-pattern；Brook 結合 Chris Do AIDA 框架 + reference few-shot → `agents/brook/style-profiles/ig-carousel.md` 含 4 episode_type 各自 sub-template + 卡 1 hook 公式庫 + CTA pattern

修修明天（2026-05-02）會交 FB samples（按 4 tonal axes 分類）+ IG reference carousels — Slice 5/6 unblock 後即可開工。

### 重用既有基建

- `shared/transcriber.py`（含 diarization restore） — Slice 1 的 base
- `shared/anthropic_client.ask_claude()` — LLM 統一介面、token cost 落 `state.db`
- `agents/brook/style_profile_loader.py` — Style profile 讀取 reuse
- `agents/brook/compose.py` — 既有 compose pattern reference
- `agents/usopp/publisher.py` — Blog auto-publish 經 Usopp WP adapter
- `thousand_sunny/routers/` + Bridge UI mutation pattern（PR #140）— Slice 10 surface 基底
- ADR-006 `approval_queue` — Blog publish HITL 維持，不繞過

## Testing Decisions

### 什麼是好測試

- **只測 external behavior，不測 implementation detail** — Stage 1 JSON schema 是 external contract（要驗）；Stage 1 prompt 內部 phrasing 是 implementation（不驗）
- **Mock 走 caller-binding** — `from X import Y` 的 caller 必 patch caller-module binding（[`feedback_facade_mock_caller_binding`](../../memory/claude/feedback_facade_mock_caller_binding.md)）
- **Mock 第三方 SDK 用 `spec=` / `autospec=True`** — 避免 truthy MagicMock 掩蓋 nonexistent-method bug（[`feedback_mock_use_spec`](../../memory/claude/feedback_mock_use_spec.md)）
- **真實契約對齊** — 輸入形狀對齊真實 schema（[`feedback_test_realism`](../../memory/claude/feedback_test_realism.md)）
- **API isolation 走 conftest autouse**（[`feedback_test_api_isolation`](../../memory/claude/feedback_test_api_isolation.md)）

### 各模組測試對應

- **WhisperX diarization restore** — 從 PR #273 砍掉的 `test_transcribe_with_diarization` 拉回，含 `[SPEAKER_XX]` label 驗證 + diarize/align pipeline 被正確呼叫；`pytest.importorskip("whisperx")` 保 CI 沒裝 extras 也能 skip
- **`RepurposeEngine`** — integration test mock `Stage1Extractor` + `ChannelRenderer`，驗 parallelism（三 channel 真的並行不阻塞）、error handling（一 channel 失敗其他不掛）、output paths
- **`Line1Extractor`** — golden fixture round-trip：sample SRT (~5 min 短訪談) → 期望的 Stage 1 JSON 結構（schema 驗 8 段全有材料、金句 ≥1、title ≥3、episode_type ∈ enum）；Sonnet mock 走 `ask_claude` patch
- **`BlogRenderer`** — golden output test：curated Stage 1 JSON → markdown 結構驗（含 frontmatter / 8 段 H2 / ≥1 block quote / podcast link）
- **`FBRenderer`** — 4 tonal 各自 golden output test，驗 4 篇 voice 真的不同（簡單 heuristic：句尾標點分佈 / hashtag 數量差異）、長度 300-500 字、≥1 block quote、podcast link
- **`IGRenderer`** — 4 episode_type 各自 golden output test，驗卡數對（5/7/5/10）、首卡 ≤10 字 hook、最後卡含 CTA、字數規範符合 Chris Do best practice
- **CLI orchestrator** — e2e smoke test 跑 small fixture SRT，驗三 channel 6 個 artifact 落地 + Stage 1 JSON 落地
- **Bridge UI surface** — HTTP smoke 驗 list / detail page 載入 + edit-in-place mutation + per-channel approve mutation；mocked Usopp WP draft 觸發

### Prior art

- `tests/test_transcriber.py` 既有 WhisperX mock pattern（`load_model` / `transcribe` mock）
- `tests/test_seo_router.py`（PR #200）Bridge UI surface test reference
- `agents/brook/compose.py` + 對應 tests — compose flow integration test pattern
- `tests/test_publisher.py`（Usopp Slice C）— publish flow mock pattern

### Coverage gate

依 [`feedback_no_regression_gate`](../../memory/claude/feedback_no_regression_gate.md) — 不退步既有 baseline 5%/10%；新模組 default ≥80% line + branch coverage（同 Phase 6 standard）。

## Out of Scope

明文排除（避免 scope creep）：

- **第四 channel YouTube 主題剪輯影片** — 訪談 → LLM 抽亮點 → 自動剪 10-20 min 主題影片 + Title / Description / Thumbnail；自有 PRD（[`project_podcast_theme_video_repurpose`](../../memory/claude/project_podcast_theme_video_repurpose.md)）
- **Line 2 / Line 3 接入** — 讀書心得 / 文獻科普；RepurposeEngine 預留 plug-in 介面但不實作
- **FB follow-up posts** — 一集多波發布（main + quote + question 三 angle）；Phase 2 評估
- **Bridge UI 校正 surface** — 修修留 VSCode 校正 SRT，不蓋 web 化 SRT editor（會輸 VSCode）
- **Usopp FB Page Graph API adapter** — Phase 3 等 cadence 證明值得投資 Meta API 整合
- **Usopp IG Business API adapter** — 同上
- **FB / IG 圖片 attach 流程** — 修修 publish 時手動 attach（podcast 影片縮圖 / 合照 / 側拍），不在 Brook pipeline
- **Bridge UI batch approve → 三平台 auto publish (γ)** — Phase 3 終極目標，須 Usopp adapter 先到位
- **Multi-variant blog / IG** — 只 FB 4 tonal variants；blog / IG 走 single output + edit-in-place
- **Stage 1 manual override UI** — Phase 2 Bridge UI 才開 JSON 預覽 + 欄位 override（金句 / title / episode_type）

## Further Notes

### Slices 拆分

| # | Slice | Deps | Phase 1 Acceptance |
|---|-------|------|------|
| 1 | WhisperX diarization 重啟 | — | 1 hr 訪談 SRT 含 `[SPEAKER_00/01]` label，回歸 PR #273 砍前能力；既有 `test_transcribe_with_diarization` 綠 |
| 2 | RepurposeEngine 抽象 + I/O 約定 | — | `engine.py` 介面 + path scheme + Bridge UI panel renderer skeleton；Line 2/3 plug-in 介面明文 doc |
| 3 | Line 1 Stage 1 extractor | 1 | Sample 訪談 SRT → Stage 1 JSON 含 8 段材料 + episode_type + ≥5 金句候選 + ≥3 title 候選 |
| 4 | Blog renderer (Stage 2) | 3 | 1 集 → `blog.md` 套 people.md 8 段骨架，~2-3K 字 + frontmatter + ≥1 受訪者引述 + podcast link |
| 5 | FB profile authoring（light-weight） | — | `agents/brook/style-profiles/fb-post.md` 含 4 sub-tonal profile + few-shot；修修 2026-05-02 交樣本 unblock |
| 6 | IG profile authoring（light-weight） | — | `agents/brook/style-profiles/ig-carousel.md` 含 4 episode_type 各自 sub-template + Chris Do AIDA + 修修 reference few-shot；修修 2026-05-02 交 reference carousels unblock |
| 7 | FB renderer (Stage 2) | 3, 5 | 1 集 → `fb-{light,emotional,serious,neutral}.md` 各 300-500 字 + ≥1 block quote + podcast link |
| 8 | IG renderer (Stage 2) | 3, 6 | 1 集 → `ig-cards.json` 5/5/7/10 卡（看 episode_type） + 首卡 ≤10 字 hook + 末卡 CTA |
| 9 | CLI orchestrator | 4, 7, 8 | `python -m scripts.run_repurpose <srt> --host X --guest Y` 跑完三 channel；artifact 全落 staging path |
| 10 | Bridge UI 3-panel review surface | 9 | `/bridge/repurpose/<id>` 顯示 3 panel + edit-in-place + per-channel approve；blog approve → Usopp WP draft；FB/IG approve → mark done |

**Critical path**：1 → 3 → 4 → 9 → 10（最小 E2E + Bridge）
**並行可開**：1 / 5 / 6 三 PR 不互鎖

### Acceptance criteria（三層）

- **Beta**：跑通 1 集（任何 podcast）三 channel artifact 出來、Bridge UI 三 panel 能 review approve、blog 落 WP draft
- **Production**：連續 5 集 podcast 跑完，修修 review 平均人工修補時間 ≤ 10 min/集，三 channel voice 一致性可接受
- **Line 1 done**：production criteria 達標 + ADR-013 amendment 落地（diarization 重新 in scope）+ 文件 runbook 寫完

### Phase 2 / 3 Roadmap

- **Phase 2A**：Bridge UI CLI trigger 替代（升級 Q4 (B)）
- **Phase 2B**：Bridge UI 校正 surface（評估必要性）
- **Phase 2C**：FB follow-up posts（一集流量乘 3）
- **Phase 2D**：Stage 1 manual override UI（修修在 Bridge 看金句 / title / episode_type 並改）
- **Phase 3A**：Usopp FB Page Graph API adapter
- **Phase 3B**：Usopp IG Business API adapter
- **Phase 3C**：Full auto batch approve → 三平台 publish（升級 Q5 (γ)）

### 預期成本

每集 podcast pipeline LLM cost：

- Stage 1（Sonnet 4.6 × 1，~30K input + 5K output）：~$0.15
- Stage 2 blog（Sonnet 4.6 × 1，~10K input + 4K output）：~$0.10
- Stage 2 FB × 4（Sonnet 4.6 × 4，~8K input + 0.5K output each）：~$0.10
- Stage 2 IG（Sonnet 4.6 × 1，~10K input + 2K output）：~$0.05
- **Total ~$0.40/集**

修修預期 weekly podcast cadence → ~$1.6/週 / ~$80/年 LLM cost。比起品質 trade-off 微不足道。

### Pre-conditions

- 修修 2026-05-02 交：FB samples（按 4 tonal axes 分類，5-10 篇正例 + 3-5 反例）+ IG reference carousels（3-5 個喜歡的 + 1-2 anti-pattern）
- ADR-013 amendment（diarization 重新 in scope）— Slice 1 PR 一起落
- WhisperX dep 已在 `pyproject.toml`（[`feedback_whisperx_pip_torch_downgrade`](../../memory/claude/feedback_whisperx_pip_torch_downgrade.md) 教訓在）

### Grilling Decisions

| # | 題目 | 決策 |
|---|------|------|
| Q1 | Dependency graph | 三 channel 並行 fan-out from SRT；後 Q7 精煉成「並行 fan-out from Stage 1 JSON」 |
| Q2 | Style profile gap（FB / IG missing） | (b) Light-weight extraction（修修挑樣本 + Brook 抽 + few-shot） |
| Q3 | Diarization 怎麼解 | (d) WhisperX 內建重啟 — PR #273 反轉 |
| Q4 | Trigger | (A) CLI 第一輪、(B) Bridge UI button 留 Phase 2 |
| Q5 | Review surface | (β) Bridge UI 3-panel + edit-in-place + per-channel approve；終極 (γ) full auto |
| Q6 | Speaker mapping | (c) CLI args `--host` `--guest` |
| Q7 | Generator strategy | (II) Two-stage：Stage 1 共享 JSON + Stage 2 fan-out；Stage 1 = Sonnet 4.6 |
| Q8 | FB variants | 4 tonal axes：輕鬆風趣 / 感性 / 嚴肅 / 一般；blog / IG 不走 multi-variant |
| Q9 | IG structure | (b) episode_type routing：narrative_journey 5 卡 / myth_busting 7 卡 / framework 5 卡 / listicle 10 卡 |
| Q10 | Engine 抽象 | (B) 輕量：`RepurposeEngine` 介面，Line 2/3 plug-in 預留 |
| Sanity 1 | Stage 2 模型 | Sonnet 4.6（同 Stage 1，呼應品質優先） |
| Sanity 2 | FB / IG 樣本何時交 | 2026-05-02（修修確認） |
| Sanity 3 | Acceptance 三層 | Beta / Production / Done 確認 |

### Anchor docs

- [`memory/claude/project_three_content_lines.md`](../../memory/claude/project_three_content_lines.md) — 三條 line 凍結需求（主錨）
- [`memory/claude/project_repurpose_flow.md`](../../memory/claude/project_repurpose_flow.md) — 既有 repurpose backlog
- [`memory/claude/project_brook_design.md`](../../memory/claude/project_brook_design.md) — Brook 設計
- [`memory/claude/project_brook_style_extraction_todo.md`](../../memory/claude/project_brook_style_extraction_todo.md) — 既有 3 style profiles
- [`memory/claude/project_whisperx_engine_swap_2026_04_30.md`](../../memory/claude/project_whisperx_engine_swap_2026_04_30.md) — transcribe 上游 + diarization 反轉
- [`memory/claude/project_podcast_theme_video_repurpose.md`](../../memory/claude/project_podcast_theme_video_repurpose.md) — 第四 channel out-of-scope reference
- [`memory/claude/feedback_minimize_manual_friction.md`](../../memory/claude/feedback_minimize_manual_friction.md) — 最高指導原則 1
- [`memory/claude/feedback_quality_over_speed_cost.md`](../../memory/claude/feedback_quality_over_speed_cost.md) — 最高指導原則 2
- [`memory/claude/reference_external_skills_for_nakama.md`](../../memory/claude/reference_external_skills_for_nakama.md) — 外部 skills 候選
- [`docs/decisions/ADR-001-agent-role-assignments.md`](../decisions/ADR-001-agent-role-assignments.md) — agent 職責
- ADR-012 — Brook 對內 / Zoro 對外 邊界
- [`docs/decisions/ADR-013-transcribe-engine-reconsideration.md`](../decisions/ADR-013-transcribe-engine-reconsideration.md) — 待 amendment（diarization 反轉）
- ADR-006 — Usopp publish HITL approval queue
- [`CONTEXT-MAP.md`](../../CONTEXT-MAP.md) — Brook = Composer, Usopp = Publisher 等凍結詞彙
- `agents/brook/style-profiles/people.md` — 既有人物專訪 13-sample profile（Slice 4 用）
- IG carousel best practice 研究（Chris Do AIDA 框架，Slice 6 reference）
