---
name: feedback_design_iteration_converge
description: 視覺元件跟修修逐輪調設計時，不要每輪往上疊零件；越改越糟就退回他唯一說過好的版本，只改他點名的那一處；ImageGen 不守品牌規則，不拿來發想品牌元件
metadata:
  type: feedback
---

**每輪只動修修點名的那一處，不要順手加東西；「越改越糟」時退回他明確說過好的版本。**

**Why**：2026-11-15 做 YouTube 照稿影片第 4 類「書封」。修修說「外框還不錯，pattern 不太一樣」，我接連改了斜線→品牌碎片→減量→加斜度→藏進框後，每輪都在框外疊零件，修修：「我覺得真的是越改越糟」「你的美感真的是不太夠」。退回「只有細橘框、什麼都不加」才回到他接受的方向（之後只改進場效果：從左上角順時針把框畫出來，且框要保留「被 skew 裁切後粗細有變化」的那個版本——換成等粗的 SVG 描邊他立刻指出「框就沒有粗細、沒有變化了」）。

同一輪試了 OpenAI 圖像模型（`chatgpt-image-latest`，`/v1/images/edits`，參考圖給品牌手冊三頁＋錄影一格＋書封＋封面）：四張全部違反品牌規則——書加投影或立體厚度、形狀整個轉斜、**真人被重畫、T 恤字變亂碼**。修修：「他應該沒有按照我們的品牌設計吧？」

**How to apply**：
- 修修說「X 還不錯」＝ X 是錨點，後面所有修改都不准動 X 的外觀，只動他點名的部分。
- 他否定的是「多出來的東西」時，先試「拿掉」再試「改」。
- 品牌元件（影片字卡、畫面元件）的發想：從「張修修的不正常宇宙」品牌手冊 Brand In Use 頁與他實際用過的成品（例：該集 YouTube 封面 `E:\data\AgentOutput\<date>-...-cover\`）出發，不用 ImageGen；ImageGen 生的人像也違反「真人畫面不交 AI 合成」。
- 相關：[[feedback_content_design_uses_shosho_universe]]、[[feedback_aesthetic_first_class]]
