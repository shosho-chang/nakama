---
name: Single-worktree subagent prompt 不要過度要求 P7 報告
description: 子 agent dispatch prompt 寫完工要求時，不要要 self-review 三問 + aesthetic direction + remaining work — 那會回 5-10k 字長報告。改成「commit hash + 一句 trade-off」。2026-05-05 EPUB Reader 4 個 single-worktree subagent 報告 ~30k 浪費教訓。
type: feedback
---

dispatch single-worktree subagent 跑 UI / 多檔工作時，**P7 完工要求要寫得很簡潔**，不然 agent 真的會詳細寫滿。

**Why**：2026-05-05 Slice 1D / 2D / 3C / 4D 4 個 single-worktree subagent，我的 prompt 都要求：

```
P7 completion report:
1. What I changed — file list + one-sentence per file
2. Tests run — pytest + ruff summary
3. Self-review — 方案正確 / 影響全面 / 回歸風險
4. Aesthetic direction — color choices, layout, etc.
5. Remaining work
```

每個 agent 真的回 5-10k 字。Self-review + aesthetic + remaining work 多半是禮貌性填充，沒實際信息密度——commit + diff + tests pass status 才是 ground truth。

**How to apply**：

新版精簡 P7 要求：

```
When done, output:
- commit hash
- one-line summary of trade-off / surprising decision (if any)
- pytest + ruff status (pass / fail count)
```

如果我真要 self-review，自己讀 git diff + run tests 就好——比 agent 自填可信。

**Edge case**：若 agent 跑了非預期路徑（拒絕了一條 acceptance、發現 spec 漏洞），希望它主動講。可加一行：「If you skipped any acceptance item OR found an issue with the spec, mention it. Otherwise omit.」

**整體 prompt 大小目標**：≤ 2k token。當前 EPUB workflow 那種 3-5k token 的 subagent prompt 過大——試圖把所有 context 塞進去，但 agent 可以自己 read 檔案；只要 spec 文字精簡、acceptance 寫清楚、邊界明確就夠。
