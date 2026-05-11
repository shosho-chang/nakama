# Textbook Ingest v3 — Path B Rewrite + 28 章重 ingest 完整計畫

**起算**: 2026-05-07
**前置**: PR #441（5/6 S8 batch burn handoff memory）+ Codex 5/7 review YELLOW（thread `a5c16ef7fd0cd8103`，7 條 critique 全接受並套入本版）
**動機**: 5/6 S8 batch 28 章 3 PASS / 21 FAIL / 4 ERROR + $22.23 燒掉。Root cause = chapter-source.md prompt 叫 LLM emit body 違反 ADR-020 §Phase 1 verbatim 設計，Sonnet 16k output cap 對長章節崩。
**這次目標**: $0 marginal cost（走 Path C sandcastle + OAuth）+ **28/28 PASS**（after reruns）+ ship 進正式 KB。

---

## 已決策（Stage 0 拍板）

| Code | 決策 | 內容 |
|---|---|---|
| **D1a** | 砍 fake llm_description | Phase 1 frontmatter 不再產 figures 的 `llm_description`，只留 `alt_text` + `vision_class` + **`vision_status: caption_only`**（Codex Q3 — 留升級槽：`caption_only` / `true_vision_pending` / `true_vision_done`）|
| **D1b** | 延後真 Vision enrichment | Path B core ship 後再決定要不要做獨立 Phase 1.5 vision_describe.py。升級時把 `vision_status: caption_only` → `true_vision_done` 並回填 llm_description |
| **D2** | 砍 check_claim_in_page | acceptance gate 改純看 verbatim_match_pct ≥ 99% + figures_embedded == figures_count + wikilinks 動態門檻 + section identity match。**前提：Stage 1.5 必先凍結 verbatim_match_pct 比對演算法**（Codex Q1+Q2 連動）|
| **D3** | 保留 Section concept map | 為 Obsidian 閱讀體驗保留每節 mermaid wrapper |
| **D4** | 真章號檔名 | `ch{真章號}.md`（chapter_index 來自 payload，walker chunk index 拋棄）|
| **D5** | Path C (sandcastle + OAuth) | 28 章 batch 走 sandcastle + `CLAUDE_CODE_OAUTH_TOKEN`。**Stage 4.0 必先 1 章 dry-run 驗 OAuth-in-Docker 真的吃 Max 不踩 anti-automation throttle**（Codex Q4）。Path A fallback 保留 |

OAuth setup 已完成（user 5/7）。

---

## Stage 1 — Path B 架構改寫（**拆 4 sub-stage 獨立 dispatch**，純 code，$0）

Codex Q5：原計畫 7 檔同 dispatch 太貪（sandcastle 紀錄最多 4 檔 PR #264）。拆 4 個獨立 sandcastle dispatch，每個有自己的驗收。

### Stage 1a — `_assemble_body()` core function + tests

**dispatch 1**：寫純函式 + 完整 unit test，先確認 body 組裝邏輯本身對。

| # | 檔 | 改動 |
|---|---|---|
| 1a.1 | `scripts/run_s8_preflight.py:_assemble_body` (新) | 純函式：input = walker `verbatim_body` + figures + sections JSON + book_id；output = markdown body |
| 1a.2 | `tests/scripts/test_assemble_body.py` (新) | unit test 含：V2 transform byte-equivalent / wrapper 插對位 / verbatim 100% by construction / 多節 + 單節 + 無 wrapper edge case / **section_anchor identity mismatch fail-fast**（Codex Q1）|

**驗收**：`pytest tests/scripts/test_assemble_body.py` 全綠。

### Stage 1b — `run_phase1_source_page` 接上 `_assemble_body` + 1 章 dry-run

**dispatch 2**：把 1a 接進主 runner。

| # | 檔 | 改動 |
|---|---|---|
| 1b.1 | `scripts/run_s8_preflight.py:run_phase1_source_page` | LLM call 拿 JSON → schema validate → call `_assemble_body()` → 寫檔 |
| 1b.2 | `scripts/run_s8_preflight.py:_pick_chapter` + 寫檔路徑 | 檔名走真章號 `ch{payload.chapter_index}.md`（D4）|

