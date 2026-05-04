# Sync LLM JSON Contract Fix — PRD #337 P0 Hotfix

> 用途：修 PRD #337 ship 後 QA 揭露的 sync LLM systematic invalid JSON bug — 讓 annotation sync to Concept page 真正可用、解 Phase 4-5 驗收 blocker。
> 對應 PRD：GH issue [#337](https://github.com/shosho-chang/nakama/issues/337) closed
> 對應 ADR：[`docs/decisions/ADR-017-annotation-kb-integration.md`](../decisions/ADR-017-annotation-kb-integration.md)
> QA 觀察來源：[QA bug log §7](vault://Inbox/qa-line2-bugs-2026-05-04.md)
> 估時：~2-3h（含 tests）

---

## 1. 背景

PRD #337 三 PR 全 merged（PR #342/#343/#344）後 2026-05-04 晚 QA 揭露 sync 真實使用噴 systematic 失敗：

| Sync # | 結果 | 說明 |
|---|---|---|
| 1 (creatine 首次) | ✅ 「已同步 3 個概念」 | LLM 出 valid JSON，3 concept page 正確 mutate |
| 2 (creatine idempotency) | ❌ 「無匹配概念」 | server log: `merger LLM returned invalid JSON` |
| 3 (cardio cross-source) | ❌ 「無匹配概念」 | server log 同上、concept page 0 mutation |

**2/3 失敗率 = systematic**，不是偶發。

### 真正影響

- **Phase 4 cross-source aggregate** 工程驗收 block — 兩 source 同 concept page 的 boundary 隔離 / per-source full replace 語意**從未驗證過**
- **Phase 5 edit/delete 傳播** 同 block — sync 才能把 edit/delete 推到 Concept page
- **Line 2 Stage 2→3 銜接路徑表面綠實際斷** — annotation 標完進 `KB/Annotations/<slug>.md` 沒問題，但「sync 到 Concept page 累積個人觀點」這條 PR #343 核心能力 production 跑 2/3 失敗
- **誤導 UX**：用戶看「無匹配概念」會以為 LLM 真的判斷沒匹配，實際是 LLM 出歪 JSON 被 silent swallow → 心智模型錯亂、真實匹配 lost

---

## 2. 三層問題

### 2.1 LLM 輸出不穩（root cause）

`agents/robin/annotation_merger.py:98-103`：

```python
raw = ask(
    prompt=prompt,
    model="claude-opus-4-7",
    max_tokens=8000,
    # temperature=0.2,  ← 已刪（reasoning model 不接 temperature, fix 卡點 #6）
)
result = json.loads(raw)  # ← 這裡會 parse 失敗
```

**Opus 4.7 是 reasoning model，對「raw text → 必須 valid JSON」契約不穩**。prompt 帶的 concept slugs 累積到一兩百個 + annotations 內容後，LLM 容易出 markdown fence、附加 commentary、或 schema 飄。

### 2.2 silent swallow（誤導 UX）

`agents/robin/annotation_merger.py:104-112`：

```python
try:
    result = json.loads(raw)
    if not isinstance(result, dict):
        logger.warning("merger LLM returned non-dict JSON", extra={"raw": raw[:200]})
        return {}
    return {k: v for k, v in result.items() if isinstance(k, str) and isinstance(v, str)}
except (json.JSONDecodeError, AttributeError):
    logger.warning("merger LLM returned invalid JSON", extra={"raw": raw[:200]})
    return {}
```

JSON parse error catch 成空 dict 回去，caller `sync_source_to_concepts` 看到空 linkage → `concepts_updated=[]` → reader UI 顯示「無匹配概念」。**「LLM 真判斷無匹配」 vs 「LLM 出歪 JSON」混在一個訊號**。

### 2.3 沒 idempotency short-circuit（浪費 + 撞 bug 概率）

不論 annotation 有沒有變動，每次按「同步到 KB」都會重新打 LLM。第二次按時 `unsynced_count == 0`（first sync 已 mark_synced），明明該直接 return cached state，但目前實作仍 trigger `_ask_merger_llm` 全流程 → 浪費 ~$0.1-0.5/次 + 引入非確定性 + 撞 LLM JSON parse fail 概率。

---

## 3. 拍板凍結（Grill 2026-05-04 晚）

### Q1 LLM 契約 → **A. Anthropic tool_use forced JSON**

**rationale**：tool_use 是 Anthropic 官方推薦解，schema 強約束、跨 model 通用、改動 isolated 在 `_ask_merger_llm` 一處。對比：

| 選項 | 優 | 劣 | 拍板 |
|---|---|---|---|
| **A. tool_use forced JSON** | Anthropic 推薦、schema-valid 保證、prior-art established | prompt 改寫成 tool definition、增 wrapper layer | **✅ 採用** |
| B. JSON repair retry layer (`json-repair`) | 改動小 | 治標不治本、retry 燒錢 | ❌ |
| C. 換 Sonnet | 簡單、non-reasoning model 對 strict JSON 較穩 | 失去 Opus 4.7 推理力（concept matching 質可能降）、不解根因 | ❌ |
| D. Structured outputs (2025 新 API) | 最新 | 沒驗、無 prior art | ❌ defer |

### Q2 PR scope → **三件並做**

三件互相依賴 — LLM contract 修了若沒 short-circuit，idempotency 仍會打 LLM 撞概率失敗；error surface 是把卡點 #7 誤導 UX 一次解清。

**不**包含：
- 卡點 #1（reader URL encode 4 處）— 不同主題、獨立 PR
- 卡點 #6（`shared/kb_writer.py:132` temperature 同 bug）— 跟 sync robustness 不同主題、textbook ingest 範疇、獨立 PR
- 卡點 #5（annotation 渲染位置 multi-occurrence）— ADR-017 位置 anchor 設計題、獨立 PR

### Q3 時序 → **B. 現在開 branch + hold push 等 Slice 1 #352 merge**

**狀態 update（2026-05-04 晚）**：Slice 1-5 全 merged 進 main（Slice 2 #353 / Slice 3 #354 / Slice 5 #356 / Slice 1 #352 推測也已 ship）→ Q3=B 條件**已滿足**，下個 session 直接開 branch + 寫 code + push + open PR，無須 hold。

---

## 4. 實作步驟

### 4.1 開 branch

```bash
git checkout main && git pull
git checkout -b fix/sync-llm-json-contract
git stash pop  # 把 annotation_merger.py 的 temperature removal 拿回
```

### 4.2 重寫 `_ask_merger_llm` 走 tool_use

```python
# agents/robin/annotation_merger.py

from typing import NamedTuple

class MergerLLMResult(NamedTuple):
    """Typed result for sync LLM call — distinguishes empty match vs LLM failure."""
    matches: dict[str, str]  # {concept_slug: callout_block}
    error: str | None  # None on success (incl. empty matches); error msg on LLM/contract failure


_MERGE_TOOL = {
    "name": "merge_annotations",
    "description": "Map annotations to matching concept pages with callout blocks.",
    "input_schema": {
        "type": "object",
        "properties": {
            "concept_matches": {
                "type": "object",
                "description": "Map of concept_slug → callout_block markdown string",
                "additionalProperties": {"type": "string"},
            }
        },
        "required": ["concept_matches"],
    },
}


def _ask_merger_llm(prompt: str) -> MergerLLMResult:
    """Call LLM via tool_use forced JSON to map annotations → concept callouts."""
    from shared.anthropic_client import call_claude_with_tools

    try:
        message = call_claude_with_tools(
            messages=[{"role": "user", "content": prompt}],
            tools=[_MERGE_TOOL],
            tool_choice={"type": "tool", "name": "merge_annotations"},
            model="claude-opus-4-7",
            max_tokens=8000,
        )
    except Exception as e:
        logger.exception("merger LLM API error")
        return MergerLLMResult(matches={}, error=f"LLM API error: {type(e).__name__}: {e}")

    # 取出 tool_use 的 input
    tool_use_blocks = [b for b in message.content if b.type == "tool_use"]
    if not tool_use_blocks:
        return MergerLLMResult(matches={}, error="LLM did not invoke tool")

    matches = tool_use_blocks[0].input.get("concept_matches", {})
    # tool_use 已保證 schema valid（input_schema），但仍 sanity check
    return MergerLLMResult(
        matches={k: v for k, v in matches.items() if isinstance(k, str) and isinstance(v, str)},
        error=None,
    )
```

### 4.3 Error surface — `sync_source_to_concepts` propagate 到 SyncReport

```python
def sync_source_to_concepts(self, slug: str) -> SyncReport:
    # ... existing setup ...

    # NEW: idempotency short-circuit (Q2 第三件)
    if store.unsynced_count(slug) == 0:
        return SyncReport(
            source_slug=slug,
            concepts_updated=[],
            annotations_merged=0,
            skipped_annotations=len(annotations),
            errors=[],  # not an error — just nothing to sync
            short_circuited=True,  # NEW field
        )

    # ... existing prompt build ...

    llm_result = _ask_merger_llm(prompt)
    if llm_result.error is not None:
        return SyncReport(
            source_slug=slug,
            concepts_updated=[],
            annotations_merged=0,
            skipped_annotations=0,
            errors=[llm_result.error],
        )

    linkage = llm_result.matches
    # ... rest unchanged ...
```

### 4.4 Reader UI 區分 error vs empty

`thousand_sunny/templates/robin/reader.html` line ~608 syncToKB 函式：

```js
const res = await fetch('/sync-annotations/' + encodeURIComponent(SLUG), { method: 'POST' });
if (res.ok) {
  const data = await res.json();
  const updated = (data.concepts_updated || []).length;
  const errors = (data.errors || []);
  if (errors.length > 0) {
    // NEW: LLM 真壞掉 → 紅字
    syncIndicator.textContent = `⚠️ 同步錯誤：${errors[0]}`;
    syncIndicator.style.color = '#e53935';
  } else if (data.short_circuited) {
    // NEW: idempotency hit
    syncIndicator.textContent = '已是最新，無需同步';
    syncIndicator.style.color = 'var(--text-muted)';
  } else {
    syncIndicator.textContent = updated > 0 ? `已同步 ${updated} 個概念 ✓` : '無匹配概念';
    syncIndicator.style.color = updated > 0 ? '#4caf50' : 'var(--text-muted)';
  }
  // ...
}
```

### 4.5 Tests

| Test | 涵蓋 |
|---|---|
| `test_ask_merger_llm_returns_typed_result_on_success` | tool_use happy path → MergerLLMResult(matches={...}, error=None) |
| `test_ask_merger_llm_returns_error_on_no_tool_use` | LLM 沒 invoke tool → error msg surface |
| `test_ask_merger_llm_returns_error_on_api_exception` | API exception → caught, error msg in result |
| `test_sync_short_circuits_when_unsynced_count_zero` | unsynced_count==0 → 不 call LLM, returns short_circuited=True |
| `test_sync_propagates_llm_error_to_report` | LLM error → SyncReport.errors 非空 |
| `test_reader_ui_shows_error_on_sync_failure` (manual / playwright) | UI 區分 error / empty / short-circuit / success 四態 |

### 4.6 Acceptance（驗收）

- [ ] 連續 5 次同 source 不改 annotation sync — 第 1 次正常 mutate / 第 2-5 次 short-circuit「已是最新」 — 0 次 invalid JSON warning
- [ ] LLM 真出 invalid JSON 時 — UI 顯示紅字「⚠️ 同步錯誤：...」（手動 monkeypatch tool_use 回 broken response 模擬）
- [ ] cross-source aggregate 真實 work — 兩篇 source 各標 annotation sync 同 concept page，per-source boundary 隔離正確
- [ ] tests/ 全綠（unit + integration）
- [ ] 本機 reader 重跑 Phase 3-5 全綠

---

## 5. 出 scope（明確排除）

- `shared/kb_writer.py:132` 同 temperature bug — textbook ingest / Concept upsert 範疇，獨立 PR
- reader URL encode 4 處（卡點 #1）— 獨立 PR
- annotation 位置 anchor multi-occurrence（卡點 #5）— ADR-017 設計題，獨立議題
- `sqlite3.OperationalError: no such column: prefix` — 跟 sync 無關的另條 log error，獨立查
- migration script — vault 已存在 0 個 sync 過的 concept page 跟舊 schema，這次改不影響舊資料

---

## 6. 風險

- **tool_use 跟 reasoning model 互動** — Opus 4.7 對 tool_use 應該穩（這是 Claude 主流場景），但需驗一次。如果撞 issue 退路：改 caller 用 Sonnet 4.6 跑 sync（Q1=C 退路），保留 Opus 4.7 給其他 deep reasoning 用途。
- **prompt 改寫風險** — tool definition 跟原 prompt 字串契約不同，可能丟失某些 instruction（例如 source slug、author 標記等）。寫完後跑一次 happy path 比對輸出跟 grill 前 PR #343 行為一致。
- **idempotency short-circuit 漏洞** — 如果 user edit annotation 但 modified_at 沒更新，short-circuit 會誤跳。先檢查 PR #342 寫入路徑保證 modified_at 必更新（unit test 確認）。

---

## 7. Reference

- [QA bug log](vault://Inbox/qa-line2-bugs-2026-05-04.md) — §7 卡點完整 trace
- [ADR-017](../decisions/ADR-017-annotation-kb-integration.md) — annotation sync 語意 spec
- PR #343 (`af1c709`) — sync to Concept page 原始實作
- [feedback_llm_pipeline_consume_all_fields](../../memory/claude/feedback_llm_pipeline_consume_all_fields.md) — schema 強約束哲學
- [feedback_quality_over_speed_cost](../../memory/claude/feedback_quality_over_speed_cost.md) — 三件並做的優先序依據
