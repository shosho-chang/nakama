---
name: 任何功能都可能個別開源，開發時保留參數化與擴充點＋能力/IO/成本說明卡
description: nakama 所有 agent/module 都視為「未來可能獨立開源」來設計，避免硬編碼內部假設；並對使用者明確說明能力、能力範圍、輸入輸出、成本
type: feedback
created: 2026-04-18
updated: 2026-04-19
originSessionId: c2ace3b3-f24c-4428-9d8b-ddd315f7d92e
---
開發 nakama 任何功能（transcriber、Robin、Nami、Brook、Zoro、shared/ 等）時，**假設它之後可能被單獨抽出來開源給其他使用者**。設計決策要為這個未來保留彈性。

**Why:** 修修 2026-04-18 明確指示：「之後我這裡所有的功能，都有可能分別拆出來開源出去。請幫我在開發的時候記住這一點。」Transcriber 已是第一個規劃開源的對象（見 `project_transcriber.md` 未來方向），但原則適用於所有模組。

**How to apply（設計時的 checklist）：**

1. **可調整的數值要暴露為參數**，不要埋進常數
   - threshold（如 `_QC_CONFIDENCE_THRESHOLD`）、budget（thinking budget、max tokens）、timeout、批次大小 → 都該允許 override
   - 如 PR #24 `thinking_budget=512`（有預設，允許 None 或自訂）✅
   - 反例：若把 `_REFUSAL_PATTERNS` 寫死成 module constant，他人場景（英文 podcast、其他語言）要擴充就得改 source；考慮下次改進時讓呼叫端可傳入 extra patterns

2. **個人化資訊當參數預設值，不當硬編碼**
   - `host_name="張修修"` / `show_name="不正常人類研究所"` 是參數預設 ✅
   - 路徑、檔名 pattern、語言代碼 → 同樣處理

3. **Backend 盡量可替換**
   - LLM provider（Claude / Gemini / local）、ASR engine、audio 前處理工具 → 介面層設計要讓其他使用者能換自己的 API key 或本地方案
   - 如 arbiter 目前寫死 Gemini，未來應考慮 pluggable arbiter backend（見 `project_transcriber.md` 降本路線）

4. **CLI / 入口點要完整暴露核心參數**
   - 如 PR #26 `run_transcribe.py` 加上 `--project-file` / `--no-auphonic` / `--no-arbitration` ✅
   - 不要強迫使用者改 source code 才能調整行為

5. **依賴外部服務的功能要 optional-fail-graceful**
   - Auphonic 沒設帳號 → 跳過 normalization 並 warn ✅
   - Gemini 沒 key → 應允許只跑 Opus 校正、不做仲裁
   - LifeOS 整合（project_file、vault 路徑）→ 非必須，預設不啟用

6. **文件與 README 同步維護**
   - 每次加新參數都要想「開源使用者第一次用會不會看得懂」
   - docstring 寫使用範例、不只寫類型

7. **權衡：不要過度抽象**
   - 「未來可能開源」不是「現在就做成 framework」的理由
   - 最小可行介面 + 明確擴充點就好，不要為未知使用情境預建複雜 plugin 系統
   - 工程取捨原則仍適用（YAGNI），參考 CLAUDE.md「Don't design for hypothetical future requirements」

8. **能力/輸入輸出/成本 說明卡（capability card）**
   修修 2026-04-19 追加：開源前需要能向使用者「一頁說清楚」這個 app/skill/agent 是什麼。每個可獨立開源的單位（skill、agent、CLI entrypoint）都該有一張 capability card，涵蓋五件事：
   - **能做什麼（Capabilities）**：用一句話描述主要用途，不含 jargon
   - **不做什麼（Scope / Non-goals）**：明確列出邊界，避免使用者誤用（例：keyword-research 不做 SERP 爬取、不做競品分析）
   - **輸入（Inputs）**：args、env vars、前置檔案、API key 需求、網路 / 配額依賴
   - **輸出（Outputs）**：檔案路徑、格式、schema（frontmatter keys 等）、log / side effects
   - **成本（Cost）**：典型一次執行的**實測**值 — 時間、$（含 LLM input + output + thinking tokens）、外部 API 配額耗用量；附上量測日期與版本 commit
   
   放在哪裡：
   - Skill → 寫在 SKILL.md「When to Use」+「Cost」段，或 references/cost-estimation.md
   - CLI → `--help` 訊息 + docstring
   - Agent / service → README.md 或 module docstring
   
   關鍵要求：**成本必須是實測，不只是 a priori 估算**。事前公式可以放 cost-estimation.md 作為參考，但 SKILL.md 裡給使用者看的那一行要是「量測自 v<commit>，<date>，N 次平均」。

**如何驗證一個 PR 有做到：**
- 設計面：想像把該 module 連同測試抽到新 repo，其他開發者能不能不改 source code 就能跑起來？（對應 1–7 點）
- 文件面：capability card 五件事是否都在使用者會看到的地方、成本是否為實測？（對應第 8 點）

**具體先例：** 2026-04-19 keyword-research skill eval 發現 cost-estimation.md 只有 a priori 公式（`~$0.03–0.08/run`），沒有實測；CLI 不印 token usage。→ 已進 backlog，未來所有 skill 初次 eval 要同步產出實測成本行並寫回 SKILL.md。
