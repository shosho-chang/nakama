# ADR-056: Podcast Subtitle V2 — Memo-first production contract

- Status: Superseded for production by ADR-063; retained as legacy forensic design
- Date: 2026-08-16
- Policy revision: 2026-08-19
- Owners: Brook / Podcast production
- Stage: 5 製作
- Supersedes: ADR-056 revisions before 2026-08-16
- Retains: ADR-032 exact-copy、ADR-050 D4 provenance
- Superseded by: ADR-063 Memo Dual-Audit Release V1 (2026-08-21)

> **Historical scope only.** The decision below documents the former Full Subtitle V2 production
> contract. Podcast E2E must not call this checkpoint／Generation／Verified Projection route by default.
> It remains readable only through an explicit `legacy-forensic` entry point. The active production
> decision and migration gates are defined by ADR-063; until its implementation verification finishes,
> documentation must not claim that the new cutover is already live.

## Context

Podcast Subtitle V2 的交付目標是：接收一份已完成 normalization、且與完整節目
clock 對齊的音檔，輸出準確、可校對、單行、時間穩定的 SRT。

鄭國威實際交付驗證顯示，Memo 1.7.5 bundled Whisper
`ggml-large-v2.bin` 的 GUI cue 邊界明顯優於先前的全域重切結果。V2 因此不再把
辨識文字、校對文字與顯示切點混成同一個可任意重算的問題。

Audio normalization 是上游已成熟能力，不屬於 Subtitle V2 runtime。V2 不持有
normalization provider 帳號、production history、重試、付費 gate、jingle 處理或
recovery state machine。

## Decision

### 1. Production input boundary

Subtitle V2 只接受：

- 已 normalized 的完整節目音檔；
- normalized-audio handoff manifest，綁定輸入音檔本身的 exact SHA-256、size、
  duration 與 accepted time。

`VerifiedNormalizedAudioHandoffAdapter` 是 provider-neutral 的驗證 seam。它只驗證
傳入音檔本身，回傳 identity clock receipt；production factory 不 import、呼叫、設定或
修改任何 normalization provider implementation。

### 2. Memo-first Recognition Evidence

Primary Recognition Evidence 固定來自一個已被 operator 接受的 Memo import：

- Memo 1.7.5；
- bundled Whisper `ggml-large-v2.bin`；
- exact normalized-audio digest；
- exact Memo source export bytes；
- exact acceptance receipt bytes；
- prompt/context、token text、timing、confidence 與 speaker observation；
- canonical manifest bytes與 lineage hash。

Memo source export 或 acceptance receipt 被改動時必須 fail closed。Raw Memo stdout
token／micro-segment 只屬於 Recognition Evidence，不是顯示切點。

Qwen3-ASR 與 Faster-Whisper 只可由明示設定啟用為 corroborating Evidence 或 targeted
audit。它們不得成為 primary recognizer，也不得提供 display boundary authority。

### 3. Accepted Memo SRT boundary authority

Production 直接接受 Memo GUI SRT 與一張 canonical acceptance receipt。Receipt 必須
綁定 exact SRT bytes 的 SHA-256、size、reviewer 與 accepted time；operator 不必手工
預造 cue manifest。Reviewed Memo cue projection 仍可作為明示的人工作業
產物，但不是正常 production project 的必要輸入。

Memo SRT 提供 ordered candidate cue timing 與 boundary evidence；它的辨識文字不是最終
文字 authority，也不必逐字等於 Recognition token text 或預先對應 Recognition token
partition。正式 `project()` 與 CLI script 共用 `memo_projection` 的同一實作：

- 將 Memo cue text 與 corrected Canonical token text 做 global alignment；
- alignment 比較會忽略空白、標點與 presentation-only Unicode 差異，但不改輸出文字；
- 將每個 Memo boundary 映射到最近的完整 corrected-token edge，絕不切開 token；
- 多個 Memo boundaries 映射到同一 token edge 時，只能依原順序 adjacent merge；
- 保存 Memo cue index provenance、boundary time delta、alignment ratio、token-snap count、
  merge count 與 boundary-retention ratio；
- alignment 或 retention 低於明示 threshold 時 fail closed。

Authority content hash 綁定 exact Memo SRT、acceptance receipt、Recognition Evidence 與
QC thresholds，並納入 projection output lineage 與 fresh-process replay。Raw
micro-segments、semantic model 建議、字數偏好或 ASR token gap 都不能自行新增 edge。

### 4. Selective audio-audit correction policy (2026-08-19 revision)

Production correction 固定採以下順序：

