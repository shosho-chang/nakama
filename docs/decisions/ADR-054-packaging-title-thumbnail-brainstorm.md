# ADR-054: Packaging Pipeline — 標題 × 封面 Brainstorm（amends ADR-033）

**Date:** 2026-07-26 · **Status:** **Accepted**（v3；修修授權 final review 放行 —「叫 Fable review，我這裡沒有意見」；Fable 5 處必修 + 6 處建議修已全數收斂）· **Owner:** 修修
**Related:** [ADR-033](ADR-033-thumbnail-generation-pipeline.md)（本 ADR amend 之）· [ADR-026](ADR-026-llm-router-auth-dimension.md) · [ADR-030](ADR-030-vault-as-substrate-read-strategy.md) · [ADR-052](ADR-052-robin-promotion-bounded-package.md) · [publish handoff](../plans/2026-07-26-video-publishing-plan.md)（§3/§5 接縫由本 ADR 收，見附錄 D；⚠️ 該檔目前僅在 `E:\nakama-video-publish` worktree — 若本 ADR 先合，連結懸空至該側 merge）

---

## 第 0 節 — 一頁速覽（只看這頁即可裁決）

**做什麼**：highlight-cut 產出的每集 3 長片 + 3~4 短片，自動做出標題與封面 →
修修在一個 gate 上 approve → 交發布層。LLM 全走 Cowork subagent（零 API 錢）。

**狀態**：v2 的三個 blocking prerequisite **已全數解除**（2026-07-26 晚）：
- ✅ P1 修修 cutout 表情庫 — 從 VPS Syncthing `.stversions` 還原 17 張／7 表情，
  `pick_youtube_host` 七表情實測全通
- ✅ P2 reference 參考圖庫 — 查明為 **dead code**（v1.1 起品味錨定走 playbook 文字目錄，
  `reference_images` 預設 `None`，`:240` 分支不執行），缺庫不降級任何東西
- ✅ P3 復原路徑 — 即 P1 所走的 `.stversions` 路徑

### 決策一覽

