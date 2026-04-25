---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-25
confidence: high
ttl: 90d
originSessionId: cbf94814-ac39-48c7-af66-32e399edf699
---

**Usopp Slice C1 merged（2026-04-24）：** PR #97 squash merged `05d35a4`
- ✅ daemon loop + SIGTERM/SIGINT graceful shutdown + `update_post` fail-closed
- ✅ `WordPressClient._site_id` → `.site_id` 公開化（PR #77 borderline C6）
- ✅ 3 件 code-review follow-up（load_config / USOPP_* env drift / op_id in warning log）
- ✅ 17 daemon unit tests

**Usopp VPS 部署材料 merged（2026-04-24）：** PR #98 squash merged `f8f3de7`
- ✅ `nakama-usopp.service` systemd unit（TimeoutStopSec=120 graceful budget）
- ✅ `docs/runbooks/deploy-usopp-vps.md` 7 步部署 runbook（`.env` diff-append / dry-run / rollback）
- ✅ code-review fix：`reset_stale_claims()` 正確簽名 + `TimeoutStopSec` 60→120
- ✅ **VPS 部署完成（2026-04-24）**：`nakama-usopp.service` active running，PID worker_id `usopp-shoshotw`，graceful restart 1s 完成。路上修了兩個 .env legacy（`WP_SHOSHO_USER`→`USERNAME` typo + BASE_URL `/wp-json` 後綴）+ append `LITESPEED_PURGE_METHOD=noop`。詳見 [project_usopp_vps_deployed.md](project_usopp_vps_deployed.md)

**Usopp Slice C2a merged（2026-04-24）：** PR #101 squash merged `916b8eb`
- ✅ Docker WP staging + `run.sh` 一鍵 boot + seed + 產 `.env.test`
- ✅ `live_wp` pytest marker + `tests/e2e/conftest.py` auto-skip guard
- ✅ `tests/e2e/test_phase1_publish_flow.py` 黃金路徑（enqueue → publish → round-trip meta）
- ✅ code-review fix：`/wp-json` convention bug 修正（見 `feedback_wp_base_url_convention.md`）
- ✅ **Slice C2b LiteSpeed Day 1 實測完成（2026-04-24）**：`LITESPEED_PURGE_METHOD=noop` 為生產正解（不是 fallback）。發現 REST endpoint 不存在（404），WP `save_post` hook 已自動 invalidate cache。code follow-up：`shared/litespeed_purge.py` 清理 + ADR-005b §5 放寬硬規則

**Nami 收尾（本次對話完成大部分，剩兩項）：**
- ✅ PR #59：Deep Research deferred bug 3 個 + lint 修
- ✅ PR #60：移除 Slack 回應的 `:tangerine: Nami` header
- ✅ PR #61：Firecrawl `lang=` kwarg revert（search 壞掉的根本原因）
- ✅ PR #62：Gmail `list_messages` 空結果 ThreadPoolExecutor crash
- ✅ PR #63：Deep Research max_tokens — fetch 截斷 20k→5k + 預算 2+3
- ✅ PR #64：`call_claude_with_tools` max_tokens 2048→8192
- ✅ Deep Research 功能驗收通過（Vault 有報告輸出）
- ✅ Gmail 大量搜尋策略加入 system prompt（分批 5 封 + ask_user）
- ✅ **project-bootstrap template 同步**：2026-04-25 re-scan 確認 primary path 全對齊（dispatcher `tpl-project.md` + 4 partials + `tpl-action.md` 1:1 match Nami source/`render_task`），詳見 [project_lifeos_template_drift.md](project_lifeos_template_drift.md)。⚠️ vault 內有 3 份 dispatcher 遷移前的 legacy templates（`tpl-new-project.md` / `tpl-project-podcast.md` / `tpl-project-youtube.md`），不在 active path 上但留著有誤選風險 — 修修決定要不要 archive 或刪除
- ⬜ **Slack thread 續問實機測試**（多輪對話未驗）

**Robin（今晚要做）：**
- ⬜ `/kb/research` E2E 未測（skill 化前先驗）
- ⬜ Robin Reader：metadata 卡片顯示 + 貼上圖片顯示（本機測試）
- ⬜ KB Research UI 呈現方式（修修想再改）

