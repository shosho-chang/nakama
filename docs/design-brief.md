# Nakama — Aesthetic Exploration Brief

> 給 Claude Design 的 input brief。目的：讓它理解產品 + 限制，然後**提出 2-3 個不同方向的 aesthetic direction 讓我選**，而不是直接收斂到某個 default。
>
> 修改這份 brief 再丟給 Claude Design；每次迭代完後回頭更新這份 + [docs/design-system.md](design-system.md)。

---

## Product Identity

**Nakama** 是 Health & Wellness / Longevity 內容創作者的 **AI agent 團隊系統**。每個 agent 有人格、有分工；我（創作者）是船長，agents 是我的夥伴。

使用場景：我每天跟 agents 協作產出 YouTube 影片、部落格文章、社群問答。Bridge 是我觀察 agent 狀態的**工作介面**，不是 marketing 網站。

**One Piece 角色是 internal naming**（取「團隊精神 — 各司其職、個性鮮明、有冒險感」），但**視覺不要卡通化、不要漫畫化**，也不要海盜船主題。視覺語言要獨立成立。

## 目標用戶

- **主要**：我自己（內容創作者，台灣，美學有要求，長時間盯著工具）
- **次要**：未來 Chopper 社群平台的會員（Health & Wellness 關注者）
- **語言**：繁體中文為主，英文為輔（所有 UI 字串要能塞中文不破版）

## Agent 團隊（設計主角）

每個 agent 在 Bridge UI 上都有自己的卡片與狀態顯示。個性種子影響視覺識別的**語氣與密度**，不是靠不同字體做淺層差異化。

| Agent | 角色定位 | 互動介面 | 個性種子 | UI 視覺語氣 |
|-------|---------|---------|---------|------------|
| **Robin** | 知識管理 — KB ingest / wiki 建立 / 概念提取 | Web UI（`/robin`）| 博學、安靜、系統化、連結者 | 書感偏靜態、密度高、像索引卡 |
| **Nami** | LifeOS 秘書 — 任務／行事曆／Project 管理，Slack bot | Slack（@Nami）+ 對話式 tool-use | 精明、務實、有主見、不卑不亢、高執行力 | 導航感、資訊流動、即時狀態 |
| **Zoro** | 情報蒐集 — Keyword Research（YouTube / Trends / Reddit / Twitter 雙語） | API endpoint + Web UI | 敏銳、追蹤者、數據獵手、少話 | 數字密集、圖表導向、沒廢話 |
| **Brook** | 內容 Composer — 對話式長文寫作、格式轉換（Blog / YouTube / IG / Newsletter） | Web chat UI（`/brook/chat`）| 作曲家、藝術家、協作引導、文體把握 | 大留白、段落感強、書寫節奏 |
| **Sanji** | 社群管理 — Fluent Community 社群營運、成員互動、xAI Grok 路由 | Slack（@Sanji）| 熱血、重情義、坦直、對每個人細心 | 溫度感、對話泡泡、即時互動 |
| **Franky** | 系統維護 — 健康檢查、套件掃描、工程週報、事件 emit | Cron job + Vault 寫入 | 工程師魂、監測者、邏輯清晰、責任心 | 終端機感、數字儀表、log 流 |
| **Usopp** | 發布者 — WordPress / YouTube / 社群媒體 / Fluent CRM（規劃中） | Human-in-the-loop 核准 + Web UI | 說故事的人、謹慎求證、需人核准才發 | 待機狀態、等待批准的節奏 |
| **Chopper** | 1:1 健康顧問 — 會員個人問答、記憶式陪伴（規劃中） | Webhook + 對話平台（待定） | 親和、傾聽、不評判、醫師的溫柔 | 柔和對話感、進度追蹤 |
| **Thousand Sunny** | 甲板儀表板 — multi-agent 控制台（規劃中） | Web dashboard | 承載全員的船、整合視角 | 總覽感、船艙地圖式佈局 |

**Bridge UI 的三個頁面顯示哪些 agents：**
- **Landing `/bridge`**：全員狀態卡（最後執行時間、本日 token 用量、是否在線）
- **Memory `/bridge/memory`**：依 agent 分頁，顯示記憶條目（preference / fact / decision / project 四類型）+ 編輯 modal + 封存確認
- **Cost `/bridge/cost`**：每個 agent 的 token 用量趨勢（stacked bar by model）+ 本月費用摘要表（agent × model 矩陣）