| # | 一句話 | 詳見 |
|---|---|---|
| D1 | 兩條正交軸：`information_origin`（full_text/one_liner）×`visual_recipe`（podcast/youtube_host/youtube_book）；`aspect` 獨立維度，目前僅 16:9 | 附錄 A1 |
| D2 | `visual_recipe` 三值宣告、`youtube_book` 標 not-implemented fail loud；**不動 `content_type` 舊 enum** | 附錄 A2 |
| D3 | 發想單元 = episode 資料夾裡的一支影片，不是 Obsidian Project 頁 | — |
| D4 | **長短片分流**：長片完整 packaging；**短片不做封面、標題 LLM 直出不跑 panel**（修修同日二修：「短影片的 title 直接用 LLM 決定就好」）。硬理由：Test & Compare 不支援 Shorts | 附錄 A4 |
| D5 | brand-lens 分流：title-level flag 冒到 packaging gate；**content-level（數據錯誤/需字卡/需重剪）留在 materialize 之前** | 附錄 A5 |
| D6 | 關鍵字拆「抓」（Python 零金額 connector）與「判」（subagent）；堵 `_auto_translate()` 與 KEYWORDS.md Branch A 兩條付費殘留 | 附錄 A6 |
| D7 | 關鍵字 per-集 1 次（≈202 units，配額 10k/日）+ per-影片 narrow；narrow 落空追加抓取，不許目測 | — |
| D8 | 來賓 cutout：funnel Stage 3 subagent vision 自動挑表情 + 四個防呆條件；修修 cutout 用預建庫 | 附錄 A8 |
| D9 | **approve 單位 = 長片每支 3 個 package（標題×封面綁定）**。⚠️ Test & Compare **無 API** → 上傳只帶 primary，另 2 組 gate 上一鍵複製、修修在 Studio 手動貼（一支一次） | 附錄 A9 |
| D10 | 雙落點：working set `G:\footages\<ep>\packaging\`；**SoT = vault `Attachments/packaging/<episode>/`**（packages.json + approval.json + PNG）。三條硬規則：vault-relative 路徑／ASCII 檔名／approve 獨立檔 | 附錄 A10 |
| D11 | Bridge 是**重寫不是減法**：5 endpoint retire／6 rewrite／HTMX chain 作廢；UI 零 LLM；approve 表單帶 `reject_note` 純文字欄位 | 附錄 A11（endpoint 清單） |
| D12 | 改造 `title-brainstorm` 不新建：顯式 `--batch` 參數、description 不加批次字樣、新增 PLAYBOOK.md（angle↔archetype 映射 + title 側 D/F-grade gate） | 附錄 A12 |
| D13 | **長片標題深度不可簡化**（panel 2–3 輪／真訊號／淘汰賽）；要省先砍封面側→支數。短片豁免（見 D4） | 附錄 A13 |
| D14 | resume/冪等：working set `manifest.json` 逐段記進度；推導鏈逐支落地 `title_trace.json`；失敗停段不跳段 | 附錄 A14 |
| D15 | **交接契約（收 publish handoff §3/§5）**：發布層只讀 vault packaging 目錄的 `packages.json` + `approval.json`，不再有第三份交接檔；citations/brand_flags 由 packaging skill 從 `review_brandlens.json`（機器可讀）填入 | 附錄 D |
| D16 | **編排入口 = 擴充 `podcast-pipeline`**（原 OQ1，修修 2026-07-26 裁決）：packaging 作為它在 highlight-cut 之後的新末段，沿用其「依檔案存在判斷進度、段間停下」機制（= D14 的 resume 邊界）。Cowork 啟動整條；HITL 在 Web UI（Bridge 讀 vault packaging 目錄 — Syncthing 是既有同步面）。不新開 orchestrator skill、highlight-cut 不長大。**兩個前置**：① title-brainstorm 執行權**移交 packaging 段** — highlight-cut Step 4 降為規則指標（packaging 上線前照舊，上線後不重跑，標題候選單一落點 packages.json，選段企劃報告不再存候選）— 否則同批 panel pass 跑兩次、quota 翻倍 ② podcast-pipeline 進度偵測表需先納入 highlight-cut（檔案 marker：`candidates.json`/`winners.json`），其現行「Resolve 內已有同名 project ＝ 全部完成」末列語意隨之修正 | — |
| D17 | **Reject 是 revision command，不是死狀態**（2026-08-21）：Bridge 仍零 LLM，只封存 feedback + source hashes 為 `revision_job`；桌機 watcher 備份舊版後啟動 bounded Agent，驗證輸出後只回 `ready_for_review`，不得自動核准。失敗 fail loud，只有人工 Retry 才重跑。 | thumbnail-brainstorm Step 4.9 |

### 連動改動（同 PR）

- `highlight-cut/SKILL.md` Step 4 mandate 改為**僅長片**必經 title-brainstorm（短片標題 LLM 直出）+ 執行權移交註記（D16①）— 原「每個當選段落…跳過＝違規」為同日上午裁決，已被二修部分推翻 ✅ 本 PR 已改
- `docs/VAULT-LAYOUT.md` 補登 `Attachments/packaging/<episode>/`（owner／conflict policy／rotation 三項）✅ 本 PR 已改
- `agents/brook/script_video/CONTEXT.md` Packaging 段 — 視覺配方改繫 `visual_recipe` 新欄位（勿寫 content_type）、guest cutout 檔名帶 emotion ✅ 本 PR 已改
- `shared/llm_router.py`（實作 PR）：**刪 `:178-180` stale 註解**；`:264` docstring 是優先序清單第 4 項，**整行保留、僅把括號內 `subscription_preferred` 改 `api`**（code `:192` 是對的，理由在 `:186-191` Codex audit §4）— 不要反向改 code
- ADR-033 → `Accepted (as amended by ADR-054)`

### 成本

- **金額 ≈ 0**（LLM 走 subscription；外部 connector 零金額 — 僅 YouTube 有配額制，其餘為無 SLA 刮取）
- **quota**：短片豁免後一集約 **50+ → ~30 次 persona pass**（3 長片 × 完整 panel + 封面 + vision 挑表情）
- **工程**：Bridge 側重寫（見 D11）；tests 48 個中 34 個受影響（17 刪／7 重寫／9 re-target／1 反轉）— 測試重寫獨立 slice

### Open questions（實作前必收）

1. ~~編排入口~~ → **已裁，見 D16**
2. `packages.json` schema 凍結（草案見附錄 C）
3. 短片 caption 形狀 + 走不走 `lint_titles.py`（現行 no-emoji/≤80 字對 Reels/TikTok 慣例必 FAIL）
4. Packaging 是否獨立 bounded context（`shared/cutout_library.py`+`thumbnail_funnel.py` 依 ADR-052 該住 Brook package）
5. `one_liner` 起點的入口形狀
6. `brainstorm_meta.json` 既有 revealed-preference 歷史遷移或棄用

---

---

## 附錄 A — 各決策 rationale（只記非顯而易見的）

### A1/A2 — 兩軸與 enum
「A=有逐字稿／B=一句話」是**資訊起點**；code 的 `content_type: youtube|podcast` 是**視覺配方**。
壓成單一 enum 則「讀書心得出影片」（full_text × youtube_book）無家可歸。
`visual_recipe` 散在至少 8 處 → enum 難改而 composition 好加，宣告三值、書封版面延後。
**不動 `content_type`**：真正的四值 validator 是 `shared/lifeos_writer.py:22`（youtube/blog/research/podcast，
三處共用）；`shared/state.py:339` 還有 SQL CHECK 要 migration；thumbnail 側 `:306/:647` 只是窄化 gate。
`visual_recipe` 是新欄位，兩者不重疊 — v1「順手修」是自找的 scope creep，已刪。

### A4 — 短片分流（v3 擴充）
封面：**Test & Compare 官方不支援 Shorts**（桌機 Studio only、測試期 ≤14 天）+ 垂直自動播放 feed
不顯示封面。**明確承認放棄**：Shorts 封面仍出現在搜尋結果／頻道 grid／Shorts shelf；IG Reels
主 feed 吃封面（裁 4:5）。**補償**：短片 cover frame 不得落在 opener 0–4s 上下分割段
（grid 尺寸下兩張小臉零文字 = 最差縮圖 — v1 說「首幀已設計過」是反的），gate 上顯示該格。
標題：修修同日二修 —「對於短影片，title 的重要程度可以被忽略，直接用 LLM 決定就好」。
支撐事實同上：無 A/B 裁判 + Shorts 點擊由前 3 秒內容決定。LLM 直出吃該段 hook 原句 + 關鍵字表。

### A5 — brand-lens 分流
實測 `review_brandlens.json`（謝伯讓集，40 verdicts＝16 長 + 24 短：27 pass / 12 caution /
1 veto；長片段 caution 5 條）：`story-L5` 標題與逐字稿不符 →
title-level，冒到 packaging gate 對；`story-L4` Lancet 47% 族群層級 vs 個人風險 → 處置是
「字卡標明或剪掉一句」= **剪輯層**，現況出現在 materialize 之前。v1 把它一起往後搬，
會逼修修在手調過構圖後重建 timeline（director 重跑洗掉 titles 層）— 是對已 ship 流程的退步。
另：這類逐集新事實的正確性判斷**不隨 skill 成熟收斂**，不歸「開發期回饋」類。

### A6 — 關鍵字拆抓/判
花錢的是 code path 不是工作：`research_keywords()` 把抓（免費）與 LLM 合成（`shared.llm.ask` →
`DEFAULT_AUTH="api"`）綁在一起。留 Python 的理由是**資料品質**：Data API 回結構化觀看數，
WebSearch 估不出 volume。兩條付費殘留必堵：① `agents/zoro/keyword_research.py:30-43`
`_auto_translate()` — en_topic 改由 subagent 給詞 ② KEYWORDS.md Branch A 的
`python -c "...research_keywords(...)"` 改指 `collect_keyword_signals()`。措辭紀律：僅 YouTube 有「免費配額」；Trends/autocomplete/Reddit/
DDG-Twitter 是無 SLA 刮取（會壞、會限流、不產生帳單）。

### A8 — funnel Stage 3 四條件
① `window` 需貫穿 `run()` → `stratified_sample()` → `_detect_audio_peaks()` 三層（只改一層 =
「看起來有做」但窗口沒生效）② 來賓從拍來賓機位 × 說話區間交集抽格 ③ 機位對應交叉驗證後
fail loud（`run_short_director.py:50-54` 是本集硬寫 default，換集對調會穩定抽到修修的臉且不報錯）
④ active-cutout 檔名改 `{role}_v{i}_{emotion}.png`（現行不帶 emotion，`cutout_library.py:207`
的 `emotion in p.stem.lower()` 永遠 miss → 隨機 fallback，vision 挑的表情被靜默丟棄）。

### A9 — package 與 A/B 的現實
平台 2025-12 起支援 3 組 combined package 測試、判 watch time per impression —— ADR-033 D2
的「正交軸」論述被推翻。但 **Test & Compare 無 API**（桌機 Studio only）→ 全自動 A/B 做不到。
實際流程：上傳帶 primary package；另 2 組的「一鍵複製」按鈕放**發布層審核頁**（不在
packaging gate — Studio 手動建測試發生在上傳之後，複製動作貼著上傳時機才不會白做），
修修在 Studio 手動建測試，一支長片一次、一集 3 次。保留 3 package 的理由：`PANEL.md` 自言
「真正裁判是 Test & Compare，panel 只是過濾器」— 放棄 = 最高槓桿環節永遠沒有客觀回饋。
`packages.json` 存 Top 5 標題、只 render 3 張封面，gate 顯示落選 2 條 + 理由（保住
「最後拍板是創作者本人」）。gate 語意 = 「三個都願意花早期 impression 測」。
負擔實測：gate = 9 package + 3~4 條短片標題（LLM 直出，看一眼可改）。

### A10 — 雙落點三硬規
① `packages.json` 內路徑一律 vault-relative（`.env VAULT_PATH="E:/Shosho LifeOS"` vs VPS
`/home/Shosho LifeOS`，絕對路徑跨機器必爛）② PNG 檔名 ASCII slug（`_safe_filename` 正則排除
CJK，episode 名是「20260723 謝伯讓」）③ approve 寫獨立 `approval.json`（讀寫方不互相覆寫、
縮小 Syncthing conflict 破壞面 — ADR-030:214 已列 `*.sync-conflict-*` 為 real data-loss scenario）。

### A11 — Bridge endpoint 處置清單

（檔案：`thousand_sunny/routers/bridge_project_thumbnails.py`，1586 行；行號為 def 行 ±1）

| endpoint | 處置 | 關鍵理由 |
|---|---|---|
| `thumbnail_brainstorm` `:286`／`thumbnail_idea_reroll` `:624`／`thumbnail_brainstorm_titles` `:1038`／`thumbnail_brainstorm_title_reroll` `:1127` | **retire** | LLM 搬進 skill |
| `thumbnail_idea_save_edit` `:574` | **retire** | v1 漏列；留著會成「skill 寫檔／UI 寫 frontmatter」雙寫入源 |
| `thumbnail_render` `:751` | **rewrite** | 輸入從 frontmatter 改讀 packages.json；cutout 宣告改顯式欄位 |
| `thumbnail_commit` `:942`／`thumbnail_podcast_active_cutouts` `:1453` | **rewrite** | persistence 走 `Projects/{slug}.md`，D3 後不存在；改寫 approval.json；檔名帶 emotion |
| `thumbnail_candidate` `:902`／`thumbnail_podcast_funnel_candidate` `:1429` | **rewrite** | `_safe_filename`/`_safe_ts` 正則重寫 |
| `thumbnail_podcast_funnel` `:1322` | **rewrite** | `_resolve_video_path`（`:1299-1319`）拒絕 repo 外路徑（ADR-033 刻意的 defense-in-depth）；改 allowlist（repo root + `FOOTAGE_ROOT`）並反轉 `tests:586` |
| 純運算層（`shared/thumbnail_funnel.*`、`_u2net_cutout` argv、`render_*_still`） | **keep** | 不在 router 內，原封不動（funnel 僅加 window） |
| HTMX partial chain | **作廢重寫** | approve 表單巢狀在 render partial → idea card → `thumbnail_ideas` frontmatter（寫入者已退役）— 退役後 approve 在 UI 上不可達 |
| `prepare_existing_ideas_for_template` `:509` | 保留 shim 或同步改 | 被 `bridge_projects.py:74-75,362-366` import，隨手刪 = ImportError |

UI 零 LLM 的硬約束：VPS FastAPI 呼叫不到桌機 Cowork。v1 用 taste loop 正當化無重抽按鈕，
但 `shared/taste_loop.py` 不存在 — v3 改為兩件具體的事：`reject_note` 純文字欄位（零 LLM 的
capture）+ `title_trace.json` 存完整推導鏈（冷 session 只重抽一條的基礎，兼 D14 resume）。

### A12/A13 — skill 改造與深度紅線
批次模式顯式 `--batch <dir>`，不做語意判斷；description 不加批次字樣（防「幫我想這篇的標題」
誤觸發直接寫檔）；Step 7 改互斥分支。PLAYBOOK.md 給 `angle_combo → archetype_id` 映射表 +
title 側 D/F-grade gate（現行只有 thumbnail 側有）。長片深度三不砍：panel 2–3 輪迭代／
關鍵字真訊號／淘汰賽。可砍：對話推導鏈（改寫 JSON）。詳見
`memory/claude/feedback_title_brainstorm_is_highest_leverage.md`（同日二修版）。

### A14 — resume
兄弟 skill 全有（podcast-pipeline 進度偵測表＋不跳段；highlight-cut 冪等單條重建；
subtitle-correct 兩段式），本線是 7 支多階段流程不能沒有。`title-brainstorm` 現行「預設列在
對話、不落檔」（SKILL.md:38）若不改，第 5 支掛掉時前 4 支的 TA 畫像/Tier 池/panel 分數全蒸發。

---

## 附錄 B — Amendments to ADR-033

| ADR-033 條目 | 變更 |
|---|---|
| D2（title/thumbnail 獨立 A/B） | Superseded by D9 — 綁定成 package；A/B 建立為 Studio 手動（無 API） |
| D8（funnel Stage 3 列 follow-up） | Amended — subagent vision 落地 + A8 四條件 |
| D9（`thumbnail_active_cutouts` frontmatter 宣告） | Amended — 遷到 packages.json 顯式 `host_cutout`/`guest_cutout` 欄位 |
| D6/PR4（sibling router） | Amended by D11 — 5 retire／6 rewrite／HTMX chain 作廢 |
| 入口契約 | Amended by D3+D10 — episode-segment 入口 + vault packaging SoT |
| 背景層（`thumbnail_worker.py:177` "PR5 wires Unsplash"） | 記為未落地 — 封面現為純色/漸層；「背景：」是死欄位；diversity 契約收斂到做得到的軸（表情/大字/裝飾）；`director_notes` 同被忽略 |
| Status | → `Accepted (as amended by ADR-054)` |

---

## 附錄 C — `packages.json` schema 草案（OQ2 素材）

```jsonc
{
  "episode": "20260723 謝伯讓",
  "generated_at": "...",
  "cuts": [{
    "cut_id": "punch-L1",                  // = winners.json id；對 Resolve timeline 的機器對應
    "format": "long",                       // long | short — 發布層 render preset 依據
    "information_origin": "full_text",
    "visual_recipe": "podcast",
    "aspect": "16:9",
    "titles": [ /* 長片 5 條；短片 1 條（LLM 直出） */
      {"text": "...", "archetype_id": "T-A3", "angle_combo": ["反直覺","恐懼"],
       "payoff": "...", "cite": "srt/punch-L1_r003.srt#12", "rank": 1,
       "panel_note": "rank 4-5 必填落選理由一句 — gate 顯示用（VPS 讀不到 G: 的 trace）"}
    ],
    "packages": [ /* 長片 3 組；短片 [] */
      {"title_rank": 1, "thumbnail_png": "Attachments/packaging/20260723-xieboran/pkg-L1-1.png",
       "thumb_archetype_id": "T-V8", "joint_pairing_id": "JP-1",
       "host_cutout": "Attachments/cutouts/shosho/surprised/1.png",
       "guest_cutout": "Attachments/cutouts/podcast/20260723-xieboran/guest_v2_thoughtful.png"}
    ],
    "citations": ["Science 2010 心思漫遊與快樂度"],          // ← 自 review_brandlens.json / 選段企劃
    "brand_flags": ["Lancet 45–47% 為族群層級數據，需標出處"], // ← 同上；發布層流進描述欄
    "title_trace_ref": "packaging/punch-L1/title_trace.json"
      // ⚠️ D10 硬規則①的唯一豁免欄：working-set 相對路徑（G: 碟），僅桌機 skill 解析、
      //    VPS gate 不讀（gate 需要的落選理由已在 titles[].panel_note）
    // 頂層縮圖欄位規則：**長片不帶此欄**（縮圖 = packages[approval.primary_package].thumbnail_png，
    // 不重複存）；**短片必須明填 "thumbnail": null**（≠ 省略 — 省略分不出「不需要」與「還沒做」）
  }]
}
```

`approval.json`（Bridge 寫、skill 與發布層讀）：
`{cut_id, approved: bool, primary_package: 1, reject_note: "...", decided_at}`

---

## 附錄 D — publish handoff 接縫（回答 §3.3 / §5，形成 D15）

**§3.3 三題的裁決**：
1. **縮圖只長片需要** — 是。硬理由：Test & Compare 不支援 Shorts；短片 `thumbnail: null` 明填
2. **citations/brand_flags 由 packaging skill 填** — 且來源**不是散文**：`highlights/review_brandlens.json`
   是機器可讀 JSON（handoff §3.3.2 說「活在散文裡」不準確 — 散文的是選段企劃報告，結構化的
   review_*.json 一直都在）。skill 從 JSON 搬進 packages.json，發布層零 parse
3. **交接檔不放 episode 資料夾** — 統一為 **vault `Attachments/packaging/<episode>/` 是唯一交接面**
   （packages.json + approval.json）。理由：審核 UI（VPS）與 uploader（桌機）都讀得到 vault；
   episode 資料夾只放 working set（發布層不讀）。發布層在桌機讀本機 vault，同樣零網路。
   **不再有第三份 `highlights/publish/<cut_id>.json`** — 同一份資料兩個位置必然漂移

**§5 五條反向依賴的對齊**：
| # | 要求 | 回覆 |
|---|---|---|
| 1 | 必須落檔 | ✅ packages.json（D10/D14 已定） |
| 2 | `format` 必填 | ✅ schema 一等欄位（短片燒字幕的 render 地雷收到） |
| 3 | 長片縮圖路徑／短片明填 null | ✅ 皆 vault-relative |
| 4 | citations/brand_flags 上游填 | ✅ 見上第 2 題 |
| 5 | 標題是「已決定」而非候選 | ⚠️ **形狀變了，請發布層側跟進**：approve 後 `approval.json.primary_package` 即「已決定」，發布層上傳用它；**另 2 組是 A/B 備用**（Test & Compare 無 API → 修修 Studio 手動建測試），審核頁照原設計「顯示已決定 + 改寫欄」即可，另加「複製 A/B 備用」兩顆純文字按鈕 |

**留在發布 session 的**（本 ADR 不碰）：§4.1 vault vs DB SoT、§4.2 code 歸屬、§4.3 審核手勢、
§4.4 排程語意、§4.5 OAuth/token（三個 env key 已驗證全空）、§4.6 續傳；§6 Slice 0 探針
**不依賴本 ADR，建議立即先行**。

---

## 附錄 E — Consequences 全文

**好的**：運算層零重寫續用；金額成本 ≈ 0；highlight-cut mandate（長片部分）續有效；
A/B 的 watch-time 結果天然是 revealed-preference 資料。

**要付的**：quota 一集 ~30 次 persona pass；Bridge 重寫（A11）；tests 34/48 受影響
（17 刪：TestBrainstorm/TestTitleBrainstorm/TestPodcastBrainstormHappyPath/TestPlaybookIntegration/
兩組 reroll；7 fixture 重寫；9 re-target：TestCommit/TestCandidateServing 舊落點；1 反轉 `:586`；
另 `test_bridge_projects.py:129,247`）；VAULT-LAYOUT 補登三項；A/B 測試每支長片一次 Studio 手動操作。

**明確不做**：social post/carousel（`data/repurpose/` 本機無 run 證據）；上傳/排程細節
（發布 session，Slice 0 先行）；`aspect: 9:16`；highlight-cut 選段邏輯與字幕產線。

**發布層已驗證前提**（供下一場用）：`.env` 三個 YouTube OAuth key **值全空**（僅唯讀
`YOUTUBE_API_KEY` 有值）→ 需 OAuth consent + refresh token；Test & Compare 桌機 Studio only、
≤14 天、不支援 Shorts、**無 API**。
