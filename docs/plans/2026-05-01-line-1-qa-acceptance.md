# Brook Line 1 Podcast Repurpose — QA 驗收步驟

> 用途：跑通一集 podcast，驗證 Slice 1-9 端到端工作。Slice 10（Bridge UI mutation/approve）未 ship，故 Beta acceptance 不含 approve flow。
> 對應 PRD：[`docs/plans/2026-05-01-line-1-podcast-repurpose-prd.md`](2026-05-01-line-1-podcast-repurpose-prd.md) §Acceptance criteria
> 估時：第一次跑 ~45 min（含品質 review）；之後例行 ~15 min/集

---

## Phase 0 — Pre-flight checklist

### 0.1 環境變數

```bash
cd /Users/shosho/Documents/nakama
grep -E "^ANTHROPIC_API_KEY|^HUGGINGFACE_TOKEN" .env
# 期待：兩條都有值
# ANTHROPIC_API_KEY 給 Brook (Sonnet 4.6)
# HUGGINGFACE_TOKEN 給 WhisperX diarization pipeline（pyannote/speaker-diarization-3.1）
```

如果缺 `HUGGINGFACE_TOKEN`：
1. https://huggingface.co/pyannote/speaker-diarization-3.1 接受 EULA
2. https://huggingface.co/settings/tokens 開 read-only token
3. 加進 `.env`：`HUGGINGFACE_TOKEN=hf_...`

### 0.2 套件確認

```bash
.venv/bin/python -c "import whisperx, pyannote.audio, anthropic; print('OK')"
# 期待：印 OK；缺套件就 pip install -r requirements.txt
```

### 0.3 測試資料準備

需要一段 **1 小時左右、雙人訪談**的音檔（你跟一位來賓）。先決定要驗的那一集。

```bash
# 假設音檔放這
ls -la /path/to/podcast-ep.mp3
# 或 .wav / .m4a 也行
```

### 0.4 Smoke：unit + integration test 全綠

```bash
.venv/bin/pytest tests/agents/brook/ tests/scripts/test_run_repurpose.py tests/test_repurpose_engine.py tests/test_repurpose_router.py -q
# 期待：xx passed, 0 failed
```

如果紅了，**這份 QA 不要繼續往下跑** — 先回頭修 test。

---

## Phase A — Transcribe 上游驗證（diarization 復活）

目標：確認 PR #280 (diarization restore) 真的有效，產出含 `[SPEAKER_XX]` label 的 SRT。

```bash
.venv/bin/python -m scripts.run_transcribe /path/to/podcast-ep.mp3 \
    --output-dir /tmp/qa-transcribe
```

跑完檢查：

```bash
ls /tmp/qa-transcribe/
# 期待：podcast-ep.srt（或同檔名）

# 抽前 20 行看 speaker label
head -40 /tmp/qa-transcribe/podcast-ep.srt
# 期待看到 [SPEAKER_00] / [SPEAKER_01] 出現在文字行
# 如果完全沒有 SPEAKER label → diarization 沒跑到（最可能：HUGGINGFACE_TOKEN 缺/錯，或 EULA 沒接受）
```

**人工 gate**：
- [ ] SRT 含 `[SPEAKER_00]` / `[SPEAKER_01]` label
- [ ] Speaker 切換大致對得上（你 vs 來賓）— 細部錯漏可在 VSCode 裡手動微調
- [ ] 如有空白段（音樂/長停頓）label 沒 leak 到無聲處

校正完存成 `<ep-slug>-corrected.srt`，下一階段用。

---

## Phase B — CLI dry-run（不發 LLM call）

目標：驗 argparse / channel routing / output path resolver 邏輯。

```bash
.venv/bin/python -m scripts.run_repurpose /tmp/qa-transcribe/podcast-ep-corrected.srt \
    --host "張修修" \
    --guest "朱為民醫師" \
    --slug "qa-test-ep" \
    --podcast-url "https://example.com/qa-test" \
    --dry-run
```

期待 stdout：
```
DRY RUN — no LLM calls will be made
SRT path     : /tmp/qa-transcribe/podcast-ep-corrected.srt
Host         : 張修修
Guest        : 朱為民醫師
Slug         : qa-test-ep
Podcast URL  : https://example.com/qa-test
Channels     : ['blog', 'fb', 'ig']
Skip         : (none)
Run dir      : data/repurpose/2026-05-01-qa-test-ep
Expected outputs (7):
  - stage1.json
  - blog.md
  - fb-light.md
  - fb-emotional.md
  - fb-serious.md
  - fb-neutral.md
  - ig-cards.json
```

