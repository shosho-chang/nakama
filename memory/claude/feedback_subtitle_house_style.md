---
name: 字幕 house style 與時間軸鐵則
description: 修修 2026-07-25 驗收裁決 — 書名《》/專有名詞「」必標、其他標點省略；斷句必須語意/詞邊界（禁字數硬切）；字幕時間軸必須等於原始錄影（禁靜音裁切預設）
type: feedback
created: 2026-07-25
---

修修 2026-07-25 對字幕產線首次真檔驗收的三條裁決，全部是 house style 級規則：

1. **《》「」必標、其他標點省略**：書名／作品名必須用《》標出、專有名詞／術語必須用「」標出（校正時要**主動補上**，幫助讀者閱讀）；逗號句號等其他標點照舊省略、停頓用半形空格。⚠️ 這條**推翻** PR #23 時代「全部標點都刪」的舊 house style——`_ZH_MID_PUNCTUATION`、`subtitle_correct._PUNCT_RE` 已停止清除《》「」，改動這兩處前先想到這條。
2. **斷句禁止字數硬切**：「你沒有判斷那個句子是不是完整句子，就直接用字數硬切嗎？」——cue 必須語意/詞邊界斷句。實作 = `shared/cue_builder.py`：字級真實時間戳 + jieba 詞邊界（詞不切半、跨界 bigram 成詞回退切點）+ 語音停頓優先 + 《》「」內絕不切 + 14/22 軟硬上限，時間戳零內插。舊 `_whisperx_to_srt`（線性內插硬切）不要再用在新路徑。
3. **時間軸鐵則**：字幕時間軸必須與**原始錄影完全一致**（要對回 DaVinci 的原始影片）。頭尾靜音裁切預設關（`--trim-silence` opt-in、僅純音訊用途）；Auphonic Jingle 裁切保留（那是免費方案「外加」的 12 秒，裁掉才還原原始時間軸）。任何會位移時間軸的處理都先想到這條。

**Why:** 第一輪產出被打回：「這三句慘不忍睹」（先請/教老師 型切爛句）＋「為什麼把之前的靜默都刪掉了？我有要求這個事情嗎？這樣子就會跟原本的影片對不起來啊」。註：靜音裁切當初確實在規劃對話中提過，但「對回影片」的後果使它必須是 opt-in——規劃時要主動想到下游對齊需求。

**How to apply:**
- 動 cue 切分邏輯 → 跑 `tests/test_cue_builder.py` 全套 + 抽真檔 SRT 人眼掃前 20 cue
- cue 參數迭代用 `run_subtitle_gen.py --recue`（吃 `subs/aligned_segments.json`，零 GPU）
- 相關：[[feedback_chinese_srt_word_boundary_jieba]]（jieba 詞邊界的前身教訓）、[[feedback_subscription_first_no_api_spend]]（校正走 subagent）
