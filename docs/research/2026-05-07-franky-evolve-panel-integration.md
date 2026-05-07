# FrankyEvolveV1 — Panel Integration Matrix（2-way）

**Date**: 2026-05-07
**Inputs**:
- Spec v1: `docs/research/2026-05-07-franky-evolve-v1-spec.md` (commit 51fb525)
- Codex audit: `docs/research/2026-05-07-codex-franky-evolve-audit.md` (commit 093efb8) — gpt-5.5 / API mode / reasoning_effort=medium
- Gemini audit: SKIPPED — `GEMINI_API_KEY` 未設，graceful degraded to 2-way per skill spec
- Claude self-cross-check: ADR-001 / ADR-006 / ADR-007 read directly to补 Codex 因 path 錯誤未能驗證的部分

**Verdict aggregate**: **Approve with substantial modifications**. Codex 找到 5 條 substantive 修正 + 7 個 Q 個別判決，**多數命中 Claude 的 confirmation bias 高發區**。Claude 自審 ADR-001 後**確認 Codex Q6（split agent）不是建議是必要** — Franky 的 scope 已經因為過寬被 ADR-007 multi-model review 砍過一次（slim 版），FrankyEvolveV1 等於再做一次同樣擴張錯誤。

---

## 1. Adopt / Modify / Reject Matrix