**驗收**：`python -m scripts.run_s8_preflight --book-id bse --chapter-index 1 --dry-run` 跑得起來（不真打 LLM，但 walker → assemble → 路徑全鏈通）。

### Stage 1c — Prompt 改寫 + coverage gate 砍

**dispatch 3**：動 prompt + acceptance gate 邏輯。

| # | 檔 | 改動 |
|---|---|---|
| 1c.1 | `.claude/skills/textbook-ingest/prompts/chapter-source.md` | 砍 PART B body emission 整段；LLM 只 emit JSON `{frontmatter, sections}`；frontmatter figures 砍 `llm_description`、加 `vision_status: caption_only`（D1a + Codex Q3）|
| 1c.2 | `scripts/run_s8_preflight.py:run_coverage_gate` | 砍 `check_claim_in_page` LLM call；acceptance 改 4 條（見 Stage 1.5 凍結的演算法）|

**驗收**：grep 確認 `check_claim_in_page` 不再被呼叫；prompt 不含 `<<<BEGIN_VERBATIM_BODY>>>`-style body emission instruction。

### Stage 1d — Sandcastle env 配置（**獨立 dispatch，不混 code refactor**）

Codex Q7：env 配置與 code refactor 分離。

| # | 檔 | 改動 |
|---|---|---|
| 1d.1 | `E:\sandcastle-test\.sandcastle\main.mts` | 加 vault mount + `VAULT_PATH=/mnt/vault` + `CLAUDE_CODE_OAUTH_TOKEN` 透傳 |
| 1d.2 | `E:\sandcastle-test\.sandcastle\prompt.md`（template B：execute Python batch）| 寫死「跑 `python scripts/run_s8_batch.py`」風格 prompt，不從 GitHub issue 拉 |

**驗收**：`docker exec` 進 container 跑 `python -c "import os; print(os.environ.get('CLAUDE_CODE_OAUTH_TOKEN','MISSING')[:10])"` 看到 token prefix（不是 MISSING）。

### Stage 1e — 觸不到的零改動

`scripts/run_s8_batch.py` 共用 1a-c import，零改動。

### Stage 1 設計決策（不變）

- **S1-Q1**：sections JSON 與 walker section_anchors **identity** 不對（不只 count，含 anchor text equality）→ **fail-fast**（Codex Q1）
- **S1-Q2**：LLM 回壞 JSON → **1 次 retry**，再壞拋 error
- **S1-Q3**：寫 code → **dispatch sandcastle agent**（每個 sub-stage 一個 dispatch）

### Stage 1 cost / time

- LLM cost: $0（4 個 sandcastle dispatch 走 OAuth Max）
- Wall: 1a ~20min + 1b ~15min + 1c ~15min + 1d ~10min = **~60 min**

---

## Stage 1.5 — 凍結 verbatim 比對演算法 + L2/L3 acceptance hard rules（**Codex Q1+Q2+Q7b**）

**新 stage**：Stage 1 完工到 Stage 2 之間，明文凍結兩個演算法寫進 ADR-020 §Phase 1.5，避免 Stage 2 的 verbatim 99% 門檻被各自解釋。

### 1.5a — verbatim_match_pct 比對演算法（凍結）

**原則**：source page body 在比對 walker `verbatim_body` 前，必先 strip 掉所有「設計上允許的非 verbatim 內容」，剩下的才比對。

```python
def normalize_for_verbatim_compare(page_body: str, payload) -> str:
    # 1. 砍 frontmatter（已在外層砍）
    # 2. 每個 ### Section concept map ... ### Wikilinks introduced (END_OF_BLOCK) 區塊整段 strip
    # 3. 把 ![[Attachments/Books/{book_id}/{filename}]]\n*{caption}* 還原成原本的 ![alt](path)
    # 4. trailing whitespace normalize
    # 不做：punctuation normalize / case normalize / paragraph reorder

def verbatim_match_pct(page_body: str, walker_verbatim: str) -> float:
    normalized = normalize_for_verbatim_compare(page_body, ...)
    return paragraph_substring_match(normalized, walker_verbatim)
```

**section identity check**（除上述演算法）：
```python
def section_anchors_match(page_body, walker_section_anchors) -> bool:
    page_h2s = re.findall(r"^## (.+)$", page_body, re.MULTILINE)
    return page_h2s == walker_section_anchors  # exact list equality (順序 + 文字)
```

