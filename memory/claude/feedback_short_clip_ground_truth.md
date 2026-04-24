---
name: 短片段 ASR 當 ground truth 的除錯技巧
description: 懷疑長音檔 ASR 輸出不準時，切一段 15 秒丟同一模型單獨轉，當 oracle 比對
type: feedback
created: 2026-04-24
originSessionId: b3e311d8-3e4a-427d-a7e2-147b0d4bab90
---
**Rule：** 當你要質疑 ASR 對長音檔的輸出（時間戳、內容）時，先用 `shared/audio_clip.extract_clip()` 切 10–15 秒短片段，**獨立餵同一個 FunASR 模型**，把結果當 ground truth 比對。

**Why：** 2026-04-24 EP107 的 FunASR char_idx bug 差點被誤判。初版 auto-align 算出 slope=1.115 看似「字幕線性漂 11%」，我一度想寫全局線性擬合去修字幕。實際上字幕是對的、FunASR 整檔模式時間戳才是錯的。切一小段獨立跑：
- 長音檔模式在 2400s 輸出「我已經聽到小孩的心跳了」
- 切 2395–2410s 獨立跑輸出「因為要抱抱爸爸可以抱啊」（= 字幕內容）
- 確認字幕對、FunASR 長音檔壞

**How to apply：**
- 懷疑任一 ASR（或任一模型）在長輸入上的輸出有系統性偏差時，先取 2–3 個取樣點切短片段丟同一模型
- 短片段模式的 VAD / batch splicing / 內部 state accumulation 都不會觸發，基本等於 ground truth
- 取樣點挑「頭 / 中 / 尾」三段不同位置，避開 bug 在特定時段才出現的情形
- 比對方向：短片段 vs 長音檔輸出 vs 人工素材（字幕、筆記）— 三角驗證比兩兩對比可靠
- 避免只靠「我覺得字幕應該是對的」直覺當 oracle — 每方都可能有 bug，找獨立第三源
