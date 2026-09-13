---
name: feedback_dont_run_full_suite_during_renders
description: 產線渲染在跑的時候不要跑整套測試——會被餓到 6 倍慢，看起來像卡住；改跑受影響的目錄
metadata:
  type: feedback
---

Resolve 物化、hyperframes 字卡渲染、ffmpeg 出片任何一項在跑的時候，**不要跑
`pytest tests/`**。改跑受影響的那幾個目錄，或等渲染結束再跑整套。

**Why**：2026-09-12 20260721 呂冠緯 那一夜，我讓整套測試跑了兩次、各接近兩小時，
兩次都以為它卡死而自己砍掉，然後又重跑一次——白燒了將近四小時的牆鐘時間，而且
中間沒有任何可用的訊號。分段跑才看清楚真相：

| 目錄 | 時間 |
|---|---|
| tests/brook | 12:28 |
| tests/scripts | 5:39 |
| tests/shared | 0:31 |
| tests/skills | 0:29 |

**合計約 19 分鐘。** 兩小時是 6 倍的拖慢，來源是同一台機器上同時在跑 Resolve
（1080×1920 ProRes）、Chrome／hyperframes、以及 ffmpeg。`pytest -q` 在結束前不
吐任何東西，所以「慢」和「死」在輸出上長得一模一樣。

**How to apply**：
- 渲染在飛的時候，只跑受影響的目錄（`tests/brook/script_video/…`）。那才是能證明
  我的改動沒壞事的東西，而且幾分鐘就有答案。
- 真的要跑整套，先確認沒有背景渲染，並且**分段跑**——一段一段有輸出，卡在哪一
  段立刻看得出來。
- `pytest -q` 沒有中途輸出。要判斷「還活著還是卡死」，用分段，不要盯著一個沒有
  輸出的整批等。
- 這個 repo 的整套測試本來就有幾個**既有失敗**（`test_r2_client` 2 個、
  `test_vault_layout_audit` 2 個、`test_podcast_pipeline_v2_skill` 3 個），
  以及 `test_raw_ingest.py` 因環境少裝 `markdownify` 而收集就爆。
  看到失敗先用 `git log --oneline <base>..HEAD -- <被測檔案>` 歸因，不要預設是自己弄壞的。
- 相關：[[feedback_fix_failures_dont_report_them]]（自己診斷、自己往下推）。
