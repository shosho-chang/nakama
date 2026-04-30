# 2026-04-30 — FunASR Paraformer-zh vs Whisper Large V2（MemoAI）對照測試

## 1. 目的

決定 nakama transcribe pipeline 的 ASR 引擎是否需要換。產出可影響三件事：

1. Line 1（podcast → 訪談多 channel）最緊急 — ASR 是地基，地基不穩後面 LLM 校正再強也救不回
2. 1hr 真實訪談 E2E 驗收（per `project_transcriber.md` 計畫但未執行）順便走完
3. 既有 ADR 的 ASR 選型 rationale（Whisper Chinese CER 4.72% vs FunASR 0.54%，引自 AIShell1）— 在台灣 podcast 訪談**真實素材**上重驗，不靠 paper benchmark

## 2. 假設

| | 預期勝出 | 信心 |
|---|---|---|
| 純中文長句 | FunASR | 高（paper benchmark + 工程直覺）|
| 中英 code-switching | Whisper | 中 |
| 人名 / 專有名詞 | 兩家無 hotwords 都會錯 | 高 |
| 時間戳精度 | FunASR（char-level）| 中 |
| 斷句品質（VAD）| 互有勝負 | 低 |
| Speaker diarization | Whisper（生態）| 高，但本次**不測**（兩家裸跑沒做）|

關鍵不確定項：MemoAI 用 Whisper Large V2，而我們的 reference benchmark 是 V3。**V2 對中文的 CER 會更差**（Whisper V3 是在 V2 基礎上加了大量非英文資料），所以這次 FunASR 勝率可能比 paper benchmark 高。

## 3. 範圍

### 範圍內

- Stage 2（FunASR ASR）+ Stage 3（SRT 轉換）only
- `--no-auphonic --no-llm-correction` 跑純 ASR + SRT 格式輸出
- 修修 MemoAI 用同一個音檔跑出 Whisper Large V2 SRT
- 段落級人工抽樣比對 + 評分

### 範圍外

- LLM 校正（Opus pass 1）— 這是 ASR 之上的層，比 ASR 是要看 raw 品質
- Gemini 多模態仲裁 — 同上
- Auphonic normalization 影響評估 — confound 太多（見 §5.1）
- Speaker diarization — 兩家裸跑都沒做，不在本次比較範圍
- 全 1hr 全文 ground truth — 成本太高，採抽樣

## 4. 輸入

修修需要提供：

1. **音檔路徑** — `<path>` (.wav / .mp3 / .m4a)
   - 理想：1hr 左右真實訪談（有來賓對話、code-switching、人名）
   - 已 Auphonic normalized 或 raw 都可以，但兩家要跑**同一個檔**（見 §5.1）
2. **MemoAI 跑完的 SRT** — `<path>.memoai.srt`（修修自己命名）
3. **5–10 個 ground truth 段落**（修修人工標）— 可選但強烈建議
   - Format：`HH:MM:SS - HH:MM:SS | 真實逐字稿`
   - 涵蓋：純中文長句 ×2 / 中英混 ×2 / 人名專名 ×2 / 模糊發音 ×2 / 訪談對話切換 ×2

## 5. Confound 警告

### 5.1 Auphonic normalization 已拍板（2026-04-30 修修決定）

兩家都跑同一個 **Auphonic-normalized** 音檔。Auphonic 在 production pipeline 已是定數，不在本次測試範圍內檢討。

- 音檔：`E:\nakama\tests\files\20260415.wav`（已 Auphonic normalized）
- MemoAI SRT：`E:\nakama\tests\files\20260415-memo.srt`（修修在同一個 normalized 音檔上跑出來）
- FunASR 用 `--no-auphonic` flag 跳過再次 normalize（檔案本身已是 normalized 結果）

### 5.2 Whisper Large V2 vs V3

MemoAI 用 V2，nakama benchmark reference 是 V3。Whisper V3 中文表現顯著優於 V2。本次比較的對手是 V2，所以「FunASR 勝出」結論不能外推到 V3。

但 V2 vs V3 的差距遠不到 V3 vs FunASR 的差距，所以結論方向應該不會翻盤。