**Phase 4 Bridge UI：**
- ✅ PR-A backend merged（#41）
- ✅ PR-B Memory UI（#42）、PR-C Cost UI（#44）、Bridge Hub（#45）
- ✅ Direction B Instrument Panel 重設計 + VPS 部署（PR #65，2026-04-21）
- ✅ Tech debt：`agent_memory.update` rollback / `MemoryUpdate.type` Literal / docstring（PR #123 merged 2026-04-25）— rollback claim 降級為 defensive hardening，見 [feedback_defensive_vs_bug_fix_claim.md](feedback_defensive_vs_bug_fix_claim.md)
- ⬜ 細節 UI polish（修修說「還有很多細節要改，但先這樣」）

**Zoro：**
- ⬜ Zoro bot Slack app 上線（Phase 2 brainstorm blocker）
- ⬜ keyword-research backlog 6 項 GH issues（術語表 / normalize / {today} / reddit_zh / twitter / CLI cost）

**Skill 化工程：**
- ⬜ `kb-search` (Robin) — E2E 未測，skill 化前先驗
- 🚧 `style-extractor` — PRD v4 草稿完成，等 3 個 StyleSamples 資料夾備齊
- ⬜ `weekly-report` (Franky)
- ⬜ `morning-brief` (Nami)
- ⬜ `interview-to-article`、`kb-synthesize-article`、`book-reflection-compose`（需 PRD）

**SEO Solution（ADR-009 進行中）：**
- ✅ prior-art-research、ADR-009 凍結、multi-model triangulation
- ✅ Slice A merged（PR #132）— SEOContextV1 schema family + gsc_client + site_mapping + runbook
- 🚧 **Slice B PR #133 open** — seo-keyword-enrich skill GSC-only baseline；72 new tests 全綠；3 subagents parallel dispatch 13 min 完工。**ultrareview 完成**（2026-04-25 Mac，commit `cb0afdb`）：5 bug 全修（now_fn forward / SKILL.md unrunnable invocation / cannibalization recommendation 500-char overflow / `_select_primary_metric` broken guard / capability-card 3-backtick nested fence）+ 4 regression test。等修修 T1 benchmark + merge。順手發現 `shared/seo_enrich/striking_distance.py:74-89` 有相同類別的 malformed-row 防護缺口（`row["keys"]` 沒 None/empty 守衛），未在此 PR 修。教訓詳見 [feedback_skill_scaffolding_pitfalls.md](feedback_skill_scaffolding_pitfalls.md)
- 🚧 **PR #134 open 但要 close** — gsc-oauth-setup.md 跨機策略補丁；發現整個 runbook 要 deprecate（重工 nakama-franky setup），下次 session close
- 🔥 **Cleanup 待開新 PR**：env key `GSC_SERVICE_ACCOUNT_JSON_PATH` → `GCP_SERVICE_ACCOUNT_JSON`（reuse ADR-007 convention）+ deprecate `gsc-oauth-setup.md` → redirect `setup-wp-integration-credentials.md`。修修踩到此坑：「拒絕重工 + 變胖變複雜」是最高指導原則，見 [feedback_prior_art_includes_internal_setup.md](feedback_prior_art_includes_internal_setup.md)
- 🔄 **修修手動 unblock OAuth**：改用 nakama-franky@nakama-monitoring sa（GSC 已授權兩 property），不要繼續 nakama-seo path；GCP console SHUTDOWN nakama-seo project
- ⬜ Slice C：Brook compose `seo_context` opt-in 整合（task prompt phase-1-seo-solution.md §C 已凍結，依 Slice B merged）
- ⬜ Phase 1.5：seo-audit-post skill + DataForSEO + firecrawl + PageSpeed（ADR-009 已定延後）

**雙語閱讀 Pipeline：**
- ✅ P1 PubMed flow（PR #71）：PDF 全文 → 雙語 reader（pymupdf4llm + translator）
- ⬜ P2A：BabelDOC 整合（學術論文雙語 PDF，需 Immersive Translate API key）
- ⬜ P2B：Docling 升級（書籍 / 掃描 PDF，VPS 3.8GB RAM 不能裝）
- ⬜ P3：Annotation → Source page 自動 append「我的筆記」

