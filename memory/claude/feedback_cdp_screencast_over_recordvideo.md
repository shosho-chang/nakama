---
name: feedback-cdp-screencast-over-recordvideo
description: Playwright recordVideo 用 VP8 low bitrate (~2 Mbps)，1080p 拍出來像 720p；要真 1080p 改走 CDP Page.startScreencast 拿 JPEG q92 → ffmpeg libx264 CRF 14 → ~30 Mbps 真銳利 1080p
metadata:
  type: feedback
---

Playwright 內建 `recordVideo` 用 VP8 編碼，default bitrate 低（~2 Mbps for 1080p），文字邊緣會糊。轉 H.264 mp4 再經一次 lossy。

**Why:** 2026-05-25 reader-record spike v3-v4 跑出來修修反應「解析度還太低」，源頭是 VP8 第一手 bitrate 太低。

**How to apply:** 任何要錄製網頁動畫成高品質 mp4 的場合（不是 quick smoke test）：
1. 跳過 `record_video_dir` / `record_video_size` context option
2. 改用 CDP session：`client = context.new_cdp_session(page)`
3. `client.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1})`
4. listen `Page.screencastFrame` → base64 decode + 寫 JPEG 落地 + 每幀都要 `screencastFrameAck`
5. ffmpeg `-framerate <actual_fps> -i f_%05d.jpg -c:v libx264 -crf 14 -preset slow -pix_fmt yuv420p`

實測 v8 拍到 60fps source，輸出 ~32 Mbps 1080p H.264，文字銳利。比 recordVideo 提升 ~15× bitrate。

對比參考實作：`E:\nakama-reader-record-spike\scripts\web_highlight_record.py`（spike，未進 main tree）。