## 要設計的 Surfaces（按優先度）

1. **Bridge UI**（internal dashboard，優先做）
   - Landing hub `/bridge` — agent 狀態總覽
   - Memory 管理 `/bridge/memory` — 看 agent 記憶、編輯、歸檔
   - 成本儀表板 `/bridge/cost` — 每個 agent 的 token 用量、費用趨勢
2. **Thousand Sunny 甲板儀表板** — 多 agent 控制台（規劃中）
3. **Chopper 社群 UI** — 健康問答平台（未來）
4. **Brook 對外 template** — SEO report / content brief 的 PPTX / 部落格 post 樣式

## 產品氣質方向（open — 請 Claude Design 提出 2-3 個方向）

**正面關鍵字**（任選幾個組合）：
- Editorial 知識誌感（像 Popular Science 早期、Wired、MIT Tech Review）
- 日式實驗室 / 精密工具感
- 東方藥典 × 現代資訊圖表
- Lab notebook / 科研手稿
- Swiss modernist / Kenya Hara 式的 intentional emptiness
- Brutalist 但科學（不是粗暴，是結構裸露）

**明確反面**（硬性禁止）：
- Wellness app default — sage green + Inter + 柔和圓角卡片
- SaaS default — 紫漸層 + shadcn 標配
- 醫療 app default — 藍白 + 無感情嚴肅
- Instagram wellness — 米白 + 柔光 + 大留白
- 任何 AI slop default

**請你做的**：提出 **2-3 個不同方向**的 aesthetic proposal（每個一段話 + 一張 Bridge landing mock），讓我選一個或混合，不要直接收斂到一個。

## 硬限制

- **繁體中文**為主，英文為輔，字型要能塞中文且有台灣語境美感
- **Mono font 給數字**（cost、token count、timestamp）— 可以跟 body font 對比強烈
- **Contrast**：body on bg ≥ 7:1（AAA）、secondary ≥ 4.5:1（AA）、accent ≥ 3:1
- **Motion**：要支援 `prefers-reduced-motion`
- **Responsive**：主要用 Mac 瀏覽器但會掏 iPhone 看，mobile 不能破版

## 禁用 AI Slop（硬性）

- Inter / Roboto / Arial 當主要字型（除非 aesthetic direction 明確要 invisible typography）
- `bg-gradient-to-*` with purple
- `grid-cols-3` / `grid-cols-4` 均勻 card grid（card 一定要有至少一張破格）
- 通用 CTA：「Get Started」「Learn More」
- 永遠 centered 1200px column layout
- 寫死色碼 / 字型在 class 裡（全部走 tokens）

## 想要你產出的東西

按這個順序：

1. **2-3 個 Aesthetic Direction 提案**（每個一段話，具體到兩個設計師看了能做出相似作品 + 一張 Bridge landing mock）
2. 我選一個方向後，產出完整系統：
   - Typography set（display + body + mono，各自 font source）
   - Color tokens（dominant + accent + semantic）
   - Spacing scale + motion 語彙
   - Bridge **三個頁面** reference HTML（landing / memory / cost）
   - Component patterns（button / card / data display / form）
   - 每個 component 的 states（default / loading / empty / error / hover / focus / disabled）

## Handoff

匯出「交付套件」給 Claude Code（我）時：

- HTML reference 放 `docs/design-ref/<topic>/`
- Tokens 我會轉成 `bridge/static/tokens.css` + tailwind config
- 更新 [docs/design-system.md](design-system.md) v1
- 照 reference 重寫 `bridge/templates/` 三頁

---

## 使用這份 brief 的方式

1. 打開 claude.ai/design
2. 貼上本文件全文當 prompt
3. 上傳一兩張 inspiration 參考圖（可選 — 雜誌內頁、lab 介面截圖、日式海報等）
4. 等它出 2-3 個方向提案 → 選一個 → 讓它展開完整系統
5. 匯出 → 回頭更新這份 brief 的 revision log + design-system.md

## Revision Log

| 日期 | 版本 | 變更 |
|------|------|------|
| 2026-04-20 | v0 | 首版 brief 起草 |
| 2026-04-20 | v0.1 | Agent 表格補 Sanji / Franky / Usopp；加互動介面 + UI 視覺語氣欄；補 Bridge 三頁面顯示說明 |
