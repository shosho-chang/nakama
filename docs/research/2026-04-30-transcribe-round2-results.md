# 2026-04-30 — Transcribe 第二輪測試（PR #274 fix 驗證 + Qwen3-ASR D2 路徑試水）

## TL;DR

**WhisperX-v2 (PR #274) 通過 ship 條件，Qwen3-ASR-1.7B 在台灣 podcast 工況失敗**。同 ADR-013 對 FunASR 的教訓 — Mandarin SOTA benchmark 不對位真實工況。

| 指標 | WhisperX-v1 | **WhisperX-v2** | **Qwen3-ASR-1.7B** | MemoAI-V2 |
|---|---|---|---|---|
| Wall clock (76min audio) | 1.0 min | **1.4 min** | 7.7 min ASR + 2.1 min model load | — |
| 「主持人 張修修」幻覺 | 10 處 ❌ | **0 處 ✅** | 0 處 | 0 處 |
| 「數位遊牧」 | 5 處 ✅ | **5 處 ✅** | **0 處（聽成「諸位遊牧」x2 + 簡體「数位」x1）❌** | ✅ |
| 「心酸血淚」 | 1 ✅ | **1 ✅** | **0（聽成「辛酸血泪」）❌** | ✅ |
| 「Traveling Village」 | 3 ✅ | **3 ✅** | **0（聽成「超柏林部落」）❌** | ✅ |
| 「Hell Yes」 | ✅ | **✅** | **錯成 Hello + Hell 中段不一致 ❌** | ✅ |
| 「Paul / 保羅」 | 4 + 35 ✅ | **4 + 35 ✅** | 3 + 38（簡體保罗）⚠️ | ✅ |
| 「本尊」重複 bug | 0 ✅ | **0 ✅** | **1 處「本本尊」❌（同 FunASR 老 bug）** | 0 |
| 「花蓮」重複 bug | 0 ✅ | **2 處「花蓮花蓮」⚠️ 回歸** | **8 處「花莲花莲」⚠️** | 0 |
| 簡繁 | 全繁體 ✅ | **全繁體 ✅** | **全簡體 ❌（無 s2twp）** | 全繁體 |
| 開頭問候詞 | 可以喔 ✅ | 可以喔 ✅ | **Hello ❌** | 可以齁 ✅ |
| Cue 結構 | 2055 cue / avg 11.5 字 | **1326 cue / avg 18.0 字** | 23323 cue（字級時間戳）需後處理 | 3101 cue / avg 7.9 字 |

## Phase A：WhisperX-v2 (PR #274 fix 驗證)

PR #274 修兩件：
1. ✅ `condition_on_previous_text=False` + `compression_ratio_threshold=2.4` + `no_speech_threshold=0.6` 三件 anti-hallucination
2. ⚠️ jieba 詞邊界 force_break

### 結果

**主目標 prompt-leak 完全修好**：「主持人 張修修」幻覺 10 處 → **0 處 ✅**

**詞邊界部分修好**：21 處 → 16 處（76% 還在），表示 jieba 邏輯沒 100% cover。實例：
- v2 cue 813「...馬 / 上的醒來」 — 詞「馬上」被切
- v2 cue 824「...重的力量然 / 後但是」 — 詞「然後」被切
- v2 cue 888「...如果可以跟這個 / 人約會」 — 詞「這個人」被切

**新發現回歸**：「花蓮花蓮」within-segment 重複（v1 沒此 bug，v2 有 2 處）— 推測 `condition_on_previous_text=False` 副作用：跨片段 prompt-leak 抑制了，但片段內 token 重複沒抑制。

**Cue 結構變化**：v1 avg 11.5 字 / v2 avg 18.0 字；20 字硬拆 cue 從 377 變 665。jieba force_break 邏輯傾向 pack 滿 20 字，而非按 ASR segment natural 拆。對訪談文字稿可能反而難讀。

### 結論

**WhisperX-v2 ship 條件達成，但有 follow-up**：
- ✅ 主要目標 prompt-leak 修好可 ship
- ⚠️ Follow-up：詞邊界 16 處 + 花蓮花蓮重複 + cue 結構過長 → 下一輪再優化
- ✅ 速度依然 76× real-time

## Phase B：Qwen3-ASR-1.7B 試水（D2 路徑）

ADR-013 D2 替代路徑：[Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) + ForcedAligner-0.6B（阿里官方，2026-01-29 release，Apache 2.0，benchmark 自稱 Mandarin WER 2× 領先 Whisper V3）。

### 安裝紀錄

```
pip install qwen-asr  # PyPI 0.0.6
```

- 自動裝 transformers 4.57.6（剛好 nakama 既有版本對齊 ✅）
- 降 accelerate 1.13.0 → 1.12.0（qwen-asr pin）
- 模型自動 HF download：1.7B (4.7GB) + ForcedAligner-0.6B (1.84GB) = 6.5GB
- VRAM 實測 ~10GB peak（fits RTX 5070 Ti 16GB ✅）
- **不需 FlashAttention 2**（Windows native skip，SDPA fallback OK）
- Python sox wrapper warning「SoX could not be found」**不影響 inference**

inference snippet：[scripts/run_qwen3_asr.py](../../scripts/run_qwen3_asr.py)

### Wall-clock

```
模型載入 + HF download:    127.3s
ASR + alignment:           459.6s  (~7.7 min)
總計:                      587s    (~9.8 min)
```

對比 WhisperX-v2 = 1.4 min，**Qwen3-ASR 慢 7×**。

### 致命準確度問題

**Qwen3-ASR 在 podcast 主題詞 / 在地名詞上全面失誤**：

1. **「數位遊牧」→「諸位遊牧」**（podcast 主題核心詞）
   ```
   Qwen:  ...過著诸位游牧生活这个感想...
   WX-v2: ...過著數位遊牧生活的這個感想...
   ```

2. **「心酸血淚」→「辛酸血泪」**（同 FunASR 老錯誤模式）
   ```
   Qwen:  ...有一些辛酸血泪是不是...
   WX-v2: ...有一些心酸血淚是不是...
   ```

3. **「Traveling Village」整段名詞→「超柏林部落」hallucination**（podcast 核心話題）
   ```
   Qwen:  ...這個社群叫做超柏林部落然後它是由丹麥...
   WX-v2: ...這個社群叫做Traveling Village然後它是由丹麥...
   ```

4. **「Hell Yes」→「Hello」**（開頭把講話前雜音認成英文）+ 中段 Hell 識別不一致

5. **「本本尊」重複 bug**（同 FunASR 老 bug）
   ```
   Qwen:  ...所以現在就本本尊來幫我們...
   WX-v2: ...所以現在就本尊來幫我們...
   ```

6. **「花莲花莲」重複 bug**（8 處，同 WhisperX-v2 但更多次）

7. **全篇簡體輸出**（无 s2twp 後處理；可後製救但不該由消費端負責）

### 結論

**Qwen3-ASR-1.7B 不適合台灣 podcast 工況**。

教訓對應 ADR-013：
> AIShell1 = 朗讀普通話 + 零 code-switch + 零台灣腔 + 純中文 vocabulary。**完全不對應**台灣 podcast 訪談工況。

Qwen3-ASR 同樣問題：阿里大廠訓練資料偏 PRC 普通話 + 大陸用詞，**台灣腔 + 在地名詞（花蓮、Traveling Village、數位遊牧、本尊）系統性失誤**。Mandarin WER 4.97（WenetSpeech net）的 paper benchmark 跟我們業務工況解耦。

## 三方總評

| 排名 | 引擎 | 適合 nakama 工況？ |
|---|---|---|
| 🥇 | **WhisperX (Whisper Large V3)** | **✅ ship** — 76× RT、prompt-leak 已修、繁體 s2twp、英文 code-switch + 在地名詞贏 |
| 🥈 | MemoAI (Whisper V2) | ✅ 平手 — 但這是 Whisper 親戚 |
| 🥉 | Qwen3-ASR-1.7B | ❌ 不適 — paper benchmark 漂亮但台灣 podcast 工況失敗，且慢 7× |
| 退場 | FunASR Paraformer-zh | ❌ ADR-013 已決 |

## 下一步

### WhisperX 路徑收尾

1. **詞邊界 16 處還沒清** — 看 jieba 累加邏輯為何沒 cover「然後 / 馬上 / 這個人」這類常見 bigram/trigram
2. **「花蓮花蓮」within-segment 重複回歸** — `condition_on_previous_text=False` 副作用，可能要靠 post-process dedupe
3. **Cue avg 字數從 11.5 飆到 18.0** — pack 太滿，看 force_break trigger 條件是否要調

### LLM 校正 + Gemini 仲裁天花板驗證（修修待做）

至此只跑了**裸 ASR**，PR #274 後沒跑過 full pipeline。修修待做：
```
python scripts/run_transcribe.py tests/files/20260415.wav --no-auphonic
# 含 Opus 校正 + Gemini 仲裁，看天花板能拉多高
```

### Qwen3-ASR

放棄當主 ASR。**仍可考慮 Qwen2.5-Omni-7B 作多模態仲裁層替代 Gemini 2.5 Pro**（benchmark 維度不同 — Omni 是 audio understanding 不是純 ASR；本測試結論不直接套用）。
