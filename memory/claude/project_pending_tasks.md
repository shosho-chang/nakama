---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-23
confidence: high
ttl: 90d
originSessionId: cbf94814-ac39-48c7-af66-32e399edf699
---
**Nami 收尾（本次對話完成大部分，剩兩項）：**
- ✅ PR #59：Deep Research deferred bug 3 個 + lint 修
- ✅ PR #60：移除 Slack 回應的 `:tangerine: Nami` header
- ✅ PR #61：Firecrawl `lang=` kwarg revert（search 壞掉的根本原因）
- ✅ PR #62：Gmail `list_messages` 空結果 ThreadPoolExecutor crash
- ✅ PR #63：Deep Research max_tokens — fetch 截斷 20k→5k + 預算 2+3
- ✅ PR #64：`call_claude_with_tools` max_tokens 2048→8192
- ✅ Deep Research 功能驗收通過（Vault 有報告輸出）
- ✅ Gmail 大量搜尋策略加入 system prompt（分批 5 封 + ask_user）
- ⬜ **project-bootstrap template 同步**（`tpl-project.md` + `tpl-action.md` 脫節）
- ⬜ **Slack thread 續問實機測試**（多輪對話未驗）

**Robin（今晚要做）：**
- ⬜ `/kb/research` E2E 未測（skill 化前先驗）
- ⬜ Robin Reader：metadata 卡片顯示 + 貼上圖片顯示（本機測試）
- ⬜ KB Research UI 呈現方式（修修想再改）

**Phase 4 Bridge UI：**
- ✅ PR-A backend merged（#41）
- ✅ PR-B Memory UI（#42）、PR-C Cost UI（#44）、Bridge Hub（#45）
- ✅ Direction B Instrument Panel 重設計 + VPS 部署（PR #65，2026-04-21）
- ⬜ Tech debt：`agent_memory.update` rollback / `MemoryUpdate.type` Literal / docstring
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

**SEO Solution（下一個重點）：**
- ⬜ prior-art-research（DataForSEO MCP / Ahrefs MCP / 部落格 audit workflow）
- ⬜ skill 家族設計（`seo-audit-post` / `seo-keyword-enrich` / `seo-optimize-draft`）

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
- ⬜ Robin 核心流程（ingest、kb_search）補測試覆蓋率
- ⬜ Brook compose.py 補測試覆蓋率
- ⬜ Thousand Sunny routers smoke test

**Phase 1 Wave 2（foundation 已合併，見 project_phase1_foundation_pr.md）：**
- 🚧 VPS baseline 壓測 24h，2026-04-23 ~22:21 滿 → `ssh nakama-vps "python3 /home/nakama/scripts/vps_baseline_monitor.py --analyze"` 看 verdict
- ⬜ `shared/gutenberg_validator.py`（ADR-005a §4，round-trip + whitelist + attr JSON 驗）
- ⬜ `agents/brook/compose.py` + style_profile_loader + tag_filter + compliance_scan
- ⬜ `agents/usopp/publisher.py` + wp_client + seopress_writer + litespeed_purge + advisory locks
- ⬜ `shared/schemas/external/wordpress.py` + `external/seopress.py`（anti-corruption layer）
- ⬜ Bridge `/bridge/drafts` UI + routes + CLI fallback
- ✅ `agents/franky/` 全三 slice + `/healthz` + weekly digest + `/bridge/franky`（PR #74/#75/#76，2026-04-23；見 [project_franky_phase1_parallel_session.md](project_franky_phase1_parallel_session.md)）
- ⬜ Franky VPS 上線：`.env` 補 `SLACK_SHOSHO_USER_ID` + R2_* + 加 3 條 cron + 跑 UptimeRobot runbook
- ⬜ `config/style-profiles/*.yaml` 三個 profile（book-review / people / science）

**Phase 1 foundation borderline follow-ups（6 項，非 blocker）：**
- ⬜ `PRAGMA synchronous=NORMAL` + `busy_timeout=5000` 移到 `_get_conn()` 開啟時設（ADR-006 §5 checklist）
- ⬜ `claim_approved_drafts` 的 `UnknownPayloadVersionError` 改 `mark_failed` 兜底，不 raise 上來
- ⬜ `BlockNodeV1.children` 加 per-block-type whitelist（list 只能 list_item）
- ⬜ 3 個 docstring 準確度修正（atomic mutex / filter-out / CHECK 反向生成）