## 6. 執行步驟

```
1. 修修：給 audio_path + MemoAI SRT path
2. 我：跑 FunASR
   python scripts/run_transcribe.py <audio_path> \
       --no-auphonic --no-llm-correction \
       --output-dir tests/files/out/benchmark
3. 我：寫 scripts/compare_srt.py（簡單時間戳對齊 + 並排輸出）
4. 我：產出對照表（兩家 SRT 並排 + 時間戳對齊）
5. 修修：人工抽樣評分 5-10 段（每段標 ✓/✗/局部錯 + 一句評語）
6. 我：彙整評分 → 結論 markdown 寫進 docs/research/
```

預估時間：FunASR 1hr 音檔 GPU 約 1-2 min；compare script 寫 + 跑 30 min；人工抽樣評分 30-60 min。

## 7. 評估維度與權重

| 維度 | 權重 | 評分方法 |
|---|---|---|
| 純中文長句 CER | 30% | 抽樣 ground truth 對照，算字級錯誤率 |
| 中英 code-switching | 20% | 段落人工標 ✓/✗ |
| 人名 / 專有名詞 | 15% | 段落人工標 ✓/✗（含「漏字」算錯）|
| 時間戳精度（cue start） | 15% | ground truth 的時間誤差中位數 |
| 斷句品質（VAD）| 10% | 看會不會把詞拆段（FunASR 有此 known issue per memory）|
| 漏字 / 多字 | 10% | 段落比對額外算 |

總分 100。FunASR 領先 ≥10 分 → 結論「不換引擎」；落後 ≥10 分 → 認真考慮 Whisper hybrid；±10 分內 → 看 §8 各維度策略。

## 8. 結論決策樹（預先寫好，避免事後合理化）

```
FunASR 總分 ≥ Whisper + 10
    └→ 維持 FunASR；本次驗收即 production
       下一步：1hr 訪談 E2E 跑全 pipeline 驗 LLM 校正
       
Whisper 總分 ≥ FunASR + 10
    └→ 認真考慮換引擎，先檢討：
       - 是不是 V3 / FunASR-streaming 等更新版本能補
       - hybrid 策略是否成本/工程值得（純中文 FunASR / 英文段 Whisper）
       
±10 分內
    └→ 看細項贏在哪：
       - FunASR 純中文贏 + Whisper 英文贏 → 維持 FunASR + 加 hotwords + LLM 校正補英文
       - 兩家差不多 → 維持 FunASR（生態 + ADR 已凍結）
```

## 9. 改善方向預判（FunASR 輸了的話）

按工程量遞增：

1. **加 hotwords**（既有支援，但需 context file 或 LifeOS Project file）— 最便宜，5 分鐘
2. **升級 FunASR 模型** — Paraformer-zh-streaming / SenseVoice / 其他更新模型；30 分鐘 + shadow test
3. **LLM 校正補強 prompt** — 已有 pass，prompt 可加「英文專有名詞優先 keep original」等規則；1-2 hr
4. **Hybrid 引擎** — 純中文 FunASR / 英文段 Whisper；3-5 hr 工程 + 對齊邏輯
5. **整體換 Whisper** — 重寫 transcriber.py FunASR 段；1 天 + 重做 E2E test

## 10. 產出

- 本 plan：`docs/plans/2026-04-30-funasr-vs-whisper-benchmark.md`（你正在讀的）
- 比對 script：`scripts/compare_srt.py`（執行階段寫）
- 結果報告：`docs/research/2026-04-30-funasr-vs-whisper-results.md`（執行完寫）
- 結論進 memory：更新 `project_transcriber.md` 加上「2026-04-30 對照 Whisper Large V2 結果」段

## 11. 起跑卡點

修修需要提供：

```
□ 1hr 真實訪談音檔路徑（raw 即可）
□ 同一檔在 MemoAI 跑出來的 SRT 路徑
□ （建議）5-10 個 ground truth 段落
```

兩個必要、第三個強烈建議。沒第三個只能跑「主觀並排比較」（修修自己看哪邊順），有了才能算 CER 客觀分數。
