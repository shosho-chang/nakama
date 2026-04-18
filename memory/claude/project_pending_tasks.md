---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-18
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

**Skill 化工程（2026-04-18 重排）：**
- ✅ transcribe（`f:/nakama/.claude/skills/transcribe/`）— 週一 Angie 實測作為首次 eval
- 🚧 下一個：keyword-research (Zoro) — 已部署穩定，改寫風險低
- ⬜ weekly-report (Franky)
- ⬜ morning-brief (Nami) — Nami 還沒開發，先做 skill 再 agent
- ⬜ kb-search (Robin) — `/kb/research` 未 E2E 測，skill 化前先驗
- ⬜ style-extractor — 新功能，需先定義 PRD

**雙語閱讀 Pipeline（2026-04-18，P0+P1+P2A 完成）：**
- ✅ PR #27：`shared/translator.py` + 台灣術語表（150+ 詞，user_terms 自動學習）
- ✅ PR #28：`shared/web_scraper.py` 三層擷取（Trafilatura → Readability → Firecrawl）
- ✅ PR #29：Robin Reader 雙語切換 + `/scrape-translate` 端點（URL → scrape → translate → Reader）
  - `bilingual: true` frontmatter 自動啟動雙語模式（reader.html IS_BILINGUAL）
  - source_type/content_nature allowlist + url newline 清理（YAML injection 防護）
- ✅ PR #30：`shared/pdf_parser.py` pdfplumber 精確表格（with_tables=True）+ Firecrawl result.markdown 修正
  - research/textbook/clinical_protocol → 自動 with_tables=True（ingest.py）
- ⬜ **P2B：BabelDOC 整合**（PDF 學術論文 → 雙語 PDF，需 Immersive Translate API key，下次討論是否需要）
- ⬜ **P3：Annotation → Ingest 整合**（reader 畫線/注解 → KB 入庫時一起加入）

**待開發（agent 功能）：**
- Nami（航海士）— 消費 Robin/Franky 事件，產出 Morning Brief
- Zoro 其餘功能 — PubMed / KOL 追蹤
- Brook Phase 2 — SSE streaming、風格參考庫、Prompt Caching、匯出到 Vault
- PubMed 整合 — 修修有 n8n RSS 工作流，預計用於其他功能，不是 Zoro

**基礎建設 — 補測試覆蓋率：**
- Robin 核心流程（ingest、kb_search）
- Brook compose.py
- Thousand Sunny routers smoke test

**已完成（2026-04-15）：**
- Transcriber 升級：FunASR + Auphonic（feat branch，待 merge）
- Thousand Sunny web server 重構
- Zoro Keyword Research 雙語版 + 直寫 markdown
