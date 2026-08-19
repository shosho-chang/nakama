---
name: podcast-pipeline
description: >
  訪談集正式 E2E 編排：素材 preflight、Auphonic normalization、Memo large-v2、
  Podcast Subtitle V2、Verified Projection、Resolve、highlight、packaging 與發布。
  Use when the user points to an episode folder and asks to run, resume, review,
  or diagnose the podcast pipeline. Production subtitles are always Memo-first V2.
---

# Podcast Pipeline — supervised production E2E

這是 orchestration skill。字幕唯一正式路徑是 Podcast Subtitle V2；不得把舊
`subtitle-gen`、`subtitle-correct` 或裸 SRT 當 production。細節與 JSON 模板見
[`references/v2-upstream-runbook.md`](references/v2-upstream-runbook.md)。

## 不可變規則

- `Audio/Live-Mix.wav` 是完整訪談的 canonical program mix。保留完整 clock，不裁收工
  閒聊、不 trim silence。
- `Audio/1_COMBO-1.wav` 固定是主持人修修；`Audio/2_COMBO-2.wav` 固定是來賓。
- 來賓身份先由本集訪綱、前期報告與訪談資料交叉確認；一致時不重問。
- Auphonic、Memo GUI、人類核准、YouTube upload 都是明確 gate。不得冒充 reviewer。
- Reference source 預設 `contextual`、零 authority scope。只有使用者提供 exact、
  source-bound authority attestation 才能提升；outline 永遠不能成為 authoritative。
- 任一 hash、size、duration、receipt、episode 或 source binding 漂移立即停止。
- 沒有 silent fallback。V1 只可在使用者明確要求 **explicit legacy forensic** 時讀取舊
  artifact 比對，不能產生本集 production output。

## State machine

只有前一 state 的必要 artifact 與 gate 都成立才前進。

| State | 完成條件 | 人類 gate |
|---|---|---|
| S0 INPUT | 三軌存在、可解碼、clock 相符；guest identity 有來源 | 異常才停 |
| S1 REFERENCES | `episode-references.v2.json` 驗證通過 | 選來源；authority 逐來源明示 |
| S2 NORMALIZED | `normalized.wav` + `normalized-handoff.v1.json` exact-match | Auphonic 外部上傳前 GO |
| S3 MEMO RECOGNITION | canonical review → typed recognition receipt/manifest | 聽過後才 accept |
| S4 MEMO CUES | canonical cue review → typed SRT receipt | 看過 cue 後才 accept |
| S5 V2 CANONICAL | V2 `run/status/review`，full audit packets 全部完成 | unresolved issue 決策 |
| S6 PROJECTION | `project` 產生 Verified Projection，fresh replay 通過 | 最終字幕 gate |
| S7+ VIDEO | Verified Projection → Resolve → highlight → final QA → packaging → publish | 沿各 downstream gate |

## S0 — preflight

先 ffprobe、hash、比對三軌 duration。不要因為訪談結尾有閒聊而詢問裁切。正式 episode
workspace 與所有 CLI 必須固定在同一 worktree/commit 與同一 episode root。

## S1 — Reference Manifest

建立一份 canonical source plan，再執行：

```powershell
python scripts/podcast_subtitle_v2_references.py prepare `
  --source-plan "<episode>/subtitle-v2/reference-plan.v1.json" `
  --output "<episode>/subtitle-v2/reference-review.v1.json"
```

先向使用者列出每個 source。沒有 authority attestation 時全部保持 contextual/no scopes。
使用者核准來源集合後：

```powershell
python scripts/podcast_subtitle_v2_references.py accept `
  --review "<episode>/subtitle-v2/reference-review.v1.json" `
  --reviewer "<human-id>" --accepted-at "<timezone-aware-ISO8601>" `
  --confirm-reviewed `
  [--authority-attestation "<exact-attestation.json>"] `
  --output "<episode>/subtitle-v2/episode-references.v2.json"
