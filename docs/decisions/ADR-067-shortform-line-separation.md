# ADR-067: 短片產線與長片分家，短片素材層自帶 gate

- Status: Accepted
- Date: 2026-08-30
- Owner: Brook / Podcast Stage 5
- Stage: 5 Multi-channel Production
- Preserves: ADR-064 Editorial Master 為內容邊界的唯一真值
- Narrows: ADR-065 的 Director／DP／語意稽核收據鏈，範圍限縮為**長片**
- Depends on: ADR-066 承認 Long/Short 各有 creative policy（本 ADR 補上 Short 那半邊今天能跑的路）

## Context

2026-08-30 修修裁決：「反正現在就是長片跟短片的流程跟呼叫的東西完全都要獨立，不要混在一起了。
就算用到同一段不同的程式，你要給它不同的名稱。要不然之後要改，又會全部混在一起。」

裁決之前，`run_short_director.py` / `run_short_titles.py` / `run_short_broll.py` 這三支同時服務
長短片，用 `FORMAT_*` 字典分流。實際後果不是理論上的耦合，是三次具體事故：

1. ADR-064 cutover（`d5695921`）把「不得從原始機位重建」一併套到短片，短片因此只能拿成片
   中央硬裁 3.2 倍成 9:16——臉被切掉、中間出現空畫面。長片不需要機位，所以沒人發現。
2. 短片的 highlight gate 覆寫掉長片的 `winners.json` 與五份 review 檔，長片的
   `packaging-plan.json` 與 winners 互相矛盾。
3. 短片的字卡走逐字稿保證（見下）之後，`run_short_broll` 仍然會去讀
   `<cid>_titles.json` 並要求每張字卡都帶 DP 的 `visual_materialization`——素材層一接上，
   字卡那條保證就整個失效。

素材層還多一層時間問題。ADR-065 的收據鏈已被 ADR-066 標為退役（SKILL Step 3.2：
「已停用（ADR-066）…不要執行」），而 ADR-066 的 `ShortPolicy` 目前只驗「片長 ≤ 60 秒」與
「title-like 卡片 ≤ 2 張」——後者跟短片逐子句字卡直接衝突，`_materialization` 裡也沒有
short 分支。也就是說：**短片今天沒有任何一條能把 stock 放上去的路。**

## Decision

### 1. 三個階段各有短片專屬入口

| 階段 | 短片 | 長片 |
|---|---|---|
| 導播 | `scripts/run_shortform_director.py` | `scripts/run_short_director.py` |
| 字卡 | `scripts/run_shortform_titles.py` | `scripts/run_short_titles.py` |
| 素材 | `scripts/run_shortform_broll.py` | `scripts/run_short_broll.py` |

資料同樣分家：winner 依 format 找 `winners.<format>.json`，長片沒有該檔時才退回
`winners.json`（既有行為不變）。

機械件（Resolve append、Fusion transform、幾何、hyperframes render）暫時仍由短片入口
`import` 長片模組，並在 import 處標註；那是純機械、與畫幅語意無關的部分，後續搬到中性命名的
模組，不再新增跨線相依。

### 2. 短片字卡：逐字稿保證取代 DP 稽核鏈

字卡企劃宣告 `covers_full_transcript` 時，畫面上的每個 state 都逐 cue 承接該支的 tight SRT
原話——沒有創作、沒有改寫。這個保證是機械可驗的（`_validate_transcript_driven_title`、
`_validate_full_transcript_coverage`），所以短片字卡不需要 DP 稽核鏈。

代價是短片的字卡**不上 Resolve burn-in 字幕軌**（兩層一起出現會疊字），而那份 tight SRT 的
角色也隨之改變：它是**逐字證據**，不是顯示層，因此不切成呼吸單元、保留子句邊界。排不進字卡
版面的過長子句在導播那一步拆開（`_split_long_cues`），不在字卡層拆——字卡層拆會讓同一個 cue
出現兩次，逐字覆蓋驗證不會過。

### 3. 短片素材層：授權照驗，語意改用逐字稿錨定

`shared/shortform_broll.py`（`shortform-broll-receipt-v1`）。

**保留**：每支素材必須有 acquisition receipt（`podcast-highlight-asset-acquisition-receipt-v1`，
含 provider／source_url／license），且檔案 bytes 的 SHA-256 必須對得上收據。授權這條不因為
是短片就鬆。

**取代**：獨立 DP agent 覆核語意 → 兩條機械可驗的條件

1. **逐字稿錨定**：每個 item 宣告 `source_cues`，落點 t0/t1 必須包在那幾句的時間範圍內
   （容差 0.35s）。這證明不了「這支素材好」，但證明得了「它對的是哪句話」，剩下的由人看一眼。
   這與字卡的逐字稿保證是同一個精神：**把可機械驗的部分驗到底，把審美留給人。**
2. **直式**（修修 2026-08-30：「短片的素材是要直式的」）：`height > width`，否則拒收。
   橫素材裁進 9:16 只會剩中間一條，與 ADR-064 cutover 造成的「硬把橫的切成直的」同一種病。

另加兩條照抄 SKILL Step 9 的版面紀律：素材不可壓到開場上下分割（track 2 已被佔用），
也不可蓋掉 punch zoom——衝突時縮短 punch 讓位 footage，所以 gate 報錯要你改 zoom 企劃，
而不是默默疊上去。

