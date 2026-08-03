---
name: 桌機 GPU 重工作全面暫停
description: 【2026-07-31 已解禁】2026-05-01 hard hang 後的全面禁令，修修 2026-07-31 親口解除；現況為可跑，保留 Gen 4 + 長音檔留意的務實紀律
type: feedback
---

> **狀態：2026-07-31 已解禁。** 修修原話：「GPU 早就已經解禁才對，現在拿一個手冊還不准你用？」
> 以下禁令內容保留作為歷史脈絡，**「一律不跑」的部分已失效** —— 現在可以直接跑 GPU 工作，
> 不需要每次複述禁令或要求三件事齊備。

桌機（RTX 5070 Ti）2026-05 期間跑 GPU 重工作會撞硬體層 PCIe 不穩→畫面全黑→hard reboot。

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

---

## 2026-07-31 解禁與首次實跑

修修指出禁令早該解除（該 session 我還在引用它擋自己跑 WhisperX，被當場糾正）。當天實跑驗證通過：

- `nvidia-smi` 確認 `pcie.link.gen.current = 4`（BIOS Gen 4 鎖生效，當初的根因對策）
- WhisperX large-v3 跑 294s 音檔（`channel-comeback-0730`），1563 words / 0.9 分鐘，**無 hang**
- 環境事實：whisperx 只裝在 user-level Python 3.10（`py -3.10`），
  **不在** `E:\nakama\.venv`（3.10 但無 whisperx），也不在預設 `python`（3.14）

**現在的規則**：GPU 工作直接跑，不用問。只留一條務實紀律 —— 第一次跑沒跑過的長音檔
（>30 min）留意 `nvidia-smi`，其餘照常。
