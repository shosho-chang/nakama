---
name: Script-Driven Video — 修修錄影 cue workflow 設計（2026-05-02 凍結）
description: 失敗拍兩下、成功不用 cue + 4 frame lead-in buffer + voice onset 偵測（LPF + 3 consecutive guard）+ 連續 marker cascade merge
type: project
created: 2026-05-02
---

修修 2026-05-02 grill 凍結 Slice 1 mistake removal cue workflow。設計選擇從原 ADR-015 §Q3 「拍掌作 NG marker，cut lookback」翻轉。

## Cue scheme

| 場景 | 動作 | 演算法處理 |
|---|---|---|
| 唸到一半失敗 | 立刻**拍兩下** | 砍 [失敗 take voice 起點, retake voice 起點 − 4 frame] |
| 唸完成功 | **不用 cue** — 直接進下一段 | 自然保留，成功 voice 留到下一段或結尾 |
| 連續失敗 retake | 每次失敗都拍兩下 | cuts overlap → fcpxml_emitter._build_segments cascade merge |

## 為什麼不加成功 cue

修修 propose 過「失敗拍一拍 / 成功拍兩拍」分流。**否決理由**：
- 錄影 cognitive load 越低越好（修修要專心唸稿）
- 「成功」是預設狀態，不需要 cue
- 演算法靠 voice activity detection 對齊邊界，雙拍偵測既有不動

## Cut 範圍演算法

```
For each double-clap marker M:
    search_start = previous_marker.clap_end_sec if exists else 0
    voice_before = find_voice_onset(audio, search_start, M.clap_start)  # 失敗 take voice 起點
    cut_start = voice_before if found else search_start
    
    search_end = next_marker.clap_start_sec if exists else audio_duration
    voice_after = find_voice_onset(audio, M.clap_end, search_end)  # retake voice 起點
    cut_end_voice = voice_after if found else search_end
    cut_end = max(M.clap_end_sec, cut_end_voice − 4/fps)  # 留 4 frame lead-in buffer
    
    cuts.append([cut_start, cut_end])

# 不在 detect_clap_markers 內 merge — overlapping cuts 由 fcpxml_emitter._build_segments 在 emit 時 merge
```

## Voice onset 偵測

`_find_voice_onset(audio, sr, start_sec, end_sec, rms_threshold) -> float | None`

- LPF 3kHz（去 clap 高頻能量，留 voice 100-3000Hz formants）
- 30ms RMS window，10ms hop
- Threshold default 0.005（~ −46 dBFS，clean recording 適用，soft voice 可調低）
- **3 consecutive frame guard**：必須連 3 個 window 都 > threshold 才算 onset，rejects:
  - clap impulse residue + filter ringing（衰減快，1-2 frame 就 sub-threshold）
  - 咳嗽 / 椅子嘎吱 等短暫雜音

## Lead-in buffer

- Default 4 frames @ 30 fps ≈ 133ms
- 不用「嘎然而起」也不留太長空白
- 從 `lead_in_frames` 參數傳入，未來 manifest 可調

## 參數化點

`detect_clap_markers(audio_path, *, fps=30, lead_in_frames=4, voice_rms_threshold=0.005, ...)`：
- `fps` — 跟 manifest 對齊（Slice 1 鎖死 30）
- `lead_in_frames` — 修修可調 buffer 大小
- `voice_rms_threshold` — 環境噪音大時降低
- `max_clap_gap_sec` (default 0.30s) — 雙拍 inner gap，固定不調

## 雙拍偵測（不變）

- 高通 3kHz Butterworth 4-th order
- 5ms hop / 10ms RMS frame
- `find_peaks` height = 0.3 × max_energy, distance ≥ 50ms
- 配對 gap ≤ 300ms → `_MarkerBounds(midpoint, clap_start, clap_end)`

## 已凍結 / 未來 follow-up

**已凍結**：
- ✅ 失敗拍兩下（不需 user training，反射性）
- ✅ 成功不用 cue
- ✅ 4 frame lead-in (133ms @ 30fps)
- ✅ Voice onset LPF + 3 consecutive guard

**Slice 2+ follow-up**：
- WhisperX-derived `total_frames` 取代 `wave.open()` overwrite（pipeline.py L172-185）
- WhisperX 對齊驗證下一段「真的開始講」位置（比 RMS energy 更可靠）
- 修修錄音常用 environment noise level → tune `voice_rms_threshold`
- 導出參數到 manifest top-level（讓修修不改 code 只改 script.md frontmatter 即可調）

## 跟 ADR-015 的關係

ADR-015 §Q3 原寫「拍掌標 NG，alignment fallback (β) Slice 2 補」— 但 cut 範圍方向沒定義。本次 grill 凍結成「failed take + clap + 構思 silence 全砍，retake 留 4 frame lead-in」，**ADR-015 之後 supersession 段需要補一句「Slice 1 cut semantics override」**（看下次摸 ADR 時順手補）。

## 相關記憶

- [project_script_video_phase2a.md](project_script_video_phase2a.md) — Phase 進度 + PR #320 head 1d7ad8d
- [feedback_e2e_smoke_for_subprocess_pipelines.md](feedback_e2e_smoke_for_subprocess_pipelines.md) — Phase 5 mac e2e 抓 5 bug 教訓
- ADR-015：[docs/decisions/ADR-015-script-driven-video-production.md](../../docs/decisions/ADR-015-script-driven-video-production.md)
- Runbook：[docs/runbooks/2026-05-02-davinci-import-smoke.md](../../docs/runbooks/2026-05-02-davinci-import-smoke.md)
