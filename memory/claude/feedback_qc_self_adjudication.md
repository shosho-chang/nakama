# feedback_qc_self_adjudication

- **id**: feedback_qc_self_adjudication
- **type**: feedback
- **created**: 2026-07-25
- **confidence**: high
- **description**: QC/uncertain 清單自己裁決完，只升級真無法判定的極少數；「聽音檔」= 自己重開 WhisperX 驗證

## 事實

2026-07-25 字幕校正 QC 67 項全數列給修修拍板，被糾正：「既然你都知道了，為什麼
不直接聽完然後做決定？要我拍板的地方實在太多，我沒那麼多時間。」

## Why

修修的時間是系統最稀缺資源。HITL gate 的意義是「攔住我判斷不了的」，不是
「把我能判斷的也排隊給他」。凡是我有工具能自行驗證的（重跑 ASR、分軌 mic、
refs 查證、web 查證），標「請人工確認」就是偷懶把成本外部化給修修。

## How to apply

- QC uncertain 項目：先窮盡自有手段——裁音檔片段重開 WhisperX（無 prompt 偏置）、
  重疊語音改聽分軌 stem、人名地名派 agent 查 refs/web——然後直接裁決
- 重聽支持建議 → 改；重聽兩次仍是原文 → 保留（講者口誤忠實保留）
- 裁決 + 證據寫成 decisions 檔（修修可否決），升級清單目標 <5 項
- 通則：任何「需人工確認」標籤打出去之前，先問自己「我真的沒有工具能驗證嗎？」
- 相關：feedback_dont_ask_permission_at_every_step（同一原則的另一面）
