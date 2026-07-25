---
name: resolve-project
description: >
  字幕校正完成後一鍵生成 DaVinci Resolve 專案：project 名稱 = episode 資料夾名
  （如 20260723 謝伯讓），timeline 上已擺好主影片（program feed）與校正後字幕
  （subtitle 軌），六機位進 Cameras bin、音軌進 Audio bin。Use when the user says
  「進 DaVinci」「建 Resolve 專案」「resolve-project」「把字幕放上 timeline」,
  or after subtitle-correct completes in the podcast pipeline. QC 裁決後更新字幕
  用 --refresh-subtitles。
---

# resolve-project — 一鍵建 DaVinci Resolve 專案

`scripts/build_resolve_project.py`（repo：`E:\nakama`）的互動包裝。
透過 **Resolve Studio 外部 Scripting API** 直接操作執行中的 Resolve。

## 前提（跑之前確認）

1. **DaVinci Resolve 正在執行**（外部 scripting 需要 app 開著）——沒開請修修開
2. Studio 版（已確認修修是 Studio 20.3）；Preferences → System → General →
   External scripting using = Local
3. episode 已有 `transcript.srt`（subtitle-correct 產出）
4. ⚠️ 執行會**切換 Resolve 當前 project**——若修修正在別的 project 工作中，先問一聲

## 執行

```
C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe ^
  E:\nakama\scripts\build_resolve_project.py "<episode 資料夾>"
```

- `--dry-run` 先看計畫（主影片選擇、機位、音軌清單）
- 主影片自動選 episode 根目錄 `Default_*.mp4`（program feed），`--video` 可覆寫
- 冪等：project / timeline 同名已存在會跳過重建
- 產出佈局：timeline（同 project 名）V1 = 主影片；subtitle 軌 = transcript.srt；
  media pool `Cameras` bin = Video/ 全機位、`Audio` bin = Audio/ 全音軌

## QC 裁決後刷新字幕

修修裁決 QC → 更新 `corrections.json` → `run_subtitle_correct.py --apply` 重產
transcript.srt → 然後：

```
... build_resolve_project.py "<episode>" --refresh-subtitles
```

只換字幕軌不動其他（1.3 秒）。技術註記：Resolve media pool 依檔案路徑快取，
所以每次匯入用 `subs/resolve_subs/transcript_rNNN.srt` 版本化複本——**這些複本
不可刪**（media pool item 引用中）。

## 完成後回報

讀 script 輸出 JSON：回報 project 名、字幕句數（`subtitle_items` 應等於
transcript.srt 的 cue 數）、字幕是否自動上軌。用 API 讀回第一/最後一句抽驗。
