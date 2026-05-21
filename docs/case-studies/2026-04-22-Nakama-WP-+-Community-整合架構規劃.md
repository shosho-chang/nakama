---
type: case-study
title: 用 Claude Code 一個工作 session 走完 WordPress + Community 整合的架構規劃
date: 2026-04-22
collaborator: Claude Opus 4.7 (1M context)
project: Nakama AI Agent 團隊
status: 規劃凍結，Phase 1 實作開工中
tags:
  - case-study
  - claude-code
  - wordpress
  - fluentcommunity
  - ai-agents
  - adr
  - prior-art-research
  - hitl
---

# 一個下午，把 Nakama 接上 WordPress 與 FluentCommunity 的全盤架構規劃

> 這是一個實戰紀錄。不是 tutorial，不是整理過的成品——而是我和 Claude 在一個工作 session 裡，從模糊的需求出發，走完 prior-art research → 架構決策 → ADR 寫作 → VPS 實測 → 憑證建立的完整過程。中間踩了幾個坑，改過幾次方向，最後產出 3 份 ADR、1 份 Phase 1 plan、3 張 capability card、1 份 runbook，跟 1 份給自己人工挑文章訓練的清單。
>
> 如果你也在用 Claude Code（或 Claude 家族的任何介面）處理**不只寫程式，而是跨系統基礎設施規劃**的工作，這個案例應該有參考價值。

---

## 起點：模糊但重要的需求

我的 AI agent 團隊「Nakama」（以《海賊王》角色命名）已經跑起來幾個月——Robin（KB）、Nami（Secretary / Morning Brief）、Zoro（關鍵字研究）都上線了。

但有兩個核心能力還沒打通：

1. **Agent 能發布 blog**——我的部落格 shosho.tw 上面，未來要讓 Brook（Composer）寫文、Usopp（Publisher）真正發到 WordPress
2. **Agent 能和社群互動**——我的社群站 fleet.shosho.tw 跑在同一台 VPS，之後要讓 Chopper 回答會員的健康問題

這一句話丟給 Claude。以下是 Claude 怎麼把它攤開、研究、決策、執行的。

---

## 第一輪：prior-art research（先查別人做過什麼）

Claude 沒有直接開始寫程式。它**先啟動了一個叫 `prior-art-research` 的 skill**——這是我之前要求它在寫任何新功能前必跑的 gate。

這個 skill 分六個 channel 查：
1. 本地 repo 有沒有相關實作
2. 本機裝過的 skills
3. skills.sh 開放 skill registry
4. Claude Marketplaces
5. MCP server 生態
6. GitHub 熱門 repo
7. PyPI / npm

30 秒內跑出一張比較表，有三個**重大發現**。

### 發現 1：2026 年初，WordPress 官方加入 MCP

2026-02，Anthropic 和 Automattic 合作發布官方 Claude Connector；WordPress Core AI 團隊同步推出 **MCP Adapter**（`WordPress/mcp-adapter`），把 WP 新的 Abilities API（WP 6.9 內建）橋到 MCP。2026-03，WordPress.com 開放 AI agent 的寫入權限。

**意義**：我本來預期要自己刻整個 WP REST 封裝，現在變成「要用 MCP 路徑還是自己打 REST」的設計選擇題。

Claude 的判斷是：**Phase 1 直接打 REST API，不走 MCP**。理由——我的 Python agent 已經有 tool dispatch 機制，多一層 MCP 只是複雜度。但如果未來我想讓 Claude Desktop 之類的外部 client 也能直接操作我的 WP，MCP Adapter 是值得加的「零成本能力」。

這種「先評估、但不一定採用」的決策方式，比網路教學說的「MCP 是未來，一定要用 MCP」實用多了。

### 發現 2：Envato Elements 其實沒有 API（計畫轉彎）

我跟 Claude 說我有 Envato Elements 年度會員、可以無限下載 stock photo/video，還丟了一個 URL：`https://build.envato.com/api/` 給它研究。

Claude 先假設我說對的，計劃讓 Brook 直接呼叫 Envato API 取圖。

