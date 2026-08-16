# ADR-056: Podcast Subtitle V2 — Memo-first production contract

- Status: Accepted
- Date: 2026-08-16
- Owners: Brook / Podcast production
- Stage: 5 製作
- Supersedes: ADR-056 revisions before 2026-08-16
- Retains: ADR-032 exact-copy、ADR-050 D4 provenance

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

### 3. Explicit Memo cue authority

顯示切點只能來自另一份明示接受的：

- Memo GUI SRT；或
- reviewed Memo cue projection。

Cue manifest 必須：

- 綁定同一份 Memo Recognition manifest；
- 綁定 source cue export 與 acceptance receipt；
- 依原順序、無遺漏、無重複地 partition 全部 Memo source token IDs；
- 保存每個 accepted cue 的 identity、start/end time 與原始文字；
- 將 authority content hash 納入 projection output lineage 與 fresh-process replay。

Raw micro-segments、semantic model 建議、字數偏好或 ASR token gap 都不能自行新增
顯示切點。

### 4. Correction changes text, not cue boundaries

Full Audit 與 human correction 可以修改 Canonical Transcript 的文字與 provenance，
但每個 corrected token 必須仍可追到唯一 accepted Memo cue。一般校對不得：

- 跨越 cue 邊界合併 Evidence lineage；
- 改變 accepted cue order；
- 改變 cue outer start/end time；
- 遺失 Memo source-token coverage。

投影時，allowed edges 與 mandatory edges 都等於 accepted Memo cue edges；輸出的 cue
數量、順序與時間必須逐一相同。校對後文字只在相同 cue 裡重新投影。

### 5. Local boundary repair

只有明示、局部、可機器重算的 repair 可以改變 accepted cue manifest。允許的 closed
reason code 為：

- `hard_length`：原 cue 的 display columns 確實超過 hard limit；
- `cps_duration`：原 cue 的 reading units / duration 確實超過 hard CPS；
- `invalid_empty`：原 cue 確實為空；
- `invalid_overlap`：來源 cues 確實時間重疊；
- `explicit_punctuation`：指定標點、scalar offset 與新 edge 完全吻合；
- `explicit_semantic`：引用 manifest 內 exact bytes、digest、size 都驗證過的 semantic
  receipt。

每筆 repair 最多涵蓋八個來源與八個輸出 cues，必須保留整組 outer span、完整文字與
source-token partition。沒有 repair log 的變動、理由與實際 Evidence 不符、或全域重切
一律 fail closed。

### 6. Projection profile and delivery

16:9 長影片 profile 固定 `max_lines = 1`。Projection 仍執行文字完整性、時間合法性、
reading speed 與其他 hard QC，但不得以這些規則重新開放 Memo cue 之外的 edge。

Production 主路徑只有：

```text
verified normalized-audio handoff
  -> Memo large-v2 immutable Recognition Evidence
  -> full text/audio audit and correction
  -> accepted Memo cue authority
  -> exact-edge semantic text projection
  -> fail-closed QC
  -> one-line SRT + sidecar + projection receipt
```

### 7. Public operator surface

Public CLI 保留交付所需的 `create`、`status`、`review`、`resolve`／native decision 與
`project`。Human Gold custody、paired V1/V2 comparator、superiority benchmark 與
recognition pilot 不屬於 production runtime 或 public operator workflow。

## Invariants

1. Evidence immutable；Canonical Transcript 只能建立新的 Generation。
2. Correction Decision 必須綁定 target spans、Evidence fingerprint、actor、reason 與
   timestamp。
3. Reference Evidence 只能提出或支持 correction，不得覆蓋清楚的 Audio Evidence。
4. Accepted Generation 仍須通過完整 text/audio audit、speech coverage 與 replay。
5. Projection 必須綁定 accepted Memo cue authority hash；authority drift 時 fresh replay
   失敗。
6. SRT 是 Canonical Transcript 的可重建 projection，不是 truth source。
7. Production factory 對 normalized audio 之前的處理沒有 ownership。

## Consequences

- 優點：沿用 Memo 已驗證的斷句品質；校對不再破壞切點；一行字幕與 exact timing
  可直接測試；外部 Evidence 與人工接受都有 immutable lineage。
- 代價：Memo GUI cue export 與 acceptance receipt 成為必要 production input；需要改
  切點時必須留下局部 repair proof，不能靠全域 optimizer 靜默重算。
- 遷移：舊 Qwen-primary、Subtitle V2 內部 normalization orchestration、全域 boundary
  optimizer、Human Gold comparator 與 benchmark CLI 決策均已 superseded，不再描述
  production 現況。

## Acceptance criteria

- Production recognizer index 0 是 Memo；corroborators 預設關閉。
- Production import surface 不含 normalization provider implementation。
- Corrected text 經 `PodcastSubtitleV2.project()` 後仍維持 exact Memo cue count、order、
  start/end time。
- Cue authority hash 參與 projection lineage，fresh replay 可重現；hash drift fail closed。
- 16:9 SRT 每個 cue 恰好一行。
