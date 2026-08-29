# Plan — 三支 Long Highlight ＋ 對應 Packaging 跑順（20260805 林之晨）

- **日期**：2026-08-29
- **執行者**：Opus session（本文件自足，不依賴撰寫時的對話）
- **模式**：P9 任務規劃；每個 slice 完成用 P7 格式回報
- **範圍裁決（修修 2026-08-29）**：只做 3 long ＋ packaging。Short、carousel、E2E 直跑另案。

---

## 0. 執行前必讀（事實錨點，全部已驗證）

- **Worktree**：`E:\nakama\worktrees\lin-zhi-chen-e2e`，branch `codex/lin-zhi-chen-e2e`。
  主倉庫 `E:\nakama` 是 control plane，不可寫。
- **Episode**：`G:\Footages\20260805 林之晨`
- **三支 long 的 current Release**（manifest v3 `manifest-135e2060e0d612e2499658a1`）：

  | cut_id | release_id | 性質 |
  |---|---|---|
  | `long3-fresh-20260828-r4`（=punch-L04 血統） | `release-af65a1d7a2ac611eb78be493` | ADR-066 全新 run |
  | `value-L01` | `release-migrated-abc8c2b2c72d148150b18399` | migrated（已含 8-22 咳嗽修剪＋名牌 retime） |
  | `value-L02` | `release-migrated-dc172c6bd624366f1b7849a8` | migrated |

- **測試基線**：`tests/brook/` 全套 **1052 passed / 0 failed**（2026-08-29）。任何步驟後不得低於此。
- **Bridge**：修修的 instance 在 `:8128`（**不可動、不可 kill**）。自己起一個用
  `NAKAMA_DEV_AUTH_BYPASS=1` + 空閒 port（8129 曾用過），uvicorn `thousand_sunny.app:app`，
  `--env-file E:\nakama\.env`。
- **packaging 現況**（實查 `packaging/packages.json`，2026-08-27 生成）：
  - 4 cuts（full / value-L01 / value-L02 / punch-L04）各 3 個 package，`title_rank`、`thumbnail_png`、
    `render_recipe` 齊全 → **title＋thumbnail 的生成階段已完成，不必重跑 brainstorm**
  - `final/` 只落了 full ＋ value-L01 的 `cover-*.png`/`title-*.txt` → **L02、L04 未走完審核落地**
  - `packaging/briefs/` 只有 full ＋ value-L01 → **L02、L04 的 brief 缺**
  - description 只有 `packaging/description-full-draft.md` → **三支 long 的 description 全缺**
- **工具鏈**（全部已存在，不必新寫）：
  `scripts/packaging_brief.py` → `scripts/publish_description.py` →
  Bridge packaging 審核（`thousand_sunny/routers/highlight_review.py`，核准即自動觸發
  `scripts/publish_prep.py` 全解析度 Resolve 輸出，receipt 在
  `highlights/exports/.publish_prep_<cut>.json`）→ `scripts/publish_upload.py`（**本 plan 不碰**）。
  packaging 工作分派狀態機：`scripts/packaging_manifest.py`（load_manifest / stage_parallel_jobs /
  claim / finish / mark）。

## 1. 目標

三支 long 各自達到：**finished-cut review 核准 → title＋thumbnail＋description 落進 `final/` →
publish_prep 輸出 rendered**。並且整段流程走的是可重複的正式路徑（scripts ＋ Bridge gate），
不是手工搬檔案。

## 2. 範圍（動什麼）

| Slice | 內容 | 預期產物 |
|---|---|---|
| A | 內容定稿 gate：三支 cut 在 Bridge finished review 過審 | 三支 approved（HITL） |
| B | 補 L02、L04 的 packaging brief | `packaging/briefs/value-L02.json`、`punch-L04.json` |
| C | 三支 long 的 description | `packaging/description-<cut>-draft.md` × 3 |
| D | Bridge packaging 審核＋落地 | `final/` 三支齊：cover＋title＋description |
| E | publish_prep 驗證 | `.publish_prep_<cut>.json` status=rendered × 3 |
| F | 擋路 skill 修正（最小範圍） | 見 §6 |

## 3. 逐步執行

### Slice A — 內容定稿（第一個 HITL）

1. 起自己的 Bridge instance，開
   `/bridge/highlights/20260805 林之晨/finished`，逐支播放確認可播、timeline 事件正確
   （L04 應顯示 15 components：8 B-roll / 2 Hero / 5 Fullscreen Transition）。
2. 請修修在 Bridge 對三支 cut 做 CUT DECISION。
3. **若有修改意見**：一律走 `FinishedCutProduction.request_revision(current_release_ref,
   event_id, feedback)`（單事件 targeted），透過
   `scripts/run_finished_cut_production.py request-revision`。
   **禁止**重跑 Director/DP 全 stage、禁止手改 `state.json`、禁止碰 `.cache` 裡的舊救火腳本。
4. 修訂後 current pointer 會換新 release —— 這是唯一允許 pointer 變動的途徑。

### Slice B — 補齊 brief