**人工 gate**：
- [ ] 7 個 expected output 全列出
- [ ] Run dir 用今天的台北日期（`Asia/Taipei`）
- [ ] Slug sanitization 正確（CJK 自動轉）

也試一次 `--skip-channel ig --skip-channel fb`：

```bash
.venv/bin/python -m scripts.run_repurpose /tmp/qa-transcribe/podcast-ep-corrected.srt \
    --slug qa-blog-only --dry-run --skip-channel ig --skip-channel fb
# 期待：Channels: ['blog']，expected outputs 只 2 個（stage1.json + blog.md）
```

---

## Phase C — 單 channel real run（cost-bounded smoke）

目標：先跑 blog only 確認 Stage 1 + BlogRenderer 鏈。失敗早期捕捉，不浪費 4 個 FB 並行 LLM call。

```bash
.venv/bin/python -m scripts.run_repurpose /tmp/qa-transcribe/podcast-ep-corrected.srt \
    --host "張修修" --guest "朱為民醫師" \
    --slug "qa-blog-smoke" \
    --podcast-url "https://example.com/qa-test" \
    --skip-channel fb --skip-channel ig
```

期待 stdout 結尾類似：
```
Run dir: data/repurpose/2026-05-01-qa-blog-smoke
Artifacts (1):
  ✓ blog.md  (blog)
Wall: ~30-60s
Cost (main-thread calls only): ~$0.20-0.30
```

**Sanity check**：

```bash
RUN_DIR=data/repurpose/$(date +%Y-%m-%d)-qa-blog-smoke
ls $RUN_DIR
# 期待：stage1.json + blog.md
```

### Stage 1 JSON 結構驗（自動）

```bash
.venv/bin/python -c "
import json, sys
from pathlib import Path
data = json.loads(Path('$RUN_DIR/stage1.json').read_text())
required = ['hooks','identity_sketch','origin','turning_point','rebirth',
            'present_action','ending_direction','quotes','title_candidates',
            'meta_description','episode_type']
missing = [k for k in required if k not in data]
print('missing:', missing or 'none')
print('hooks:', len(data['hooks']))
print('quotes:', len(data['quotes']))
print('titles:', len(data['title_candidates']))
print('meta_desc len:', len(data['meta_description']))
print('episode_type:', data['episode_type'])
"
# 期待：
# missing: none
# hooks: ≥3
# quotes: ≥5
# titles: ≥3
# meta_desc len: 80-200
# episode_type: 4 個 enum 之一
```

如果 schema 紅 → Line1Extractor 出包，stop and 開 issue。

### Blog 半結構驗（自動）

```bash
head -20 $RUN_DIR/blog.md
# 期待：YAML frontmatter（---）含 title / meta_description / category / tags / podcast_episode_url
wc -w $RUN_DIR/blog.md
# 期待：~2000-3000 字（中文 wc -w 不準，但量級對的上即可；精確用 wc -m 看字符數 ~ 2000-3000）
grep -c "^>" $RUN_DIR/blog.md
# 期待：≥1 行 block quote（受訪者引述）
grep -c "podcast_episode_url\|https://example.com/qa-test" $RUN_DIR/blog.md
# 期待：≥1（CTA 段該有 podcast 連結）
```

**人工 gate（Phase C）**：
- [ ] stage1.json schema 全綠
- [ ] blog.md frontmatter 完整 + ≥1 引述 + 含 podcast 連結
- [ ] **品質直覺判斷**：blog 讀起來像「人物專訪」不是「逐字稿摘要」

---

## Phase D — 全 channel real run（FB 4 tonal + IG）

確認 Phase C 都綠後再跑 full：

```bash
.venv/bin/python -m scripts.run_repurpose /tmp/qa-transcribe/podcast-ep-corrected.srt \
    --host "張修修" --guest "朱為民醫師" \
    --slug "qa-full-ep" \
    --podcast-url "https://example.com/qa-test"
```

