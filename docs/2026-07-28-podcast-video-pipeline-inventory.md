# Podcast → 影片 產線總盤點（2026-07-28）

修修要求：「這條 Pipeline 中間呼叫了哪些 skills？現在做了哪些功能？進度到哪裡？
要怎麼觸發？」三個 Explore agent 平行盤點的合成結果，所有結論都有 file:line 佐證。

---

## 一、一句話總覽

**一個入口（`podcast-pipeline`）串起 8 個 skill**，從錄音檔一路到「標題×封面等你 approve」。
**全線零 API 費用**（LLM 全走 Cowork subagent）。

```
錄音檔 ──▶ 字幕 ──▶ Resolve 專案 ──▶ 選段 ──▶ 標題×封面 ──▶ gate ──╳── 發布
        [4 個 skill]   [1]        [1]      [2 skill]      ✅UI    零實作
```

真正的斷點在最後一段：**gate 之後沒有任何上傳程式碼**。

---

## 二、Skill 鏈與觸發語

| # | Skill | 觸發語 | 輸入 → 輸出 | 狀態 |
|---|---|---|---|---|
| 0 | **podcast-pipeline** | 「跑字幕產線」「整條跑完」「一路跑到 gate」或直接丟 episode 資料夾 | 編排層，靠檔案存在偵測進度、可續跑 | ✅ |
| 1 | **audio-prep** | 「跑 audio-prep」「normalize 音檔」 | `Audio/Live-Mix.wav` → `normalized.wav` + `prep_manifest.json` | ✅ |
| 2 | **subtitle-gen** | 「產字幕」「跑 ASR」 | `normalized.wav` → `subs/raw.srt` + `words.json`（字級） | ✅ |
| 3 | **subtitle-correct** | 「校正字幕」 | `raw.srt` + refs → `transcript.srt` + `transcript.qc.md` | ✅ |
| 4 | **resolve-project** | 「進 DaVinci」「建 Resolve 專案」 | episode → Resolve 專案（六機位 bin + 字幕軌） | ✅ |
| 5 | **highlight-cut** | 「選段」「切精華」 | `transcript.srt` → 3 長片 + 3–4 短片 timeline | ✅ 長片到此為止 |
| 6 | **title-brainstorm** | 「幫我想標題」/ `--batch <packaging_dir>` | 逐字稿 → 5 個標題（長片跑 panel 淘汰賽）| ✅ |
| 7 | **thumbnail-brainstorm** | `/thumbnail-brainstorm`「配封面」 | 標題 → 3 張 16:9 封面 package | ✅ 品質待升級 |

**夾層 script**（correct → resolve 之間，pipeline 自動跑）：
`run_speaker_split.py`（說話者切分）→ `run_gap_fill.py`（>3s 無字幕補洞）→
選配 `run_line_polish.py`（斷句全片掃描）

**五個 HITL 停點**：prep 後（裁切秒數）／gen 前（refs + GPU）／gen 後（抽 cue）／
correct 後（QC 清單，最終字幕 gate）／packaging 後（給 gate URL 即停）

---

## 三、各段的實際完成度

### ✅ 完全可用（Stage 4：字幕）
五個 skill 全部實跑過整集。關鍵紀律：
- **時間軸鐵則**：`normalized.wav` 必須與原始錄影同軸，頭尾靜音預設不裁
- **GPU**：subtitle-gen 必須用 Python 3.10（torch cu128）；PCIe Gen5 警告要停下來問
- **零 API 錢**：`--api` / `--arbitrate` 不主動用
- **字幕樣式只能靠 DRT 模板帶**（Resolve API 不開放 subtitle preset）

### ✅ 可用（Stage 5：選段）
3 個 miner 平行開採 → persona 盲審（阿哲/凱文/淑芬 + brand-lens + 長片專屬 Renee）
→ 中位數排名 → 物化成 timeline。長片 16:9、短片 9:16。

### 🚧 三條子線成熟度差很多

| 子線 | 狀態 | 說明 |
|---|---|---|
| **短片（60–120s 直式）** | ✅ 最深 | Step 6–11 六層、七支 script、28 輪裁決、四支已驗收。**2026-07-28 修修裁決收線**（與人類剪接仍有差距）|
| **長片（8–12min 橫式）** | 🚧 只到 timeline | Step 6–11 **全是短片專屬**，長片明文列為「不做」。緊湊化/導播/字卡/B-roll/音效/自檢六層全無 |
| **script-driven video（修修 talking head）** | 🚧 機器齊、從未跑通 | brook-director/dp/replan-beat 三 skill + 10 個 CLI 子命令 + Bridge 兩層 HITL 全在，但 `data/script_video/*/out/` 是空的，卡在素材交接（有 `asset_requests.yaml`、無 `asset_manifest.yaml`）|