```

`--authority-attestation` 只能來自使用者明確裁決；agent 不得自行產生 confirmed=true。

## S2 — Auphonic normalization

先顯示 exact `Live-Mix.wav`、duration、size 與 action，取得外部上傳 GO 才執行：

```powershell
python scripts/run_audio_prep.py "<episode>"
```

不得加 `--trim-silence`。成功必須同時存在：

- `<episode>/normalized.wav`
- `<episode>/prep_manifest.json`（舊 operator telemetry，不是 V2 trust root）
- `<episode>/normalized-handoff.v1.json`（V2 exact handoff）

若 Auphonic 已由使用者手動完成，可用 `--pre-processed "<exact-wav>"`；仍要產生並驗證
handoff。跳過 normalization 的 raw copy 不具 V2-ready 資格。

## S3 — Memo recognition evidence

使用者在 Memo 1.7.5 以 bundled `ggml-large-v2.bin` 匯入 `normalized.wav`，輸出 UTF-8
SRT。SRT 是真實可得的 recognition export；CLI 直接從 exact bytes 建 canonical、
sequential tokens，不需要手寫 token JSON：

```powershell
python scripts/podcast_subtitle_v2_evidence.py prepare-recognition `
  --normalized-audio "<episode>/normalized.wav" `
  --normalized-manifest "<episode>/normalized-handoff.v1.json" `
  --source-export "<episode>/subtitle-v2/memo-recognition.srt" `
  --source-export-kind memo_srt --memo-version 1.7.5 --language zh `
  --prompt "<episode vocabulary>" `
  --output "<episode>/subtitle-v2/memo-recognition-review.v1.json"
```

把 review 摘要與 unresolved findings 交給使用者。只有本人完成聽審後才能執行：

```powershell
python scripts/podcast_subtitle_v2_evidence.py accept-recognition `
  --review "<episode>/subtitle-v2/memo-recognition-review.v1.json" `
  --normalized-audio "<episode>/normalized.wav" `
  --normalized-manifest "<episode>/normalized-handoff.v1.json" `
  --source-export "<episode>/subtitle-v2/memo-recognition.srt" `
  --reviewer "<human-id>" --accepted-at "<timezone-aware-ISO8601>" `
  --confirm-reviewed `
  --receipt-output "<episode>/subtitle-v2/memo-recognition-acceptance.v1.json" `
  --manifest-output "<episode>/subtitle-v2/memo-recognition.v1.json"
```

JSON/stdout importer 只接受另外提供的 strict canonical token export；未知格式停止，不猜。

## S4 — Memo cue authority

可使用同一份 Memo GUI SRT，但 recognition acceptance 與 cue acceptance 是兩個獨立 gate：

```powershell
python scripts/podcast_subtitle_v2_evidence.py prepare-cues `
  --recognition-manifest "<episode>/subtitle-v2/memo-recognition.v1.json" `
  --source-export "<episode>/subtitle-v2/memo-recognition.srt" `
  --output "<episode>/subtitle-v2/memo-cue-review.v1.json"

python scripts/podcast_subtitle_v2_evidence.py accept-cues `
  --review "<episode>/subtitle-v2/memo-cue-review.v1.json" `
  --recognition-manifest "<episode>/subtitle-v2/memo-recognition.v1.json" `
  --source-export "<episode>/subtitle-v2/memo-recognition.srt" `
  --reviewer "<human-id>" --accepted-at "<timezone-aware-ISO8601>" `
  --confirm-reviewed `
  --receipt-output "<episode>/subtitle-v2/memo-cue-acceptance.v1.json"
```

最後執行 `python scripts/podcast_subtitle_v2_evidence.py status` 並傳入六個 artifact path；
只有輸出 `ready=true` 才可組 production environment。

不要手抄六個 path。把 `status` 的 JSON 輸出存入 `$evidenceStatus`，再把
`environment` 的每個 property 寫入目前 PowerShell process：

```powershell
$evidenceStatus = python scripts/podcast_subtitle_v2_evidence.py status `
  --normalized-audio "<episode>/normalized.wav" `
  --normalized-manifest "<episode>/normalized-handoff.v1.json" `
  --recognition-manifest "<episode>/subtitle-v2/memo-recognition.v1.json" `
  --recognition-source-export "<episode>/subtitle-v2/memo-recognition.srt" `
  --recognition-acceptance-receipt "<episode>/subtitle-v2/memo-recognition-acceptance.v1.json" `
  --cue-source-export "<episode>/subtitle-v2/memo-recognition.srt" `
  --cue-acceptance-receipt "<episode>/subtitle-v2/memo-cue-acceptance.v1.json" |
  ConvertFrom-Json