第一版只收 `video` / `photo`。貼紙、概念卡、icon 動畫仍在長片線的收據鏈上，短片要用得各自
定 gate，不因為「反正都是 track 4」就一起放行。

### 4. 短片的 Director 與 DP 是獨立角色

修修 2026-08-30：「短影音的 Director 跟 DP 應該是要分開才對，因為他們做出來的素材跟
剪輯的緊湊程度完全都是不一樣的。」

`.claude/skills/shortform-director/` 與 `.claude/skills/shortform-dp/` 是短片專屬的
創意手冊，不再由 `brook-director` / `brook-dp` 兼任。實質差異不是換個名字：

- **畫幅**：短片 DP 的候選頁**一律鎖 Vertical**，`height > width` 才收；
  brook-dp 的對應段落寫死 Horizontal（「Podcast Long Highlight 的 query／候選頁一律鎖
  Horizontal」），兩者互斥，同一本手冊裝不下
- **景別**：直式只有 1080 寬，全景在手機上讀不出來——短片 DP 優先 close up／單一主體，
  長片沒有這個限制
- **密度**：長片是 4.5–5.5 視覺事件/分。短片在 mode B 之後**這把尺失效**——字卡逐子句
  出現，46 秒就有 20 個 state（26 事件/分），指標永遠自動達標。短片改用**具象覆蓋率**：
  逐 cue 分成具象句與抽象句，每個具象句給畫面、抽象句一律留 talking head，
  一支 45–60s 通常只有 2–3 個落點
- **支數與長度**：短片全片 2–3 支、每支 1.5–4s；長片逐章鋪數十支
- **錯一支的代價**：短片只有 2–3 個 cutaway，錯一個就是三分之一——「寧缺勿猜」在短片
  比長片更嚴

刻意共用的只有與畫幅無關的品牌事實（修修本人情境的固定 stand-in 模特兒、
負面意象禁用清單、「畫面＝語意／情緒極性」的判準），並在短片手冊裡明確標註
「改那邊兩線一起變」——共用是決定，不是遺留。

### 5. punch zoom 錨在逐字稿座標

`<id>_zoom.json` 不再寫 timeline 秒數，改寫 `cue` / `phrase` / `until_cue`：

- `phrase` 必須是該 cue 的**句首**——句中起跳需要詞級時間戳，本集只有 memo 的句級 segment，
  用字元比例內插實測會打錯句子（算出「早就被淘汰了」在 14.32s，實際放大落在「喜歡玩的物種」上）
- 放掉的點一律落在 `until_cue` 的句尾，不可能停在句中
- 同一句裡放掉又拉回直接報錯（修修：「這一句為什麼要拉遠又拉近？很奇怪」）
- ramp 提前 `PUNCH_LEAD_SEC = 0.5` 秒起跳，讓推進在句子出口前走完（修修：「要在他講那一句話前
  0.5 秒就要 zoom in，這會讓觀眾產生『等一下要講的那句話非常重要』的感覺」）；硬切不提前

絕對秒數由導播解出來寫進 `<id>_zoom.resolved.json`，下游（音效、素材 gate）吃那一份。


### 流程收斂成單一手冊：`shortform-cut`（2026-08-30 補記）

分家之後短片的操作知識散在四個地方：`highlight-cut` 的 Step 6–11、
`shortform-director`、`shortform-dp`，加上 scratchpad 的一次性企劃腳本。
實測代價是真的：punch-S02 的 20 句字卡企劃被舊的 scratchpad 腳本蓋成 31 句版本，
重跑一次就毀掉一支已驗收的短片。

所以再收一次：

- **`.claude/skills/shortform-cut/SKILL.md` 是短片線 Step 6–8 的唯一流程手冊**
  （前置 → 緊湊化 → 導播 → 字卡企劃 → 字卡 → 素材 → 音效 → 音樂 → 自檢），
  含軌道契約與已知地雷。
- `highlight-cut` 的短片 Step 6–11 整段搬走，原地只留指標——與長片線 2026-08-04
  的處理方式一致，兩線在該 skill 裡現在對稱。
- `shortform-director` / `shortform-dp` 保持不動：它們是**創意判斷**（哪幾句要畫面、
  哪一句升級成 emphasis），`shortform-cut` 是**流程與門檻**。
- 字卡企劃的機械部分升格成 repo 工具 `scripts/author_shortform_titles.py`，
  規格（論證骨架）落在 episode 的 `<id>_titles.plan.json`。scratchpad 腳本不再是流程的一部分。

判準：**只要是「重跑同一條指令要拿到同一個結果」的東西，就不能住在 scratchpad。**

## Consequences

- 短片線今天可以完整跑完 Step 6–9，不必等 ADR-066 的 Short 半邊實作完成。
- 語意正確性的把關從「獨立 agent 覆核」變成「機械錨定 ＋ 人看樣張」。短片的 cutaway 多半
  3–4 秒、一支 2–3 個，人看一眼的成本遠低於一輪 agent 來回；長片維持原本的收據鏈不變。
- ADR-066 的 Short 半邊完成後，本 ADR 的素材 gate 應被 `ShortPolicy` 吸收，`shortform-broll-receipt-v1`
  屆時退場。在那之前它是短片素材的唯一授權來源。
- 長片線一行未改：`run_short_broll` 的預設路徑仍然是 `build_authoritative_broll_receipt`。
- 短片線的操作知識只有一個入口（`shortform-cut`）。新增規則寫在那裡，不要回填 `highlight-cut`。