但**查過文件後發現**：
- `build.envato.com/api/` 是 **Envato Market**（單買制 marketplace）的 API
- **Envato Elements**（訂閱制）和 Envato Market 是兩個不同產品
- Envato Elements **官方無下載 API**
- 2026-03 更新 ToS 明確禁止「scraping / bots / scripts / 任何自動化下載工具」

Claude 標為**🚨 重要發現**立刻告訴我，並提了替代方案：

| 路徑 | 合法？ | 成本 |
|---|---|---|
| Unsplash / Pexels API | ✅ | 免費 |
| Flux via fal.ai / Replicate | ✅ | $0.003-0.04/張 |
| 本地 FLUX.1-dev（我的 RTX 5070 Ti 16GB） | ✅ | 零 runtime |
| Envato Elements 人工下載 | ✅ | 已付年費 |
| 第三方 scraper | ❌ 違反 ToS | 有封號風險 |

**短短 3 分鐘研究，省了我幾天後才會踩的坑**。如果 Claude 沒做 research、直接開始寫 `envato_client.py`，我會在部署時才發現不能用。

這個發現立刻被寫進記憶：`project_envato_api_reality.md`，未來其他對話也不會再犯。

### 發現 3：Bricks Builder 是個「可能致命」的坑——但有解

我的 blog 用 Bricks 這個 visual builder（朋友幫我架站時裝的，我沒深究）。Claude 查了之後臉色一沉：

> **Bricks 的頁面內容存在 `_bricks_page_content_2` post meta**，是 Bricks 專屬的序列化 JSON，**WordPress REST API 讀不到、也寫不到**。
> 官方論壇和 headless CMS 社群都在抱怨這個 limitation。

如果我的 blog 每篇文章都是在 Bricks builder 裡編輯的——**AI 發布基本破功**，除非要反向工程 Bricks JSON schema，工作量接近重寫。

但 Claude 多問了一個關鍵問題：

> 絕大多數 Bricks 用戶**只把 Bricks 用在 template**（single post template、archive、header、footer），**個別文章內文還是 Gutenberg 寫的**。Bricks 官方甚至提供 `Post Content` element 就是做這件事。
> 你的 blog 是哪一種？

我誠實說「不知道，朋友幫我裝的」，並授權它進我的 WordPress 資料庫查。

它用 `wp-cli` 抽一篇文章的 `post_content` + 檢查 `_bricks_page_content_2`，**30 秒內**有答案：

```
--- post_content 前 500 字元 ---
<!-- wp:quote -->
<blockquote class="wp-block-quote"><!-- wp:paragraph -->
<p>...</p>

--- _bricks_page_content_2 meta ---
(空的)
```

**確認了好消息**：Bricks 只做 template，內文是標準 Gutenberg blocks。REST API 完全可用。AI 發布無 blocker。

這是一個典型的「根據實際資料下結論，不是根據假設」的工作方式。Claude 甚至在我授權之前，**已經想好了兩種情境的不同 plan**——如果是內文也用 Bricks，plan 會完全不同。

---

## 第二輪：釐清需求與 agent 分工

Research 告一段落後，Claude 沒有急著寫 code。它給我一張**問題清單**，分三級重要度：Level 1 blocker（必答）、Level 2 architecture（影響實作細節）、Level 3 details（可先假設）。

中間它挑到一個我自己都沒注意到的矛盾：

> 你寫的「Frankie」要做 publishing——但 ADR-001 明確寫 Publisher = **Usopp**，Franky 是系統維護。你是筆誤，還是想改職責？

我承認是筆誤，順手確認「Chopper 先上線做社群 QA、Sanji 先觀察不互動、Franky 擴編為監控中心」的新分工。

這種「讀你過去的決策文件（ADR）、發現矛盾、逼你二選一」的工作方式——**比起直接按我表面意思做事，更有價值**。它用我自己過去的決策來校正我現在的。

### 我提到 Claude Design，它問 API handoff 機制

我期望讓 Brook 和 Claude Design（Anthropic 的視覺設計工具，claude.ai/design）合作優化。

Claude 點出：**Claude Design 目前沒有 public API**，只有 web UI + export。Brook 是 Python agent，兩邊怎麼「合作」其實需要定義。