if (-not $evidenceStatus.ready) { throw "Subtitle V2 evidence not ready" }
$evidenceStatus.environment.psobject.Properties | ForEach-Object {
  Set-Item -Path "Env:$($_.Name)" -Value $_.Value
}
```

六個 production trust-root 名稱固定為：

- `PODCAST_SUBTITLE_V2_NORMALIZED_HANDOFF_MANIFEST`
- `PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_MANIFEST`
- `PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_SOURCE_EXPORT`
- `PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_ACCEPTANCE_RECEIPT`
- `PODCAST_SUBTITLE_V2_MEMO_CUE_SOURCE_EXPORT`
- `PODCAST_SUBTITLE_V2_MEMO_CUE_ACCEPTANCE_RECEIPT`

## S5–S6 — V2 CLI

先用上段機器輸出的六個 evidence path。另需由 repo `.env`／執行環境提供已核准的
`PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL(_VERSION)`、
`PODCAST_SUBTITLE_V2_SEMANTIC_MODEL(_VERSION)`、
`PODCAST_SUBTITLE_V2_AUDIO_AUDIT_MODEL(_VERSION)`；version 必須是 immutable identity，
不得由 agent 猜版本或填 `latest/main/stable/default`。正式 run 必帶 Reference Manifest
與兩軌 mic：

```powershell
python -m agents.brook.podcast_subtitles `
  --episode-root "<episode>" `
  --reference-manifest "<episode>/subtitle-v2/episode-references.v2.json" `
  run --episode-id "<episode-id>" --source-audio "<episode>/normalized.wav" `
  --language zh `
  --mic-track "host=<episode>/Audio/1_COMBO-1.wav" `
  --mic-track "guest=<episode>/Audio/2_COMBO-2.wav"
```

`Interrupted` 表示等待 deterministic subscription work packet，不是失敗。禁止手寫或猜測
response schema、檔名與目的路徑。唯一 workspace 是
`<episode>/.subtitle-v2/subscription-work/`，一律使用 operator CLI：

```powershell
python scripts/podcast_subtitle_v2_work_packets.py list `
  --episode-root "<episode>"

python scripts/podcast_subtitle_v2_work_packets.py render `
  --episode-root "<episode>" `
  --request "<list 輸出的 request_path>"

# 將 render 的 worker_instruction、request_json、response_json_schema 交給支援該 modality 的 worker。
# worker 只寫候選檔；不得直接寫 subscription-work/responses。
python scripts/podcast_subtitle_v2_work_packets.py validate `
  --episode-root "<episode>" `
  --request "<同一 request_path>" `
  --candidate "<candidate.response.json>"

python scripts/podcast_subtitle_v2_work_packets.py accept `
  --episode-root "<episode>" `
  --request "<同一 request_path>" `
  --candidate "<candidate.response.json>"
```

`accept` 只會把已通過 production parser 的 exact candidate bytes 原子寫入唯一 response path，
且永不覆寫。Audio packet 必須交給能實際存取並聆聽 render 所列 exact WAV clip 的人類或
audio-capable worker；若目前 worker 無法聽音訊，就停在 pending，不得由文字推測後偽完成。
詳細 contract 見 [S5 subscription work packet operator](references/v2-s5-work-packets.md)。
逐一 accept 後重跑相同 V2 `run` command。使用 `status`、`review` 與 `decide-native` 處理 stable
Generation issue；不得直接改 SRT。最後：

```powershell
python -m agents.brook.podcast_subtitles `
  --episode-root "<episode>" `
  --reference-manifest "<episode>/subtitle-v2/episode-references.v2.json" `
  project --generation-id "<generation-id>" --profile nakama-zh-hant-16x9
```

只有 Verified Projection 才能交給 Stage 5。裸 SRT、只有 QC report、或檔案存在都不算完成。

## S7+ — 可執行 downstream runbook

### Resolve 與 highlight shortlist

先啟用 `resolve-project` skill；Resolve Studio 必須開著，且此動作會切換當前 project。只用
Verified Projection 的四個 lineage 值，不得加 `--legacy-v1`：

```powershell
$env:RESOLVE_SUBTITLE_TEMPLATE = "E:\nakama\data\resolve\subtitle-template.drt"
if (-not (Test-Path -LiteralPath $env:RESOLVE_SUBTITLE_TEMPLATE)) { throw "Resolve subtitle template missing" }
py -3.10 scripts/build_resolve_project.py "<episode>" `
  --projection-id "<projection-id>" `
  --expected-episode-id "<episode-id>" `
  --expected-generation-id "<generation-id>" `
  --expected-manifest-sha256 "<projection-manifest-sha256>" `
  --reference-manifest "<episode>/subtitle-v2/episode-references.v2.json"
```