### ✅ 已上線（Stage 5→6 交界：packaging）
ADR-054 Accepted，S1–S9 九個 slice 全數落地，Bridge gate `/bridge/packaging` 可用。
- approve 單位 = **標題×封面綁定的 package**（Test & Compare 無 API）
- **短片不做封面**、標題直出不跑 panel（Test & Compare 不支援 Shorts）
- UI **零 LLM**，只裁決不生成；要重抽回 Cowork 跑 skill
- **只剩 S10**：下一集新訪談的端到端驗收

### ❌ 零實作（Stage 6：發布）
**全 repo 沒有任何 YouTube / IG / FB 上傳程式碼。**
- `videos.insert` / `captions.insert` / `publishAt` 全 repo 7 個命中**全在 .md 文件**，零個 `.py`
- `YOUTUBE_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` **三個都空**
- `publish_prep.py`（設計裡的入口）**不存在**
- 唯一的發布狀態機 `agents/usopp/publisher.py` 只做 WordPress，且**生產環境 0 次成功**
  （`publish_jobs` 表 0 列，service 空轉三個月）
- packaging gate 的 `approval.json` **從未產生過**——沒有任何一集被 approve

發布線設計已凍結 Q1–Q5（桌機當 uploader、VPS 當控制面、新開 releases 兩張表、
長片不燒字幕只上 CC / 短片必燒），但 **§4 一半以上分支還沒 grill**，且 grill 停在 Q6。

### ⚠️ 半層（Stage 7：監控）
SEO 中控台 ✅（Line 3）、Franky probe/GSC/cost ✅；**YT/IG insights ❌**。
Stage 7 → Stage 1 的回灌迴路**斷裂**。

---

## 四、盤點時發現並修掉的問題

1. **`podcast-pipeline` skill 有兩份且已分岔**（已修）
   - `C:\Users\Shosho\.claude\skills\`（07-26，60 行）vs `E:\nakama\.claude\skills\`（07-28，92 行）
   - **這個 session 載入的是舊版**——舊版只編排到 subtitle-correct，且會在 Resolve 建完時
     宣告「全部完成」，不會續往 highlight-cut / packaging
   - 已把六個 skill 全部從 repo 同步到 user-level，`transcribe` 也補上

2. **manifest 欄位 stale**（未修，低風險）
   `prep_manifest.json` / `gen_manifest.json` 指向 `normalized_aligned.wav`，
   但該檔名全 repo grep 不到、實體也不存在。進度偵測看的是 `normalized.wav` 本身，
   所以續跑不會壞，但任何讀 manifest 欄位當路徑的程式會踩空。

3. **模式命名三套**（未修，純命名）
   subtitle-correct 的同一件事在 SKILL.md 叫 `subagent`、CLI 叫 `llm`、manifest 寫 `cowork`。

---

## 五、擋在最前面的事（依阻塞程度）

| # | 阻塞點 | 為什麼重要 |
|---|---|---|
| 1 | **發布線 Slice 0 探針未跑** | OAuth → 上傳 private → `publishAt` 排程 → 確認準時公開且沒被降權。**這步過不了後面全部不用做**；不依賴任何其他工作，可立即先行 |
| 2 | **約 40 支影片積壓** | `G:\footages\` 9 集 × (3 長 + 3–4 短)。瓶頸不是平台額度，是**修修的審核點擊數** |
| 3 | **長片線 Step 6–11 空白** | YT 主戰場是長片，但投入 28 輪的是短片。唯一擋著的決策點是「等修修看完短片版成效再決定」——短片線已收線，這個決策點現在到期了 |
| 4 | **vault vs DB 當 SoT 未裁** | 刻意偏離 house 原則，需要 ADR |
| 5 | **封面品質 P0–P3 待裁** | 修修回饋「慘不忍睹」，升級計畫寫好但未動工 |
| 6 | **`CONTENT-PIPELINE.md:207` 自我矛盾** | 同句話說「不要再擴 Usopp」又說「新開 usopp sub-publisher」，發布層歸屬未凍結 |

---

## 六、怎麼跑（最短路徑）

```
# 一句話跑完整條（會在五個停點停下來問你）
指著 G:\footages\<episode> 說：「跑字幕產線」或「一路跑到 gate」

# 單段觸發
「跑 audio-prep」 / 「產字幕」 / 「校正字幕」 / 「進 DaVinci」 / 「選段」
「幫我想標題」（互動、不寫檔） / 「配封面」

# 續跑：pipeline 自己看檔案判斷進度，不用告訴它跑到哪
```

**執行環境紅線**：本產線只能在 **Claude Code + 本機**跑，不走 CoWork / Computer Use
（Resolve 官方 Python Scripting API 需要逐幀精度）；Resolve script 用 `py -3.10`。