**Acceptance gate**（替代 check_claim_in_page）：
1. `verbatim_match_pct ≥ 99%`
2. `section_anchors_match == True`
3. `figures_embedded_count == walker.figures_count`
4. `len(wikilinks_introduced) ≥ char_count // 2000`（動態門檻，Codex Q6）

### 1.5b — L2/L3 acceptance hard rules（**重要**，Codex Q7b）

5/6 crisis 起點 = phase-b-reconciliation 寫 87.5% stub。本次絕不重蹈。

```python
# shared/concept_dispatch.py：寫 concept page 前 hard validate

L2_RULES = {
    "min_word_count": 200,
    "must_include_paragraph_from_chapter": True,  # 至少 1 個 source paragraph 在 body
    "forbidden_strings": [
        "Will be enriched later",
        "TODO",
        "Placeholder",
        "(this chapter)",  # 5/6 _build_seed_body 殘留
    ],
}

L3_RULES = {
    "min_word_count": 200,
    "min_source_paragraphs": 2,  # 來自 ≥2 different chapters
    "forbidden_strings": [...同上],
}

# 違反 → IngestFailError，不寫檔。Phase 2 dispatch log 記下，給 4.5 重跑時人工介入
```

### Stage 1.5 cost / time

- LLM cost: $0（純 spec freeze）
- Wall: 30 min（寫進 ADR-020 §Phase 1.5 + grep 改 shared/coverage_classifier.py + concept_dispatch.py 對齊）

---

## Stage 2 — 1 章驗證（最壞案例 BSE ch10）

**先 ch1 確認流程，再 ch10 驗極端**（S2-Q2=B）。**走 host run**（S2-Q1=A）。

### 執行

```powershell
cd E:\nakama
.venv\Scripts\Activate.ps1
$env:VAULT_PATH = "E:\Shosho LifeOS"

# 2a. 清 staging（先清 ch1 + ch10 兩 target file，保留其他壞檔備查）
Remove-Item "E:\Shosho LifeOS\KB\Wiki.staging\Sources\Books\biochemistry-for-sport-and-exercise-maclaren\ch1.md" -ErrorAction SilentlyContinue
Remove-Item "E:\Shosho LifeOS\KB\Wiki.staging\Sources\Books\biochemistry-for-sport-and-exercise-maclaren\ch10.md" -ErrorAction SilentlyContinue

# 2b. 跑 ch1（短，24k 字）
python -m scripts.run_s8_preflight `
  --book-id biochemistry-for-sport-and-exercise-maclaren `
  --chapter-index 1

# 2c. 5 條 stage gate 檢查 ch1（見下）

# 2d. 通過才跑 ch10（長，122k 字）
python -m scripts.run_s8_preflight `
  --book-id biochemistry-for-sport-and-exercise-maclaren `
  --chapter-index 10

# 2e. 5 條 stage gate 檢查 ch10
```

### Stage gate（**6 條 deterministic** 才進 Stage 3，Codex Q6）

| # | 條件 | 自動驗證 cmd | 失敗代表 |
|---|---|---|---|
| 1 | `verbatim_match_pct ≥ 99%`（依 Stage 1.5a 演算法）| `python -m scripts.verify_verbatim ch{N}.md` | `_assemble_body` 切 H2 / concat 邏輯錯 |
| 2 | `section_anchors_match == True` | 同上 | LLM sections JSON 漏節 / 順序錯 |
| 3 | `cost < $0.30 / 章` | preflight report | LLM 還在 echo body，prompt 沒改乾淨 |
| 4 | `len(wikilinks) ≥ char_count // 2000`（動態）| frontmatter parse | LLM 不再抽 concept |
| 5 | `figures_embedded_count == walker.figures_count` | grep `\[\[Attachments` count | walker 與 page 圖 list 不一致 |
| 6 | `grep -c '```mermaid' ch{N}.md ≥ section_count` | shell | wrapper 規格遺失（取代「人眼看 mermaid」）|

6 條全綠 → 進 Stage 3。任 1 條紅 → 停下分析，回 Stage 1。

### Cost / time

- ch1 ~$0.05 + ch10 ~$0.15 = **~$0.20**（API key，因為 host venv 走 ANTHROPIC_API_KEY）
- Wall: ~10 min 兩章