它給三個方案：
- α：Brook 輸出 prompt，我手動到 claude.ai/design 生成，匯出套件嵌入 Gutenberg
- β：Claude Design 做整站 template，一次性手動匯入 Bricks
- γ：等 Anthropic 開 Claude Design API

我選 α + β 並行。這個細節從「Brook 能和 Claude Design 合作」這句模糊需求，被 Claude 追問到「具體哪一個 API 路徑」的可執行程度。

---

## 第三輪：實際進 VPS 驗證（大解脫）

我的 VPS 跑在 Vultr 上，xCloud 管 WordPress。之前我對規格的印象是「有點緊」（2 vCPU / 4 GB RAM）。

Claude 授權進去之後，不是亂探，是**先問我同意、列出它打算跑的指令**：

```bash
free -h; df -h; uptime; systemctl list-units ...
wp post list; wp plugin list ...
wp eval-file /tmp/dump-posts.php ...
```

結果讓我鬆一口氣：

| 項目 | 實測 |
|---|---|
| RAM | 1.7G 使用 / **2.1G 可用** |
| Swap | 6.2G 已配 |
| 磁碟 | 120G / **86G 空** |
| Load avg | 0.08 / 0.03 / 0.01（幾乎沒事） |
| Web server | OpenLiteSpeed |
| PHP | 8.3.28 |

不緊。原本我以為要升級 server，暫時不用。

然後它抽出**我的 192 篇 blog 文章**的分類統計：

| 類別 | 篇數 |
|---|---|
| 讀書心得 | 36 |
| 人物故事（Podcast + 人物專訪） | 78 |
| 科普文章（神經 / 運動 / 營養 / 睡眠 / 情緒 / 長壽 / 預防 / 生產力 / 減重） | 71 |
| 其他 | 7 |

這裡 Claude 做了一件我沒想到的事——它自動**把 192 篇的 title + date + excerpt + url + categories 輸出成一份 Markdown checklist**，放到我的 Obsidian vault `Projects/Brook 風格訓練.md`。

讓我可以在 Obsidian 裡**用 checkbox 勾選**哪幾篇要當 Brook 的風格訓練材料。

這個細節展示了 Claude 懂我的工作流——它知道我用 Obsidian，知道 checkbox 是最低摩擦的操作，知道 frontmatter 格式要對。

---

## 第四輪：寫 ADR 和 Plan

資料蒐集完畢後，Claude 花約 10 分鐘產出以下文件：

| 檔案 | 內容 |
|---|---|
| `ADR-005 Publishing Infrastructure` | WP + SEOPress Pro + Bricks 模型 + FluentCRM 完整架構 |
| `ADR-006 HITL Approval Queue` | Bridge `/bridge/drafts` 通用 approval 機制 |
| `ADR-007 Franky Scope Expansion` | 監控中心 + Cloudflare + GSC + GA4 + R2 備份驗證 |
| `docs/plans/phase-1-brook-usopp-franky.md` | 4 週實作順序 |
| `docs/capabilities/wordpress-client.md` | 可獨立開源的 capability card |
| `docs/capabilities/fluent-client.md` | Fluent 全家桶 Python client |
| `docs/capabilities/approval-queue.md` | HITL queue 獨立開源單位 |
| `docs/runbooks/setup-wp-integration-credentials.md` | 我要手動做的憑證 checklist（90 分鐘） |

每份 ADR 都有 Context → Decision → Consequences → Open questions 四段結構，跟我既有的 ADR-001..004 風格一致（Claude 是先讀了那些才動手寫的）。

更重要的是，每個決策都有**「不做的事」section**——這很關鍵，因為它限制了未來 scope creep。例如 ADR-005 明確寫：

> **不做的事**：
> - 不支援 Bricks 原生內文編輯（Brook 永遠走 Gutenberg）
> - 不寫自己的 SEO plugin 替代 SEOPress
> - 不自動新增 category / tag（修修批准才建）
> - 不在 Phase 1 做圖片自動生成

未來誰打開這份 ADR，會明確知道「沒做某件事不是忘了、是故意的」。

---

## 第五輪：實戰 setup credentials（還在進行中）

