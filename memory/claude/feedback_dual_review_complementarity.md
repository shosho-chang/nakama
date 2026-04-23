---
name: Dual review (ultrareview + local) complementary coverage
description: ultrareview 跟本地 3-agent review 各找不同類型 bug；PR #77 實證 9 個真 bug 零重疊
type: feedback
originSessionId: 865a0876-3948-4e97-ad48-33a2464b8443
---
重大 PR（新 production 路徑、跨 agent 整合）pre-merge 審查時，**同時跑 ultrareview 和本地 3-agent parallel review**。兩份報告的發現幾乎不重疊，互補。

**Why**：PR #77 Usopp Slice B 實證 — 桌機觸發 ultrareview 抓到 2 個 score-72 blocker（WP*Error 跨 stage 沒接、`reviewer_compliance_ack` 語意錯）；Mac 本地 3-agent parallel review（publisher / compliance / docs-integration）抓到 7 個 blocker（orphan hole resume-from-`media_ready`、naive datetime crash、compliance 漏掃 image `alt`、vocab 覆蓋不足、簡體變體不擋、`.env.example` WP 鍵名 drift、`LITESPEED_PURGE_METHOD` 缺）。9 個真 bug、零重疊。

兩邊有不同 attention bias：
- **Ultrareview（多 model triangulate）**：偏 behavioral / flow correctness（error propagation、FSM 語意、ADR 對齊度）
- **Local 3-agent parallel**：偏 data / config / boundary edge（attr bypass、naive datetime、env 鍵名、vocab 清單覆蓋）

**How to apply**：
- 重大 PR 跑 ultrareview 後別當終點，本地再跑 3-agent parallel 補槍（三個 prompt 切 publisher/compliance/docs-integration 這種獨立維度）
- 反之亦然：本地 review 通過 ≠ cover behavioral；有 budget 就補 ultrareview
- 兩份報告到齊做交叉分類：重疊（高信心）、獨家（按 agent bias 權重）、兩邊都沒碰（clean verdict）
- ultrareview 雲端跑 5–10 min，筆電 suspend 可能 miss 推送；結果反而常在分支上直接看到（其他 session 修完 push 回來）