1. 全集、每個 Canonical span 都做 deterministic QC；
2. 每個 span 都先完成 Reference Evidence retrieval 與 text/reference audit；
3. 所有 risk-targeted spans 都做 audio audit，再對其餘 ordinary spans 做 deterministic、
   stratified 10% audio sample；
4. 10% sample 出現 major error，或 material error rate 大於 2%，擴大成 cumulative 30%；
5. 30% sample 仍大於 2% 才升級 universal audio full audit；catastrophic finding 則直接升級
   full audio audit，integrity 類 finding 立即 fail closed。

Risk-targeted selector 只接受 stored Evidence／text audit 的 closed reason codes：recognition
confidence 或 alternative-hypothesis warning、cue repair、speech coverage／timing／overlap／duration／
boundary／alignment warning、speaker/mic/crosstalk conflict、未解 name/title/foreign term/homophone/
number/unit/date/negation/quotation/reference conflict，或有 exact audio 才能決定的 insertion、deletion、
substitution、omitted-speech finding。每個 reason 必須綁 exact finding ID；只為聽審加入的 adjacent
context span 不納入 error-rate denominator。

Ordinary population 是 frozen、未被 risk-targeted 的 auditable spans。10% 與 30% tier 分別選
`ceil(0.10 * N)`、`ceil(0.30 * N)`；30% 包含原 10%。Strata 是十個 equal-duration episode
clock buckets 與 resolved speaker identity 的交叉；依比例分配，tier 容量允許時每個 non-empty
stratum 至少一筆。Allocation remainder 與 stratum 內順序以 SHA-256 決定，seed 綁 policy
version、episode ID、exact normalized-audio SHA-256、tier、stratum ID 與 stable AudioSpan ID，
不得使用 runtime randomness 或 worker discretion。

Material error 以「有至少一項 accepted correction 會改變 spoken lexical content 或 attribution 的
sampled ordinary span」計數，包含 insertion、deletion、substitution、omitted speech、speaker、
name/title/term、number/unit/date、negation 或 meaning-bearing boundary；純標點、空白、正字體例及
presentation edit 不計。Rate 的 numerator 是 material-error spans，denominator 是 completed
ordinary sampled spans；risk-targeted 與 context-only spans 分開報告，不可灌入分母。

Major error 是改變 key claim、negation、speaker、proper-name identity、number/unit/date，或遺漏至少
兩秒 intelligible speech 的 material error。Catastrophic finding 是 wrong episode/audio binding、
hash/receipt/clock integrity failure、連續至少十秒 intelligible speech omission，或任一十分鐘 clock
bucket 影響至少 5% spans 的 systematic recognition/timebase failure。Integrity failure 立即停止；
其餘 catastrophic finding 直接 full audio audit。

每一 tier 必須產 immutable selection receipt，綁 policy version、frozen population、seed inputs、
strata/allocation、risk reason/finding IDs、selected/context-only span IDs、exact clip hashes、worker/model、
responses、findings、rate numerator/denominator 與 escalation decision。Fresh replay 必須重現相同
selection 與 decision；receipt/response 缺失、格式錯、未綁定或不完整，一律 `Interrupted` 或 fail
closed，不能視為通過。Agent quorum 可接受完整 tier；finding 衝突、unresolved catastrophic finding、
evidence drift、provider/data destination 改變或最終字幕核准才進 human gate。

Universal text/reference coverage、每-span reference retrieval 與 fail-closed QC 都維持不變；本次修訂
只取消 ordinary production 對每個 span 強制 audio audit 的要求。

### 5. Corrected Canonical text is output authority

Correction audit 與 human correction 可以修改 Canonical Transcript 的文字與 provenance；最終
SRT 文字只能是 corrected Canonical token stream 的 exact ordered copy。Memo 的文字只用
來對齊其 timing boundaries，不能覆蓋校對結果。

投影必須保證：

- 每個 corrected token 恰好出現一次、順序不變，且不被 boundary 切開；
- Memo boundary order 不變；若 boundary 可落在不同 token edges，保留對應 Memo cue 的
  outer timing；
- duplicate mapped edges 可將相鄰 Memo cues 合併，合併 cue 使用第一個來源 cue 的
  start 與最後一個來源 cue 的 end；
- 除上述 token-safe adjacent merge 外，不做全域重切或發明新 timing edge；
- 每個輸出 cue 都是單行、非空、正 duration、互不重疊。

因此 output cue count 不保證與 Memo 原始 cue count 相同；有 adjacent merge 時也不要求
逐 cue timing 一對一相同。必須保留的是 ordered Memo timing provenance、outer span、
corrected-token integrity，以及通過 retention/alignment QC。

### 6. Local boundary repair

