---
name: 真人畫面不交給 AI 合成 — AI 只做 graphic / 標題 render
description: 修修 2026-07-28 封面回饋時立的創作原則 — 有實拍素材就用實拍，任何真人 image 不用 AI 生成/合成；AI 的角色限 graphic、概念圖、標題 rendering
type: feedback
created: 2026-07-28
updated: 2026-07-28
---

**封面（與任何視覺產出）裡的真人一律用實拍畫面，不用 AI 生成、合成、換臉。
AI 允許的角色：graphic／概念示意圖／背景圖層／標題（文字）rendering。**

## Why

修修 2026-07-28 在 packaging 封面品質回饋（對標 Chris Williamson）時原話：

> 「既然我們都有實際拍攝到來賓以及受訪者的畫面，我就不希望用 AI 來合成任何真人的
> image。AI 可以幫我生成 Graphic、可以幫我 Render Title，但是有關人真實的部分，
> 我目前不希望交由 AI。」

這與對標頻道的實務一致：Chris Williamson 的人物全是真棚拍，AI 化空間只在
概念圖層（inset 卡的迷因／示意圖）。真人真實感是訪談內容產品的信任基礎。

## How to apply

- **禁止**：AI 生成的人臉／人身、用來賓或修修照片做 img2img 重繪、換臉、
  AI「重新打光」到重生像素的程度（IC-Light 類 relight 屬合成，不用）
- **允許**：實拍 frame 的無損向操作 — 去背（BiRefNet/u2net）、裁切、調色、
  傳統銳化；概念 graphic／背景／裝飾元素的 AI 生成；標題文字的 AI render
- **邊界判準**：操作後像素還是「那一刻真的發生過的畫面」→ 可；像素是模型
  想像的 → 不可。升頻（Real-ESRGAN 類）屬灰區，預設不用，需要時先問修修
- 適用範圍：thumbnail-brainstorm、B-roll、任何社群視覺產出

相關：[[feedback_aesthetic_first_class]]／[[feedback_title_brainstorm_is_highest_leverage]]
