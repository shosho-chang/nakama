---
name: PCIe link instability blocks GPU refine on RTX 5070 Ti
description: 2026-05-01 hard hang 至少 3 次（srt_refine × 2 + 第三次 session）→ 修修明令全面暫停 GPU 重工作；強制執行請見 feedback_no_gpu_heavy_work_until_user_ok.md
type: project
---

**2026-05-01 update：第三次掛機（畫面全黑）後修修親口下令禁止任何 GPU 重工作直到他親自解禁。從這刻起的剛性禁令詳見 [feedback_no_gpu_heavy_work_until_user_ok.md](feedback_no_gpu_heavy_work_until_user_ok.md)。下方原 incident 紀錄保留作脈絡。**

---

開發機（修修桌機，RTX 5070 Ti + Intel 13/14 gen + ASUS）跑 stable-ts `model.refine()` 會撞硬體層 PCIe 錯誤導致整機 hard hang。**BIOS 鎖 PCIe Gen 4** 是已知 workaround，2026-05-01 user 待重開機改完後再驗證 srt_refine.py 是否能跑完。

**2026-05-01 第三次 incident 摘要**：在新 session 跑到一半（疑似 iter4 transcribe 路徑或殘留 refine call，待 user 回家後從 event log 確認），畫面再次全黑、機器 hard reboot；user 出門前留話「千萬不要再做這個測試了」。原本被視為 safe 的 transcribe 路徑也納入禁區直到查清。

**Why:** 2026-05-01 srt_refine.py 對 76 min 訪談跑 `model.refine()` 兩次，兩次都掛（第 1 次跑到 53% / 5 分 21 秒，第 2 次 ~3-5 分鐘）。Event log 鐵證：
- `WHEA Event 17` × 2（11:50:28 + 12:55:18）— `Component: PCI Express Root Port`，Source: `Advanced Error Reporting (PCI Express)`，Device: `VEN_8086&DEV_A70D&SUBSYS_86941043` = Intel CPU PEG x16 root port (ASUS 板)
- `Kernel-Power Event 41` × 2（12:44:14 + 12:57:24）— hard reboot
- 沒 minidump（`C:\Windows\Minidump`）也沒 LiveKernelReport — driver hang 不是 BSOD
- `refine.log` 顯示 `torch.AcceleratorError: CUDA error: unknown error`，崩在 `whisper/model.py:169` cross-attn forward
- `nvidia-smi`：PCIe link 跑 Gen 5 x16，max Gen 5 x16 — **這就是根因**

軟體 stack 已實證健康：torch 2.11.0+cu128 / CUDA 12.8 / arches 含 `sm_120` / RTX 5070 Ti 認得（cap=(12,0)）。Python 環境是 **system Python 3.10**（`C:\Users\Shosho\AppData\Local\Programs\Python\Python310\`），**不是** repo `.venv`（.venv 是 Python 3.14 沒裝 torch — 純測試用）。

PCIe 5.0 + Blackwell 對 signal integrity 極度敏感是業界已知議題；鎖 Gen 4 對 GPU compute 幾乎無感（GPU 不是 bandwidth bound）。

`model.refine()` 撞牆但 `model.transcribe()` 不撞的差異：refine 是「逐 word mute audio + 重跑 forward pass」（76 min audio = 1500-3000 word × 重跑），PCIe 是高頻短 transaction；transcribe 是 30s sliding window，相對連續 traffic。前者剛好暴露 link instability 邊界。

**How to apply:**
- BIOS Gen 4 改完並驗證前：**禁止 auto 跑 srt_refine.py**（兩次中兩次掛機）；transcribe 路徑（iter4 / iter4_1 / iter4_2 全是 transcribe）安全可跑
- 任何 GPU 重工作前先 `nvidia-smi --query-gpu=pcie.link.gen.current,pcie.link.width.current --format=csv` 確認 Gen 4 鎖住
- WHEA Event 17 計數可當 leading indicator：dump system event log（`cmd //c "wevtutil qe System /c:N /rd:true /f:text"`）grep `WHEA-Logger` + `Event ID: 17`，看 PCIe AER 是否復發
- 若 Gen 4 還掛 → 升級到物理層診斷：重插 GPU、檢查 12VHPWR / 8-pin 接頭、stress test（FurMark / OCCT）、PSU 瓦數評估（5070 Ti TGP 300W，瞬時 350W+）、可能 GPU RMA
- 此 incident 標記 in flight，直到 user 驗證 BIOS Gen 4 跑 srt_refine.py 通過為止
