---
name: 桌機 GPU 重工作全面暫停
description: 2026-05-01 第二次 hard hang（畫面全黑）後修修明令禁止；srt_refine + iter4 transcribe + 任何 GPU heavy job 在 user 親口解禁前一律不跑
type: feedback
---

桌機（RTX 5070 Ti）跑 GPU 重工作會撞硬體層 PCIe 不穩→畫面全黑→hard reboot。**修修親口下令，跨 session 一律遵守，不要重蹈覆轍。**

**Why:** 2026-05-01 至少兩次 hard hang：
- 第一次 srt_refine.py × 2 連掛（已記在 project_pcie_link_instability_2026_05_01.md）
- 第二次（這個 session 開頭）「跑到一半畫面又全黑」+ 修修「千萬不要再做這個測試了」+ 「重複一次，千萬不要再做這件事情」+ 即將出門遠端控制這台電腦

修修能說「重複一次」+ 出門前留話，等於 P0 紅色禁令。違反代價：機器再掛一次 → 修修出門無法救援 → 整天 dev 環境停擺。

**How to apply:**
- **新 session 起手第一件事**讀這條，再讀 `project_pcie_link_instability_2026_05_01.md`；兩條都讀完再決定動哪些指令
- 在修修親口（Slack/對話）解禁之前，**全面禁止**自動執行下列：
  - `python scripts/srt_refine.py` 或任何 `model.refine()` 呼叫
  - `python scripts/iter4*.py` / `iter_test.py` 等任何會 load Whisper / WhisperX / stable-ts 並 transcribe 76 min audio 的 script
  - 任何 `nvidia-smi` 之外的 GPU 重工作（包含但不限於 ASR / image gen / fine-tune / batch inference）
- 如果使用者「只是叫我看 code」「只是叫我寫 test 而沒叫我跑」→ 寫完 code 不要自動跑驗證，**先回報並等修修確認**才執行
- 即使修修在新 session 說「跑一下 iter4 試試」這種輕語氣，也要先複述這條禁令 + 確認 BIOS Gen 4 已改完 + 確認 `nvidia-smi --query-gpu=pcie.link.gen.current` 真的是 4，**三件事齊備才跑**
- 解禁後仍記得：`srt_refine` 即使 Gen 4 鎖住也是 high-risk（它本身就是觸發 PCIe AER 的 workload pattern），第一次 retest 用 BIOS Gen 4 + 短音檔（5-10 min）+ 全程盯著
- 真有需要驗證 transcribe 工作流，走 Mac（本機 MPS）或 VPS CPU fallback，不要用桌機 GPU
- 出門遠端模式下使用者不在旁邊救機，**不確定的東西一律不跑**，寧可空轉等修修回家