期待 stdout：
```
Artifacts (6):
  ✓ blog.md  (blog)
  ✓ fb-light.md  (fb-light)
  ✓ fb-emotional.md  (fb-emotional)
  ✓ fb-serious.md  (fb-serious)
  ✓ fb-neutral.md  (fb-neutral)
  ✓ ig-cards.json  (ig)
Wall: ~90-180s
Cost (main-thread calls only): ~$0.25-0.35  ← 注意：FB 4 call 不在這
```

> ⚠️ **Cost 顯示限制**：FBRenderer 的 4 個 thread-pool call 不會 aggregate 到 stdout 的 cost summary（thread-local `_local.usage_buffer` 不繼承）。實際單集成本 ~$0.40，差額在 thousand_sunny `state.db` API call DB 裡。修法在 PR #300 review NOTE。

### FB 4 variant 差異化驗（自動 heuristic）

```bash
RUN_DIR=data/repurpose/$(date +%Y-%m-%d)-qa-full-ep
for tonal in light emotional serious neutral; do
    chars=$(wc -m < $RUN_DIR/fb-$tonal.md)
    emoji=$(grep -oE "[😀-🙏🤣🤝🤔💪😓☺️]" $RUN_DIR/fb-$tonal.md | wc -l)
    echo "fb-$tonal: $chars 字 / $emoji emoji"
done
# 期待：light emoji 最多、neutral emoji 最少
# 期待：每篇 300-500 字（中文字符）
```

### IG cards 結構驗（自動）

```bash
.venv/bin/python -c "
import json
from pathlib import Path
cards = json.loads(Path('$RUN_DIR/ig-cards.json').read_text())
ep_type = json.loads(Path('$RUN_DIR/stage1.json').read_text())['episode_type']
expected_count = {'narrative_journey':5,'myth_busting':7,'framework':5,'listicle':10}[ep_type]
print(f'episode_type: {ep_type}')
print(f'expected cards: {expected_count}, got: {len(cards)}')
print(f'cover headline: {cards[0].get(\"headline\",\"?\")[:20]}...')
print(f'last card has CTA: {\"CTA\" in str(cards[-1]) or \"連結\" in str(cards[-1]) or \"聽\" in str(cards[-1])}')
"
# 期待：
# episode_type ∈ enum
# expected == got 卡數
# 首卡 headline ≤10 字
# 末卡含 CTA 信號詞
```

**人工 gate（Phase D）**：
- [ ] 7 個 artifact 全落地（含 stage1.json）
- [ ] FB 4 篇 voice 真的有差（不是 4 篇高度相似）
- [ ] IG 卡數對得上 episode_type
- [ ] 沒有 errors（stdout 不出現 `✗`）

---

## Phase E — Bridge UI read-only 驗收

> Slice 10 mutation 沒 ship，這階段只驗讀取面。

```bash
# 跑 thousand_sunny 本機 dev server
.venv/bin/uvicorn thousand_sunny.app:app --reload --port 8000
```

開瀏覽器：

1. `http://localhost:8000/bridge/repurpose`
   - [ ] List 顯示 `2026-05-01-qa-full-ep`（或你跑的所有 run）
   - [ ] `episode_type` 欄顯示對的值
   - [ ] `artifact_count` 顯示 6（stage1.json 不算）

2. `http://localhost:8000/bridge/repurpose/2026-05-01-qa-full-ep`
   - [ ] 3-panel 顯示 blog / FB / IG 內容
   - [ ] FB 段能切 4 個 tonal variant
   - [ ] IG cards JSON 可讀（pretty print 或 raw）
   - [ ] 沒有按 approve / edit button — Slice 10 沒 ship 是 expected

3. **路徑安全（必驗）**：
   ```bash
   curl -sI http://localhost:8000/bridge/repurpose/..%2F..%2Fetc
   # 期待：HTTP/1.1 404 Not Found（_RUN_ID_RE 擋掉）
   ```

---

## Phase F — 品質 review（修修 must do）

> 這段沒法自動。Beta acceptance 真正的 gate 在這。

每個 channel 各自評 1-5 分（5 = 直接可發、3 = 微調可發、1 = 重寫）：

