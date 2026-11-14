# ADR-065 舊路線：從 skill 移出的原文（2026-11-14）

ADR-066 Phase 6 規定 production skills 不得再含 ADR-065 呼叫；長片視覺的唯一路線是
`scripts/run_finished_cut_production.py`（實跑手冊在 `.claude/skills/longform-cut/SKILL.md`）。
2026-09-11 呂冠緯集，agent 照著 skill 裡殘留的 ADR-065 段落跑完整條舊 chain，三支長片的
Director 企劃全部白工。以下是當時留在各 skill 裡的原文，一字未改地搬到這裡，**只供讀舊
receipt／舊 state 時對照，不是操作指示**。

---

## 出處：podcast-pipeline／S9（接在 `run_short_director.py` 名稱邊界那句之後）

完成後呼叫 trusted producer（base generation不帶
request；finished-review revision必須傳該 job immutable `request.json`，不可推斷 latest）：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_orchestrator.py "<episode>" `
  --cut-id <winner-id> [--revision-request "<episode-local immutable request.json>"]
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py status "<episode>" --cut-id <winner-id>
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py verify "<episode>" --cut-id <winner-id>
```

Truth root是 `highlights/visual-pipeline/<winner-id>/`；`PENDING.json`指向工作中的 generation，
`CURRENT.json`只在 semantic audit全數接受後 pointer-last切換，四份 canonical artifacts位於
`revisions/<revision-id>/`。新 generation crash/invalid不得破壞上一 CURRENT；同一 immutable request retry
必須 resume同一 revision。

接著的 agent-owned receipt 順序固定為：

```text
revisions/<revision-id>/DIRECTOR-WORK.json
  → dispatch subscription agent，完整讀取 brook-director skill
  → DIRECTOR-PLAN.json (podcast-highlight-director-plan-v1)
  → trusted accept-director（actual worker/execution/session identity）
  → dispatch另一個 subscription agent，完整讀取 brook-dp skill
  → DP-FULFILLMENT.json (podcast-highlight-dp-fulfillment-v1)
  → trusted accept-dp（different worker + session）
  → resume原 Director session做 second-pass semantic audit
  → SEMANTIC-AUDIT.json (podcast-highlight-visual-semantic-audit-v1)
  → trusted accept-audit → CURRENT pointer-last
  → deterministic verify = ready_to_materialize
```

Codex orchestrator以 `codex exec --json`保存 Director session、另開 DP session，再用
`codex exec resume <DIRECTOR_SESSION_ID>`做 audit；proposal自報 identity一律拒絕。Claude Code route則以
兩個隔離 subagents完成相同順序並恢復原 Director handle；accept CLI exact flags以 `brook-director`／
`brook-dp` skill為準，不能用一個 generic agent handwrite receipts或 `_broll.json`／`_titles.json`。

Director 必須逐 event 綁 exact tight-SRT quote/time/hash、分類、描述與 negative constraints；DP 必須對每個
event 保存 mode、`target_lane`、不同搜尋切面、候選、selected reason與 source/license/hash。這條 contract
涵蓋**所有 content visuals**：Stock／Hero／keyword／quote／chapter／card，不是只管理 Stock Video。
結構性 badge／camera correction／guest namecard可維持各自既有 deterministic contract，不冒充內容視覺。
Semantic audit 必須 exact
覆蓋所有 event，逐一判斷「畫面是否就是這句語意」，不能把檔案存在、來源可信或數量達標冒充語意通過。
Semantic audit worker identity必須等於原 Director、且不同於 DP；由 Director回頭檢查 DP是否忠實實現
原意圖，不是 DP自審。Missing coverage 或 mismatch 直接 invalid。

