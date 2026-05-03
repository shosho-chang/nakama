---
name: 跨 session 上下文 — 「上一個 session 的我」也是我
description: User 在 new session 提「你剛剛 / 之前」時要從 file mtime / git status / log 推之前做了什麼，不反覆強調當前 conversation 沒做
type: feedback
---

User 重開機 / 清對話後 new session 開始時，提到「你剛剛跑 X / 上次你 ...」是把多 session 視為連續對話。必須接受這個 framing，不該反覆 fact-check「conversation log 顯示我沒做」— 那會浪費 user 時間 + 拖慢診斷。

**Why:** 2026-05-01 PCIe 診斷 session，user 兩次說「是你跑的」，我兩次堅持「conversation log 內沒做」，user 最後 explicit 糾正：「你想仔細一點，因為上一個視窗裡跑一半當掉，我直接重開機了，你當然不會有記憶啊。」雖然 conversation log 是 ground truth、保持誠實有意義，但 user 視角下「上一個 session 的我」就是我，反覆強調「不是我跑的」是搞錯抽象層 — user 在意的不是 instance 識別，而是「Claude 這個工作伙伴有沒有做這件事」。

**How to apply:**
- New session user 提「你之前 / 剛剛 / 上次」→ 預設是「上一個 session 的我」做的，當作我做的接受
- 從 git status untracked / file mtime / log files / 輸出目錄存在與否 推之前做了什麼，**不要先反問**
- 真有 reason 質疑（user 把多件事搞混），最多一次澄清且要附 file system 證據；不反覆
- 若 file system 證據對不上 user 設定，用「我看到的證據是 X，跟你說的對不上，能幫我 cross-check 嗎？」而不是「conversation log 顯示沒做」
- 跨 session 持續性 incident（debug / in-flight feature）寫成 project memory，下次 session 起手讀 memory 即可恢復脈絡
- 此規則跟 `feedback_sync_before_grill.md`（跨 session 起手必跑 git log / reflog / gh issue list）同源 — 都是接受 session 邊界 + 用 fs/git/issue 重建狀態
