---
name: 5/7 ADR-021 v2 + ADR-022 ship session handoff
description: 全 5 implementation slice + ADR docs + 3 fix-ups 全 merged 進 main；Reader UI verify 進行中；下個 session 起手指引
type: project
---

## 5/7 session 已完成

### Merged 進 main（依序）
- **#463** ADR-021 v2 + ADR-022 docs（panel-revised）
- **#469** N452 ADR-022 multilingual embedding default → bge-m3
- **#470** N453 v3 annotation schema (W3C target+body) + Reader compat + migration
- **#471** N454 Brook server-side synthesize store + `/api/projects/{slug}/synthesize`
- **#478** N455 annotation indexer + recursive rglob + `_KB_SUBDIRS` sync
- **#482** N456 kb_search hybrid default + KBHit metadata
- **#484** N452 follow-ups (VAULT_PATH config / `check_dim` bypass / FlagEmbedding dep)
- **#485** #457 bench harness（shosho 待本機跑 + freeze default）— 等 CI merge

### Issue tree 推進
全 11 issue 中：
- ✅ ship: #452 #453 #454 #455 #456（5 條核心）
- 🟡 bench harness ship 但需 local run + HITL freeze: #457
- ⏸ HITL pending: #453 Reader UI verify（A1-B5 9 項）/ #458 design system review UI（HITL 美學）
- ⏸ AFK ready: #459 Brook synthesize（等 #457 freeze）/ #460 reject 降權 / #462 writing mode
- ⏸ Final: #461 E2E walkthrough

### 本機 KB 狀態
- BGE-M3 1024d full re-embed **已跑完**（修修跑 `python -m shared.kb_indexer --rebuild --vault "E:/Shosho LifeOS"`）
- 待 #485 merge 後可省 `--vault` flag（會走 `shared.config.VAULT_PATH`）

## Reader UI verify 進度（#453 HITL）
- **Step 1 ✅**: 頁面載入成功（creatine paper）
- **Step 2 ✅**: 新建 v3 highlight 立刻 render 正常（v3 寫入路徑驗收）
- **Pre-existing drift**: 既有 v1 highlight 位置跑掉 — diagnostic 確認**非 N453 regression**（reader.html 沒被碰）；root cause = v1 沒存位置資料 + `findInStr` 文字錨定取第一個 regex match + bilingual 翻譯插入位移段落。獨立 follow-up issue 待開（建議方案：W3C TextQuoteSelector 加 prefix/suffix window，或新建時抓段落 index 存 v3 `cfi`）
- **Step 3+ pending**: Playwright MCP 卡在 `/login`（cookie httpOnly + secure 無法 JS set） — 修修需在 Playwright 視窗手動輸入 password 一次後 Claude 接手代跑 annotation create / book reader B1-B5

### 沒驗到的（下個 session 補）
- A3 新建 v3 annotation（短 note）— 需 Playwright login
- A4 reload persistence
- B1-B5 全部書籍 Reader（既有 highlight render / 新建 highlight/annotation/reflection v3 / v2 `comment` backward compat for reflection icon）
  - **B5 是最關鍵的一條**：v2 `type=comment` 必須仍被 `isReflection()` 認成 reflection（改名 backward-compat）
  - 注意：`KB/Annotations/` 目前**沒有書籍檔**，所以 B 區可能要先在書 reader 內新建幾筆才能驗

## Sandcastle 機制踩到的 bug（記憶已存）

`isolation: "worktree"` 在**並行 dispatch 多個** sandcastle 時，第二（含後）個 agent 常 fall back 到主 E:/nakama tree 而非自己的 isolated worktree（task notification 沒回 `worktreePath` 是訊號）。此 session 三次踩（N456 / N452-fixups / N457）。Workaround：sequential dispatch 而非 parallel batch。詳見 `feedback_sandcastle_default.md`。

## 下個 session 起手 sequence

1. **接 Reader UI verify**：
   - 假設 `python -m thousand_sunny.app` server 已起（修修啟動）
   - 修修在 Playwright 視窗 login 一次（Claude 帶他到 `/login`）
   - Claude 接手代跑 A3 / A4 / B 區
   - 全 pass → 結 #453 HITL gate

2. **Bench freeze**：
   - 修修 local 跑 `python -m scripts.bench_kb_search`
   - 看 `docs/research/2026-05-07-brook-synthesize-bench.md` 結果
   - HITL 選 default `top_k` + engine
   - Commit 凍結結果到 ADR-021（`#3` section 加實際數字）

3. **Bench freeze 後 dispatch #459**：
   - Brook synthesize multi-query 廣搜 + outline draft
   - 一條 sequential sandcastle（不要 parallel）
   - 完成後 #460 reject 降權 接著 dispatch

4. **設計探索 #458**（並行修修自己跑）：
   - 走 Claude Design 視覺探索 review mode UI
   - 設計凍結後 handoff 給 Claude Code 落地

## 本 session 新增 feedback memories（已索引）

- `feedback_sandcastle_default.md` — 加並行 isolation 失效坑警告
- `feedback_wakeup_completion_not_session_end.md` — wakeup 重複觸發≠session 結束
- `feedback_local_shell_ops_just_do_it.md` — 本機 shell ops 直接跑
- `feedback_use_mcp_browser_for_ui_verify.md` — UI verify 用 Playwright MCP 代跑機械步驟

## Open follow-ups（小 PR 級）

- Reader UI 文字錨定升級（W3C TextQuoteSelector with prefix/suffix） — 獨立 issue
- 主 E:/nakama 殘留 stash@{0}/{1}（N455 / N454 stray）— 可 `git stash drop` 兩次清掉

## GH Actions quota

90% used（5/7 為止，月底重設前剩 ~300 min）。下個 session 推 PR 會更慢；docs-only 改動可 `[skip ci]` commit。
