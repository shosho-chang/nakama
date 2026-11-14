---
name: resolve-project
description: >
  Memo Dual-Audit 字幕 release（ADR-063）完成後一鍵生成 DaVinci Resolve 專案：project 名稱 = episode 資料夾名
  （如 20260723 謝伯讓），timeline 上已擺好主影片（program feed）與校正後字幕
  （subtitle 軌），六機位進 Cameras bin、音軌進 Audio bin。Use when the user says
  「進 DaVinci」「建 Resolve 專案」「resolve-project」「把字幕放上 timeline」,
  or at podcast-pipeline S7 after the Memo Dual-Audit release handoff is ready.
  字幕 release 更新後用 --refresh-subtitles。
---

# resolve-project — 一鍵建 DaVinci Resolve 專案

`scripts/build_resolve_project.py`（repo：`E:\nakama`）的互動包裝。
透過 **Resolve Studio 外部 Scripting API** 直接操作執行中的 Resolve。

## 前提（跑之前確認）

1. **DaVinci Resolve 正在執行**（外部 scripting 需要 app 開著）——沒開請修修開
2. Studio 版（已確認修修是 Studio 20.3）；Preferences → System → General →
   External scripting using = Local
3. episode 已有 Memo Dual-Audit 字幕 release（podcast-pipeline S6 完成）：
   `<episode>/subtitle-release/memo-dual-audit-v1/STAGE5-HANDOFF.json` 存在
4. 直譯器一定是 `E:\nakama\.venv-v2\Scripts\python.exe`（3.12）——3.10／3.14 會在
   `import DaVinciResolveScript` 當下崩潰
5. ⚠️ 執行會**切換 Resolve 當前 project**——若修修正在別的 project 工作中，先問一聲

## 執行

不傳任何字幕 flag：`build_resolve_project.py` 預設就會找並驗證上面那份
`STAGE5-HANDOFF.json`（`--subtitle-release-handoff` 只在 handoff 不在預設位置時才用）。
先 dry-run，再實跑：

```powershell
$env:RESOLVE_SUBTITLE_TEMPLATE = "E:\nakama\data\resolve\subtitle-template.drt"
if (-not (Test-Path -LiteralPath $env:RESOLVE_SUBTITLE_TEMPLATE)) { throw "Resolve subtitle template missing" }
E:\nakama\.venv-v2\Scripts\python.exe scripts/build_resolve_project.py "<episode>" --dry-run
E:\nakama\.venv-v2\Scripts\python.exe scripts/build_resolve_project.py "<episode>"
```

`--projection-id`／`--expected-*`／`--reference-manifest` 是舊 Podcast Subtitle V2 的參數，
`--degraded-release-handoff` 只屬抹布舊 bundle；新集都不要用（見 podcast-pipeline
「Explicit legacy forensic only」）。主影片上軌回 `[None]` 的排錯見 podcast-pipeline S7–S8 節。

- `--dry-run` 先看計畫（主影片選擇、timeline 音軌來源、機位、音軌清單）
- 主影片自動選 episode 根目錄 `Default_*.mp4`（program feed），`--video` 可覆寫
- 冪等：project / timeline 同名已存在會跳過重建
- 產出佈局：timeline（同 project 名）V1 = 主影片（純視訊）；A1 = 根目錄
  `normalized.wav`（Auphonic 處理後、與錄影同起點；沒有時退回影片內嵌音軌）；
  subtitle 軌 = Memo Dual-Audit release SRT 的**exact 顯示層定版副本**；不得再套 V1 的
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

## 字幕 release 更新後刷新字幕

Memo Dual-Audit release 重新 finalize、`STAGE5-HANDOFF.json` 更新後，只刷新既有 timeline
的字幕內容（同樣預設讀那份 handoff，不能使用舊 `transcript.srt`）：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts/build_resolve_project.py "<episode>" --refresh-subtitles
```

Intro／Outro 剪進完整節目之後的字幕上軌（ST1 重建）不是這條，走 podcast-pipeline S7E 的
`scripts/resolve_subtitle_rebuild.py`。

只換字幕軌不動其他（1.3 秒）。技術註記：Resolve media pool 依檔案路徑快取，
所以每次匯入用 `subs/resolve_subs/transcript_rNNN.srt` 版本化複本——**這些複本
不可刪**（media pool item 引用中）。

## 完成後回報

讀 script 輸出 JSON：回報 project 名、字幕 handoff 來源、字幕句數（`subtitle_items`
應等於 release SRT 的 cue 數）、字幕是否自動上軌。用 API 讀回第一/最後一句抽驗。

## Legacy forensic only

`--legacy-v1` 只可在使用者明確要求 `explicit legacy forensic` 時比較舊 episode；不得用它
建立任何新集的 production timeline。
