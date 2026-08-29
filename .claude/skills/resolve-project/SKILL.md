---
name: resolve-project
description: >
  Podcast Subtitle V2 Verified Projection 完成後一鍵生成 DaVinci Resolve 專案：project 名稱 = episode 資料夾名
  （如 20260723 謝伯讓），timeline 上已擺好主影片（program feed）與校正後字幕
  （subtitle 軌），六機位進 Cameras bin、音軌進 Audio bin。Use when the user says
  「進 DaVinci」「建 Resolve 專案」「resolve-project」「把字幕放上 timeline」,
  or after Podcast Subtitle V2 projection completes in the podcast pipeline. QC 裁決後更新字幕
  用 --refresh-subtitles。
---

# resolve-project — 一鍵建 DaVinci Resolve 專案

`scripts/build_resolve_project.py`（repo：`E:\nakama`）的互動包裝。
透過 **Resolve Studio 外部 Scripting API** 直接操作執行中的 Resolve。

## 前提（跑之前確認）

1. **DaVinci Resolve 正在執行**（外部 scripting 需要 app 開著）——沒開請修修開
2. Studio 版（已確認修修是 Studio 20.3）；Preferences → System → General →
   External scripting using = Local
3. episode 已有已驗證的 Podcast Subtitle V2 projection；準備好 projection ID、generation
   ID、episode ID、projection manifest SHA-256 與同一份 Reference Manifest
4. ⚠️ 執行會**切換 Resolve 當前 project**——若修修正在別的 project 工作中，先問一聲

## 執行

```powershell
$env:RESOLVE_SUBTITLE_TEMPLATE = "E:\nakama\data\resolve\subtitle-template.drt"
if (-not (Test-Path -LiteralPath $env:RESOLVE_SUBTITLE_TEMPLATE)) { throw "Resolve subtitle template missing" }
py -3.10 scripts/build_resolve_project.py "<episode>" `
  --projection-id "<projection-id>" `
  --expected-episode-id "<episode-id>" `
  --expected-generation-id "<generation-id>" `
  --expected-manifest-sha256 "<projection-manifest-sha256>" `
  --reference-manifest "<episode>/subtitle-v2/episode-references.v2.json"
```

- `--dry-run` 先看計畫（主影片選擇、timeline 音軌來源、機位、音軌清單）
- 主影片自動選 episode 根目錄 `Default_*.mp4`（program feed），`--video` 可覆寫
- 冪等：project / timeline 同名已存在會跳過重建
- 產出佈局：timeline（同 project 名）V1 = 主影片（純視訊）；A1 = 根目錄
  `normalized.wav`（Auphonic 處理後、與錄影同起點；沒有時退回影片內嵌音軌）；
  subtitle 軌 = Verified Projection SRT 的**exact 顯示層定版副本**；不得再套 V1 的
  句尾／補隙 rewrite。media pool `Cameras` bin = Video/
  全機位、`Audio` bin = Audio/ 全音軌

## 既有 timeline 換音軌

早期建的 timeline 音軌是影片內嵌音軌時，換成 normalized.wav：

```
... build_resolve_project.py "<episode>" --swap-audio
```

解除 video-audio link → 移除內嵌音軌 clip → normalized.wav 放 timeline 起點
（前提：影片與錄音開錄點一致——修修 2026-07-25 確認本流程如此）。

## 字幕樣式 preset（一次性設定，之後每集自動）

Scripting API **不開放** subtitle style preset，樣式靠 **DRT 模板**攜帶：

1. 修修在任一 episode timeline 手動套 preset：點字幕軌任一句 → Inspector →
   Track Style → 選「Shosho YT」→ Apply to Track（唯一手動步驟）
2. 跑 `--make-template`：複製該 timeline → 清空內容留樣式軌 → 匯出
   `data/resolve/subtitle-template.drt`（env `RESOLVE_SUBTITLE_TEMPLATE` 可覆寫路徑）
3. 之後 build 自動偵測模板：timeline 從模板長出（字幕軌自帶 Shosho YT 樣式）
   → 改名 → 填影片與字幕。模板不存在則退回無樣式建立

模板是**本機檔案**（`data/*` gitignored，不進 git）——只在有 Resolve 的機器有意義。
遺失時重做步驟 1–2 即可重建（已建過樣式軌的任何 timeline 都能當來源）。

⚠️ 樣式掛在「軌」上——任何流程都**不可刪字幕軌重建**（refresh 已改為只清內容）。

## V2 QC 裁決後刷新字幕

修修在 Podcast Subtitle V2 `review`／`decide-native` 完成裁決後，重新 `project` 產生新的
Verified Projection。刷新時仍需帶新的 projection lineage，不能使用舊 `transcript.srt`：

```powershell
py -3.10 scripts/build_resolve_project.py "<episode>" --refresh-subtitles `
  --projection-id "<new-projection-id>" `
  --expected-episode-id "<episode-id>" `
  --expected-generation-id "<new-generation-id>" `
  --expected-manifest-sha256 "<new-projection-manifest-sha256>" `
  --reference-manifest "<episode>/subtitle-v2/episode-references.v2.json"
```

只換字幕軌不動其他（1.3 秒）。技術註記：Resolve media pool 依檔案路徑快取，
所以每次匯入用 `subs/resolve_subs/transcript_rNNN.srt` 版本化複本——**這些複本
不可刪**（media pool item 引用中）。

## 完成後回報

讀 script 輸出 JSON：回報 project 名、projection lineage、字幕句數（`subtitle_items`
應等於 Verified Projection SRT 的 cue 數）、字幕是否自動上軌。用 API 讀回第一/最後一句抽驗。

## Legacy forensic only

`--legacy-v1` 只可在使用者明確要求 `explicit legacy forensic` 時比較舊 episode；不得用它
建立任何新集的 production timeline。
