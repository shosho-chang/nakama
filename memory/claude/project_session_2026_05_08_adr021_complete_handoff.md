---
name: 5/8 ADR-021 complete + design system v1 ship handoff
description: ADR-021 整套 (9 issue) 全 ship + design-system.md v0→v1 + Reader UI v3 PASS + bench freeze；下個 session 起手指引
type: project
---

## 5/7-5/8 兩天 session 累計完成

### ADR-021 全 AFK slice merged
| Issue | PR | 內容 |
|---|---|---|
| #452 ADR-022 multilingual embedding | #469 | bge-m3 1024d default + dim assertion |
| #453 v3 annotation schema | #470 | W3C target+body 單記錄 + Reader compat + migration |
| #454 Brook synthesize store | #471 | server-side `data/brook_synthesize/{slug}.json` + `/api/projects/{slug}/synthesize` |
| #455 annotation indexer | #478 | recursive rglob + `_KB_SUBDIRS` sync |
| #456 kb_search hybrid default | #482 | KBHit chunk metadata exposed |
| #457 mini-bench harness | #485 | 5 topic × 2 engine × 3 K bench script |
| #457 freeze | #487 | `top_k=15` + `engine=hybrid` 凍結進 ADR-021 §3 |
| #459 Brook synthesize | #488 | multi-query 廣搜 + outline draft + store write |
| #460 reject 降權 ranker | #490 | multiplicative discount (factor 0.5) + serendipitous rediscovery 保留 |
| #458 review UI | #492 | Claude Design 落地 + dialog dark theme fix |

副產出 PR：
- #463 ADR-021 v2 + ADR-022 docs
- #484 ADR-022 follow-ups (VAULT_PATH / `check_dim` / FlagEmbedding dep)
- #486 5/7 早 handoff doc

### 順帶完成的事
- **`docs/design-system.md` v0 → v1**：從 review UI tokens 填好（typography / 顏色 / spacing / motion 全表 + 8 states）
- **BGE-M3 1024d full re-embed** 跑完（修修本機 KB index 已重建）
- **Reader UI #453 HITL B 區全 PASS**（v3 highlight/annotation/reflection round-trip + v2 `comment` backward compat）
- **Sandcastle isolation pattern 改用 sequential dispatch**（並行多 agent 失效坑記在 `feedback_sandcastle_default.md`）

### 本機 server 狀態
- 主 uvicorn (PID 64144) 跑 :8000，main HEAD（含 #492 review UI）
- 額外 uvicorn (PID 13224) 跑 :8001，#458 worktree（驗收後可關）— 可 `Stop-Process -Id 13224 -Force` 清掉
- 假 store `data/brook_synthesize/sleep-architecture-choline.json` 在 worktree 內（不在主 tree，merge 後不會污染）

## 剩下兩條 issue

### #462 writing mode（AFK ready）
- Blocked by #458 → 已解
- 可直接 dispatch sandcastle
- AC: 寫稿 mode 的右螢幕 outline + 左螢幕 evidence viewer（修修長期使用的 mode）

### #461 E2E walkthrough（HITL final）
- Blocked by 全部其他
- 整鏈整跑驗收：Project bootstrap → keyword → Brook synthesize → review/reject → finalize → 寫稿 → publish
- 等 #462 ship 後才有意義

## Open follow-ups（小 PR）
- **Reader UI 文字錨定升級**（W3C TextQuoteSelector with prefix/suffix）— 5/7 診斷的 v1 paper 既有 highlight 漂移；獨立 issue 待開
- **stash cleanup**：主 E:/nakama 殘留 stash@{0}/{1}（5/7 N455/N454 strays）— 可 `git stash drop` 兩次
- **worktree cleanup**：`.claude/worktrees/agent-*` 累積很多老 worktree，可 `git worktree prune` + `git worktree remove --force` 對 PR 已 merge 的
- **`agents/brook/synthesize/__init__.py` finalize CTA 後端 hook**：UI stub 已在，server-side `outline_final` POST 路徑沒 wire（review UI agent 報告為 out-of-scope，等 #461 E2E 時補）

## 下個 session 起手 sequence

1. **檢查並 cleanup stale worktree + branch**：
   - `git worktree list`
   - `git worktree prune`
   - 對 5/7-5/8 已 merge 的 PR worktree（impl/N452/N453/N454/N455/N456/N457/N459/N460/N458）做 `git worktree remove --force <path>`

2. **decide #462 dispatch 或暫停**：
   - 如果繼續 ADR-021 收尾 → dispatch #462 sandcastle (single, sequential)
   - 如果想觀察系統實際使用一段時間 → 先 stop，累積 5+ 個 Project 的 synthesize run 後再回來調 bench

3. **#461 E2E walkthrough**（最後做）：
   - 修修 manual end-to-end 整跑：開 Project → Zoro keyword → Brook synthesize（CLI/UI trigger）→ /projects/{slug} review → reject → finalize → /projects/{slug}/write 寫稿
   - 驗：每一步 store / vault / UI 一致

## 本 session 新增/更新 memory
（同 5/7 早 handoff 的 4 條 feedback memories 仍有效）
- `feedback_sandcastle_default.md` — 並行 isolation 失效，sequential dispatch
- `feedback_wakeup_completion_not_session_end.md`
- `feedback_local_shell_ops_just_do_it.md`
- `feedback_use_mcp_browser_for_ui_verify.md`

新增：
- 此 handoff doc

## GH Actions quota
5/8 為止配額仍緊（5/7 為 90% used；月底前剩 ~200-250 min）。下個 session 推 PR 仍會慢；docs-only / memory commits 可 `[skip ci]`。

## 關鍵 PR 快速 reference
- ADR-021 整體：`docs/decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md`（含 §3 freeze block）
- ADR-022：`docs/decisions/ADR-022-multilingual-embedding-default.md`
- bench: `docs/research/2026-05-07-brook-synthesize-bench.md` + `tests/fixtures/brook_bench_topics.yaml`
- design system: `docs/design-system.md` v1
- review UI: `thousand_sunny/templates/projects/review.html` + `static/projects/{tokens.css,review.css,review.js}`
- store: `shared/brook_synthesize_store.py` + `shared/schemas/brook_synthesize.py`
- synthesize: `agents/brook/synthesize/` (sub-package)
