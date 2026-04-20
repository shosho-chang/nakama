---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-19
confidence: high
ttl: 90d
originSessionId: 8bece3a7-26ae-4215-bade-04d2bca1809b
---
**Transcriber（PR #9 已 merge，2026-04-15）：**
- ✅ FunASR + Auphonic + LLM 校正升級（Pinyin + JSON diff + LifeOS 整合 + Opus）
- ✅ 78 個測試全過，已 merge 到 main
- ⬜ LLM 校正 E2E 實測（`use_llm_correction=True` + 真實音檔）
- ⬜ Auphonic E2E 實測（完整 pipeline 含 normalization）
- ⬜ CLI 命令 → Skill 化

**Robin 本機專用（PR #13 已 merge，2026-04-16）：**
- ✅ Robin 服務改為僅本機執行（VPS 設 `DISABLE_ROBIN=1`）
- ✅ VPS 部署：git pull + `.env` 加 `DISABLE_ROBIN=1` + systemctl restart（2026-04-17）
- ⚠️ PR #13 漏掉 Brook 依賴 `/login` 的問題 → 已由 PR #14 修復

**Auth router 抽出（PR #14 已 merge，2026-04-17）：**
- ✅ `/login` + `/logout` 抽到 `thousand_sunny/routers/auth.py`，VPS 永遠掛載
- ✅ 支援 `?next=<path>`（含 open-redirect 防護），登入後回原頁
- ✅ VPS `/` 重導到 `/brook/chat`
- ✅ 13 個新測試全過（174 total, no regression）
- ✅ VPS 部署 PR #14 + #15 + #16（2026-04-17，git pull + restart + 實測 /login 200、Brook 登入正常）

**Tech debt 清理（PR #15 + #16 + #17 已 merge，2026-04-17）：**
- ✅ PR #15：`thousand_sunny/routers/robin.py:414` 的 `Path.unlink` 改用 `_send_to_recycle_bin`
- ✅ PR #16：Cookie `robin_auth` → `nakama_auth`（5 檔 45 行，174 tests 通過）
- ✅ PR #17：auth cookie 加 `Secure` + `SameSite=Lax`（176 tests 通過）
- ✅ VPS 部署 PR #17 + F12 驗 Secure flag 打勾（2026-04-17）

**HTTPS 部署完成（2026-04-17）：**
- ✅ Cloudflare Tunnel：`https://nakama.shosho.tw` → `localhost:8000`（VPS 的 `cloudflared` systemd service）
- ✅ ufw 關掉 8000 對外（tunnel 走 outbound，不需要對外 port）
- ✅ LiteSpeed（個人網站）不受影響
- ℹ️ CF SSL mode: 走 tunnel 不需要設
- ✅ `thousand-sunny.service` 改為 `--host 127.0.0.1`（2026-04-17，ss 驗證只 listen localhost）

**VPS 已部署完成（2026-04-15）：**
- Thousand Sunny web server 已上線
- Zoro Keyword Research 端到端測試通過

**待測試：**
- Robin Reader：metadata 卡片顯示 + 貼上圖片顯示（本機測試）
- Brook 聊天頁面端到端測試
- KB Research UI 呈現方式（修修想再改）

**Robin 大文件 Ingest（PR #11 已 merge，2026-04-15）：**
- ✅ PDF 解析（pymupdf4llm 本地 + Firecrawl 遠端）
- ✅ 本地 LLM 客戶端（OpenAI-compatible，支援 llama.cpp / Ollama）
- ✅ Map-Reduce 大文件摘要（chunker + prompts + fallback）
- ✅ 33 個新測試全過
- ⬜ E2E 實測：Gemma 4 26B 已安裝，丟 PDF 跑完整 pipeline
- ⬜ `shared/local_llm.py` 加 image 支援（OpenAI-compatible multimodal content parts array + base64 encode helper）— 解 Robin ingest PDF 圖表問題；Gemma 4 26B-A4B 原生支援 image input

