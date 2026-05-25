---
name: project-broll-dual-path-architecture
description: B-roll 生成走兩條 sibling path — 書/網頁 quote 走 Reader+Playwright+CSS（user-supply-text，不 picker）、非書頁類（BigStat/Transition/圖表/caption）走 Hyperframes；2026-05-25 凍結，取代 ADR-015 原計畫的 PyMuPDF+Hyperframes DocumentQuote 單一路線
metadata:
  type: project
---

**架構決定 2026-05-25**（Reader-Playwright spike v8 + web_highlight_record.py 驗收後）：B-roll 生成不是 Hyperframes 一條路，是 **兩條 sibling path by scene type**。

## 分派表

| Scene type | 走哪條 | 工具 |
|---|---|---|
| 書內引用（DocumentQuote 類） | Reader + Playwright + CSS | `web_highlight_record.py` |
| 外部文章引用 | Web + Playwright + CSS | 同上 |
| BigStat / 巨大數字 | Hyperframes | fork `apple-money-count` |
| TransitionTitle / 章節大標 | Hyperframes | `transitions-cover` + `caption-kinetic-slam` |
| ARollFull / ARollPip | DaVinci 直接吃原檔 V1 | 不 render |
| 圖表 / 數據視覺化 | Hyperframes | `data-chart` / `us-map` |
| 質感疊層 | Hyperframes | `grain-overlay` / `vignette` |
| Caption 動畫 | Hyperframes | 14 個 `caption-*` |

## 跟 ADR-015 的關係

ADR-015 原計畫是「PyMuPDF 算 PDF bbox + Hyperframes DocumentQuote scene 動畫」單一路線。**2026-05-25 spike 證實 Reader-Playwright 路線更貼近既有基建**（重用 Robin Reader + foliate annotation + design system tokens），且天然支援 EPUB（非 PDF only）。ADR-015 應補 amendment 把 DocumentQuote 路線換掉。

## 為什麼不再用 reader-side 自動 picker

Spike v8 reader_record_spike_v8.py 有 picker 自動猜「first ≥200 字 `<p>` 的前 240 字到最近句末」— **錯的抽象**。修修要的工作流是：
1. script.md DSL 寫 `[quote source="..." text="<exact passage>"]`
2. Pipeline 抓 text → 餵 web_highlight_record → mp4

Reader 跟一般網頁從工具角度是同一條：URL + user-specified text → mp4。差別只在 auth cookie 跟 same-origin iframe recursion（未做）。

## 動畫定型參數（v8 user-approved，未來新 component 沿用）

- **顏色**：PANTONE 165 PC / `--sho-orange` = `rgba(233, 137, 101)`
- **透明度**：alpha 0.24 edge / 0.34 mid（夠強又不蓋住底下文字）
- **Zoom**：1.00 → 1.35，`cubic-bezier(0.16, 1, 0.3, 1)` ease-out-expo，前 30% 走完 85% zoom
- **Compositing layer pre-warm 250ms** 避免 transform 起步跳動
- **Capture**：CDP `Page.startScreencast` JPEG q92（不走 Playwright recordVideo VP8）
- **Encode**：libx264 yuv420p CRF 14 preset slow → ~30 Mbps 1080p

## 跟 [[project-script-video-phase2a]] 的關係

Slice 1 (#313) 骨幹已 ship；本決定影響 Slice 3 (#315 PDF + DocumentQuote)— 路線整個換掉，issue body 該重寫。Slice 2 (#314 6 Hyperframes components) 仍合理但範圍縮小（只剩 BigStat/TransitionTitle/圖表類，書頁類拿掉）。

## How to apply

- 新做的 video B-roll 任務先問：「scene 是書/網頁 quote 嗎？」
  - Yes → `web_highlight_record.py`（加 iframe recursion 給 Reader）
  - No → Hyperframes（裝在 `E:\nakama\video\`，pin >= v0.6.42）
- 新動畫沿用 v8 定型參數，不要每次重調
- ADR-015 amendment 一定要寫，否則 Slice 3 接手會困惑
