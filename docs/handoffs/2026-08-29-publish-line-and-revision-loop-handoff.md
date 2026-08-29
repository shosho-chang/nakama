# Handoff — 發布線修正 ＋ 修訂迴路首次打通（20260805 林之晨）

- **日期**：2026-08-29
- **Worktree**：`E:\nakama\worktrees\lin-zhi-chen-e2e`，branch `codex/lin-zhi-chen-e2e`
- **本輪 commit**：19 支（`c9662113`..`02645d40`），**尚未 push**
- **測試**：`tests/brook/` ＋ publish 線 1168 passed / 10 skipped / 0 failed；
  廣泛跑 8080 passed / 8 failed（8 個皆與本輪改動無檔案重疊，其中
  `test_podcast_pipeline_v2_skill` 已直接驗證為動手前即失敗）

---

## 1. 一句話現況

**三支 long 的成品、標題、縮圖、描述全部就緒可上傳**；long3 額外套了兩筆內容修訂，
**卡在最後一道終檢閘門**，等修修跑一行指令即可核准。

---

## 2. 三支 long 的狀態

| cut | Release | 成品 mp4 | 標題／縮圖 | 描述 |
|---|---|---|---|---|
| `value-L01` | `release-migrated-abc8c2b2…` 592.9s | ✓ 206 MB | ✓ | ✓ 920 字 |
| `value-L02` | `release-migrated-dc172c6b…` 563.7s | ✓ 1.21 GB | ✓ | ✓ 696 字 |
| `long3-fresh-20260828-r4` | **`release-37058c0dbeed4b6cab280975`** 492.3s | 舊版 1.04 GB（**需重出**） | ✓ | 需重產 |

`long3` 的 Release 是今天用 amendment 新封的，`exports/punch-L04.mp4`、描述、章節都
還是舊內容，要等核准後重跑。value-L01／value-L02 不受影響。

---

## 3. 本輪修掉的根本問題

### 3.1 發布線四處不認得 ADR-066 Release（已全數修正）

每一處都各自從 ADR-065 的舊產物推導，而且**都不會報錯**：

| 環節 | 舊來源 | 錯法 | commit |
|---|---|---|---|
| render 的 timeline | `winners.json` rank+title 湊顯示名 | 挑到 329.5s／260.0s 的舊剪輯，安靜 render 出錯片 | `8f78b9d4` |
| YouTube 分章 | `tighten/<cut>_broll.json` | 舊時間軸，且標題帶換行會打壞 YT 格式 | `4d74d227` |
| description 逐字稿 | `srt/<cut>_tight_r*.srt` | 請 LLM 替一支不存在的影片寫文案 | `d63c228e` |
| 上傳的 CC 字幕 | `shared.tight_srt` | 整支片 CC 對不上畫面 | `554ea199` |

新增 `<episode>/highlights/publish-timelines.v1.json`：packaging 側 cut id ↔ Release
的唯一綁定處，也是 render 目標的權威。**只綁 timeline 名字，uid 在 job 時現查**——
每次 committed transaction 都會 duplicate-swap canonical timeline，寫死的 uid 從第一次
修訂後就過期。護欄：render 前比對 timeline 長度與 Release preview，差 >2 秒就停。

### 3.2 修訂迴路（史上第一次跑通三個語意階段）

依序撞掉五關：

1. **dark install** — watcher 組 application 沒帶 resolve configuration，
   lifecycle 拿到會 raise 的 `_InspectionOnlyTransactions`（`f70b5f05`）
2. **歷史毒死查詢** — `accepted_stages()` 為找一個 acceptance 重建整個 view，
   含 derived instruction；`DerivedAssetInstruction.__post_init__` 重跑 active
   projection 契約，兩筆退役的 `supporting_title` 讓每次修訂第一步就死（`12177110`）
3. **mojibake** — packet 用 `ensure_ascii=False` 寫原始 UTF-8，worker 用 host
   decoder 讀，cp1252 下 `20260805 林之晨` → `20260805 æž—ä¹‹æ™¨`，envelope
   比對必敗且確定性（`cb59dd9a`）
4. **acceptance 與 Release 不一致** — 8/29 的 supporting title 抑制只改 Release
   沒改 accepted stage（`cea251d4`，310 筆事件列 ＋ 125 筆孤兒 built asset）
5. **人工重試只准一次** — `retry_failed_dispatch` 卡在 `attempt == 1`，而它只有人會
   發動；targeted revision 連錯兩次即無路可走（`6dd3f80d`）

### 3.3 新增的 policy