規劃完後，進入 Phase 1 的前置：我要手動建一堆帳號、憑證、token。runbook 列了 90 分鐘 worth of work，跑到中間就踩了兩個現場坑。

### 坑 1：FluentSecurity 關掉了 Application Password

我在 fleet.shosho.tw 建了 bot user，但 profile 頁找不到 Application Password 區塊（shosho.tw 有）。

Claude 的診斷過程值得一看：

```
# Step 1：兩站 plugin 比對
→ fleet 比 shosho 多一個 fluent-security 2.1.1

# Step 2：grep plugin source code
$ grep -rn 'wp_is_application_passwords_available' \
    /var/www/fleet.shosho.tw/wp-content/plugins/fluent-security/
→ BasicTasksHandler.php:13:
    add_filter('wp_is_application_passwords_available', 
               [$this, 'maybeDisableAppPassword']);

# Step 3：看 handler logic
→ if (Helper::getSetting('disable_app_login') === 'yes') return false;
```

**3 步確認**：FluentSecurity 的 `disable_app_login` 設定被開成 `yes`，用 filter 把整個 Application Password 功能關了。

它給我解法：
1. WP admin → FluentAuth → 把 "Disable Application Passwords" 關掉
2. 補上替代的安全層（bot 不給 admin role、App Password 有辨識度名字、Cloudflare WAF rate limit、Franky 每日檢查 last_used）

這段**同步更新到 runbook**，未來我或任何 collaborator 再看就不會踩同樣的坑。

### 坑 2：Cloudflare zone 等於 root domain

我在 Cloudflare API token 的 Zone Resources 選單裡只找到 shosho.tw，沒看到 fleet.shosho.tw，以為設定錯了。

Claude 一秒解釋：

> Cloudflare zone 是 **root domain**，fleet.shosho.tw 是 subdomain，**共用同一個 zone**。選 shosho.tw 就自動包含 fleet。
> Franky 要分別追蹤時，用 GraphQL `clientRequestHTTPHost` dimension filter 分開。

同樣，這段知識**同步回寫到 runbook 和 ADR-007**（原本我設計成兩個 zone ID 存在 .env，現在改成一個 zone ID + hostname filter）。

### 坑 3（我自己造的）：貼錯 service account email

我把 GCP service account 的 **Unique ID**（21 位數字）誤當成 project_id 去授權 GSC：

```
nakama-franky@112208346716476475432.iam.gserviceaccount.com  ❌
```

正確的是 Claude 從 JSON 直接解出來的：

```
nakama-franky@nakama-monitoring.iam.gserviceaccount.com  ✅
```

**我犯錯不可怕，因為 Claude 有資料出處**——它不是用我的敘述猜，是直接讀 `/home/nakama/secrets/gcp-nakama-franky.json` 的 `client_email` 欄位。從 JSON 吐出來的數字不會錯。

---

## 成果清單（session 結束時）

| 類別 | 產出 |
|---|---|
| 記憶系統 | 5 個新 memory file（Brook 圖片管線 / Repurpose flow / Envato 實況 / VPS 規格 / Chopper 更新） |
| 決策文件 | ADR-005 / 006 / 007 三份 |
| 實作計畫 | Phase 1 實作順序（4 週節奏） |
| 開源準備 | 3 張 capability card |
| Runbook | 憑證建立 checklist（90 分鐘） |
| Vault 產出 | Brook 風格訓練挑選清單（192 篇分三類） |
| VPS 狀態 | 一份完整硬體 + WP 兩站 plugin / theme / post 結構盤點 |
| 憑證 setup 進度 | GCP Service Account（done）、GA4 × 2（done）、Cloudflare API token（done）、R2（done）、Bricks AI Studio（待購買 Agency 方案）、Slack Franky bot（待建） |

**沒寫任何一行 production code**。全部在 documentation + VPS 探測 + 憑證建立。

這是有意為之的——先把架構、決策、順序釘死，進入 Phase 1 實作時才不會返工。

---

## Lessons Learned（對想把 Claude 用在基礎設施規劃的人）

### 1. Prior-art research 是 anti-waste 武器