**Robin 內容性質分類（PR #12 已 merge，2026-04-16）：**
- ✅ 兩層分類架構（source_type + content_nature）
- ✅ 6 類別專屬 prompt（research/textbook/clinical_protocol/narrative/commentary + popular_science default）
- ✅ prompt_loader 支援 categories/ 路由 + fallback
- ✅ Web UI 兩個 dropdown + 取消按鈕
- ✅ 領域擴充：運動科學、營養科學、腦神經科學（睡眠/飲食/運動/情緒四大面向）
- ⬜ Web UI 實測：不同 content_nature 執行 ingest
- ⬜ E2E：大文件 + 本地 LLM + 類別 prompt 驗證

**Transcriber 多模態仲裁 pipeline（路線 2，2026-04-17 設計凍結）：**
- ✅ PR-A：`shared/audio_clip.py`（ffmpeg 切片 + tempfile 清理）— PR #18 merged
- ✅ PR-B：`shared/gemini_client.py`（Gemini 2.5 Pro audio + JSON schema）— PR #19 merged 2026-04-17，順手修 CI 裝 ffmpeg
- ✅ PR-C：`shared/multimodal_arbiter.py`（串 audio_clip + gemini_client）— PR #20 merged 2026-04-17，code review 修掉 thread-local cost tracking bug
- ✅ PR-D：`transcriber.py` `_correct_with_llm()` 兩輪 pipeline — PR #21 merged 2026-04-17，策略 A（自動應用 verdicts，無第二輪 Opus）；code review 修掉 `accept_suggestion` 分支沒寫 corrections 的 bug
- ✅ **2026-04-18 收尾三件**：
  - PR #24 thinking_budget=512 + thoughts token cost tracking 修（成本 5-10x 降）
  - PR #25 拒答訊號偵測 + refused verdict（code-review 抓到 final_text 誤掃 bug 同 branch 修）
  - PR #26 `run_transcribe.py` argparse + `--project-file` 等 CLI flags
- ⬜ **週一 2026-04-20：1hr Angie 首次正式實測**（驗成本、品質、QC 報告、拒答率）

**Skill 化工程（2026-04-18 更新）：**
- ✅ transcribe（`f:/nakama/.claude/skills/transcribe/`）— 週一 Angie 實測作為首次 eval
- ✅ **keyword-research** (Zoro) — PR #31 pytrends→trendspy 解阻塞 + PR #32 skill + CLI wrapper + 6 references + frontmatter output contract（2026-04-18 merged）
  - ✅ **2026-04-19 首次真實 eval 完成**（深度睡眠 × youtube/blog 雙跑）— **PASS-WITH-NOTES**
    - 報告：[docs/evals/keyword-research-2026-04-19-deep-sleep.md](../../docs/evals/keyword-research-2026-04-19-deep-sleep.md)
    - Graceful degrade 實測驗證（Run 2 Trends quota 擋住仍 7/10 達門檻）
    - Backlog 6 項已開 GH issue：術語表補深度睡眠 / lowercase normalize / {today} 注入 / reddit_zh query 精度 / twitter zh-TW 分流 / CLI emit 實測成本（實踐 capability card 原則）
- ✅ **project-bootstrap**（Nami × LifeOS）— 2026-04-19 feat branch `feat/nami-project-bootstrap`
  - 4 unit 完成：`shared/lifeos_writer.py` + `scripts/run_project_bootstrap.py` + `.claude/skills/project-bootstrap/` + `gateway/conversation_state.py` (Slack thread 多輪) + Nami `create_project` intent
  - 支援 4 content_type（youtube/blog/research/podcast），硬編碼 3-task 套餐
  - 43 新測試（28 lifeos_writer + 7 conversation_state + 8 Nami flow），全 340 tests pass
  - ⬜ Template 同步更新（`Templates/tpl-project.md` + `tpl-action.md` 已跟 gold standard 脫節，獨立工作）
  - ⬜ Slack 實機測試（需 VPS 部署 + 開 thread 續問實測）