`stock_video_asset_reused`：同一支 stock 素材不得在一支 cut 裡用兩次。
既有的 `distinct_stock_video_minimum_not_met` 只管「至少三支不同」，8 個 b-roll、
7 支不同素材照樣過關。**這條規則上線後立刻抓到修修抱怨的那支重複。**

---

## 4. ⚠️ 未解的三個問題

### 4.1 Codex 呼叫會掛住（阻斷修訂迴路的後半段）

visual_review 階段的 `codex exec` 子行程**26 分鐘只用掉 0.1 秒 CPU**，等於啟動後
就沒動作；而 `CodexSemanticAdapter` 的 900 秒逾時**沒有生效**（行程仍活著）。

- 逾時沒生效是我們這邊的 bug，可修
- 它為什麼不動則未知（封包只有 3 KB，比 dp 階段的 8 KB 還小，不是大小問題）
- 掛住當下的暫存工作區留在
  `C:\Users\Shosho\AppData\Local\Temp\nakama-finished-cut-codex-hn8po7on`
  （packet.json ＋ 995 KB 的 component-0001.png），行程未死所以未被清掉

### 4.2 DP 不知道素材是否已用過

第二筆修訂（換掉重複素材）跑完 DP，**它挑了同一支重複的 asset**。DP 的封包裡沒有
「本片已用過哪些素材」的資訊。所以就算 4.1 修好，這筆修訂仍會無限迴圈：
DP 挑同一支 → policy 擋 → 重試 → 再挑同一支。

**這是產線的缺口，不是這次的意外。**

### 4.3 素材池已被用盡

`evt_adult_game_challenge_broll` 的候選池 8 支，7 支已用在本片其他位置，唯一沒用過的
（「學生用筆電學習」）配不上旁白。修修 2026-08-29 裁決：**拿掉該支 b-roll**，並接受
由此產生的 b-roll 空窗（5:08 → 7:27，約 139 秒，超過 policy 的 75 秒上限）。
已由 `retime_and_drop_long3_broll.py` 執行完成。

---

## 5. 下一步（修修只差一個動作）

核准 long3 卡在**終檢閘門**：`qa_final.json` 是 8/22 產的，涵蓋 260 秒的舊
`punch-L04`，不涵蓋 `long3-fresh-20260828-r4`。

我已完成終檢（機械層全過；改動點畫格目視正確；六條歷史回饋逐條看過），
**唯一發現**是 cue 1「對 你如果沒看到」的冷開場殘尾，記為 `minor`（不擋交付）。

紀錄腳本已備好（分類器擋住 agent 寫 G:）：

```
python "C:\Users\Shosho\AppData\Local\Temp\claude\E--nakama--claude-worktrees-claude-rc-1bf63a\111f093f-09da-4689-a43e-3bb861e52245\scratchpad\record_qa_long3.py"
```

跑完 → 回 finished review 頁按「核准這支」→ packaging → 全解析 render → 描述（全自動，
今天已在 value-L02／punch-L04 驗證兩次）。

**若修修要修掉那個「對」**：需調整 cut 起點並重出成片，不是改字幕就好。

---

## 6. 環境備忘

- **Bridge**：修修的 8128 **已卡死**（listening 但 HTTP 無回應，CPU 燒了 3.5 小時），
  要重開。可用的是 8129（Codex 留下）與 **8132**（本輪起的，程式最新）。
  8130／8131 是本輪測試殘留，可清。
  ⚠️ **router 的 Python 模組是啟動時載入的**，改 router 後必須重啟；Jinja template
  才會自動重載。
- **watcher 執行環境**：`py -3.10` ＋
  `PYTHONPATH=C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules`
- **殭屍行程**：今天累積數個閒置 node（多數是 Playwright MCP，2 個是 Codex runtime，
  1 個是 4.1 那筆掛住的 codex）。agent 無 kill 權限。
- **分類器**：擋 agent 寫入 `G:` 與 `Stop-Process`。需要修修代跑的指令要用
  bash 區塊給出。
- `authority.json` 有備份 `authority.json.pre-acceptance-align`（本輪對齊前）。

---

## 7. 另案（本輪未做）

- `/bridge/highlights` 索引頁（四個審核面裡唯一 404 的）
- 本機正式 Bridge 的開機自動啟動（工作排程器指令已驗證可行，但**必須等本 PR merge
  進 main、且 `E:\nakama` 切回 main 之後**才能設——現在 main 沒有本輪修正，
  在上面按 Approve 會 render 出錯片）
- `full`（完整節目）仍停在 8/21 的封面 reject
- short highlight、social carousel
- `value-L01` 的描述是 8/27 產的，hook 未用新的 Release 字幕重寫（其 tight SRT 剛好
  就是成品那份，內容正確）；8/29 僅手術移除洩漏的內部路徑四行