**PubMed 整合：**
- ✅ Robin PubMed 每日 digest 上線 VPS（PR #66/#67/#68）
- ✅ PubMed OA 全文自動下載（PR #70，PMC + Unpaywall 兩層 fallback）
- ✅ PubMed 雙語閱讀本機整合（PR #71，source 頁 link → reader）
- ⬜ 觀察 pymupdf4llm 對多欄學術 PDF 品質，不夠好再上 Docling/BabelDOC
- ⬜ 調研 PubMed NCBI Entrez API（Nami Quick Lookup 替代 Deep Research）

**基礎建設：**
- ✅ Robin 核心流程 `kb_search.py` 0% → 100%（PR #119 merged，順手修 `"Entities"` type normalize bug）+ `ingest.py` 0% → 100%（PR #121 merged 60 tests，路上修 2 個 Linux CI case-sensitive bug）
- ✅ Brook compose.py 補測試覆蓋率 57% → 100%（PR #116 merged，含對話式 API + helpers）
- ✅ Thousand Sunny routers smoke test — brook + zoro 0% → 100%（PR #118 merged）+ franky 94% → 100% / robin 46% → 77%（PR #127 merged，SSE events 留下輪）

**Nakama backup / VPS 備份（2026-04-24）：**
- ✅ Nakama `state.db` daily 04:00 Taipei → R2 `nakama-backup` bucket，retention 30 天（PR #88）
- ✅ 見 [project_nakama_backup_deployed.md](project_nakama_backup_deployed.md)
- ✅ Franky 擴展 `r2_backup_verify` 檢查 `nakama-backup` bucket 的 freshness（PR #99 merged，並 PR #100 加入 Bridge dashboard 曝光）
- ⬜ R2 bucket-scoped token 分離（寫 nakama-backup / 讀 xcloud-backup 兩份 key）
- ⬜ xCloud fleet 整站 tarball 缺口（fleet 目前只有 DB dump，沒有 file tarball — 修修去 xCloud console 檢查）
- ✅ xCloud 已改 daily + 15 天 retention（修修 2026-04-24 設定完成）

**Phase 1 Wave 2（foundation 已合併，見 project_phase1_foundation_pr.md）：**
- ✅ VPS baseline 壓測 24h（2026-04-24 analyze）：**GREEN** — CPU p95 2%, RAM p95 1.73GB (threshold 3GB), avail 低點 1.99GB, Load 1m p95 0.15. ADR-007 §6 門檻全過，Phase 1 full workload 可推進
- ✅ `shared/gutenberg_validator.py`（ADR-005a §4）— PR #80 Mac session
- ✅ `agents/brook/compose.py` + style_profile_loader + tag_filter + compliance_scan — PR #78 Mac session
- ✅ `agents/usopp/publisher.py` + compliance + seopress_writer + litespeed_purge（PR #77 merged 2026-04-23）
- ✅ `shared/schemas/external/wordpress.py` + `external/seopress.py`（PR #73 Slice A）
- ⬜ Bridge `/bridge/drafts` UI + routes + CLI fallback
- ✅ `agents/franky/` 全三 slice（PR #74/#75/#76）
- ✅ Franky VPS 上線（2026-04-24，含 PR #86/#87 修 env drift）
- ✅ **External uptime probe**：PR #111 merged `6cf5475`（2026-04-24）+ 線上驗收通過。happy path / simulate_down 兩路徑全綠，Slack DM 實收確認。踩 1 個坑：原以為 GH runner 可繞 CF bot list 錯，實測 SBFM 全擋 → 加 CF WAF skip rule by UA `nakama-external-probe/1.0` 解。見更新版 [feedback_uptimerobot_cost_benefit.md](feedback_uptimerobot_cost_benefit.md)
- ✅ docs/runbooks/uptimerobot-setup.md 標 deprecated + 三坑警告（桌機 PR #110 merged 2026-04-24 `421213f`；Mac session PR `<pending>` 修錯誤的「GH runner 零 CF 配置」claim）
- ✅ `config/style-profiles/*.yaml` 三個 profile（PR #78/#79）
- ✅ Usopp Slice C1 — daemon loop + signal + follow-ups（PR #97 merged 2026-04-24）；`/healthz` 加 WP 檢查項目 superseded by ADR-007 §4 probe_wp_site
- ✅ Usopp VPS 部署材料（PR #98 merged 2026-04-24）— systemd unit + runbook；等修修手動套用
- ✅ Usopp Slice C2a — Docker WP 6.4.3 + SEOPress 9.4.1 staging E2E 黃金路徑（PR #101 merged 2026-04-24）
- ✅ Usopp Slice C2b — LiteSpeed Day 1 實測完成（2026-04-24）：`LITESPEED_PURGE_METHOD=noop` 為生產正解；REST endpoint 不存在、WP hook auto-purge 已覆蓋；決策表與 code follow-up 清單見 [docs/runbooks/litespeed-purge.md](../../docs/runbooks/litespeed-purge.md)