---

## Stage 3 — 中等章節驗證（**復活，Codex Q7c**）

Codex Q7c：5/6 失敗除長度外有 529 overload + process 因素，砍 Stage 3 不夠 grounded。$0.10 cheap insurance 划算。

```powershell
python -m scripts.run_s8_preflight `
  --book-id biochemistry-for-sport-and-exercise-maclaren `
  --chapter-index 3
```

### Stage gate（同 Stage 2 6 條）

通過 → 進 Stage 4。

### Cost / time

- ~$0.10
- Wall: ~5 min

---

## Stage 4 — 28 章 batch 重跑（**Path C，$0**）

**走 sandcastle + OAuth**（S4-Q1=A）。**先 BSE 11 章 → review → 再 SN 17 章**（S4-Q2=B）。

**Codex Q4 + Q6 修訂**：移除原「容忍 ≤ 2 fail」設定。新規則：
- Stage 4 跑時容忍個別章 FAIL（continue-on-fail），但 **Stage 6 ship gate = 28/28 PASS after Stage 4.5 reruns**
- **連續 3 章 FAIL → sandcastle 自動 abort**（kill switch）
- **單章 wall > 30 min → script 自動 abort 該章**
- **Stage 4.0（新）：1 章 dry-run 驗 OAuth-in-Docker**

### Stage 4.0 — OAuth-in-Docker 1 章 dry-run（**強制，跑 Stage 4a 前必先**，Codex Q4）

驗 OAuth token 在 sandcastle Docker container 內真的走 Max quota，不踩 anti-automation throttle。

```powershell
# 改 prompt.md 改成只跑 1 章
# python scripts/run_s8_batch.py --vault-root /mnt/vault --books bse --max-chapters 1
cd E:\sandcastle-test
MSYS_NO_PATHCONV=1 npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
```

**4.0 stage gate**：
1. 1 章在 30 min 內跑完
2. console log 不含 `429` / `auth` / `rate limit` / `unauthorized`
3. 該章 6 條 Stage 2 gate 全綠
4. host 端 `claude` 帳號頁面確認 token 仍 valid（不被自動廢）

通過 → 進 4a。失敗 → fallback Path A（API key），多花 ~$15 但不卡。

### Stage 4a — BSE 11 章

#### 設定 sandcastle template

E:\sandcastle-test\.sandcastle\main.mts 加 mount：

```typescript
mounts: [
  {
    hostPath: "E:/Shosho LifeOS",
    sandboxPath: "/mnt/vault",
    readonly: false,
  },
],
env: {
  CLAUDE_CODE_OAUTH_TOKEN: process.env.CLAUDE_CODE_OAUTH_TOKEN!,
  GH_TOKEN: process.env.GH_TOKEN!,
  VAULT_PATH: "/mnt/vault",
},
timeoutSeconds: 14400,  // 4hr 上限
```

prompt.md 改成（不從 issue 拉，直接執行）：
```
Run the BSE 11-chapter batch ingest:

  python scripts/run_s8_batch.py \
    --vault-root /mnt/vault \
    --books bse \
    --continue-on-fail

Wait for completion. Report final status (PASS/FAIL/ERROR per chapter)
to the host via stdout.
```

#### 啟動

```powershell
cd E:\sandcastle-test
MSYS_NO_PATHCONV=1 npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts
```

AFK ~1 hr。

#### 4a stage gate

- 連續 3 章 FAIL kill-switch 沒觸發（如果觸發 → 停 batch 查 root cause）
- 跑完 11 章（**個別失敗允許**，由 4.5 補；但連續 3 fail 視為架構級失敗）
- staging `KB/Wiki.staging/Sources/Books/biochemistry-for-sport-and-exercise-maclaren/ch{1..11}.md` 存在
- 自動 metric script 對 11 章跑 6 條 Stage 2 gate

通過 → 進 Stage 4b（個別 fail 進 4.5 重跑）。架構失敗 → 停下分析回 Stage 1。

### Stage 4b — SN 17 章

跟 4a 一樣，把 prompt 改成：
```
python scripts/run_s8_batch.py --vault-root /mnt/vault --books sn --continue-on-fail
```

