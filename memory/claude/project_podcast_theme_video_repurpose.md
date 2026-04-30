---
name: Podcast 主題影片自動剪輯（Line 1 補位）
description: 訪談錄音 → LLM 抽亮點 → 自動剪 10-20 min 主題影片 + SEO Title 候選 + YT Description + Thumbnail；Line 1 的 YouTube 通路
type: project
created: 2026-04-30
---

修修 2026-04-30 補上 Line 1 漏的功能：訪談錄音不只變 blog/FB/IG，**還要剪成 YouTube 主題影片**。

## 需求

**Source**：1 小時左右人物訪談錄音（同 Line 1 source）

**Pipeline**：
1. 訪談 SRT（既有 `transcribe` skill production）
2. **LLM 分析**整段訪談，抽出某個亮點 / 主題段落
3. **自動剪接**該主題相關片段，拼湊成 **10-20 分鐘專題影片**
4. 產出四件套：
   - **影片本身**（剪好的 10-20 min mp4）
   - **Title 候選**（多個，SEO + 高點擊潛力）
   - **YouTube Description**
   - **Thumbnail 縮圖**

## 四件產出細節

| 產出 | 規格 | 工具/參考 |
|------|------|---------|
| 影片 | 10-20 min mp4，自動剪接 | ffmpeg + 時間戳對齊 SRT；可能整合 yt-dlp / moviepy / Sieve |
| Title 候選 | 多個（≥3），SEO 友善 + CTR 取向 | Brook compose + keyword-research / seo-keyword-enrich 數據 |
| YT Description | 含 chapter timestamp + 連結 + 標籤 | Brook compose template |
| Thumbnail | 視覺品質高，**獨立大專題** | 詳見 [project_brook_image_pipeline.md](project_brook_image_pipeline.md)，下一輪深入 |

## 與既有 Line 1 關係

[project_three_content_lines.md](project_three_content_lines.md) Line 1 原本只列了三 channel：
- 訪談 blog 文章
- FB 社群媒體貼文
- IG Carousel 文字序列

**現在補上第四個 channel：YouTube 主題剪輯影片**。注意這個是「主題抽取式」repurpose，不是「整段重發」 — LLM 要主動找亮點不是被動切片。

## 關鍵設計問題（待後續討論）

1. **主題選定**：LLM 自己選 vs 修修指定 vs 兩段式（LLM 提候選 → 修修選）
2. **剪接策略**：
   - 純照 SRT 時間戳剪音訊（最簡）
   - 對齊聲學波形 forced alignment（避免切到字中）— 參考 feedback_retime_text_search_failure_mode
   - 加 B-roll / 字幕貼圖
3. **影片長度控制**：10-20 min 是硬目標還是軟目標
4. **多主題輸出**：一場訪談能不能產 N 個主題影片
5. **Speaker label**：只剪受訪者話、剪訪問者問、還是兩個都要
6. **音訊處理**：Auphonic 已經做過，剪接後是否要再過一次

## 候選 skill / 工具

- 既有 `transcribe`（FunASR + Auphonic + Claude 校正）— SRT input 來源
- 既有 SRT 對齊工具（[project_srt_align_tool.md](project_srt_align_tool.md)）— 時間戳精準對齊
- `coreyhaines31/marketingskills`：
  - **content-strategy** — 主題候選評估
  - **social-content** — Title 風格框架
- `coreyhaines31/marketingskills` + 既有 `keyword-research` / `seo-keyword-enrich` — Title SEO 數據驅動
- 需 ffmpeg / moviepy / yt-dlp 相容 skill（**目前 prior-art 沒找到專屬剪輯 skill，可能要自刻**）
- Composio **YouTube Automation** — 上架自動化

## How to apply

1. 不在 Line 1 第一輪 PRD 範圍 — Line 1 起手先做 blog/FB/IG 三 channel
2. 但 **Line 1 PRD 的 repurpose engine 設計要預留 `target: youtube_theme_video` 接口**，不寫死三 channel
3. Thumbnail 等 [project_brook_image_pipeline.md](project_brook_image_pipeline.md) 下一輪定設計後再接
4. 主題抽取的 LLM prompt 是核心 — 跟 Line 2 讀書心得「找亮點」邏輯類似，可考慮共用 building block