只有 complete chain fresh 驗證成 `ready_to_materialize` 後才可執行：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_short_broll.py "<episode>" --id <winner-id> --stills "<stills-dir>"
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_short_titles.py "<episode>" --id <winner-id>
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_short_sfx.py "<episode>" --id <winner-id>
E:\nakama\.venv-v2\Scripts\python.exe scripts\run_short_review.py "<episode>" --id <winner-id>
```

`run_short_broll.py` = materializer，**不代表 `brook-dp` skill** execution；它不得搜尋、挑選、改寫 intent
或為缺件補預設素材。缺少／stale／invalid 任一 receipt 就停在對應 agent-owned next work，不得用既有
`_broll.json`、至少三支 Stock Video或 legacy v1 materialization receipt旁路。普通 pending 不是 human
gate；agent應繼續完成下一份 receipt，**只有 ambiguity 才是 HITL**。`run_short_titles.py` = materializer，
同樣只能 consume DP核准的 title implementation，不得自行發明 Hero/keyword文字或落點。

Bridge finished review按「保存草稿」／request changes後的自動行為固定為：router把該 revision feedback與
fresh preview/master/tighten identities封成 episode-local immutable `request.json` → watcher的 generic agent只
能產非視覺 tightening input → `run_visual_pipeline(..., revision_request=context['request_path'])` dispatch
Director/DP/audit → trusted `emit_audited_recipe`同時產 `_broll.json`與`_titles.json` → 兩份 recipe fresh
preflight通過後才可開始 Resolve transaction → 重建 `nakama.finished_cut_review_manifest.v2`。Manifest cut row
必須綁 Stock Video v2 receipt與 CURRENT visual DAG（pointer + work/Director/DP/audit identities）。任何 phase
失敗會把 job標成 failed並保留上一 CURRENT／成片；不會進 Resolve，也不會默默沿用 generic agent手寫視覺。
新版 preview成功後回到同一 finished review human gate，不新增中途 approval。
部署時既有 `nakama.finished_cut_review_manifest.v1`只能作 legacy read-only preview：允許保存修改以觸發上述
revision，但不得 approve／進 Packaging，也不得原地改 schema或重新簽名冒充 v2；新 revision成功重建 v2
後才恢復核准。

---

## 出處：highlight-cut／Step 3.2

## Step 3.2 — Podcast Highlight visual production truth（ADR-065）

這是 Podcast derivative 的 mandatory adapter；不要拿 standalone Video Production Line 的
`data/script_video/<ep>/storyboard.yaml` 代打。每個 cut 的 truth root 是：

```text
<episode>/highlights/visual-pipeline/<id>/
  PENDING.json
  CURRENT.json
  revisions/<revision-id>/
    DIRECTOR-WORK.json       podcast-highlight-visual-work-packet-v1
    DIRECTOR-PLAN.json       podcast-highlight-director-plan-v1
    DP-FULFILLMENT.json      podcast-highlight-dp-fulfillment-v1
    SEMANTIC-AUDIT.json      podcast-highlight-visual-semantic-audit-v1