| Channel | 內容 | 分數 | 修補成本 |
|---|---|---|---|
| blog.md | 人物敘事完整、引述真實、SEO title 可選 | _/5 | ___ min |
| fb-light.md | 輕鬆風趣、emoji 自然不油膩 | _/5 | ___ min |
| fb-emotional.md | 私人共鳴段有打到、不矯情 | _/5 | ___ min |
| fb-serious.md | 議題倡議口吻、有量化證據 | _/5 | ___ min |
| fb-neutral.md | 純資訊、結構清楚 | _/5 | ___ min |
| ig-cards.json | hook 卡有抓眼、CTA 落地、卡間節奏 | _/5 | ___ min |

**Beta gate**：平均 ≥3、總修補時間 ≤ 30 min（首次容忍）

---

## Beta Acceptance Sign-off

```
Run ID:        2026-05-01-qa-full-ep
SRT 集名:     ___________________________
Host / Guest: 張修修 / ___________________
跑完日期:     ___________________________

[ ] Phase 0 pre-flight 全綠
[ ] Phase A diarization SRT 含 [SPEAKER_XX]
[ ] Phase B dry-run plan 正確
[ ] Phase C blog smoke 全綠
[ ] Phase D 6 artifact 全落地、結構驗全綠
[ ] Phase E Bridge UI 讀取正常、路徑安全
[ ] Phase F 平均品質 ≥3、修補時間 ≤ 30 min

Wall clock total:  _______ s
Estimated cost:    $______ (main-thread + 估算 FB 4 call)
品質平均分:        _____/5
```

簽完 → Beta accepted。下一步：開始 Slice 10（Bridge UI mutation + Usopp WP draft），或先連跑 5 集走 Production gate。

---

## Known limitations（出問題時先看這）

1. **FB cost not aggregated** — `Cost (main-thread calls only)` 漏 FBRenderer 4 call。實際成本看 `data/usage_log.jsonl` 或 `state.db`。修法 PR #300 review NOTE。
2. **Slice 10 未 ship** — Bridge UI 沒 approve / edit button，blog 不會自動進 Usopp WP draft；目前要手動 copy `blog.md` 內容貼 WP。
3. **FB / IG manual paste** — Phase 1 限制（PRD §Out of Scope）；沒 Meta API adapter，貼 Meta Business Suite + 手動 attach 圖。
4. **Stage 1 manual override** — Phase 2 才會有；現在 LLM 抽錯 episode_type 或金句要重跑整 pipeline。可變通：手改 `stage1.json` 後不重跑 Stage 1，但目前 CLI 不支援 stage1 reuse — 寫進 issue。

---

## Diagnostics（出錯時依序查）

### Symptom: `KeyError: 'HUGGINGFACE_TOKEN'`
→ Phase 0.1 沒做。`.env` 加 token + accept EULA。

### Symptom: SRT 沒 `[SPEAKER_XX]` label
→ HF token 對但 EULA 沒接受、或 GPU OOM。看 `journalctl -u thousand-sunny` 或 transcribe stdout。

### Symptom: Stage 1 JSON `ValidationError`
→ LLM 輸出 schema 不符、或 SRT 過短（< 5 min）資訊不足。重跑一次（Line1Extractor 自動 retry once）。仍紅就開 issue 附 SRT + LLM raw response。

### Symptom: FB 4 篇高度相似
→ Tonal directive 沒 leverage 起來。看 `agents/brook/style-profiles/fb-post.md` 是否需迭代。或 SRT 內容太單薄（說教多/故事少）讓 voice 抓不到差異。

### Symptom: IG 卡數錯
→ `EPISODE_TYPE_CARD_COUNT` mapping 跟 LLM 輸出不一致。查 `stage1.json.episode_type` vs `ig-cards.json` 長度。Line1Extractor 抽 episode_type 抽錯是常見因。

### Symptom: Bridge UI list 是空的
→ `data/repurpose/` 不存在或沒 run。確認 CLI 真的跑完（`ls data/repurpose/`）。

### Symptom: `path traversal` 想驗但 404 沒擋住
→ `_RUN_ID_RE` 沒生效，security regression。立刻開 P0 issue。

---

## 後續 follow-up（Beta 後）

- [ ] 連跑 5 集 → Production gate（修補時間 ≤ 10 min/集）
- [ ] Slice 10 開工：Bridge UI mutation + Usopp WP draft auto
- [ ] FB cost aggregation 修（PR #300 NOTE）
- [ ] Stage 1 reuse flag（避免 LLM 抽錯時整 pipeline 重跑）
- [ ] ADR-013 amendment 落（diarization 重新 in scope 正式記）