Reviewed cue projection 只有在明示、局部、可機器重算時才可改變來源 cue proposal。
這是額外的人工作業路徑；production alignment 因 duplicate token edge 產生的 adjacent
merge 由 retention QC 管理，不需要偽裝成 repair。允許的 closed reason code 為：

- `hard_length`：原 cue 的 display columns 確實超過 hard limit；
- `cps_duration`：原 cue 的 reading units / duration 確實超過 hard CPS；
- `invalid_empty`：原 cue 確實為空；
- `invalid_overlap`：來源 cues 確實時間重疊；
- `explicit_punctuation`：指定標點、scalar offset 與新 edge 完全吻合；
- `explicit_semantic`：引用 manifest 內 exact bytes、digest、size 都驗證過的 semantic
  receipt。

每筆 repair 最多涵蓋八個來源與八個輸出 cues，必須保留整組 outer span 與完整文字。
沒有 repair log 的 reviewed-projection 變動、理由與實際 Evidence 不符、或全域重切
一律 fail closed。

### 7. Projection profile and delivery

16:9 長影片 profile 固定 `max_lines = 1`。Projection 仍執行文字完整性、時間合法性、
reading speed 與其他 hard QC，但不得以這些規則發明與 Memo boundary alignment 無關的
新 edge。

Production 主路徑只有：

```text
  verified normalized-audio handoff
  -> Memo large-v2 immutable Recognition Evidence
  -> whole-transcript deterministic QC + text/reference audit
  -> risk-targeted + deterministic stratified audio audit (10% -> 30% -> full)
  -> append-only correction decisions
  -> accepted Memo GUI SRT + canonical acceptance receipt
  -> shared alignment / token-edge snap / adjacent-merge projection
  -> fail-closed QC
  -> one-line SRT + sidecar + projection receipt
```

### 8. Public operator surface

Public CLI 保留交付所需的 `create`、`status`、`review`、`resolve`／native decision 與
`project`。Human Gold custody、paired V1/V2 comparator、superiority benchmark 與
recognition pilot 不屬於 production runtime 或 public operator workflow。

## Invariants

1. Evidence immutable；Canonical Transcript 只能建立新的 Generation。
2. Correction Decision 必須綁定 target spans、Evidence fingerprint、actor、reason 與
   timestamp。
3. Reference Evidence 只能提出或支持 correction，不得覆蓋清楚的 Audio Evidence。
4. Accepted Generation 仍須通過全集 deterministic QC、完整 text/reference audit、由 authenticated
   selector 決定的 audio-audit tier、speech coverage 與 replay。
5. Projection 必須綁定 Memo SRT authority hash；authority drift 時 fresh replay
   失敗。
6. SRT 是 Canonical Transcript 的可重建 projection，不是 truth source。
7. Production factory 對 normalized audio 之前的處理沒有 ownership。

## Consequences

- 優點：沿用 Memo 已驗證的斷句品質；校對不再任意全域重切；一行字幕與 timing lineage
  可直接測試；外部 Evidence 與人工接受都有 immutable lineage。
- 代價：Memo GUI cue export 與 acceptance receipt 成為必要 production input；alignment
  或 retention 不足時必須 review，不能靠全域 optimizer 靜默重算；selective audio audit 必須
  保存可 replay 的 population、sample、risk 與 escalation receipts。
- 遷移：舊 Qwen-primary、Subtitle V2 內部 normalization orchestration、全域 boundary
  optimizer、Human Gold comparator 與 benchmark CLI 決策均已 superseded，不再描述
  production 現況。

## Acceptance criteria

- Production recognizer index 0 是 Memo；corroborators 預設關閉。
- Production import surface 不含 normalization provider implementation。
- Corrected text 經 `PodcastSubtitleV2.project()` 後是 exact ordered token copy，且不切開
  token；Memo boundaries 依序映射到 token edges，duplicate edges 只可 adjacent merge。
- Alignment ratio 與 boundary-retention ratio 通過明示 threshold；QC 保存 snap、merge、
  retention 與 time-delta metrics。
- Memo SRT authority hash 參與 projection lineage，fresh replay 可重現；hash drift fail
  closed。
- 16:9 SRT 每個 cue 恰好一行。
- 每個 Canonical span 都完成 deterministic QC、Reference Evidence retrieval 與 text/reference
  audit；audio audit coverage 精確等於 authenticated risk-targeted set 加目前必要的 10%／30%／full
  tier，且 fresh replay 重現相同 selection。
- 10% ordinary sample 的 major error 或 material error rate `> 0.02` 會確定性升到 cumulative 30%；
  30% rate 仍 `> 0.02` 才升 full，catastrophic finding 則立即 full 或 integrity fail closed。
