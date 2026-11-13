---
name: feedback_editorial_master_sealing_is_mine
description: 修修說「我完成 Editorial Master 了」＝他剪定了，不是封存了；封存是我的工作，而且封存前要先補 intro/outro 字幕
metadata:
  type: feedback
---

**「我完成 Editorial Master 了」是他的剪輯定稿宣告，不是封存完成的回報。**

他做的是：review 完整版字幕與 timeline → 自己加上 intro 與 outro → 說那句話。
**封存（`podcast_editorial_master.py seal`）從來不是他的工作**，他沒按過任何叫封存的東西。

聽到那句話之後，**在封存之前**還有一段是我的：

1. 去 Obsidian 抓他 intro／outro 的逐字稿
   （`E:\Shosho LifeOS\AgentOutputs\interviews\<日期-來賓>\開場結尾講稿.md` 這類）
2. intro／outro 跑 **Memo** 語音辨識——**不是 faster-whisper**。
   Memo large-v2 是 ADR-063 的正式辨識器，原生就是無標點的 house style；
   2026-09-18 我拿 faster-whisper 跑 intro/outro，它吐標點、把 jieba 斷詞炸成
   「台大植物/系」「鯨魚/腦」「聞之色/變」「You Tube」「App le Podcast」，
   修修一句「為什麼要跑 Whisper Large V3？直接跑 Memo 啊！」——換回 Memo 之後
   那一整類問題直接消失。
3. 依他的逐字稿把那段字幕**校對完**
4. **才** seal

**Why**：2026-09-18 我把「封存」設計成 packaging gate 上的前置條件，錯誤訊息寫
「還沒有封存 Editorial Master，完整版要先封存才能發布」。用他的視角審這份規劃的
agent 直接打回來：

> 「封存」是我做的還是你做的？我從來沒按過任何叫封存的按鈕⋯⋯這句話裡沒有任何一個
> 我可以點的東西。⋯⋯我要的是：我在 Resolve 說「這集好了」的那一刻，封存就自動發生了。

把一個他不做、也不該做的技術步驟變成一道擋他的門，是把負擔轉嫁給他。

**How to apply**：
- 聽到那句話就自己往下跑，不要回問「要我封存嗎」。
- 錯誤訊息若提到封存，要指向**會幫他跑的人**，不要丟指令給他。
- 封存完接著跑到 packaging gate 才停——見 [[feedback_run_to_the_gate_dont_stop_to_show]]。
- 相關：[[feedback_dont_ask_permission_at_every_step]]
