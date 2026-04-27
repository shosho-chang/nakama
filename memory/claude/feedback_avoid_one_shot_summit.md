---
name: 修修「一次攻頂」反射要主動 reframe 成 incremental
description: 採購 / phase / scope 決策前，修修自承有「一次攻頂」衝動 — 我要主動提醒走 incremental + 等真實 bottleneck 浮現再升級
type: feedback
created: 2026-04-27
originSessionId: f4633349-8e87-4dec-921d-fcd19990d805
---
修修自陳：「我做什麼事情都希望能夠一次攻頂」。具體表現：差點直接買 NTD 50 萬 Pro 6000、原本要把 Phase 1-9 quality uplift 一口氣推完。

**Why:** 「一次攻頂」對 solo dev / pre-revenue / 個人 tooling 階段是 over-investment trap：
- 大多 unlock 在前 80% 投資（Pro 4500 already 解鎖 95% multimodal use case）
- 真實 bottleneck 要跑半年才知道在哪
- 現金流 / 時間都比 ceiling 寶貴
- enterprise quality bar（Phase 7 staging / Pro 6000 96GB / 100% 自架）對個人 dev 是 ceremonial 不是 functional

**How to apply:**

任何採購 / 大 scope decision 前，主動提醒這條：

| 場景 | 默認 reframe |
|---|---|
| 修修問「該買 Pro 6000 嗎？」 | 先問「你已踩到 Pro 4500 32GB 的真實 bottleneck 了嗎？」沒踩到先 4500 進場 |
| 修修問「要不要 setup 完整 staging？」 | 先問「production 這個月真的有 incident 嗎？」沒有就走 lightweight safety net |
| 修修問「要不要做 X full-stack」 | 先問「Y MVP 跑半年的真實 friction 在哪？」沒驗證就 MVP first |
| 修修要把全 Phase 一口氣推 | 拆 slice，3 個 slice 後復盤要不要繼續 |

**反向訊號（一次攻頂 OK 的時機）**：
- 真實 bottleneck 有量化證據（cost / latency / failure log）
- 這不買升級會 block 直接 revenue（不是「以後可能用到」）
- 微調 / 訓練自己 model（這要一次到位 VRAM）
- healthcare / SLA 違約成本高

只有「ceiling 提升」而沒有「現有真實阻礙」→ 先 incremental。