5/6 SN ch13-16 因 529 overload ERROR，這次 OAuth 走 Max 不會踩同一條 rate limit。

AFK ~2 hr。

#### 4b stage gate

- 連續 3 章 FAIL kill-switch 沒觸發
- staging 17 章齊
- 自動 metric script 對 17 章跑 6 條 Stage 2 gate

### Stage 4 cost / time

- LLM cost: **$0**（OAuth Max quota）
- Wall: 4a ~1 hr + review 30 min + 4b ~2 hr = **~3.5 hr 總**
- Max quota 消耗: 28 章 × ~50k tok = ~1.4M tok（佔月配額一小部分）

---

## Stage 4.5 — 失敗章重跑（**必跑到 28/28 PASS**，Codex Q6）

**Stage 6 ship gate = 28/28 PASS**。失敗章必補滿。

```powershell
# host 直接跑（單章 cost 太低不值得 sandcastle wrapper）
python -m scripts.run_s8_preflight --book-id <book> --chapter-index <N>
```

每章 cost ~$0.10、wall ~5 min。

**4.5 stage gate**：所有 28 章經 6 條 Stage 2 gate 全綠才進 Stage 5。

某章重跑 3 次仍 fail → 停下個案分析（可能是該章某 corner case 暴露，需 patch `_assemble_body`）。

---

## Stage 5 — 抽檢 + 自動驗證（**host script 先，subagent 後**，Codex Q6）

Staging 28 章寫完後、ship 前**強制檢查**。

### 5.0 — host script deterministic verify（**新加，必先跑**）

```powershell
python -m scripts.verify_staging --vault-root "E:\Shosho LifeOS"
```

對 28 章逐章跑 6 條 Stage 2 gate（純規則，無 LLM）。輸出 `docs/runs/2026-05-07-staging-verify.json`。

**5.0 stage gate**：28/28 章全綠才進 5.1。任 1 章紅 → 回 4.5 重跑。

### 5.1 — 自動驗證（dispatch subagent，**secondary**）

dispatch general-purpose subagent 做語意層抽檢（5.0 抓不到的 — 例如 mermaid 是不是有意義 vs 隨便寫一個）：

```
Verify quality of staged chapters.
Sample 5 chapters (BSE ch1, ch6, ch10, SN ch3, ch12).
For each:
- mermaid blocks describe actual concept relationships, not random
- wikilinks_introduced are book-relevant terms (not stop-words)
- vision_status field present + populated
- frontmatter YAML parses cleanly
Report deviations.
```

### 5.2 — 人眼 spot-check 3 章

開 Obsidian 直接看（短/中/長 各 1）：BSE ch1 / SN ch5 / BSE ch10。

### 5.3 — Concept dispatch log 檢查（**含 L2/L3 hard rule 驗證**）

```powershell
# 看 concept dispatch 結果
ls "E:\Shosho LifeOS\KB\Wiki.staging\Concepts\" | measure
type "E:\Shosho LifeOS\KB\Wiki.staging\_alias_map.md" | measure

# 驗 Stage 1.5b L2/L3 hard rules — 抽 10 個 L2 + 5 個 L3 grep forbidden_strings
python -m scripts.verify_concept_acceptance --vault-root "E:\Shosho LifeOS"
```

預期：
- L1 alias: ~1000-1500
- L2 stub: ~800-1200（**全部 ≥ 200 word + 含 chapter source paragraph + 0 forbidden_strings**）
- L3 active: ≥ 100（**全部 ≥ 200 word + ≥ 2 source paragraphs**）
- 0 IngestFailError

### Stage 5 cost / time

- subagent: ~$0.50（一次性 verify 28 章）
- 人眼: 10 min
- Total wall: ~15 min

---

## Stage 6 — Ship 進正式區

**手動，不 dispatch**（操作敏感，要看著做）。

### 6.1 備份正式區（保險）

```powershell
$ts = Get-Date -Format "yyyyMMdd-HHmm"
$backup = "E:\Shosho LifeOS\KB\.backup-$ts"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Copy-Item -Recurse "E:\Shosho LifeOS\KB\Wiki\Sources\Books" "$backup\Sources-Books"
Copy-Item -Recurse "E:\Shosho LifeOS\KB\Wiki\Concepts" "$backup\Concepts"
```

