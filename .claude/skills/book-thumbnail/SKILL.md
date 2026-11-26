---
name: book-thumbnail
description: >
  主頻道（@shoshotw）YouTube 影片——書籍介紹（youtube_book）與主題介紹（youtube_explanation）——
  的標題與封面：從逐字稿發想標題＋封面大字，備好修修去背照／主圖（書封或主題圖）／授權背景，
  放進 vault 後由修修在 Bridge `/bridge/youtube-packaging/<slug>` 組封面、選上架標題。
  Triggers:「主頻道封面」「書籍介紹封面」「主題介紹封面」「做《X》的影片封面」「想這支 YouTube 的標題」。
  不要用在：podcast 訪談封面（那是 thumbnail-brainstorm）、Shorts（不做封面）。
---

# book-thumbnail — 主頻道 YouTube 影片標題＋封面（v1.0，2026-11-12）

**沒有上游觸發**（修修 2026-11-12：YouTube 製作 pipeline 之後再設計，接下來幾支都手動叫 agent 做）。
本 skill 負責「發想＋備料＋放進 vault」；組封面、選標題、出圖都在 Bridge。
`thumbnail-brainstorm` 對 `youtube_book`／`youtube_explanation` 會直接 fail loud 指回這裡。

## 系統分工

| 誰 | 做什麼 | 落點 |
|---|---|---|
| 本 skill（agent） | 發想標題與大字、備素材、建起手配方 | vault `Attachments/packaging/<slug>/`：`packages.json`、`full/bigtext.json`、`assets/`、`youtube_cover.json`、`title_tracking.json` |
| Bridge `/bridge/youtube-packaging/<slug>` | 修修拖曳／縮放、換臉／主圖／背景／外框、改大字、微調標題、勾已上頻道、填影片網址；存配方 | `youtube_cover.json`（`requested_at`）、`title_tracking.json` |
| 桌機 `scripts/render_watcher.py` | 看到新配方 → `scripts/render_youtube_cover.py` 出圖 | `cover.png`＋`cover-320.png`；回填 `rendered_at` |

Packaging 列表 `/bridge/packaging` 遇到 YouTube 集數會自動連到 youtube-packaging 頁。
版型（Bridge 預覽與桌機出圖共用）：`thousand_sunny/static/shosho/youtube-cover.{js,css}`；
schema：`shared/schemas/youtube_packaging.py`。

## 版面（修修 2026-11-12 定的方向）

| 元素 | 規則 |
|---|---|
| 人 | 佔畫面 1/3–1/2；去背照庫 vault `Attachments/cutouts/shosho/<表情>/*.png`（2026-11-12 全庫已換 BiRefNet）；品牌橘光暈 |
| 主圖 | 書籍介紹＝實際書封；主題介紹＝主題圖。不能太小（預設高 520/720）；外框 7 選 1（斜切框／爆裂碎片／錯位色塊／雙層框／階梯／角框／無），全是品牌橘幾何 |
| 背景 | stock photo 壓暗＋暖色疊層；**只放授權檔**（Envato 預覽有浮水印、600px，不准進 vault） |
| 大字 | 兩層：上行小字／主標大字（恰好一個橘框詞）；整塊斜放（預設 -6°）；可以疊在人和主圖上；不放英文行 |
| Logo | 不放 |
| 色 | 橘 `#F37425` 只給橘框詞、外框、碎片、光暈、上下橘條；其他黑白灰 |

## 流程

0. **逐字稿來源先問清楚**：同一本書常有多版稿（例：《財富階梯》v2／v3／定稿），要用**實際照著錄的那一版**。
   不確定就停下來問。整理逐字稿：

```bash
python .claude/skills/book-thumbnail/scripts/prepare_transcript.py "<逐字稿.md>" --packaging-dir "<work>/packaging"
```