以既有 `packaging/briefs/value-L01.json` 為 schema 樣本
（keys: `cut_id, generated_at, one_liner, duration, beats, quotes, caution`），
用 `scripts/packaging_brief.py` 對 `value-L02`、`punch-L04` 生成。
素材來源＝該 cut sealed Release 的 events（有完整 quote＋display）＋ Editorial Master SRT。
先 `--help` 確認參數再跑；若 script 需要的輸入本 plan 沒列，**讀 script 源碼確認，不猜**。

### Slice C — description × 3

用 `scripts/publish_description.py` 對三支 cut 生成（full 的 draft 已存在可當風格參照）。
內容錨定：只能引用該 cut Release events 裡真實存在的論述；杜絕腦補（CLAUDE.md 紅線）。
產物落 `packaging/description-<cut_id>-draft.md`。

### Slice D — packaging 審核落地（第二個 HITL）

1. Bridge packaging surface 載入 `packages.json` 的 3 packages/cut，請修修圈選。
2. 核准動作應由 Bridge 寫 `final/cover-<cut>.png`＋`title-<cut>.txt`
   （比照既有 full / value-L01 的落地樣式）；description 定稿同步落
   `final/description-<cut>.md`。
3. 若 Bridge 的 packaging 審核頁對 highlight cut 尚未接通（Codex `39622ee7` 只驗過部分），
   допустимо的 fallback：修修在對話中圈選，由 agent 以 script（非手工複製）落地並記
   receipt。fallback 要在回報中明示。

### Slice E — publish_prep

核准後確認三支的 `.publish_prep_<cut>.json` 進入 `rendered`；
`highlights/exports/` 有全解析度輸出。渲染是 Resolve 操作：**只能本機跑、
Resolve Studio 開著、`py -3.10`**（3.14 會 segfault）、worktree 執行需帶
`RESOLVE_SUBTITLE_TEMPLATE=E:\nakama\data\resolve\subtitle-template.drt`。

### Slice F — 擋路 skill 最小修正

只修「照文件做會走錯路」的部分，全面改寫另案：

- `.claude/skills/longform-cut/SKILL.md:24` 起的「Stage 5 Long Highlight orchestrator」一節：
  加醒目告示「本節描述之 ADR-065 orchestrator 已停用，long 生產唯一路線＝ADR-066
  Finished Cut Production（`scripts/run_finished_cut_production.py`）」，並移除
  `adopt-existing` / `adopt-winner` 用法教學。
- `brook-director/SKILL.md:68`、`brook-dp/SKILL.md:84`、`highlight-cut/SKILL.md:396` 的
  `podcast_highlight_visual_orchestrator.py` 指令行：同樣加停用告示。

## 4. 驗收（Definition of Done）

- [ ] 三支 cut：Bridge finished review 核准紀錄存在
- [ ] `final/`：`cover-{value-L01,value-L02,punch-L04}.png`、`title-*.txt`、`description-*.md` 齊
- [ ] `.publish_prep_<cut>.json` × 3 status=rendered，exports 檔案可播（ffprobe 過）
- [ ] `tests/brook/` 全套 ≥ 1052 passed / 0 failed
- [ ] current manifest 仍為單一 pointer-last 檔；任何 release 變動皆可溯源到 request_revision
- [ ] 每個 slice 以 P7-COMPLETION 回報，含實際檔案路徑

## 5. HITL 節點（就這三個）

1. **Slice A**：三支 cut 的 CUT DECISION（＋可能的修訂意見）
2. **Slice D**：每支圈一個 package（title＋cover）＋ description 定稿
3. 發生 catastrophic 失敗（媒體不可讀、hash 不符、Resolve 破壞性操作）時停下來問

## 6. 邊界（絕對不可）

- 不跑任何 legacy script：`run_long_highlight_orchestrator.py`、
  `podcast_highlight_visual_orchestrator.py`、`run_short_broll.py`、`run_short_titles.py`、
  `run_short_review.py`、`build_finished_review_manifest.py`、
  `build_long_highlight_playback_manifest.py`；不使用 `adopt-existing` / `adopt-winner`
- 不重跑三支 cut 的 semantic pipeline（Director/DP 全 stage）
- 不上傳 YouTube（`publish_upload.py` 不在本 plan）
- 不動修修的 `:8128`；port 衝突換 port 不 kill（memory 紅線）
- 不刪 G: 任何歷史資料；不動 `visual-pipeline/` 等 legacy 樹
- 不做 short、不做 carousel、不做 Forensic Archive／legacy 刪除（另案）
- Bridge UI 有改動時，merge 前必須 dev server 實機走過 golden path（memory 紅線）
- `git add` 只列明確路徑；`.cache/` 已 gitignore 但仍不准 `-f` 加入

## 7. 已知風險

| 風險 | 對策 |
|---|---|
| migrated Release（L01/L02）的 request_revision 路徑可能沒被真集驗過 | 遇 `CommandRejectedError: historical Release is read-only…` 之類即停，回報原文，不繞過 |
| Bridge packaging 審核頁對 highlight cut 的接通程度未驗 | Slice D 有 fallback，用了要明示 |
| `packaging_brief.py`/`publish_description.py` 的參數契約本 plan 未逐一列出 | 先讀源碼與 `--help`，不猜；發現契約與 plan 假設不符就停下回報 |
| publish_prep 是長時 Resolve render | 用 receipt 輪詢，不阻塞；一次只跑一支 |
