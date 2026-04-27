---
name: GPU 升級採購評估 — Pro 4500 / 5000 / 6000 vs 雲端 API
description: 修修評估升級桌機 GPU（Pro 4500 32GB / Pro 5000 48GB / Pro 6000 96GB）vs 雲端 VPS+API；預算上限單張 Pro 6000 (~NTD 50 萬整台)；revenue 來源是「自由艦隊」千人社群
type: project
created: 2026-04-27
originSessionId: f4633349-8e87-4dec-921d-fcd19990d805
---
修修評估桌機 GPU 升級。當前 RTX 5070 Ti 16GB（見 [user_hardware.md](user_hardware.md)）→ 升級成 NVIDIA RTX Pro Blackwell 系列。**不買 5090** 因為過熱 / 耗電 / 不玩遊戲不值溢價。**不買 AMD R9700** 因為 ROCm 生態折扣換不回省下的錢。

**Why:** revenue 主要來源是 Chopper「自由艦隊」千人會員社群 ([project_chopper_community_qa.md](project_chopper_community_qa.md)) — community QA + KB 查詢 + 會員數值記錄。預算上限 NTD 50 萬 (≈ 一張 Pro 6000 96GB Max-Q + 周邊)，目標年收 > NTD 1000 萬視為 ROI 合理。但「硬體 generate revenue」的因果鏈要 trace：硬體本身不直接賺錢，是「內容質量 + 社群粘性」賺錢，硬體只是 enabler。

**How to apply:** 任何「要不要本地 LLM / multimodal / image gen」的功能討論都要拉這條：(1) 雲端 API 跑得動嗎？(2) 隱私敏感嗎？(3) batch vs realtime？(4) 月用量 token cost vs 硬體攤提？

---

## 採購 ladder（從低到高）

| 卡 | VRAM | TDP | 估價 USD | 解鎖 model 天花板 |
|---|---|---|---|---|
| Pro 4500 | 32GB GDDR7 | 200W | $2,200-2,800 | Qwen 3 32B Q5 / Mistral Small 24B / Qwen2.5-Omni 32B |
| Pro 5000 | 48GB GDDR7 | ~300W | $4,500-5,500 | **Llama 3.3 70B Q4** / Qwen 2.5 72B Q4 / Llama 3.2 Vision 90B |
| Pro 6000 Max-Q | 96GB GDDR7 | 300W | $8,500-10,000 | **Mistral Large 123B Q4** / Llama 3.3 70B Q8（無 quant 損失）|

**Frontier (DeepSeek-V3 671B / Llama 405B)**：96GB 也跑不動全 weights，個人 dev 不實用。

## 質量 ladder vs nakama use case

| Local model | 質量逼近 | nakama 應用 |
|---|---|---|
| 8B (現況) | Haiku 4.5 | Robin chunk summary OK |
| 32B (Pro 4500) | Sonnet 3.5 ~ 接近 4.6 | Robin map step / Brook 70% offline 替代 |
| 70B (Pro 5000) | Sonnet 4.6 ~ 接近 Opus 4.7 | **Brook compose 主力本地化可行** |
| 123B (Pro 6000) | Opus 4.7 / GPT-4 級 | 接近 frontier；對 health content 是 ceiling |

## 雙卡 vs 單高 VRAM 卡

**Pro Blackwell 系列沒 NVLink**（NVIDIA 2024 砍了 workstation NVLink）→ 雙卡走 PCIe Gen5 ×16 = 64GB/s vs 卡內 GDDR7 1TB/s → 16x 慢。

實際使用 case：
- **Tensor Parallel**（model layer 切兩半）：throughput loss 30-50%（PCIe-only）
- **Pipeline Parallel**（前後段 layer）：loss ~10-15%，但 latency 增
- **Expert Parallel**（MoE 模型）：loss 5-15%，PCIe-friendly
- **各跑獨立 model**（最 nakama-friendly）：零通信、全速

雙 Pro 5000 48GB ≈ 單 Pro 6000 96GB 同價（~$9-10K），但：
- 雙 5000：multi-model 並跑友好（LLM + multimodal + image gen 各 1 卡）
- 單 6000：跑 70B+ frontier-class 通暢

## 進場建議框架

**循序加碼比一次到位 ROI 高**：

1. **Pro 4500 32GB 進場** (~$2.5K) — 解鎖 95% multimodal + 32B LLM use case
2. 跑 6 個月看真實 bottleneck 在哪
3. 升級時的 trigger：
   - 真常想跑 70B → 升 Pro 5000 48GB
   - 真常 image + LLM 同跑 → 加裝第二張
   - 質量已夠 → 省下來

**避免一次買 Pro 6000 96GB** — 不知 bottleneck 在哪先小台 deploy。除非確定要：
- 微調 70B+ 模型（不只 inference）
- batch 70B 處理大量文檔
- healthcare 對 hallucination zero tolerance 場景

## 自架 vs 雲端策略

**Hybrid 是務實解**（不要 100% 任一邊）：
- **production user-facing**（自由艦隊千人 query）→ 雲端（VPS + Anthropic / OpenAI API）
- **batch / dev / 隱私敏感**（textbook ingest, KB build, 試 model, member health 數據）→ 自架硬體

理由：
- production 千人 query 靠雲端 scale + redundancy（你機掛 = community 停擺）
- batch / dev 自架邊際成本接近零（電費）
- 會員 health 數據敏感性可能需要本地處理（合規 / 主權）

**轉折點**：當 community 月收 > NTD 80K（cover Pro 6000 月攤提 + 電費）且 production token cost > NTD 15K/月，才考慮把 production 也搬一部分到本地（hybrid 70/30 → 50/50）。

## 不要自己決定的事

- 採購時機：等真實 bottleneck 浮現再買，不要 speculative buy
- production 完全自架：revenue 沒驗證前一律雲端 first
- 一次買 Pro 6000：除非 70B+ 微調或 frontier-class healthcare 需求明確

## 修修自己要求我主動提醒的事（2026-04-27）

**「修修問起 Pro 6000 / Pro 5000 / 大採購時，主動套 [feedback_avoid_one_shot_summit.md](feedback_avoid_one_shot_summit.md) 提醒 incremental」** — 修修自陳有「一次攻頂」衝動，差點直接買 50 萬。提醒框架：

1. 「你已踩到 Pro 4500 32GB 的真實 bottleneck 了嗎？」
2. 「這個升級是解現有阻礙，還是 ceiling 提升？」
3. 「revenue 驗證了嗎？沒驗證 50 萬留著做 marketing / 內容生產 ROI 高 5-10x」

**Hybrid 框架是預設答案**：production cloud + dev/batch 自架（5070 Ti 已夠）。不要一次到位純自架。
