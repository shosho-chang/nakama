# Video Production Line 收斂實施計畫（ADR-050）

**Date:** 2026-07-03
**Status:** Approved — 2026-07-03 修修裁決 D1-A / D2-A / D3-A / D4-A 全數採納，計畫生效
**裁決結果：** D1-A（搬進 `agents/brook/script_video/`）/ D2-A（`shared/fcpxml/` builder）/ D3-A（單線 + cleanup stage）/ D4-A（episode.yaml 標記）。

---

## PR 切片（依賴序）

### PR-1 — 文件凍結（純 docs，0.5d）

- ADR-050 定稿（裁決結果寫入、Status → Accepted）
- ADR-032 status → `Accepted (as amended by ADR-050)`；§0.1 加 supersede 註記
- ADR-001 Foundry 條目移除 + Brook 條目 amend（第 4 sub-responsibility）
- ADR-015 status 補殘留 code 處置註記；ADR-033 路徑 amendment 註記
- `CONTENT-PIPELINE.md:59,132` / `CONTEXT-MAP.md:23,33-34,64` / `ARCHITECTURE.md` 同步
- 關閉 issues #314/#315/#316/#317（comment 指向 ADR-050）
- `memory/claude/project_foundry_*.md` 補 supersede 註記
- ⚠️ docs-only PR 記得 ci-skip.yml 鏡像（reference_github_actions_paths_ignore_deadlock）

### PR-2 — `shared/fcpxml/` builder seam（1.5d）

- `shared/fcpxml/builder.py`：`TimelineDoc` / `Asset` / `Clip` model + writer
- Quirk 知識集中：absolute `file://` media-rep、`<asset-clip>`、deterministic UID、`N/30s` rational、NDF、version fallback、DOCTYPE
- 兩個 adapter：ripple-delete 形（吃 `CutPoint` 段列表）+ overlay 形（吃 storyboard cutaways）
- foundry emitter 改為薄 adapter 呼叫 builder；brook emitter tests 移植到 builder golden tests
- **驗收**：既有 `tests/foundry/test_fcpxml_emitter.py` + `tests/brook/script_video/test_fcpxml_emitter.py` 語意全數保留通過；兩形狀 DaVinci import fixture 各一（沿用 ADR-032 gate，修修桌機手動 import 確認）
- *D3 若裁 B（record-first 全退役）：ripple-delete adapter 仍建（cleanup 概念死了才刪），或縮為只建 overlay 形 — 定稿時二選一*

### PR-3 — 機器搬遷（純 mechanical，1d）

- `agents/foundry/**` → `agents/brook/script_video/`（舊 Slice 1 檔案先在 PR-4 退役 or 本 PR 內先挪到 `_legacy/` 暫存，避免同名相撞 — 執行時擇一，傾向：PR-3 與 PR-4 合併執行順序倒過來，先退役再搬遷）
- import 改寫（`agents.foundry.*` → `agents.brook.script_video.*`）；`tests/foundry/` → `tests/brook/script_video/`
- Bridge：`thousand_sunny/routers/foundry.py` prefix `/foundry` → `/brook/video` + 301 redirect 一版 + nav / inventory / architecture templates 更新
- `.claude/skills/foundry-replan/` → `brook-replan-beat`（SKILL.md 觸發詞 + prompt 路徑更新）
- `shared/foundry_versions.py` 處置（更名 or 註記；`EXPORT_VERSION` bump 與否此 PR 內決定）
- CLI：`python -m agents.brook.script_video --episode <id> plan|render|emit|run`
- **紅線**：此 PR 禁止任何行為改動 — 只准路徑/名稱；review 用 `git log --follow` 驗 diff 純度
- **驗收**：pytest 全綠 + Bridge UI golden path 手走（storyboard 表格 + 三 action + batch + polling）（feedback_ui_browser_verification_before_merge）

### PR-4 — Record-first 處置 + cleanup stage refit（1d）

- 退役：舊 `pipeline.py`（五階段殼）/ `manifest.py` / `srt_emitter.py` / brook 版 `fcpxml_emitter.py` / `video/src/parser/`（TS DSL parser）+ 對應 tests
- 存活：`mistake_removal.py` + tests + `clap_marker_audio.wav` fixture → 掛為 `cleanup` subcommand（輸出形狀待 ADR-050 Open Q2 跟修修確認：ripple-delete FCPXML vs 直接 ffmpeg 剪 mp4）
- `data/script_video/smoke-001/` fixture 目錄轉換或標記 legacy
- *D3 若裁 B：`mistake_removal.py` 一併退役，本 PR 縮為純刪除*
- **驗收**：`cleanup` subcommand 對 clap fixture 跑通；刪除清單逐項列在 PR body（窮盡一切紀律 — 乾淨項目標「已檢查，無問題」）

### PR-5 — Provenance + path bug（0.5d）

- `episode.yaml` 增 `stages:`（各 stage 完成 timestamp）；CLI 啟動驗證（缺 episode.yaml → fail loud 附建立指引）
- 修 `_DATA_ROOT` cwd-relative bug（repo-root anchored，與舊 brook 版同式）
- `out/` 產物命名檢查（單線後自然只剩一份 `episode.fcpxml`，驗證無殘餘撞名路徑）
- **驗收**：從非 repo-root cwd 呼叫 CLI 寫入位置正確；無 episode.yaml 目錄 fail loud

**合計 ~4.5d**（agent 工時）。PR-2 與 PR-1 可平行；PR-3 依賴 PR-2（builder 先就位，搬遷才不用搬兩次 emitter）；PR-4/PR-5 依賴 PR-3。

---

## Panel（建議，待修修點頭）

ADR-050 D1 是 owner 主觀裁決不跑 panel；D2/D3 技術面跑**輕量單模型 audit**：Codex `gpt-5` + `model_reasoning_effort=medium`（feedback_codex_medium_reasoning_for_long_audit），審 builder API 形狀 + 遷移計畫盲點，1 輪即收。

## 明確不做（邊界）

- 不動 ADR-032 的 pipeline 技術設計（planner prompt、exact-copy anchor、Hyperframes worker、Tier 2 UI、edit_log、layouts YAML）
- 不動 `/transcribe` skill、`video/compositions/`（Hyperframes HTML 資產）、`docs/design-system.md`
- 不解 ADR-027「第三度收斂」follow-up（SEO 歸屬）
- 不在本計畫內補寫 ADR-038 文件（獨立 task）
- 不碰 Vault（`data/` 非 vault，`docs/VAULT-LAYOUT.md` 無需更新）

## Worktree / dispatch

- 每個 PR 開 sibling worktree（`E:\nakama-adr050-pr<N>-<topic>`），遵守 CLAUDE.md 紀律；PR-3 搬遷面積大，**不進 Sandcastle**（需要 Bridge UI 手走驗收），主 session 盯
- PR-1（純 docs）與 PR-2（自包含 library）為 Sandcastle 候選
