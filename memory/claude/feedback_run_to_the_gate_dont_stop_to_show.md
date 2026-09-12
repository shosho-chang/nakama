---
name: feedback_run_to_the_gate_dont_stop_to_show
description: Editorial Master 確定之後一路跑到 packaging gate 才停；不要在對話裡「先給他看一眼」當停點
metadata:
  type: feedback
---

**Editorial Master 封存之後，直接跑到 `/bridge/packaging/<slug>` 才停。**
完整節目 packaging（標題發想 → emit_packages → cutout 抽格 → face_measure）全部做完、
**東西進到 gate 上**，才回報。中間不要停。

**標題挑哪一條、用哪兩張臉、大字打什麼——那些是 gate 上〈組封面〉區的欄位，不是對話題目。**
ADR-054 D11：gate 端零 render、零 LLM；桌機把 PNG 做完，Bridge 只勾。在對話裡問等於把他從
那個介面拉出來，還要他自己記得回去。

**Why**：2026-09-11，20260721 呂冠緯。我叫 title-brainstorm subagent「不要呼叫 emit_packages、
不要寫 packages.json——修修要先看過」。**那句是我自己編的，流程裡沒有這個停點。** 結果 13 張
cutout、5 條標題、封面規格全部做完，卻沒有一樣進得了 gate。他問：「我不是這時候應該進
Packaging Gate 去 review 嗎？Packaging Gate 上沒看到啊」，接著：

> 以後我確定了 editorial master 之後，你就直接跑到 packaging gate 那邊再停下來，
> **不要讓我一直提醒**。這個流程到現在還沒確定下來嗎？為什麼這次又要停在奇怪的地方？

**根因有兩層，第二層是真的文件缺口**：
1. 我把他說的「想先看到 packaging」讀成「在對話裡給他看」——但 packaging 他本來就是在 gate 上看。
2. `podcast-pipeline/SKILL.md` 的 **S8F 派工圖**（我實際照著跑的那張）從「定稿」直接跳到開採 miners，
   **完整節目 packaging 不在圖上**。S7P 只存在於狀態機表格。照圖跑就會漏，漏了就憑印象補，
   補到奇怪的地方。2026-09-11 已把 S7P 補進派工圖並加上「不要發明第四個停點」。

**How to apply**：
- 停點只有三個：選段、timeline review、packaging review，加上 YouTube 上傳要明確核准。
  **沒有第四個。**「做好了給你看一眼」不是停點。
- gate 看不看得到，用 `thousand_sunny.routers.packaging._scan_episodes()` 驗，
  不要只確認檔案寫出來了——packaging root 是 **vault 的 `Attachments/packaging/`**，
  不是 episode 資料夾（我這次就是先寫錯地方才發現）。
- 派工給 subagent 時，不要自己加流程裡沒有的限制。要加就先問自己：這條規則在哪份文件裡？
- 相關：[[feedback_dont_ask_permission_at_every_step]]、[[feedback_reader_documents_go_to_obsidian]]