一個 3 分鐘的 research，直接讓我避開「自己刻 Envato client」的錯誤路徑。**AI 工具的生態在 2026 演進快到離譜**，任何沒先查就動手的功能，有 30% 以上機率在社群裡已經有更好的現成解。

### 2. ADR 格式 scale 到 AI 協作很好

我的 repo 有 ADR-001（4 月 9 日寫）到 ADR-004（幾週前）。Claude 接手時**讀過所有 ADR 再動手**，新寫的 ADR-005 到 007 風格自動對齊。ADR 不只是給人看的，也是給 AI 的 context。

### 3. 「Open questions」section 是 future-self 的禮物

每個 ADR 尾端的 Open questions 記下「這裡還沒決定」、「等某事確認才能解」。未來打開 ADR 的人（我、其他 collaborator、或未來的 Claude 對話）不用重新推導為什麼這邊空著。

### 4. Auto Mode + 明確授權範圍 = 高生產力

我在對話中段開了 Auto Mode（Claude Code 的持續執行模式），同時**明確授權**：可進 VPS、可進 WP DB（只讀）、可 scp 憑證。Claude 就一路跑下去，中間回報 + 卡住時才停。

關鍵是**授權要精確**——不是「你全權決定」，而是「這些東西可以讀、這些東西不能改」。Auto Mode 的安全性來自前置的授權邊界，不是事後 review。

### 5. 真實的踩坑比整理過的 tutorial 有用

這份案例最有價值的部分可能不是 ADR 架構，而是 FluentSecurity / Cloudflare zone / Envato API / GCP unique ID 這些「很小但會卡住」的坑。這些在 tutorial 寫不出來——因為 tutorial 作者踩完已經優化掉了。AI 協作的好處之一是**踩坑過程也會被記錄**，下一次遇到就不會再花時間。

### 6. 「幫我記起來」是個 first-class 指令

這個 session 裡我幾次說「幫我記起來」，Claude 就把對應的決策寫到 `memory/claude/*.md` 並更新 MEMORY.md 索引。這讓**記憶成為可操作的第一類資產**，不是塞在對話歷史裡的暫存。

---

## What's Next

**已授權、進行中**：
- 買 Bricks AI Studio Agency（Claude 推薦 £75 終身更新版）
- 建 Slack Franky bot
- 完成 `.env` 憑證全填

**卡在我這邊的**：
- 挑出 Brook 的風格訓練文章（每類 5-10 篇）
- Review ADR-005 / 006 / 007

**卡完就開工的 Phase 1**（4 週節奏）：
- Week 1：shared lib + Bridge approval queue + Usopp 骨架
- Week 2：Brook composer + Franky 健康監控
- Week 3：E2E smoke test + 實戰發第一篇
- Week 4：穩定化 + code review + VPS deploy

**Phase 2 預告**（等 Phase 1 穩）：
- Brook 圖片生成管線（Unsplash / Pexels / Flux + Claude Design + Bricks AI Studio 整套走通）
- 部落格 → IG carousel 的 repurpose flow
- FluentCRM newsletter 自動發

**Phase 3 預告**：
- ADR-008 FluentCommunity + Chopper 社群互動
- Chopper 三階段 HITL（全 approve → 信心閾值 → 全自動）

---

## 結語

這個 case study 想傳達的核心是：**Claude 不是寫 code 的工具，是可以跨系統規劃基礎設施的 collaborator**。前提是你願意：

1. **把決策格式化**（ADR / Plan / Capability Card）
2. **讓記憶可操作**（不只靠對話歷史）
3. **邊界精確授權**（可讀什麼、可寫什麼、可改什麼）
4. **擁抱踩坑**（用 grep plugin source 比用假設快）

我花了一個下午做完這些，如果我自己規劃至少要花兩週——還不一定能想到 FluentSecurity 的坑。

對我而言，這不是「AI 寫程式的未來」，是「AI 協作規劃的現在」。

---

> **若這份案例對你有啟發**，歡迎分享給社群。案例中的決策、ADR、runbook 都有可能日後獨立開源（見各 capability card）。如果你在做類似的 agent 團隊，記憶、授權、決策格式這三件事是我付出代價學到的。
>
> 張修修 × Claude Opus 4.7 (1M context) · 2026-04-22 於 Nakama 專案
