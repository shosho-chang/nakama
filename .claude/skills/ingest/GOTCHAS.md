# ingest gotchas（route C）

每條附 why。出手前讀一次；reject/exclude 學到的新坑追加在最後。

## 五條紅線（Centaur 規格 v0.2 §7，有 tripwire）
- **絕不寫 `KB/Permanent/` 正文與 status。** why：那是修修的腦、權威層，品味只有他驗收；
  AI 唯一入口 `shared/permanent_layer.py:update_permanent_bookkeeping()` 白名單三 key
  （`source_refs`/`modified`/`aliases`），沒有寫正文/status/連結的 API。`assert_not_permanent_target()`
  會擋 agent 寫入路徑解析到 Permanent。
- **每個事實宣稱附 citation，溯源回 Raw / Annotation 錨點。** why：防捏造。
- **Concept 可寫可 merge，但不冒充永久卡**（`author` 欄必填 `agent_robin`）。why：provenance 分離（紅線 3）。
- **ingest 不建 MOC。** why：MOC 是修修的擠壓點，建不建是人決定。
- **Concept/Output 終端證據只能是 Sources / Raw / Annotations，不得以另一個 Concept/Output 當事實來源。**
  why：防 citation laundering / wiki 自我餵食。`shared/provenance_linter.py` 在 Concept/Output 寫入時擋
  （指向 Concepts/Outputs 的 `derived` link 不可當終端證據）；`_execute_plan` → `kb_writer` 內建過這層。

## route C 專屬坑
- **ingest 全文，不要只抽劃線段。** why：只抽劃線＝注意力鏡子，給不了漏看的洞見。劃線是強調訊號，
  不是內容邊界；要強調某段用 `--guidance` 傳。
- **主動拒絕「只收某段 / 只要重點 / 過濾內容」的請求（不只是被動全文）。** why：那是內容過濾、違反 D-A。
  pipeline 後端一律全文，但對話層也要當場說明、不能默默照做、也不能讓修修以為「摘要後只收重點」是有效用法。
  這是 DO-NOT，不只是 DO。
- **資料流別搞錯：concept 抽取吃的是「摘要」不是原始全文。** why：`_get_concept_plan(summary=summary_body)`；
  全文只進 `_generate_summary`。所以「強調某段」要透過 guidance 或（未來）annotation-emphasis 增強，
  不能假設抽取器看得到全文每個字。
- **HITL gate 不可自動放行。** why：concept plan 的 accept/defer/exclude 是修修的主觀品味驗收
  （`feedback_hitl_gate_serves_subjective_taste`）；LLM 變強也吸收不掉。execute 前一定要停。
- **plan 階段只寫 Source Summary 頁，不寫 Concept/Entity。** why：Concept/Entity 要修修點頭後（execute）才寫；
  Source 摘要在審查前寫是既有行為（ingest()/Web UI 一致），且它是 draft/candidate。
- **defer/exclude 的項目要從 plan 真的移除再 execute。** why：execute 階段不再問，plan-file 裡留什麼就寫什麼。
- **寧可 update 既有 Concept（update_merge），不要狂建新頁。** why：CLAUDE.md 禁 page explosion；
  plan 出 `update_merge`/`update_conflict` 時優先順著，不要改成 create。
- **整本書 / 影片要擋。** why：route B（書）走 Centaur 每日迴圈、route E（影片）未上線；一次收整本會違背
  re-ingest/每日累積的設計。一章當一篇可以（eval #1 vs #10）。
- **不要自動把 Concept 連到修修的 Permanent 卡，只加 defer 建議。** why：不污染他的結構（規格 §8）。

## 工程坑
- **step-script 與 Web UI 後端同源。** `scripts/ingest_steps.py` 呼叫的 `_generate_summary` /
  `_get_concept_plan` / `_execute_plan` / `_update_index` 跟 `thousand_sunny/routers/robin.py` 的
  summarizing→planning→executing 步驟是同一組 pipeline 私有方法。why：pipeline 簽名若變，兩處要一起改，
  否則對話門與按鈕門行為漂移。
- **從 repo root 跑 script**（`python .claude/skills/ingest/scripts/ingest_steps.py ...`）。why：script 靠相對位置
  把 repo root 加進 sys.path 才 import 得到 `agents.robin` / `shared`。
- **raw 檔路徑**：script 不複製到 KB/Raw、也不刪原檔（非破壞性）。why：Wave 1 最小、安全；source_refs 記實際路徑。
  若要正式歸檔到 KB/Raw，修修自己放或之後再加。
