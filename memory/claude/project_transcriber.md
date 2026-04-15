---
name: Transcriber 語音轉字幕模組
description: shared/transcriber.py — FunASR Paraformer-zh 本地 ASR + Auphonic 雲端前處理，Podcast 轉繁體中文 SRT
type: project
created: 2026-04-14
updated: 2026-04-15
confidence: high
---

## 狀態：feat/transcriber-upgrade branch（3 commits），待 merge

`shared/transcriber.py` + `shared/auphonic.py`

### E2E 測試結果（2026-04-15）
- 測試檔案：`tests/files/angie-test.wav`（20 分鐘 Podcast）
- ASR 耗時：~10 秒（RTX 5070 Ti GPU）
- 字幕條目：按句拆分，每段 ≤20 字，時間戳精確
- 簡轉繁：✅ 正常
- **辨識度：差強人意** — 同音字錯誤多（蘇味→數位、樹味→數位等），英文名辨識差
- Auphonic 前處理：尚未測試（只測了 ASR 單獨）

### 待處理
1. **辨識度改善** — 開 `use_llm_correction=True` 讓 Claude 修正同音字/專有名詞
2. **Auphonic E2E** — 完整 pipeline 含 normalization + 降噪
3. **後續規劃** — 先做 CLI，再做 Skill（修修已同意方向）

## 架構（2026-04-15 升級）

```
音檔 → Auphonic 雲端前處理（normalization -16 LUFS + 動態降噪）
     → 裁切免費方案頭尾 6s Jingle（ffmpeg）
     → FunASR Paraformer-zh（本地 GPU ASR + VAD + 時間戳 + Hotword）
     → OpenCC 簡轉繁
     → 標點→空格（句中標點替換為半型空格，句尾移除）
     → 字幕斷行（≤20 字，不切斷英文單字）
     → _correct_with_llm()（可選）
     → SRT 輸出
```

### 技術細節
- FunASR 回傳**字級時間戳**（6255 timestamps 對應 6981 字元）
- 用句尾/逗號拆分後，以字元位置對齊 timestamp 計算每段起止時間
- 強制斷行時保護英文單字完整性

### Auphonic 多帳號
- 5 個免費帳號，`AUPHONIC_ACCOUNT_N=email,api_key` 格式
- **優先選離 reset 最近的帳號**，避免餘額浪費
- 所有處理參數從 .env 讀取（loudness/denoise/leveler/filtering 等）

### GPU 環境
- torch 2.11.0+cu128，RTX 5070 Ti 16GB
- `pip install funasr torch torchaudio --index-url https://download.pytorch.org/whl/cu128`
- 系統需求：ffmpeg + ffprobe