- ⬜ weekly-report (Franky)
- ⬜ morning-brief (Nami) — Nami handler 已有 create_project/continue_flow 骨架，morning-brief 可共用 Unit 4 thread 機制
- ⬜ kb-search (Robin) — `/kb/research` 未 E2E 測，skill 化前先驗
- 🚧 style-extractor — **PRD v4 草稿完成**（LifeOS KB/Wiki/Outputs/style-extractor-prd-draft.md），重新定位為 building block（產出 voice profile 給下游 workflow skill 用）；V1 產出三個 profile：修修-人物報導 / 修修-科普文章 / 修修-讀書心得；LLM 用 Opus 4.7；等修修備齊 3 個 StyleSamples 資料夾（每個 8–10 篇）就進入實作。詳見 LifeOS 記憶 [[project_style_extractor]]
- ⬜ interview-to-article skill — style-extractor 下游 workflow，Podcast 逐字稿 → 人物報導；需獨立 PRD
- ⬜ kb-synthesize-article skill — style-extractor 下游 workflow，Project + KB refs → 科普文章；需獨立 PRD
- ⬜ book-reflection-compose skill — style-extractor 下游 workflow，書 + 閱讀筆記 → 讀書心得；需獨立 PRD

**SEO Solution（下一個重點，keyword-research 完成後接棒）：**
詳見 [project_seo_solution_scope.md](project_seo_solution_scope.md)
- ⬜ 跑 prior-art-research 針對 DataForSEO MCP / Ahrefs MCP / 部落格 audit workflow
- ⬜ 設計 skill 家族（可能 2-3 個：`seo-audit-post` / `seo-keyword-enrich` / `seo-optimize-draft`）
- ⬜ Brook compose 整合 — 寫草稿時吃 style profile + SEO skill 輸出產出排行潛力內容

**雙語閱讀 Pipeline（2026-04-18，P0+P1+P2A 完成）：**
- ✅ PR #27：`shared/translator.py` + 台灣術語表（150+ 詞，user_terms 自動學習）
- ✅ PR #28：`shared/web_scraper.py` 三層擷取（Trafilatura → Readability → Firecrawl）
- ✅ PR #29：Robin Reader 雙語切換 + `/scrape-translate` 端點（URL → scrape → translate → Reader）
  - `bilingual: true` frontmatter 自動啟動雙語模式（reader.html IS_BILINGUAL）
  - source_type/content_nature allowlist + url newline 清理（YAML injection 防護）
  - ⚠️ PR #29 留了兩個 CI 債（ruff format drift + `test_scrape_translate_success` 漏 patch `_get_inbox`）→ 已於 PR #32 順手修掉
- ✅ PR #30：`shared/pdf_parser.py` pdfplumber 精確表格（with_tables=True）+ Firecrawl result.markdown 修正
  - research/textbook/clinical_protocol → 自動 with_tables=True（ingest.py）
- ⬜ **P2B：BabelDOC 整合**（PDF 學術論文 → 雙語 PDF，需 Immersive Translate API key，下次討論是否需要）
- ⬜ **P3：Annotation → Ingest 整合**（reader 畫線/注解 → KB 入庫時一起加入）

**Zoro 遷移 + Skill 化（PR #31 + #32，2026-04-18 merged）：**
- ✅ PR #31：pytrends → trendspy 遷移（pytrends 2025-04-17 archived，trendspy 0.1.6 是後繼者，non-drop-in 四個 API 改寫）
- ✅ PR #32：keyword-research skill 化（SKILL.md + 6 references + `scripts/run_keyword_research.py` CLI + 結構化 frontmatter output contract）
  - code-review 抓到 3 bug 同 branch 修：缺 `load_dotenv()` / SKILL.md「5 steps」誤標 / CLI 缺 elapsed time
  - 順手清 PR #29 的 2 個 CI 債

