# Mistake removal v2 + 修修新錄影協議（paragraph takes）

- **type**: project
- **created**: 2026-07-30
- **status**: cleanup v2 已開發完成，等第一支 paragraph 協議素材驗證後才 PR/merge

## 修修的新錄影協議（2026-07-30 拍板，下支影片起生效）

1. 逐字稿以**空行分段**（paragraph = 空行分隔的區塊）
2. 一次唸完一整段才算成功；唸完才進下一段
3. 段內任何地方出錯 → **拍一下手** → 停一拍 → 從**該段第一句**重唸
4. 臨時加講的內容（ad-lib）也自成一段：講完整段，錯了拍手整段重來
5. 收尾後多留 2 秒再停機（血淚：2026-07-30「頻道復出」檔案在句中被切斷）

演算法意義：重錄起點永遠是已知的段落開頭 → 否決範圍決定性、驗證變成
「每段的保留 take 完整且是最後一次」的機器可判定不變量。

## cleanup v2 現狀

- **Branch**: `claude/video-editing-pipeline-test-7db414`，commits `6030b6e` + `7917440`
- 模組：`agents/brook/script_video/cleanup/{clap_impulse,script_coverage}.py`；
  一鍵產線 `scripts/run_mistake_removal.py`（footage 資料夾 → Resolve timeline，
  待裁決 exit 3 → 補 `adjudications.json` 重跑）；`scripts/build_cleanup_timeline.py`
- 核心：拍手物理偵測（近滿刻度＋前後靜音＋寬頻）→ 逐字稿 coverage 選最終
  take（拍手否決硬約束、OpenCC t2s、NMS、縫隙吸收、停頓收斂）→ DoD 驗證
  （missing_script_content / hot_cut_boundaries / 重複覆蓋 / 拍手去向）
- **實測結論（舊自由式重錄素材）**：文字層驗證全綠但修修聽成品仍「亂七八糟」
  — WhisperX 字級時間戳在重錄邊界區崩壞（31 字/秒壓縮、16s 漏轉、跨拍手
  拉長字母）是根本限制，補丁修不完。**自由式素材不要再試自動剪**；
  paragraph 協議素材才是 v2 的目標場景
- 下一步（拿到第一支新協議素材時）：加逐段完整性驗證（每 paragraph 的
  保留 take 完整且是最後一次），跑通後才開 PR

## Resolve scripting 陷阱（21.0.3，實測）

- `ImportTimelineFromFile(cleanup.fcpxml)` 回 None（FCPXML 匯入失敗）；
  `ripple_fcpxml._FPS` 硬寫 30fps 對 29.97 素材會漂移 → 用 MediaPool
  `AppendToTimeline` subclips（startFrame/endFrame，素材原生 fps）
- 同 process「DeleteTimelines + 重建」後 append 字幕**靜默落空**（回傳成功、
  track 0 items），子行程重試/等 45s+ 也可能不好 — fallback：media pool
  對 transcript_rNNN 右鍵 → Insert Selected Subtitles to Timeline
- memory 直 push main 已被 branch protection 擋（須 PR）— CLAUDE.md
  「可直 push main」段落已過時