成功驗證後會原子持久化
`<episode>/.stage5/verified-subtitle-handoff.v2.json`。後續 highlight mining、validate、
materialize、refresh 全部只讀這個 handoff；episode root `transcript.srt` 即使存在也不得採用。

接著啟用 `highlight-cut` skill；開採後先驗證，**只列候選、不替使用者選**：

```powershell
python scripts/run_highlight_cut.py "<episode>" --mining-input
python scripts/run_highlight_cut.py "<episode>" --validate
python scripts/run_cut_shortlist.py "<episode>" --format long
```

使用者回覆 winner IDs 後才可寫 `winners.json` 並物化：

```powershell
python scripts/run_cut_shortlist.py "<episode>" --pick <ID1,ID2,...>
python scripts/run_highlight_cut.py "<episode>" --materialize
```

### 長 highlight production 與 finished review

對每個 long winner 啟用 `longform-cut` skill，依序跑 tightening、director、titles、b-roll、
SFX、review；Resolve 指令固定用 `py -3.10`，其餘用目前 venv 的 Python：

```powershell
python scripts/run_short_tighten.py "<episode>" --detect --id <winner-id>
python scripts/run_short_tighten.py "<episode>" --apply --id <winner-id>
python scripts/run_short_director.py "<episode>" --id <winner-id> --stills "<stills-dir>"
python scripts/run_short_titles.py "<episode>" --id <winner-id>
python scripts/run_short_broll.py "<episode>" --id <winner-id> --stills "<stills-dir>"
python scripts/run_short_sfx.py "<episode>" --id <winner-id>
python scripts/run_short_review.py "<episode>" --id <winner-id>
```

在 `/bridge/highlights/<episode>/finished` 停下來給使用者核准。finished-cut approval 會觸發
full-resolution `publish_prep`; 每次 background attempt 都有 pid/start/deadline/exit receipt，
child crash 或逾時會轉 failed 並允許明確重試；receipt 未完成時不能跳進 Packaging。

### Packaging、description 與 YouTube

依序啟用 `title-brainstorm`、`thumbnail-brainstorm`。長 highlight 每個候選都要有
`nakama.long_thumbnail_composition.v2` receipt；bbox 必須來自同次 Hyperframes render 的 DOM
measurement sidecar，Bridge 會重驗 PNG／中央圖／sidecar hash 與 identity；中央必須是圖像
素材，人物／標題不得侵入保護區。到 `/bridge/packaging/<episode-slug>` 停下來給使用者選。

Packaging 核准後自動產生可編輯 description 草稿；background attempt 有 deadline/exit receipt，
stale/crash 會變 `DESCRIPTION_DRAFT_INTERRUPTED` 並在同頁重試。非空人工稿不得覆蓋，
空白稿不得進 `/bridge/publish/<episode>/<cut>`。

YouTube upload 必須再次取得明確核准。worker 會做 OAuth scope preflight、進度落盤、
resumable session、video-id 去重、縮圖、zh-TW CC 與 reconciliation。影片存在但 CC 失敗只補
字幕，禁止重傳影片。Bridge 的「核准並上傳」走同一 worker；CLI-only supervised 路徑為：

```powershell
python scripts/publish_upload.py --approve <cut> --episode "<episode>" [--schedule "<ISO8601+timezone>"]
python scripts/publish_upload.py --run --episode "<episode>" --cut <cut>
python scripts/publish_upload.py --cc-only <cut> --episode "<episode>"
python scripts/youtube_publish_reconcile.py --episode "<episode>" --cut <cut>
```

前兩行只有在使用者針對 exact episode/cut 明確核准 upload 後才可執行；核准 metadata 不等於
核准外部上傳。

`needs_restart` 表示 session 已過期，或 worker 在 session URI 落盤前中斷；先到 YouTube
Studio 查是否已有影片，再由人重新核准。平台 `public` 與 zh-TW caption `serving` 經
reconciliation 回寫後，才算發布閉環。

不得因 downstream artifact 已存在而跳過 upstream receipt 或 gate。