```

`run_short_director.py` = camera/Timeline director，**不代表 `brook-director` skill** execution。
它完成機位與 derived Timeline 後，deterministic runtime 才能以 Editorial Master、winner/materialization
lineage與 preflight綁定的 exact tight SRT建立 work packet；agent不得自行找 mtime latest。接著由 subscription agent完整讀 `brook-director` skill產
Director plan；另一個 agent完整讀 `brook-dp` skill履約；再把 fulfillment交回**同一個 Director worker**做
exact event coverage與語意 audit（Director identity相同、DP identity不同）。普通 `awaiting_director`／`awaiting_dp`／
`awaiting_semantic_audit` 是 agent-owned next work，只有 ambiguity 才是 HITL。

`DIRECTOR-WORK.candidate.sections` 是上游完整論述地圖。Director 必須先讀它，再讀 tight SRT 校正原句錨點；
transition event 以 `transition_before=true` 為候選，不是硬性全收。Director 的責任是決定畫面與精確落點，
不是從零重做整支長 Highlight 的章節分析。

只有 fresh status `ready_to_materialize` 才能把 validated selection轉成 canonical broll recipe。
`run_short_broll.py` = materializer，**不代表 `brook-dp` skill** execution；它不得自行找素材、猜 intent、
或用 legacy receipt補過。缺少／stale／invalid 任一 receipt fail closed；既有 `_broll.json` 或素材數量
達標都不是旁路授權。`run_short_titles.py` = materializer，也只能 consume DP產的 title implementation。
Contract涵蓋所有 content visuals：Stock／Hero／keyword／quote／chapter／card；結構性 badge／camera correction／guest namecard
維持各自 deterministic contract。Bridge finished review只展示 fresh receipt
lineage，不新增正常人類 gate。

> ⛔ **已停用（ADR-066）**：`podcast_highlight_visual_orchestrator.py` 屬 ADR-065 revision-scoped
> visual DAG，已被 Finished Cut Production 取代。long 的唯一生產路線是
> `scripts/run_finished_cut_production.py`。下面的指令保留供讀舊 receipt 對照，不要執行。

Codex production route的 exact command（base不帶 request；Save Draft/reject必帶 job內 immutable request）是：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_orchestrator.py "<episode>" `
  --cut-id <id> [--revision-request "<episode-local immutable request.json>"]
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py status "<episode>" --cut-id <id>
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py verify "<episode>" --cut-id <id>
```

Orchestrator固定執行 `init → Director proposal/accept-director → 不同 DP proposal/accept-dp →
resume同一 Director session做 audit/accept-audit → verify`。Claude Code route也必須依同序 dispatch兩個隔離
subagents，audit回到原 Director handle；accept commands需使用 host觀察到的 worker/execution/session identity，
不可信任 proposal自報。Exact accept flags見 `brook-director`／`brook-dp`。`accept-audit`成功才切 CURRENT；
新 PENDING crash/invalid保留上一 CURRENT，同 immutable request retry只 resume同 revision。

---

## 出處：highlight-cut／「長片線 Step 6–11」節的交接段

新交接由上方 Stage 5 orchestrator 的 mutable state 與 targeted visual review 管理。舊 ADR-065 chain
只在明確採用 legacy adapter 時由該 adapter 自己處理，不是 orchestrator 的 outer gate。

---

## 出處：brook-director／Long Highlight orchestrator v2 與 Legacy ADR-065 adapter 兩節

## Long Highlight orchestrator v2（優先分流）

收到 payload 同時標示
`long_highlight_contract.route="long_highlight_orchestrator_v2"` 與
`long_highlight_contract.validation_profile="semantic_visual_minimal"` 時，本節優先於下方 ADR-065 legacy route。
Director 只做 LLM 擅長的完整逐字稿／section 語意理解，產出 chapter、Hero、Stock、人物
inset 等 visual intents；後續仍保留 DP fulfillment 與 targeted visual agent 實看成品，不能刪除
或改成機械關鍵字比對。

這條 v2 route 的外層 state 是 mutable draft。不得啟動
`podcast_highlight_visual_orchestrator.py`／ADR-065 frozen revision DAG，不建立或驗證 immutable
request、worker/session identity、execution receipt、hash／source lineage，也不要求 same-session
Director audit 或全量 fresh verify。單一 event 語意或畫面不符時，只退回該 event 給 DP／targeted
visual fix，不得重跑整支 Director。

Director 不負責判定上游 source、range、片長、sections 或 chapter timestamp gates；chapter 沒有可靠
cut-local／source／cue 時間時，由 orchestrator 的 upstream gate 處理，Director 不猜 0:00。在 v2 的
Director → DP → visual／preview 段，selected footage 不可播放、targeted agent 判定 semantic／visual
mismatch、Resolve mutation 會破壞既有 baseline，以及 preview 不存在、實際 <480 秒或時長未知，都會
阻止往發布前進。這些條件由各自下游 stage 回報；Director 不新增來源驗證。其他 schema 小漂移、缺
optional metadata 或 reviewer 差異只記 warning／局部修正。

## Legacy Podcast Highlight production adapter（ADR-065；僅限沒有 v2 marker）

只有 payload 沒有上述 v2 route marker 時，才使用本節 frozen revision／receipt 流程；不得把 legacy
要求帶進 `long_highlight_orchestrator_v2`。

當輸入是 Podcast episode + cut ID，唯一 production truth root 是 revision-aware DAG：

```text
<episode>/highlights/visual-pipeline/<cut-id>/
  PENDING.json
  CURRENT.json
  revisions/<revision-id>/
    DIRECTOR-WORK.json       podcast-highlight-visual-work-packet-v1
    DIRECTOR-PLAN.json       podcast-highlight-director-plan-v1
    DP-FULFILLMENT.json      podcast-highlight-dp-fulfillment-v1
    SEMANTIC-AUDIT.json      podcast-highlight-visual-semantic-audit-v1