| # | Topic | Claude v1 stance | Codex audit | Claude cross-check | Resolution | Confidence |
|---|---|---|---|---|---|---|
| 1 | **Proposal 容器：自動開 issue** | spec §2 Fork 3：硬上限 3/週、自動 `gh issue create` | **Reject as primary mode**. 改 two-stage inbox：weekly digest 先寫 vault page，**owner 顯式 promote** OR **deterministic 重複訊號規則**（同 trend ≥2 supporting items OR 直接 map 到 named ADR/issue）才開 issue | ADR-006 spirit（HITL approval as gate before action）+ ADR-007 multi-model review 教訓（過寬 scope 必砍）支持 Codex | **MODIFY**：採 two-stage inbox。Stage 1 = vault page 含 machine-readable frontmatter；Stage 2 = `gh issue create` 只在 owner promote OR ≥2 supporting items 時觸發 | High |
| 2 | **S2 切片粒度** | spec §6：「RAG corpus + Relevance」one slice 2-4h | Reject. Split into S2a（retrieval substrate：corpus / chunking / embedding / refresh / cache / bilingual eval fixture）+ S2b（scoring change：news_score.md 加 Relevance / 5-dim 權重 calibration / pick gate / tests） | 同意。Retrieval 失敗模式（recall@k 不夠）跟 scoring policy 失敗模式（權重錯）耦合在一個 slice 是 bug 工廠 | **ADOPT**：S2 → S2a + S2b，slice 數從 4 → 5 | High |
| 3 | **ADR-001 Franky scope 衝突** | spec §5 Q6：open question，Claude 預設「保留在 Franky + 開新 cron」 | **Reject Claude 預設**. Either split off `Sengoku` agent OR **正式 amend ADR-001 row**. Cleaner: 新 agent 消費 Franky digest + 內部 context，Franky 保留 source fetching / health / CVE / 操作報告 | **強確認 Codex**。ADR-001 line 29 明寫 Franky = System Maintenance；line 38 為 SEO/Repurpose 預留「**或另立 Agent**」槽。ADR-007 是 Franky scope 過寬被砍成 slim 版的結果 — 教訓本身就是「不要往 Franky 塞」 | **ADOPT**：split 出新 agent。**用 Sengoku 名稱**（船七武海前盟主，象徵「策略觀察者」），ADR-023 第一段必須先 update ADR-001 agent table 加新 row | High（panel + Claude self 共識 + 既有 ADR-007 教訓三方共證） |
| 4 | **Q3 architectural lock-in 自動標 panel_recommended** | spec §3：LLM judge with prompt 三條判準，conservative 傾向 | Reject pure-LLM. Use **deterministic positive list**（碰 ADR / 改 agent contract / 新 persistent dep / 改 storage schema / 改 HITL boundary / 改 Slack/GitHub automation 權限）+ LLM 補充 reasoning。LLM 不能是唯一 classifier | 同意。LLM judging 自己 context 的 architectural 程度是 confirmation bias 高發 | **ADOPT**：deterministic positive list + LLM rationale 補充 | High |
| 5 | **Q1 BGE-M3 robustness** | spec §3：複用 ADR-020 §S6 infra | Reject framing. 真正的決策不是 "BGE-M3 vs LLM-as-retriever"，是 "**先做 cross-domain labeled eval 才能 freeze**"。English news titles ↔ 繁中 ADR/MEMORY snippets，recall@k 達標才 ship | **嚴重信號**：ADR-020 在 main 分支**根本沒 merge**（在 `docs/kb-stub-crisis-memory` 分支 + 5/7 還在 Stage 1.5），spec 的 RAG infra dependency 是 in-flight 不是 production。Codex 因為路徑錯沒能 verify，但事實是更壞 | **ADOPT + ESCALATE**：(a) ADR-023 不能 reference ADR-020 直到 ADR-020 merged；(b) S2a 必含 cross-domain eval fixture；(c) 短期 fallback 走 **alternative (c)**（fixed `franky_context_snapshot.md`，每週 regenerate）作為 pre-RAG baseline | High |
| 6 | **Q5 success_metric 過樂觀** | spec §2 Fork 5：所有 proposal 強制 `success_metric` 欄位 | Reject 通用 metric 假設。**分類 `metric_type: quantitative \| checklist \| human_judged`** + `baseline_source` + `verification_owner`。state.db `api_calls` 只支援 cost metric，質性 metric 沒儲存場 | 同意。「KB stub 率 < 50%」這類聲音上量化但實際需特殊 query 的 metric 才是大宗 | **ADOPT**：success_metric 拆 metric_type 三類；retrospective 對 human_judged metric 報 owner verdict 不是 fake number | High |
| 7 | **Q2 weekly synthesis 0 條合法性** | spec §3：靠 prompt 「0 條也合法」+ retrospective 監控 | Reject。LLMs satisfy shape pressure。Pipeline diagram 改 0-3（不是 1-3）+ **hard quality gate before issue creation**：≥2 independent items support same trend OR 1 item 直接 map 到 named open issue/ADR assumption。Weak singletons → vault page only | 同意 | **ADOPT**：硬 quality gate。整合進 #1 two-stage inbox 的 stage 2 trigger 條件 | High |
| 8 | **Q4 GitHub trending 訊號品質** | spec §4：trending Python filter `agent\|llm\|mcp\|claude` + score gate | Reject 同信任 tier。加 repo-age / release / README / license / commit-history / issue-activity / maintainer check。Better：**先只啟用 anthropic/* + awesome-list diff**，trending 作 **experimental low-trust source**（標明分數 cap） | 同意 | **ADOPT**：分 trust tier。S1 source 擴張改 step：(i) anthropic/* releases + awesome-list diffs 先 ship；(ii) trending 作 experimental tier，分數 ceiling 4 不 5；(iii) repo sanity check（age ≥ 30d / stars ≥ 100 / has README + license） | Medium-High |
| 9 | **Q7 Relevance 權重 1.3 calibration** | spec §3：1.3 是猜的，retrospective 自我修正 | Reject。Run 1-week dry-run before freezing。對同一候選池跑舊 4-dim + 新 5-dim，跟 owner judgment 比對。**不能直接 ship hard `relevance >= 3` gate** — 可能 suppress 真正重要的 model/tool release（沒明顯 Nakama mapping） | 同意。盲區：可能漏掉 paradigm shift 級新工具（一開始看不出 mapping） | **ADOPT**：先跑 1-week shadow mode（5-dim score 跟 4-dim 並存記入 db、不影響 pick），對齊後 freeze 權重；初期 gate 條件用 `relevance ≥ 2`（寬），retrospective 收緊 | High |
| 10 | **§7 cost & token 估算** | spec §7：~$3/月、weekly 50k tokens | Reject 估法。50k tokens 估混淆了 picked-news token 跟 internal-context token 兩個來源。**不 freeze budget 直到 dry-run 記錄實際用量**。Log weekly synthesis call 進 `api_calls`（model / input / output / cache / op id） | 同意。GPT-5.5 API mode 後 token 計帳精確，dry-run 1 週就有 ground truth | **ADOPT**：ADR-023 不 freeze 成本數字。S2a 加「dry-run telemetry」driver — 至少 1 週 log 完才有 SLO claim | High |
| 11 | **proposal_metrics 儲存** | spec §2 Fork 5：retrospective 靠 issue close + state.db | Reject。state.db 現有表（agent_runs / alert_state / r2_backup_checks / api_calls）不支援 proposal lifecycle。**Add proposal_metrics table**（proposal_id / issue_number / success_metric / baseline / post_ship_value / measurement_method / status / shipped_at / verified_at / source_item_ids / related_adr）before 跑 retrospective | 同意。否則 retrospective 變敘事文不是回饋 | **ADOPT**：新增 S5 = `proposal_metrics` table + frontmatter extractor。slice 數 5 → 6 | High |
| 12 | **§4 weekly_digest.py 命名衝突** | spec 用 "weekly synthesis" 不直接 overload `weekly_digest.py` | Codex 提醒：weekly_digest.py 是 Phase 1 純模板字串（ADR-007 §10），新 LLM-generated synthesis **不能** overload 同檔 | 同意。Module 命名要分開：建議 `news_synthesis.py`（依 `news_digest.py` 沿用 news_* 前綴）| **ADOPT**：新 module = `agents/franky/news_synthesis.py` + `agents/franky/news_retrospective.py`，與 `weekly_digest.py`（engineering 週報）clearly distinct | High |

---

## 2. Codex 的 Alternative 評估（spec §5 vs Codex §5）

| Codex's alt | Codex verdict | Adopt? |
|---|---|---|
| **(a) Passive newsletter（不 auto-create issue）** | "first production mode" — 保留 ADR-001 + HITL discipline | **Yes**（已併入修正 #1 two-stage inbox 的 stage 1） |
| **(b) Dispatch synthesis to Codex/Sandcastle subagent**（非 Franky 自跑 LLM） | 適用於 architectural proposals 不是 ordinary weekly。Cost 是 orchestration latency；對 ADR/schema/dependency 層級 OK | **Partial Yes**：weekly synthesis 維持 Franky 內 LLM call（簡單 clustering），**但** `panel_recommended: yes` 級的 proposal **dispatch Codex GPT-5.5 subagent 二次審核**才開 issue。整合進修正 #4 |
| **(c) Skip RAG, fixed MEMORY snapshot** | 適用於 S1/S2 dry runs，不是 final design | **Yes for short-term**（已併入修正 #5 — pre-RAG fallback） |
| **(d) Two-stage proposal inbox** | "best modification to proposed issue flow" | **Yes**（核心改動，併入修正 #1） |

---

## 3. v1 → v2 必修清單（給修修拍板）

採 Codex 修正全部 **High confidence** 條目後，FrankyEvolveV1 spec 要做的修改（依重要度排序）：

### 🔴 Must-fix（架構級，影響 ADR-023 草擬路徑）

1. **拆 agent — 新 agent `Sengoku`**（or amend ADR-001 row）— spec §1 + §5 Q6 重寫；ADR-023 第一段先 update ADR-001 table
2. **Two-stage proposal inbox** — spec §2 Fork 3 重寫；§4 pipeline 改 0-3 + hard quality gate；overflow vault page 變 stage 1 不是 fallback
3. **Slice 重切**：4 slice → 6 slice（S1 source / S2a retrieval / S2b scoring / S3 weekly synthesis / S4 retrospective / S5 proposal_metrics table）
4. **Module 命名分離**：新 module = `agents/franky/news_synthesis.py` + `news_retrospective.py`，**不** overload `weekly_digest.py`

### 🟡 Should-fix（實作級，影響 S2 設計）

5. **ADR-020 dependency 鎖**：ADR-023 不 reference ADR-020 直到 ADR-020 merged；S2a 含 cross-domain eval fixture；短期走 fixed `franky_context_snapshot.md` baseline
6. **Q3 deterministic positive list** for `panel_recommended` auto-tag
7. **success_metric 拆 `metric_type` 三類**（quantitative / checklist / human_judged）
8. **Source trust tier** 分級（anthropic/* + awesome-list = full trust；trending = experimental low-trust + score ceiling 4）
9. **Relevance 權重 1-week shadow mode**（不直接 freeze 1.3 + 不直接 ship hard gate）
10. **`proposal_metrics` table** schema + frontmatter extractor

### 🟢 Process-fix

11. **不 freeze cost claim** 直到 dry-run telemetry 跑 1 週
12. **Weekly synthesis high-risk path 走 Codex subagent 二次審**（panel_recommended proposals 在開 issue 前 dispatch GPT-5.5 audit）

---

## 4. 還沒解的開放問題（修修需要拍板才能寫 ADR-023）

1. **新 agent 命名**：`Sengoku` OK 嗎？還是想用其他海賊王角色？（Sengoku = 元帥 + 戰略指揮，semantic match 好；but 修修可能有 lore 偏好）
2. **Sengoku scope 切多深**：只做 evolution synthesis？還是把 Franky 的 news fetching/curate 也整段移過來？我推薦 **shallow split** — Franky 保留 source fetching + daily curate + score（既有路徑），Sengoku 接 weekly synthesis + monthly retro + proposal lifecycle。理由：fetching 是 infra-flavored 跟 Franky 的 health check 同 modality
3. **Two-stage promotion 觸發**：owner-promote 走什麼 UI？建議 vault page 內加 `<!-- promote: yes -->` HTML 註解，或 frontmatter `promote: true` 由修修手動切；synthesis 跑下次掃到該 marker 才 `gh issue create`
4. **要不要補跑 Gemini audit**：原本 `GEMINI_API_KEY` 沒設。Codex audit 已強，Gemini 邊際價值是「不同 reasoning chain catch Claude+Codex 都漏的維度」。值不值得補？（先 ship spec v2 再說 / 同期跑 / 跳過）

---

## 5. 下一步 routing

依拍板結果走：

- **若 1-3 全採 Must-fix**：我把 spec doc 改成 v2（ADR-001 update 在前 + agent 拆 + slice 切 6）→ 建議跑 1 輪 multi-agent panel on v2 driver（驗證 Sengoku split 後仍然合理）→ 寫 ADR-023 → `/to-issues` 拆 6 slice
- **若部分採用**：我列出採用 / reject 的明細寫進 v2，跳過第二輪 panel 直接 ADR-023
- **若補 Gemini**：先設 `GEMINI_API_KEY`（記憶規則：不在對話框輸入）+ 安裝 google-genai → 跑 panel script → 整合 3-way

---

## 6. 信心評估

Codex 在路徑錯誤的條件下還抓出 5 條 architecture-level + 7 條 Q-level substantive critique，且多條跟 ADR-007 既有 multi-model review 教訓共證 — **panel 投資的 marginal value 已超過 0** 這次。

Claude v1 的最大盲區（confirmation-bias 致命傷）：**沒看出 ADR-001 + ADR-007 的 Franky scope 過寬教訓**。一個 agent 在歷史上已被 multi-model review 砍過一次，本次設計又往同方向擴張。這是 single-Claude analysis 不會自抓的 pattern；panel 抓到了。