**Phase 1 foundation borderline follow-ups（6 項，全部完成）：**
- ✅ `PRAGMA synchronous=NORMAL` + `busy_timeout=5000` + `foreign_keys=ON` 移到 `_get_conn()`（ADR-006 §5 四條 PRAGMA 收齊，PR #85 2026-04-23）
- ✅ `claim_approved_drafts` 的 `UnknownPayloadVersionError` 改 `mark_failed` 兜底（PR #83）
- ✅ `BlockNodeV1.children` 加 per-block-type whitelist（PR #81）
- ✅ 3 個 docstring 準確度修正（atomic mutex / filter-out / CHECK 反向生成）（PR #82）

**Mac 2026-04-23 下午 session（桌機同時做 #77 Usopp Slice B）：**
- ✅ PR #79 — Brook style profile detect_keywords enrichment
- ✅ PR #80 — `shared/gutenberg_validator.py`（ADR-005a §4）
- ✅ PR #81 — `BlockNodeV1.children` whitelist
- ✅ PR #82 — 3 個 docstring 準確度修正
- ✅ PR #83 — UnknownPayloadVersionError → mark_failed 兜底

**Mac 2026-04-24 session（桌機同時做 #88 Nakama backup to R2，零重疊並行）：**
- ✅ PR #89 — gutenberg follow-ups：`_ast_depth` iterative stack（DoS-proof）+ find-and-preserve crossed recovery + `BlockNodeV1.content × children` XOR（Group A 三項）
- ✅ PR #90 — approval_queue follow-ups：`UnknownPayloadVersionError` deprecation docstring + `ValidationError / JSONDecodeError / TypeError` soft-fail（borderline #2.5，Group B 兩項）
- Follow-up backlog 全部清空

**Mac 2026-04-24 晚間 session（桌機同時做 PR #97 Usopp Slice C1）：**
- 分工 doc: [docs/task-prompts/mac-2026-04-24-handoff.md](../../docs/task-prompts/mac-2026-04-24-handoff.md)
- ✅ 任務 A：LifeOS `tpl-project.md` + `tpl-action.md` 對齊 gold standard（純 vault，已完成）
- ✅ 任務 B：Franky `r2_backup_verify` 擴展 `nakama-backup` bucket freshness（PR #99 merged）+ Bridge dashboard 曝光（PR #100 merged）
- ✅ 桌機：PR #97 Slice C1 + PR #98 VPS 部署材料 + PR #101 Slice C2a

**2026-04-24 深夜 cleanup session（桌機 + Mac）：**
- ✅ PR #110 merged — UptimeRobot deprecated + Usopp C2b LiteSpeed Day 1 執行 checklist（桌機實測驗證）
- ✅ PR #111 merged — GHA external uptime probe workflow（Mac）
- ✅ PR #112 merged — litespeed cleanup + ADR-005b §5 放寬（桌機，刪 `_purge_via_rest()` dead code）
- ✅ PR #113 merged — fix CI trendspy pyproject.toml 漏 sync（桌機抓主幹 CI 紅）
- ✅ PR #114 merged — memory: feedback_dep_manifest_sync
- ✅ PR #115 merged — external probe CF WAF skip rule documented（Mac，修 PR #111 原 CF 假設錯誤）
- ✅ PR #116 merged — test(brook): compose.py conversational coverage 57% → 100%
- ✅ PR #118 merged — test(routers): brook + zoro 0% → 100%
- ✅ PR #119 merged — test(robin): kb_search 0% → 100% + 修 `"Entities" → "entitie"` type normalize bug
- ✅ PR #120 merged — memory: feedback_pytest_monkeypatch_where_used

