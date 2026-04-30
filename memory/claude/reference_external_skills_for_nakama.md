---
name: Nakama 適用的外部 GitHub Claude skills 候選清單
description: 開發新功能前先翻；按 Nakama 領域分類（內容創作 / 知識庫 / 工程 / Cloudflare / Python / GitHub / Podcast / 平台整合 / UI），含適用判斷與排除清單
type: reference
created: 2026-04-30
---

# 主要候選 repo（按品質）

| Repo | 規模 | 用途 |
|------|------|------|
| [anthropics/skills](https://github.com/anthropics/skills) | 官方 126k★ | docx/pdf/pptx/xlsx + frontend-design + skill-creator |
| [obra/superpowers](https://github.com/obra/superpowers) | 20+ | 戰鬥測試的工程 skills |
| [coreyhaines31/marketingskills](https://github.com/coreyhaines31/marketingskills) | 43 | 行銷整包，**對 Brook / 三條 line 最關鍵** |
| [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills) | 232+ | 含 9 個 bundle，pr-review-expert / llm-wiki / rag-architect 在這裡 |
| [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) | 177 + 80 SaaS | 平台整合大全 |
| [VoltAgent/awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) | 1000+ | 多家官方收錄（Cloudflare / TrailOfBits / OpenAI / Microsoft） |
| [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills) | 精選 | 入門掃描用 |
| [michalparkola/tapestry-skills-for-claude-code](https://github.com/michalparkola/tapestry-skills-for-claude-code) | 4 | tapestry / article-extractor / youtube-transcript / ship-learn-next |
| [sanjay3290/ai-skills](https://github.com/sanjay3290/ai-skills) | — | deep-research / google-workspace / postgres / outline |
| [PleasePrompto/notebooklm-skill](https://github.com/PleasePrompto/notebooklm-skill) | — | NotebookLM 整合 |

---

# A. 內容創作 / Repurpose（**最重要 — 對應三條 line + Brook**）

`coreyhaines31/marketingskills` 整包 43 skills，可疊現有 `seo-audit-post` / `keyword-research` / `seo-keyword-enrich`。

| Skill | Nakama 對應 use case |
|-------|---------|
| **ai-seo** | 為 AI 搜尋（Perplexity/AI Overview）優化，**現有 SEO 沒做到** |
| **programmatic-seo** | 健康主題 long-tail 大量 SEO 頁 |
| **schema-markup** | HowTo / Article / FAQ 結構化資料 |
| **content-strategy** | 主題規劃，補 keyword-research → article-compose 中間 gap |
| **copy-editing** | 改寫舊文 / 部落格體檢配套 |
| **social-content** | LinkedIn/Twitter/IG，**Line 1 FB+IG 直接命中** |
| **email-sequence** | Newsletter（未來） |
| **community-marketing** | **Chopper 社群 QA** |
| **lead-magnets** | 自由艦隊千人社群轉化 |
| **referral-program** | 千人社群 viral loop |
| **launch-strategy** | 新文章 / podcast 上線 |
| **paid-ads** + **ad-creative** | 推廣健康內容 |
| **competitor-profiling** | 健康領域對手分析 |
| **marketing-psychology** | 健康內容說服框架 |
| **page-cro** / **form-cro** / **popup-cro** | 部落格轉化 |
| **customer-research** | 千人社群訪談合成 |

---

# B. 知識庫 / 研究（Robin / Brook）

| Skill | 來源 | 用途 |
|-------|------|------|
| **llm-wiki** | alirezarezvani | Obsidian 第二大腦整合，**直接對應 LifeOS vault** |
| **rag-architect** | alirezarezvani | RAG pipeline 補強，可審 Robin 現有架構 |
| **tapestry** | michalparkola | 文件互鏈摘要，**Karpathy-style cross-source wiki 同哲學** |
| **article-extractor** | michalparkola | 抓網頁全文 + metadata（補 Robin Reader 邊角） |
| **youtube-transcript** | michalparkola | YouTube 字幕抓取，**配 transcribe 補位** |
| **NotebookLM Integration** | PleasePrompto | 跟 NotebookLM 對話查 source |
| **deep-research** | sanjay3290/ai-skills | Gemini Deep Research，**對 Nami Phase 1 補強或替代** |

---

# C. 工程補強（補既有 tdd / diagnose / code-review 之外）

`obra/superpowers` 整包：

| Skill | 用途 |
|-------|------|
| **brainstorming** | 把粗略想法問成完整設計，**對應 P9 規劃前 brainstorm** |
| **root-cause-tracing** | 把錯誤追到原始 trigger（補 diagnose） |
| **finishing-a-development-branch** | 收尾流程 |
| **subagent-driven-development** | sub-agent dispatch 範式參考 |
| **using-git-worktrees** | 既有 worktree pattern 對照 |

`alirezarezvani/claude-skills` engineering bundle：

| Skill | 用途 |
|-------|------|
| **pr-review-expert** | Blast radius + security 分析（疊在 ultrareview 之上） |
| **skill-security-auditor** | 裝外部 skill 前掃風險（**新增 skill 必跑**） |
| **dependency-auditor** | 多語套件審查 + 升級規劃 |
| **tech-debt-tracker** | 技術債識別與優先級 |
| **performance-profiler** | Python profiling（Robin/Brook 跑久時） |
| **ci-cd-pipeline-builder** | GitHub Actions 產生器 |
| **mcp-server-builder** | OpenAPI → MCP（Nami 加 SaaS 整合用） |

---

# D. Cloudflare（對應 CF Tunnel + WAF + R2 backup）

VoltAgent 收錄的 Cloudflare 官方 skills：

- **cloudflare/cloudflare** — 全平台 coverage
- **cloudflare/workers-best-practices** — 若未來把 audit/probe 邊緣化
- **cloudflare/durable-objects** — 狀態協調

---

# E. Python 工程（trailofbits 官方）

- **trailofbits/modern-python** — uv / ruff / pytest 最佳實踐
- **trailofbits/property-based-testing** — Hypothesis（**Phase 6 已用，這份正式化**）

---

# F. GitHub 工作流（OpenAI 官方）

| Skill | 用途 |
|-------|------|
| **openai/yeet** | stage→commit→push→開 PR 一條龍 |
| **openai/gh-fix-ci** | 修 GitHub Actions 失敗 |
| **openai/gh-address-comments** | 處理 PR review comment |

---

# G. Podcast / 多模態（**Line 1 訪談 + 主題影片自動剪輯**）

- **microsoft/podcast-generation** — Azure OpenAI Realtime API 產生 podcast
- **openai/transcribe** — OpenAI 版轉錄（**可跟 WhisperX 比對品質**）
- **microsoft/azure-ai-transcription-py** — Azure 轉錄
- **Video Downloader**（ComposioHQ） — YouTube/平台抓影片
- **ffmpeg / yt-dlp 相關工具**（待找專屬 skill） — Line 1 主題影片自動剪輯 dependency

詳見 [project_podcast_theme_video_repurpose.md](project_podcast_theme_video_repurpose.md)。

---

# H. 平台整合（Composio，**對應 Chopper / Brook 對外 + Line 1 多 channel**）

挑用得到的：

| Skill | 對應 |
|-------|------|
| **Slack Automation** | Nami 補強（已有自製可比對） |
| **Discord Automation** | 若 Chopper 走 Discord |
| **Reddit Automation** | VPS IP 被 Reddit 擋的解法（OAuth 必須） |
| **YouTube Automation** | **Line 1 主題影片上架 + 留言管理** |
| **LinkedIn / Twitter / Instagram Automation** | **Line 1/2/3 三平台分發** |
| **Notion / Confluence** | 若有外部協作 |
| **Google Calendar / Gmail** | Nami 補強 |
| **Google Sheets** | 數據看板 |
| **Stripe** | 千人社群收費（未來） |
| **Sentry / Datadog / PagerDuty** | 替代 Franky news_digest 監控 |

---

# I. UI 美學（**docs/design-system.md 美學要求對位**）

- **anthropics/frontend-design** — 「拒絕 generic」bold UI 指引（**對位 feedback_aesthetic_first_class**）
- **anthropics/web-artifacts-builder** — React + Tailwind artifact
- **anthropics/algorithmic-art** — p5.js（甲板儀表板裝飾）
- **alirezarezvani/landing-page-generator** — TSX + Tailwind（部落格 landing）
- **shadcn/ui skill** — Component 規範

---

# 不推薦清單（避免重複評估）

- **CRM**（HubSpot/Salesforce/Close/Pipedrive/Zoho） — 不是 SaaS 業務
- **PM 工具**（Asana/Jira/Linear/ClickUp/Trello/Monday） — 用 GH Issues
- **Helpdesk**（Zendesk/Freshdesk/Intercom/Help Scout/Freshservice） — 沒客服需求
- **E-commerce**（Shopify/Square/Stripe payments） — 不賣 SaaS
- **iOS Simulator / ffuf / threat-hunting / computer-forensics** — 不在領域
- **aws-skills** — 用 VPS 不是 AWS
- **legal / medical regulatory**（MDR-745、ISO-27001、master-claude-for-legal） — 台灣法規不對位
- **HR**（BambooHR） — 沒 HR 需求

---

# 安裝建議順序

1. **整包裝**：
   - `coreyhaines31/marketingskills`（43 行銷 skills）— **最大 ROI，三條 line repurpose 直接補位**
   - `obra/superpowers`（工程戰鬥測試）
2. **點裝**：`llm-wiki`、`tapestry`、`article-extractor`、`youtube-transcript`、`pr-review-expert`、`skill-security-auditor`
3. **試水比對**：`openai/transcribe`（vs WhisperX）、`microsoft/podcast-generation`（Line 1 polish）
4. **未來**：Composio SaaS skills 按需點裝（YouTube / IG / LinkedIn 對應 Line 1-3 上架）

# 安裝前必做

- 跑 **skill-security-auditor** 預掃 SKILL.md（外部 skill 可能有 prompt injection / 不當 tool call）
- 跑既有的 `prior-art-research` skill 驗證該 skill 是否真的補洞（避免重複）
- 進 git submodule 或 vendored copy，不直接 fork（避免上游 churn）
