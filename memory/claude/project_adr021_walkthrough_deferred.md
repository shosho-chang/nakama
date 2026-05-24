---
name: project-adr021-walkthrough-deferred
description: ADR-021 Stage 4 E2E walkthrough (#461) 延後處理，等修修有大塊時間再排
metadata:
  type: project
---

ADR-021 Stage 4 E2E walkthrough (issue #461) 2026-05-24 由修修決定**暫時放 backlog**，先處理其他較小的 HITL 項目。

**Why:** 這個項目要修修親自跑完整真實 Project（Obsidian 建頁 → Zoro → Brook synthesize → Web UI review/reject/finalize → 寫稿 → 跑第二次驗 reject 降權 → vault 乾淨度驗 → retro 文件），是 ADR-021 整套真實 dogfood，需要連續大塊時間 + 心力。所有 code-side blocker (#453/#458/#459/#460/#462) 已全 closed，技術上 ready。

**How to apply:**
- 修修主動提起 ADR-021 / #461 / 「跑 Stage 4 walkthrough」之前，不要把這個項目排進收尾清單
- 一旦修修決定要跑：reference [[feedback_aesthetic_first_class]]（這次 walkthrough 會直接審美 Thousand Sunny `/projects/{slug}` review/writing mode UI）
- Retro 寫進 `docs/research/YYYY-MM-DD-adr-021-stage4-walkthrough-retro.md`
- 跑完關 #461
