---
name: Sandcastle delegation 審查 — 可機械化工作不要自己幹
description: 跑 multi-slice workflow 第一件事是掃「可並行 sandcastle」+「可機械化丟 sandcastle」清單，不要 serialize + 不要自己手寫所有 HITL test。2026-05-05 EPUB Reader workflow 自我檢討清單，下次套用可省 ~50% wallclock + 100k+ token。
type: feedback
---

跑類似 EPUB Reader 那種 5+ slice multi-feature workflow 時，**第一步是 audit 所有可委派工作**。下面清單按「漏丟代價」排序。

## 🔴 必須避免的反模式：serialize 獨立 slice

**症狀**：plan memory 明寫「Slice 2/3/4 並行」但實際 serialize 跑（Slice 2 完才開 3、3 完才開 4）。

**Why**：sandcastle CLI 一次一 issue，但**多個獨立 git branch 可以同時跑多個 sandcastle session**（不同 cmd window / background）。各 branch 之間 95% 檔案不重疊，merge 衝突可控（甚至零衝突）。

**How to apply**：

```powershell
# Terminal A: cd E:/sandcastle-test ... → 吃 tdd-prep/slice-2
# Terminal B: cd E:/sandcastle-test-b ... → 吃 tdd-prep/slice-3
# Terminal C: cd E:/sandcastle-test-c ... → 吃 tdd-prep/slice-4
```

或單 terminal background：把 `npx tsx ... main.mts` 三個一起 `run_in_background: true` fire。

⚠️ Pre-req：要有獨立 sandcastle-test 資料夾或不同 host repo state。如果 sandcastle 都吃同一個 nakama HEAD，commits 會疊在一起——這就要小心 merge 順序。

**省下**：6 個 sandcastle round serialize ~60-90 min → 並行 ~30-40 min。

## 🔴 P0 / blocker fix 看到就 fire，不要等到「該 slice 開始」

**症狀**：知道 #367 P0 是 Slice 5 的 prereq，但我排在 Slice 4 之後才 dispatch。實際上 #367 跟 Slice 1-4 完全獨立，**Slice 1 開跑時就可以平行 fire**。

**How to apply**：

每進新 multi-slice workflow，先列「獨立 fix issue 清單」，**第一個 sandcastle round 就把這些一起 dispatch**——不要等用到才開做。

## 🟡 HITL failing test 可丟 sandcastle（有風險，要評估）

**症狀**：我手寫 5 套 HITL failing test (~1500 LOC total)。模式重複：
1. 從 issue body 抽 acceptance criteria
2. 翻成 `pytest.importorskip` + behavioral assertions
3. 用既有 fixture (`_epub_fixtures` 等)

**Why可以丟**：規格化、模式化、~150-300 LOC per file。Sandcastle 完全能吃。

**Risk**：sandcastle 寫的 test 是 "imagined behavior"（tdd skill 警告的 anti-pattern）。但**我自己手寫也是 imagined**（pre-impl phase），差別是人工/自動。對 AFK workflow 來說 wash。

**How to apply**：

開一個 `hitl-tests` sandcastle prompt 模板：

```
Read issue #N body. Extract acceptance criteria. Write tests/.../test_X.py
using pytest.importorskip on the production module that doesn't exist yet.
Each acceptance bullet → one test function. Use existing fixtures from
tests/shared/_epub_fixtures.py (or equivalent helpers).
Return: failing pytest run (importorskip → skip).
```

**省下**：5 × 15min = 75 min 手工 + 我寫 test 時的 context 消耗 ~50k token。

## 🟡 Sub-issue 拆解可丟 sandcastle utility

**症狀**：我手寫 13 份 `.tmp-step-*.md` sub-issue body。每份結構一樣（Parent / What / Interface / Acceptance / LOC budget / Run mode）。

**How to apply**：

開一個 `decompose-slice` sandcastle prompt：「讀 parent issue → 對每個 'sandcastle' sub-step 寫 issue body + `gh issue create` + label」。

省 ~30 min 機械工作。

## 🟢 不該 delegate（成本/效益不對）

- **ruff format / E501 chore commit**：~5 行修改，sandcastle ~$0.30 + 5min docker 起鍋。手動 30 秒搞定。**留 manual**。
- **Memory feedback 寫入**：需要 session context（觀察 + 反思），sandcastle 是 fresh agent 沒這個 history。**留 manual**。
- **Cross-vendor / cross-layer 診斷**（如 CSP frame-src ↔ foliate paginator）：需要多輪 hypothesis testing + 看 vendor code + 對照外部 demo。**留 manual**。

## ❌ 物理不能 delegate sandcastle

- **Browser smoke**（Playwright + 視覺判斷）— sandcastle docker 沒瀏覽器
- **PR 美學 / UX 選擇** — sandcastle prompt 明禁
- **跨 session conflict 處理**（`git pull --rebase` resolve 別 worktree session 留下的 commits）

## 🎯 標準 workflow 起手 checklist

每進新 multi-slice workflow（含 PRD parent + N 個 slice issue），第一動作：

1. **掃 prereq fix 清單** — 獨立 P0 / blocker 立刻 fire sandcastle 並行
2. **掃並行 candidate** — slice 之間檔案重疊度 < 30% → 多 worktree 並行
3. **掃機械化工作** — HITL test write / sub-issue decompose → 看是否值得開 utility sandcastle prompt（slice 數 ≥ 3 時值得）
4. **保留給自己**：
   - 跨 vendor 診斷
   - 美學 / UX
   - browser smoke
   - memory 反思
   - PR review + merge orchestration

## 量化收益

2026-05-05 EPUB Reader workflow（5 slice + 1 P0）：
- **實際**：~3-4 hr wallclock，~600k token
- **若套用本 audit**：~1.5-2 hr wallclock，~400k token
- **省**：~50% time，~33% token

下次跑類似多 slice 工程一定要先讀這個檔。