```

> ⛔ **已停用（ADR-066）**：`podcast_highlight_visual_orchestrator.py` 屬 ADR-065 revision-scoped
> visual DAG，已被 Finished Cut Production 取代。long 的唯一生產路線是
> `scripts/run_finished_cut_production.py`。下面的指令保留供讀舊 receipt 對照，不要執行。

Production 預設由 trusted orchestrator執行；base generation不得帶 `--revision-request`，finished review重做則
必須傳該 job的 immutable `request.json`，不得找 mtime latest或讀會繼續 append的 feedback JSON：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_orchestrator.py "<episode>" `
  --cut-id <cut-id> [--revision-request "<episode-local immutable request.json>"]
```

這個 command固定完成 `init → Director proposal → accept-director → 不同 DP proposal → accept-dp →
同一 Director session semantic audit → accept-audit → verify`。Codex runtime的 Director首輪是
`codex exec --json`，audit必須 `codex exec resume <DIRECTOR_SESSION_ID>`；DP另開不同 session。Agent只可寫
phase-local proposal，trusted host從實際 process/session產生 worker identity與execution receipt，再呼叫 accept；
不得採信 proposal自報的 worker/session，也不得讓 agent寫 canonical receipts。

Claude Code沒有呼叫上述 Codex worker orchestrator時，必須以兩個隔離 subagents維持相同順序；保存原 Director
subagent/session handle並在 audit階段恢復它，DP必須是另一個 handle。Deterministic CLI sequence的 accept參數是：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py init "<episode>" --cut-id <cut-id> [--revision-request "<immutable request.json>"]
# Director writes a phase-local proposal, then trusted host supplies actual execution identity:
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py accept-director "<episode>" --cut-id <cut-id> --revision-id <revision-id> --proposal "<director-proposal.json>" --worker-id <trusted-director-worker-id> --execution-id <trusted-director-execution-id> --session-id <trusted-director-session-id>
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py status "<episode>" --cut-id <cut-id>
```

1. 先用 status/validator fresh重驗 PENDING revision內的 `DIRECTOR-WORK.json`。它必須綁 exact episode/cut、
   Editorial Master、winner/materialization lineage、tight SRT與 exact revision request；不要搜尋另一份字幕。
2. 先讀 `DIRECTOR-WORK.candidate.sections` 的完整論述地圖，再完整讀 work packet 指定的 tight SRT，
   以 exact cue quote/time/hash校正 section 錨點並建立每個 visual event。`transition_before=true` 是
   highlight-cut 的語意候選：逐項接受、移動或否決，不得忽略後從零平均撒 TR。
3. 只寫意圖：category、form/description、必要 `on_screen_text`（保留人工換行）、negative constraints與
   broad source hint。
   不搜尋具體候選、不選 asset、不填 license、不寫 `_broll.json`。
4. 產出 phase-local `podcast-highlight-director-plan-v1` proposal；只有 trusted `accept-director`可寫
   `revisions/<revision-id>/DIRECTOR-PLAN.json`。不可直接覆寫 canonical receipt或即席新增欄位。
5. Fresh status必須前進到 `awaiting_dp`。交回 orchestrator dispatch一個不同 agent完整執行
   `brook-dp`；Director不得冒充 DP。DP完成後，orchestrator必須把 fulfillment交回**同一個 Director
   worker identity**做 semantic audit，再接受 `SEMANTIC-AUDIT.json`。