**Multi-bot Slack gateway（PR #55 merged，2026-04-20）：**
- ✅ 每個 agent 獨立 Slack app（`<AGENT>_SLACK_BOT_TOKEN`）
- ✅ `_discover_bots` 改 regex scan env + `get_handler()` gate（移除硬寫 tuple）
- ✅ `run()` crash_event — 任何 bot thread 死亡即 raise，讓 systemd 重啟
- ✅ 10 個測試，86 gateway tests pass
- ⬜ **Zoro bot 上線**（Phase 2 brainstorm 的 blocker）：Phase 1 — 建 Zoro Slack app + 加 token → gateway 自動啟動

**Phase 4 Bridge UI（PR #41 PR-A merged，2026-04-20）：**
- ✅ PR-A backend：`shared/pricing.py` + `api_calls` schema migration（cache tokens 補登）+ `shared/agent_memory.py` get/update/list_agents + `thousand_sunny/routers/bridge.py`（memory CRUD + cost API）
- ✅ 59 個新測試（pricing / cost tracking / memory update-family / bridge router）— 全 457 tests pass
- ✅ Code review PASS（7 issue 全部 Haiku score < 80，無 blocker）
- ⬜ **PR-B：Memory 頁 UI**（`thousand_sunny/templates/bridge/memory.html` + agent tabs + 編輯 modal + delete confirm）
- ⬜ **PR-C：Cost dashboard 頁 UI**（range selector + Chart.js stacked bar + agent×model 表）
- ⬜ **VPS 部署**（PR-C merge 後一起部署；`api_calls` migration 會自動跑）
- ⬜ **Tech debt follow-up**（review 抓到但未擋 merge）：
  - `agent_memory.update` 加 `conn.rollback()` after IntegrityError（score 68，singleton conn dirty tx）
  - `MemoryUpdate.type` 改 `Literal["preference","fact","decision","project"]`（score 60；`agent_memory.add/update` 也可順手加 enum check）
  - `shared/pricing.py` 模組 docstring lookup order 跟 function docstring 衝突（module 寫 env 是 step 3，實際是 step 1）
  - `get_cost_summary` docstring 漏新加的 cache 欄位

**Nami Google Calendar 整合（PR #39 已 merge + VPS deployed，2026-04-19）：**
- ✅ 4 個 Calendar tools（create/list/update/delete_calendar_event）+ 衝突偵測
- ✅ OAuth 2.0 user-consent + token refresh/persist（filelock 保護）
- ✅ VPS 實戰驗證：token rotate + persist OK、API 通、事件正確列出
- ✅ Slack E2E 實測通過：create/list/update/delete 全正常
- ✅ **Calendar × Task 單向同步** — PR #40 merged + VPS deployed

**Nami Gmail 整合（2026-04-20，完成並 VPS deployed）：**
- ✅ 6 個 Gmail tools：list_gmail_unread / get_gmail_message / search_gmail_history / create_gmail_draft / update_gmail_draft / send_gmail_draft
- ✅ Triage 規則：Primary only + Respond/Shosho label 24h 超時提醒
- ✅ Code review（PR #57）三個 bug 修掉
- ✅ SSL bug 修（per-thread service client）
- 詳見 `project_nami_gmail.md`

**待開發（agent 功能）：**
- Nami（航海士）— 消費 Robin/Franky 事件，產出 Morning Brief
- Zoro 其餘功能 — PubMed / KOL 追蹤
- Brook Phase 2 — SSE streaming、風格參考庫、Prompt Caching、匯出到 Vault
- PubMed 整合 — 修修有 n8n RSS 工作流，預計用於其他功能，不是 Zoro
- Memory Phase 4 Bridge UI — ✅ **PR-A backend merged 2026-04-20 (#41, commit 73a064c)**；下面是剩下的工作

**基礎建設 — 補測試覆蓋率：**
- Robin 核心流程（ingest、kb_search）
- Brook compose.py
- Thousand Sunny routers smoke test

**已完成（2026-04-15）：**
- Transcriber 升級：FunASR + Auphonic（feat branch，待 merge）
- Thousand Sunny web server 重構
- Zoro Keyword Research 雙語版 + 直寫 markdown
