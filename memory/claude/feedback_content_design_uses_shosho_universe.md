---
name: feedback_content_design_uses_shosho_universe
description: 內容設計（影片字卡、畫面元件等）一律用「張修修的不正常宇宙 Design System」，不是長精華的紙卡＋手繪橘線，也不是 Bridge 網頁的 docs/design-system.md
metadata:
  type: feedback
---

**內容的設計全部用「張修修的不正常宇宙」那一套**（修修 2026-11-15：「以後在內容的設計，全部都是張修修的宇宙那一套。之後我們這 16 個一個一個設計出來之後，長精華那邊也會套用到我們新做的這幾個。」）

- 位置：claude.ai Design System artifact <https://claude.ai/artifact/KEfVLi769roGThzUkQXj5S>（標題「張修修的不正常宇宙 Design System」）。用 Artifact `read` 讀 `project/README.md`、`project/tokens/*.css`、`project/ui_kits/video_graphics/`；權威來源是裡面的 `project/assets/reference/brand-identity.pdf`（品牌識別 0827，33 頁；p20–24「Youtube 影片字卡應用」）。
- 要點：三色（Cool Gray 1 `#DDDDDA`、黑、白）＋唯一輔色橘（螢幕 `#F57331`／PANTONE 165 `#E96925`）；思源黑體／Noto Sans TC Black；方角、完全平面、沒有陰影（字壓影片只准 text-shadow／黑色漸層遮罩）；品牌物件斜貼 ±2–6°；碎片 Motif A/B；動作像剪紙啪地貼上（140–380ms、snap／overshoot、快速退場）。

**Why**：2026-11-15 做 YouTube 照稿影片第 2 類「重點概念 title」，我拿長精華的「統一設計系統」（半透明紙卡＋手繪橘線，`longform-cut` SKILL §統一設計系統）發想了四版，修修：「我覺得都不好看」。repo 裡 grep 不到「宇宙」，這套只在 claude.ai artifact 上。

**How to apply**：
- 影片線 16＋類畫面元件、長精華改版、任何影片字卡／畫面元件：先讀這套，從品牌手冊的 Brand In Use 頁找原型（例：重點概念 title ＝ p23「重點字字卡」：白色 Black 字＋字下半部壓一條平的橘色色塊）。
- 長精華現有的 `punch_card_wide`／`chapter_label_wide`／`transition_title_wide paper_hand` 屬舊系統；新元件做好後長精華會改套新的，不要再拿舊系統當基準。
- Bridge／網頁 UI 仍是 `docs/design-system.md`（不在這條規則範圍）。
- 相關：[[feedback_aesthetic_first_class]]、[[feedback_migration_must_carry_the_craft]]