Semantic audit是 Director對照自己原意圖、exact transcript quote與 DP實現的第二次獨立判斷：worker identity
必須等於 `DIRECTOR-PLAN.json` 的 Director，且不得等於 DP。逐 event exact coverage；任一 mismatch就
invalid，不可為了進 materializer改成 pass。只有 trusted `accept-audit`全數通過時才 pointer-last切換
`CURRENT.json`；pending generation crash/invalid必須保留前一個 CURRENT，重跑同一 immutable request只 resume
同一 revision，不得另造 generation。

Podcast route沒有 standalone `/brook/video/<ep>` text approval中繼 gate。普通
`awaiting_director` 是 agent-owned next work；只有 transcript／authority／創意意圖真正歧義才升級 HITL。
Bridge `/bridge/highlights/<episode>/finished` 只在成片 review時 read-only展示 fresh receipts。

名稱紅線：`scripts/run_short_director.py` 是 **camera/Timeline director**，只做機位、構圖與 Resolve
derived Timeline；它的成功**不代表本 skill執行過**。既有 `_broll.json`、Stock Video數量或舊
materialization receipt也不能替代 `DIRECTOR-WORK.json` → `DIRECTOR-PLAN.json`。

Director plan涵蓋所有 content visuals：Stock／Hero／keyword／quote／chapter／card；結構性
badge／camera correction／guest namecard由各自 deterministic contract處理，不為了湊coverage重複規劃。

---

## 出處：brook-dp／Long Highlight orchestrator v2 與 Legacy ADR-065 adapter 兩節

## Long Highlight orchestrator v2（優先分流）

收到 payload 同時標示
`long_highlight_contract.route="long_highlight_orchestrator_v2"` 與
`long_highlight_contract.validation_profile="semantic_visual_minimal"` 時，本節優先於下方 ADR-065 legacy route。
DP 只履行 Director 已定義的 visual intents：用 LLM 搜尋／挑選語意相符的 Stock 或規劃
Hyperframes／composition，保留具體選擇理由；之後仍由 targeted visual agent 實看 selected 畫面，
不能刪除 Director、DP 或 visual review 的語意工作。

這條 v2 route 的外層 state 是 mutable draft。不得啟動
`podcast_highlight_visual_orchestrator.py`／ADR-065 frozen revision DAG，不建立或驗證 immutable
request、worker/session identity、execution receipt、hash／source lineage，也不要求 same-session
Director audit 或全量 fresh verify。單一 event 搜尋失敗、語意不符或畫面不符時，只將該 event
留 pending／交 targeted visual fix；不得重跑整輪 DP 或 Director。

DP 不負責判定上游 source、range、片長、sections 或 chapter timestamp gates；chapter 沒有可靠
cut-local／source／cue 時間時，由 orchestrator 的 upstream gate 處理，DP 不猜 0:00。在 v2 的
Director → DP → visual／preview 段，selected footage 不可播放、targeted agent 判定 semantic／visual
mismatch、Resolve mutation 會破壞既有 baseline，以及 preview 不存在、實際 <480 秒或時長未知，都會
阻止往發布前進。DP 不新增來源、fingerprint 或 receipt 驗證；其他 schema 小漂移、缺 optional
metadata、候選數不足或 reviewer 差異只記 warning／局部修正。

## Legacy Podcast Highlight production adapter（ADR-065；僅限沒有 v2 marker）

只有 payload 沒有上述 v2 route marker 時，才使用本節 frozen revision／receipt 流程；不得把 legacy
要求帶進 `long_highlight_orchestrator_v2`。

Podcast episode + cut ID 不讀 standalone `data/script_video/<ep>/storyboard.yaml`。唯一 truth是
revision-aware DAG：

