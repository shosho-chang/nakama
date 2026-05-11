---
name: 5/7 FrankyEvolveV1 ADR + 6 issue ship — fresh session 起手執行 3 步（全 AFK）
description: ADR-023（原草為 ADR-022，main 撞號 rename 至 023）凍結 + 6 vertical slice issue 開好（#472-#477），下一個 session compact 後執行 push PR / triage / dispatch 並行三 slice
type: project
created: 2026-05-07
---

## TL;DR

5/7 完成 grill-with-docs 5 fork → Codex panel audit（API mode + GPT-5.5 + medium）→ integration matrix → ADR-023 + ADR-001 amend → 6 vertical slice issue 全建。修修要 fresh session **執行 3 步全 AFK**。

## 現在 state

- **Worktree**：`E:/nakama/.claude/worktrees/franky-evolve-spec`，branch `feat/franky-evolve-v1-spec`，**未 push**
- **主 repo working tree** 仍有別視窗在做的 modified files（不要碰）：`.claude/settings.json`、`.claude/skills/grill-with-docs/SKILL.md`、`agents/robin/CONTEXT.md`、`memory/claude/MEMORY.md` + 5 個 untracked
- **ADR-023 + ADR-001 amend** commit `c818eec` 在 worktree branch（main 還沒有）
- **新加 5/7 OpenAI Startup credit $10k**：codex CLI 已切 `auth_mode: apikey`，model `gpt-5.5` + `reasoning_effort=medium`（auth.json.bak 備份在 `~/.codex/`）

## 4 個 commit 在 worktree branch（時序）

```
51fb525 spec v1（panel input）
093efb8 Codex audit（API mode rerun）
5a2739d panel integration matrix（2-way）
c818eec ADR-023 + ADR-001 amend
```

## 6 個 GitHub issue（已建，全 needs-triage / enhancement）

```
#472  S1   Source 擴張 + trust tier            (no deps)               AFK
#473  S2a  Context snapshot                    (no deps,  blocks #475) AFK
#474  S5   proposal_metrics table              (no deps,  blocks #477) AFK
#475  S2b  Scoring 5-dim + shadow mode         (deps #473, blocks #476) AFK
#476  S3   Weekly synthesis + two-stage inbox  (deps #475, blocks #477) AFK
#477  S4   Monthly retrospective                (deps #476 + #474)      AFK
```

並行起手：S1 / S2a / S5（無 dep）

## 修修指示的 3 步（compact 後 fresh session 執行）

### 步驟 1：Push worktree branch + 開 PR

```bash
git -C E:/nakama/.claude/worktrees/franky-evolve-spec push -u origin feat/franky-evolve-v1-spec
gh pr create --base main --head feat/franky-evolve-v1-spec --title "ADR-023 Franky Evolution Loop V1 + ADR-001 amend" --body ...
```

PR body 含：(a) panel triangulation 摘要（Claude + Codex 2-way，Gemini skip）；(b) ADR-023 5 條 must-fix 採納清單；(c) link 6 issue #472-#477。**修修不做 review** — ship 完 dispatch review skill / agent 群跑（見 `feedback_no_self_review.md`）。

### 步驟 2：Triage 6 issue → ready-for-agent

每個 issue（#472-#477）`gh issue edit ... --add-label ready-for-agent --remove-label needs-triage`。修修判斷全 AFK。

### 步驟 3：並行 dispatch 3 個 sandcastle / agent worktree

**並行起手**：S1 (#472) + S2a (#473) + S5 (#474) 三個無 dep，**同時** dispatch 各自獨立 worktree（**不要動主 worktree** — 規則：要改東西必走 worktree）。

派工建議：
- 三個都走 sandcastle template B（修修目前用的 OAuth flow）或本機 agent worktree
- 每個 dispatch 含：本 handoff 路徑 + ADR-023 路徑 + 對應 issue body 完整 acceptance criteria
- 完工 PR 自動跑 review skill（修修不親自 review）

**S2b → S3 → S4 序列**等 S2a + S5 ship 後再 dispatch（可下個 cycle 接力）。

## 主要設計決定（懶人摘要 — 細節在 ADR-023）

1. **不拆新 agent**（修修 push-back 翻盤）— Franky 漫畫設定本來就含「研究+升級船」一體；workload 比較後 Franky 加 +1 weekly + 1 monthly LLM call 還是船員裡偏輕
2. **ADR-001 row formal amend**（透明性，不偷渡）
3. **Two-stage proposal inbox**（vault page Stage 1 + owner promote OR ≥2-item rule 才開 issue）— 守 ADR-006 HITL spirit + 防 LLM shape pressure 週週擠 mediocre
4. **ADR-020 dependency 鎖** — 用 fixed snapshot Phase 1（立即 ship）+ Phase 2 等 ADR-020 merge 再切 RAG（另起 ADR-023b 或新編號 ADR）
5. **Module 命名分離** — `news_synthesis.py` + `news_retrospective.py`，不 overload `weekly_digest.py`（後者是 ADR-007 §10 純模板）
6. **deterministic positive list** for `panel_recommended` auto-tag — LLM 不能單獨 classifier
7. **Cost claim 撤回** — 不 freeze `~$3/月`，等 dry-run telemetry 1 週

## Codex API mode 經驗教訓（避免重蹈）

5/7 14:07 第一次 dispatch Codex audit 用 `reasoning_effort=xhigh` + 整 repo 自由探索，43 分鐘 silent hang（累積 1.6M token cached input 但 reasoning step 卡死）。
切 API mode + `medium` + 限縮讀檔清單後，2.8 min 跑完。**規則**：

- panel audit / 大 repo 場景**禁用 xhigh**（除非單檔小 spec 才考慮）
- dispatch prompt 必須**列死讀檔清單**，禁止 Codex 自由 grep
- 每次 dispatch 預估 token cost 用 input × 5x reasoning factor（medium）

## 4 個 open question（已留 ADR-023 但 Phase 2 才解）

- ADR-020 merged 後 cross-domain eval（English news ↔ 繁中 ADR）labeled fixture 怎麼建
- BGE-M3 vs LLM-as-retriever 的 gate metric（recall@k 多少才 ship）
- 補跑 Gemini audit（要先 set GEMINI_API_KEY，現未設）
- to-issues skill 自動建議「triage labels」對 6 個 issue 的應用 — 修修人工拍

## References

- ADR-023：`docs/decisions/ADR-023-franky-evolution-loop.md`（worktree branch only）
- ADR-001 amended row：`docs/decisions/ADR-001-agent-role-assignments.md`（worktree branch only）
- Spec v1：`docs/research/2026-05-07-franky-evolve-v1-spec.md`
- Codex audit：`docs/research/2026-05-07-codex-franky-evolve-audit.md`
- Integration matrix：`docs/research/2026-05-07-franky-evolve-panel-integration.md`
- 過往相關 memory：`feedback_no_self_review.md`、`feedback_dispatch_everything_minimize_main_context.md`、`project_session_2026_05_06_07_s8_burn_handoff.md`、`feedback_adr_principle_conflict_check.md`