### 6.2 mv staging → 正式（**不用 -Force**，Codex Q7a）

```powershell
# 先確認 backup 完整
$expected = (Get-ChildItem "E:\Shosho LifeOS\KB\Wiki\Sources\Books" -Recurse -File).Count
$backed   = (Get-ChildItem "$backup\Sources-Books" -Recurse -File).Count
if ($expected -ne $backed) { throw "Backup incomplete: $backed / $expected" }

# 對每本書：先 rename 舊 dir 進 backup（不 -Force 蓋），再 mv staging 過來
foreach ($book in @("biochemistry-for-sport-and-exercise-maclaren","sport-nutrition-jeukendrup-4e")) {
    $oldDir = "E:\Shosho LifeOS\KB\Wiki\Sources\Books\$book"
    $newDir = "E:\Shosho LifeOS\KB\Wiki.staging\Sources\Books\$book"
    if (Test-Path $oldDir) {
        Move-Item $oldDir "$backup\Sources-Books\$book.replaced"  # 顯式 rename，不蓋
    }
    Move-Item $newDir $oldDir
}

# Concepts — merge 進現有 Concepts/
Get-ChildItem "E:\Shosho LifeOS\KB\Wiki.staging\Concepts" -File | ForEach-Object {
    $target = "E:\Shosho LifeOS\KB\Wiki\Concepts\$($_.Name)"
    if (Test-Path $target) {
        Write-Warning "Concept exists, skipping: $($_.Name)"
    } else {
        Move-Item $_.FullName $target
    }
}

# Entities/Books — 逐檔 mv，target 存在則先 rename 舊檔
Get-ChildItem "E:\Shosho LifeOS\KB\Wiki.staging\Entities\Books" -File | ForEach-Object {
    $target = "E:\Shosho LifeOS\KB\Wiki\Entities\Books\$($_.Name)"
    if (Test-Path $target) {
        Move-Item $target "$backup\$($_.Name).replaced"
    }
    Move-Item $_.FullName $target
}

# 清空 staging（PowerShell 回收桶）
Add-Type -AssemblyName Microsoft.VisualBasic
[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(
    "E:\Shosho LifeOS\KB\Wiki.staging",
    'OnlyErrorDialogs', 'SendToRecycleBin')
```

### 6.3 KB index + log 更新

```powershell
# 兩件事：
# 1. KB/index.md 加兩本書 entry
# 2. KB/log.md append milestone "2026-05-07: ADR-020 v3 ingest BSE 11ch + SN 17ch shipped"
# 由 housekeeping subagent 做（內含 hard CLAUDE.md 規則 — append-only、繁中、frontmatter 英文）
```

dispatch general-purpose subagent 做這兩件 housekeeping。

### Stage 6 cost / time

- $0
- Wall: 10 min

---

## Stage 7 — Path B 雙 path 化（**延後**）

修修原話「以後可能會用到」。Path C 走順之後再寫 Agent tool dispatch flavor。

**延後到下次 session**。本計畫不含。

---

## Stage 8 — Memory + ADR 收尾

### 8.1 ADR-020 加 §Cost Model

把 Phase 1 / Phase 2 / Phase 2.5 token 表貼進 ADR（之前對話那張）。

### 8.2 ADR-020 加 §Path A vs Path B vs Path C

寫進 ADR-020：三 path 比較表 + 何時用哪條。

### 8.3 寫 memory `project_textbook_ingest_v3_shipped.md`

收 5/3-5/7 整段歷程：crisis 起點 → ADR-020 → S0-S7 ship → S8 burn → Path B rewrite → 28 章 ship。

### 8.4 過時 memory 標 supersede

`project_session_2026_05_06_07_s8_burn_handoff.md` 加 frontmatter `superseded_by: project_textbook_ingest_v3_shipped.md`，但**不刪**（歷史教訓保留）。

### 8.5 PR + commit + close

- 所有 Stage 1-8 改動 commit 進 `docs/kb-stub-crisis-memory` branch（PR #441 之上）
- 或開新 branch `feat/textbook-ingest-v3-path-b`
- ship 後 squash merge

### Stage 8 cost / time

- $0
- Wall: 30 min

---

## 總帳