```text
<episode>/highlights/visual-pipeline/<cut-id>/
  PENDING.json
  CURRENT.json
  revisions/<revision-id>/
    DIRECTOR-WORK.json       podcast-highlight-visual-work-packet-v1
    DIRECTOR-PLAN.json       podcast-highlight-director-plan-v1
    DP-FULFILLMENT.json      podcast-highlight-dp-fulfillment-v1
    SEMANTIC-AUDIT.json      podcast-highlight-visual-semantic-audit-v1
```

> ⛔ **已停用（ADR-066）**：`podcast_highlight_visual_orchestrator.py` 屬 ADR-065 revision-scoped
> visual DAG，已被 Finished Cut Production 取代。long 的唯一生產路線是
> `scripts/run_finished_cut_production.py`。下面的指令保留供讀舊 receipt 對照，不要執行。

Production預設呼叫一次 trusted orchestrator；finished review revision必須傳 exact immutable request snapshot：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_orchestrator.py "<episode>" `
  --cut-id <cut-id> [--revision-request "<episode-local immutable request.json>"]
```

Claude Code手動/subagent route不得省略 accept順序。DP proposal只能寫 phase-local output；trusted host提供實際
DP execution/session identity，不能從 proposal抄 worker欄位：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py accept-dp "<episode>" --cut-id <cut-id> --revision-id <revision-id> --proposal "<dp-proposal.json>" --worker-id <trusted-dp-worker-id> --execution-id <trusted-dp-execution-id> --session-id <trusted-dp-session-id>
# Resume original Director worker/session, never DP, for the semantic-audit proposal:
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py accept-audit "<episode>" --cut-id <cut-id> --revision-id <revision-id> --proposal "<semantic-audit-proposal.json>" --worker-id <same-trusted-director-worker-id> --execution-id <new-trusted-director-audit-execution-id> --session-id <same-trusted-director-session-id>
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py verify "<episode>" --cut-id <cut-id> --revision-id <revision-id>
```

1. Deterministic status/validator必須 fresh驗出 `awaiting_dp`；work packet、Director plan、Editorial
   Master或 work packet綁定的 exact tight SRT任一 missing/stale/invalid就停，不得自行找 latest或修 receipt。
2. Exact覆蓋 `DIRECTOR-PLAN.json` 的所有 events。每個 fulfillment保存 mode、`target_lane`、2–5 個不同
   語意切面的 search queries、候選、selected candidate、選擇理由、negative checks及 source/license/hash；找不到就
   明示 none/合法降級，不拿同主題 generic footage湊數。
3. 畫面必須對應 event的 exact transcript quote，不只對應廣泛主題。來源可信、ffprobe/hash通過與至少
   三支 Stock Video都是機械條件，不能替代語意證明。
4. 產出 phase-local `podcast-highlight-dp-fulfillment-v1` proposal，只有 trusted `accept-dp`可寫
   `revisions/<revision-id>/DP-FULFILLMENT.json`；不可直接手寫 canonical receipt或 production `_broll.json`。
5. Fresh status必須前進到 `awaiting_semantic_audit`。交回 orchestrator，讓**原本同一個 Director
   worker identity**做 semantic audit；該 identity必須不同於本 DP。DP不得自審或寫
   `SEMANTIC-AUDIT.json`。

Auditor exact覆蓋每個 selected materialization且全數 semantic match後，`accept-audit`才 pointer-last更新
`CURRENT.json`，status成 `ready_to_materialize`。失敗的 PENDING不得破壞前一個 CURRENT。
`scripts/run_short_broll.py`只是一個 **materializer**；它的成功不代表本 skill執行過，也不授權 DP
在 renderer內臨時選片。普通 pending是 agent-owned next work，只有 authority、license或主觀語意真的
ambiguity才是 HITL。Bridge只 read-only展示 fresh receipt與 audit，不新增正常人類 gate。

DP履約涵蓋所有 content visuals：Stock／Hero／keyword／quote／chapter／card，並同時產出 **B-roll 與 title implementations**。
結構性 badge／camera correction／guest namecard維持各自 deterministic contract。
`scripts/run_short_titles.py`也只是 materializer；不得在 receipt chain外另選 Hero text或落點。
