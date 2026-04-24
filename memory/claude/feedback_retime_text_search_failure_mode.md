---
name: Hand-edited SRT retime via text substring 根本行不通
description: Abandoned PR #109 教訓：hand SRT 文字被編輯過，substring-match ASR text 無法橋接差異
type: feedback
---

**規則**：不要嘗試用「text substring match（hand SRT cue text ↔ ASR text）」去對齊**人工校過**的 SRT 到音訊時間戳。這個假設從根本上錯的。

**Why**：修修的 hand SRT 是編輯過的產物——會改寫、合併多句、省略語氣詞/填詞、統一書名/英文名拼寫。ASR text 逐字逐句包含原始口語（「我記得我們第一次見面的時候是」vs ASR「是在你們家」邊界不同；hand 保留「《有溫度的溝通課》」vs ASR「溫度的功課」）。substring 根本找不到對應。

PR #109 表面上修好了 retime（所有結構不變量通過：0 零時長、0 同起、0 重疊、0 倒退、單元測試 19 過、anchor ≤3s），但實際 Premiere 播放時「一開始就漏字、對不齊」。結構健康 ≠ 可用。

**How to apply**：
- hand SRT 要重新對齊音訊 → **不走文字比對**。走聲學對齊：word boundary detection（WhisperX / Gentle / Montreal Forced Aligner）或 speech-to-timestamp via phoneme alignment。
- 短期：`--shift` / `--auto`（單一線性變換）對震盪漂移的 hand SRT 本來就修不全，用戶繼續手動 Premiere 微調最可靠。
- Long-term：若要自動化，用音訊 forced alignment 拿每個字的音訊時間，再把 hand 文字「餵」進對齊器（不是 matching 出去）。
- 評估新字幕工具「可用性」時：**實際播放驗證**比任何結構統計重要。下次類似任務，先 demand 用戶播一段驗證再迭代演算法。

**相關**：PR #109（closed, not merged），分支 `fix/retime-char-level-alignment` 保留遠端備查。