**Mac 2026-04-24 晚間 Part 2 session（桌機 Robin kb_search + coverage 並行）：**
- 分工 doc: [docs/task-prompts/mac-2026-04-24-p2-handoff.md](../../docs/task-prompts/mac-2026-04-24-p2-handoff.md)
- ✅ 任務 D1 → PR #122 merged — `docs/decisions/ADR-009-seo-solution-architecture.md`（ADR 凍結 3 skill + SEOContextV1 + Brook opt-in 整合契約）
- ✅ 任務 T1 → PR #123 merged — `shared/agent_memory.py` tech debt：`update()` `conn.rollback()` + `MemoryType = Literal[...]` + `_validate_type()` + docstring audit + `shared/memory_extractor.py` import `VALID_TYPES` + `thousand_sunny/routers/bridge.py` 1-char description fix（drive-by）
- 踩坑新 feedback：[feedback_defensive_vs_bug_fix_claim.md](feedback_defensive_vs_bug_fix_claim.md)（PR #123 reviewer 發現 rollback 原本就不 leak，claim 降級 defensive hardening）

**桌機 2026-04-24 晚間 Part 2 session（Mac D1 + T1 並行）：**
- ✅ **PR #121 merged** — test(robin): ingest.py 0% → 100%（60 tests）。路上抓到 2 個 Linux CI 大小寫敏感 bug（slugify 保留大小寫但 test seed 用小寫），以 fix commit 解
- ✅ **PR #124 merged** — docs(seo): ADR-009 multi-model triangulation（Gemini 4/10 / Grok 6/10 / Claude 通過；6 blockers 消化 → T5/T6 ADR body 改、T4 Revised Slice Order；`scripts/adr_multi_model_review.py` 加 skip-if-exists + dynamic date）

**2026-04-25 深夜 auto-mode session（修修睡覺前授權全 merge + 四任務全收）：**
- ✅ PR #116/#118/#119/#121/#122/#123/#124/#125 全 merged（前置授權範圍）
- ✅ PR #126 merged — `feat(external-probe)`: PR #111 四 follow-up 一次收（Task A）
  - Quota: 3-matrix → 單 job for-loop，省 ~66%
  - `curl --retry 2 --retry-delay 10 --retry-all-errors`
  - Slack DM per-target 30-min dedupe（`actions/cache`）
  - `simulate_down` → choice enum `[none, nakama, shosho, fleet, all]`
  - runbook 同步更新
- ✅ PR #127 merged — `test(routers)`: franky 94%→100% + robin 46%→77%（Task B，SSE events 留下輪）
- ✅ PR #128 merged — `chore`: ignore runtime artifacts (`.claude/scheduled_tasks.lock` + `.coverage`) + reconcile pending_tasks（Task D）
- ✅ PR #129 merged — `docs(task-prompts)`: ADR-009 Phase 1 Slice A/B/C 六要素凍結（Task E，讓下個 PR 落地有六要素 prompt 可用）

**PR #111 review 找到的 follow-up**：全部在 PR #126 收掉，見上。

**2026-04-25 出門 Mac handoff**：
- 分工 doc: [docs/task-prompts/mac-2026-04-25-handoff.md](../../docs/task-prompts/mac-2026-04-25-handoff.md)
- Mac 主推 ADR-009 Phase 1 Slice A（`SEOContextV1` schema + `shared/gsc_client.py` + `shared/schemas/site_mapping.py` + GSC OAuth runbook），純 `shared/` 層、全 mock test、零外部 API、零檔案衝突
- 桌機在 Mac 出門期間做三件零衝突小工：memory reconcile（本 PR）+ SSE events coverage + project-bootstrap template drift 掃描
