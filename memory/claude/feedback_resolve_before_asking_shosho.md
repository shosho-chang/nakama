---
name: 問修修之前先自己查到底：網路、記憶、現場證據
description: 轉述 subagent 的「待確認清單」前自己先判斷一輪；查得到的（網路、repo 記憶、逐字稿/聊天室/投影片）自己查，只問真的只有他知道的
type: feedback
created: 2026-11-20
---

修修 2026-11-20（直播字幕 14 處待確認字詞）：「你有自己判斷過一輪嗎？」「你自己上網查不就可以知道了？既然都是講 Agent，這個能力沒有在 Skill 裡面嗎？不確定就上網查」「人馬筆記本，我們已經共事這麼久，你不知道我有這個東西嗎？第四個跟第五個不是都很容易判斷嗎？」

**Why:** 我把稽核／仲裁 subagent 列的候選直接整理成表丟給他，自己沒驗。結果 14 處裡 13 處我自己就能定：
- 產品名（GroupBot→Grok Bot、Muse、Dot→Dots、Cloudway→Claude Duet）一次 web search 就查到；
- 「Claude Duet」聊天室裡有會員原文、「克朗」投影片講者備註與前後 cue 都有、「Project 記憶空間會不會比較大」聊天室有提問原文、他自己下一段就回答了；
- 截音檔用另一套 ASR（faster-whisper）重聽，人名／句子大多直接聽得出來。
把能自己查的丟回給他，等於把決策成本推給他（CLAUDE.md 決策呈現原則同源）。

**How to apply:**
- 拿到 subagent 的「需要人確認」清單 → 先自己逐項查：web search（產品、模型、公司名）、repo `memory/`／skills（他的自創詞、系統名）、現場證據（聊天室、投影片、前後文、他後面自己的回答）、截音檔重聽。
- 只把「查遍仍無解、且確實只有他知道」的留給他；問的時候附上我查過什麼、我的最佳猜測。
- 他的自創詞要記得：**人馬筆記本**＝他的 Centaur 筆記系統（Obsidian＋AI 收集文章，Robin KB 的 Centaur 規格同源）；**克朗**＝他講 LLM／Agent Harness 時的比喻（忍者龜反派，大腦坐在機器人身體裡）。
- 相關：[[feedback_verify_agent_claims_against_data]]、[[feedback_run_dont_ask]]、[[feedback_check_before_asking_user_to_setup]]
