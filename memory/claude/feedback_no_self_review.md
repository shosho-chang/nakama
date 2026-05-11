---
name: 修修不自己做 PR review，Claude 必須 dispatch review agent
description: PR ship 完不要等修修 review；自動叫 review skill / agent 群跑（repo 有 review skill），修修只看 review 結論決定 merge
type: feedback
---

修修不自己做 code review。PR 出單後**不要被動等 review**，必須主動 dispatch review skill / agent 群把 review 跑完，修修才有結論可以決定 merge。

**Why**：修修明確說過很多次「我不做 review」。等 review 等於 PR 卡死。Repo 有現成 review skill（`/ultrareview` user-triggered + billed 我不能自己叫；其他 review skill 如 `code-review:code-review` 我可叫）+ Agent 工具能 spawn 多模型 panel review。

**How to apply**：
- 任何 PR ship 完，下一步默認是 dispatch review（`/review` skill 或 spawn review agent），不是「等修修看」
- 對重大戰略 PR 可以建議跑 multi-agent panel（不同模型 push-back）
- `/ultrareview` 我不能叫但可以提醒修修「這條值得 ultrareview，要不要打」
- review 結論回來再 ping 修修決定 merge / iterate