| Stage | 內容 | LLM cost | Wall | 風險 |
|---|---|---:|---:|---|
| 1 | Path B 架構改寫（4 sub-dispatch）| $0 | 60 min | 低 |
| 1.5 | verbatim 演算法 + L2/L3 hard rules 凍結 | $0 | 30 min | 低 |
| 2 | ch1 + ch10 驗證（host）| $0.20 | 10 min | 中 |
| 3 | ch3 中段驗證（host）| $0.10 | 5 min | 低 |
| 4.0 | OAuth-in-Docker 1 章 dry-run | $0 | 15 min | 中 |
| 4a | BSE 11 章 batch（sandcastle/OAuth）| $0 | 1 hr | 中 |
| 4b | SN 17 章 batch（sandcastle/OAuth）| $0 | 2 hr | 中 |
| 4.5 | 失敗章重跑直到 28/28 PASS（host）| ~$0.30 | 15-30 min | 低 |
| 5 | 5.0 host script + 5.1 subagent + 5.2 人眼 + 5.3 concept | $0.50 | 25 min | 低 |
| 6 | Ship 進正式區（手動 + backup verify）| $0 | 15 min | 中 |
| 8 | Memory + ADR 收尾 | $0 | 30 min | 低 |
| **合計** | | **~$1.10** | **~6 hr** | — |

對比 5/6 燒 $22.23 全 fail → 這次目標 $1.10 + 28 章全 ship + 設計 spec 凍結。

---

## Stage gate 摘要（fail 在哪 stop）

| Gate | 條件 | 失敗動作 |
|---|---|---|
| Stage 1a | `_assemble_body` pytest 全綠 | 修 1a，不進 1b |
| Stage 1b | `--dry-run` 鏈通 | 修 1b，不進 1c |
| Stage 1c | `check_claim_in_page` 不再呼叫 + prompt 無 body emission | 修 1c，不進 1d |
| Stage 1d | `docker exec` 看到 OAuth token | 修 1d，不進 1.5 |
| Stage 1.5 | ADR-020 §Phase 1.5 凍結 | 不進 Stage 2 |
| Stage 2 ch1 | 6 條 deterministic gate 全綠 | 不進 ch10，回 Stage 1 修 |
| Stage 2 ch10 | 6 條 全綠 | 不進 Stage 3，分析 long-chapter edge |
| Stage 3 ch3 | 6 條 全綠 | 不進 Stage 4，分析中段 edge |
| Stage 4.0 OAuth dry-run | 4 條（30min wall + 無 429 + 6 gate + token valid）| Fallback Path A（API key）|
| Stage 4a/b | 連續 3 fail kill-switch 沒觸發 | 觸發 → 停查 root cause |
| Stage 4.5 | 28/28 PASS | 某章 3 次 retry 仍 fail → 個案分析 |
| Stage 5.0 | host script 28/28 全綠 | 回 4.5 |
| Stage 5.1-5.3 | 抽檢 + 人眼 + L2/L3 hard rules 全綠 | 回 4.5 或 patch concept_dispatch |
| Stage 6 ship | backup verify 通過 + 全 mv 不 -Force | 不 mv，先補 backup |

---

## Path C OAuth + sandcastle 操作前提（已就緒）

- ✅ OAuth setup（user 5/7 完成）
- ✅ `.sandcastle/.env` 含 `CLAUDE_CODE_OAUTH_TOKEN`
- ⏳ Stage 1d 獨立 dispatch 在 `main.mts` 加 vault mount + VAULT_PATH（**不混 Stage 1a/b/c code refactor**，Codex Q7d）

---

## References

- ADR-020：[docs/decisions/ADR-020-textbook-ingest-v3-rewrite.md](../decisions/ADR-020-textbook-ingest-v3-rewrite.md)
- 5/6 burn handoff：[memory/claude/project_session_2026_05_06_07_s8_burn_handoff.md](../../memory/claude/project_session_2026_05_06_07_s8_burn_handoff.md)
- Sandcastle runbook：[docs/runbooks/sandcastle.md](../runbooks/sandcastle.md)
- Sandcastle OAuth issue：https://github.com/mattpocock/sandcastle/issues/191
- 5/6 Final report：[docs/runs/2026-05-06-s8-final-report.md](../runs/2026-05-06-s8-final-report.md)