1. **標題**：照 `title-brainstorm` 跑，批次落檔用它的 `emit_packages.py`，**`VAULT_PATH` 要設**（才會寫進 vault）：
   `episode`＝footage 資料夾名、`episode_slug` ASCII、`cut_id: "full"`、`format: "long"`、
   `information_origin: "full_text"`、`visual_recipe: "youtube_book"` 或 `"youtube_explanation"`。
   - ⚠️ Windows 一律 `python -X utf8` 跑 emit（否則 stdin 被當 cp1252，且先留下 0 byte 的 keywords.json）。
   - **關鍵字研究一定要看同主題／同書競品**：撞角度的標題直接淘汰。
   - **A/B 測試用的標題要押不同點擊動機**（修修 2026-11-12）：例如 A 身分＋方法／B 本人故事／C 代價恐懼；
     方向太像，測完只會得到「差不多」。結尾統一後綴（書名或主題），讓前段鉤子是唯一變因。每條 19–30 字、只放一件事。
   - **persona 不能拿來排序主頻道標題**（2026-11-12 校準：頻道 74 萬觀看的爆款被三位 persona 判標題黨淘汰）。
     persona 只篩「看不懂／兌現不了」，排序交給 YouTube 實測。寫法參考頻道爆款原型
     （第一人稱親身經驗、作者／專家頭銜、具體數字、括號反轉、「沒背景的普通人」式對號入座）。
   - 修修實測受眾（2026-11 Analytics）：35–44 歲最多、25–34 次之、45–54 約兩成；男 63%；一半流量來自訂閱者。
2. **封面大字**：2–4 組（上行 ≤10 字／主標 2–7 字／主標裡恰好一個橘框詞），大字是第二個鉤子、不與標題同義、
   不替觀眾下診斷。落檔：`python -X utf8 .claude/skills/book-thumbnail/scripts/emit_bigtext.py "<vault>/Attachments/packaging/<slug>" < bigtext_input.json`
3. **素材**（缺任何一項就停下來問，不腦補）
   - 主圖：書籍介紹用出版社／書店／修修提供的實際書封；主題介紹用授權主題圖
   - 背景：Elements MCP `search_photos` 挑概念 → **到 `app.envato.com` 下載授權檔**（Elements 品項頁卡死；
     找不到同一張時，用 Elements 頁面標題（tab title）＝ app 網域改寫後的名稱去搜）→ 按一次 Download，落點 `E:\`
4. **放進 vault**（主圖、背景、起手配方；配方 `requested_at` 已設，桌機 watcher 會先出一張）：

```bash
python -X utf8 .claude/skills/book-thumbnail/scripts/init_youtube_packaging.py \
  --episode-slug <slug> --episode "<footage 資料夾名>" --feature "<書封或主題圖>" --bg "<授權背景>" [--bg ...]
```

5. **請修修到 Bridge** `/bridge/youtube-packaging/<slug>` 調整並按「存配方並出圖」；〈標題〉微調文字、
   上片後勾「已上頻道」（＝參加 A/B，最多 3 條）、貼影片網址。
6. **上片＋標題 A/B**（support.google.com/youtube/answer/16391400）：Studio 影片「詳細資料」標題框 →
   「A/B Testing」→「Title only」→ 最多 3 個標題 → Done。只限長片；測試中改標題或封面會中止；
   幾天到兩週出結果，勝負看觀看時間佔比；**API 拿不到各標題分組數據**，要到 Studio 讀。
7. **7 天後追蹤**：用影片網址排一次性排程任務（scheduled-tasks），抓 `scripts/youtube_analytics_fetch.py --channel main`
   ＋reach 報表的曝光／點閱率，A/B 狀態到 Studio 讀，報告寫在 vault 本集目錄。

## 已知缺口

- 沒有上游觸發與專案連結（等 YouTube 製作 pipeline 設計）
- 沒有量測 gate（臉高、遮蔽平衡）；靠修修在 Bridge 目視調整
- 本機原型 `editor/`（port 8765）保留作離線備援，資料寫在本機工作資料夾、Bridge 看不到——正式流程用 Bridge
