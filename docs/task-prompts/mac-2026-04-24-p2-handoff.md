# Mac Session Handoff — 2026-04-24 (Part 2)

**桌機正在做什麼：** 三個 test coverage PR 開著，等 review / merge / 延伸：
- [PR #116](https://github.com/shosho-chang/nakama/pull/116) — `agents/brook/compose.py` coverage 57%→100%（含對話式 API + helpers）
- [PR #118](https://github.com/shosho-chang/nakama/pull/118) — `thousand_sunny/routers/{brook,zoro}.py` smoke + coverage 0%→100%
- [PR #119](https://github.com/shosho-chang/nakama/pull/119) — `agents/robin/kb_search.py` coverage 0%→100% + 修 `"Entities"` type normalize bug

桌機可能延伸下一個 coverage gap：`agents/robin/ingest.py`、`thousand_sunny/routers/franky.py`、`thousand_sunny/routers/robin.py`。

**Mac 絕對不能碰的檔案：**
- `agents/brook/compose.py`（#116）
- `agents/brook/` 下其他 helper 測試（#116 touching）
- `agents/robin/kb_search.py` + `agents/robin/entities.py`（#119，Entities bug）
- `thousand_sunny/routers/brook.py` + `thousand_sunny/routers/zoro.py`（#118）
- 任何 `tests/` 下 `test_compose*.py` / `test_kb_search*.py` / `test_routers_{brook,zoro}*.py`

**建議一起收 D1 + T1（半 session，~2-2.5 小時）。**

---

## 任務 D1：SEO Solution ADR（純 design doc）

### 1. 目標

把 `docs/research/seo-prior-art-2026-04-24.md` §6 的 8 個 open questions 收斂成架構決策，凍結 3 個 skill（`seo-audit-post` / `seo-keyword-enrich` / `seo-optimize-draft`）的邊界、IO schema、phase 1 / phase 2 界線。輸出 ADR，讓後續實作有清楚 contract。

### 2. 範圍

| 路徑 | 動作 |
|---|---|
| `docs/decisions/ADR-008-seo-solution-architecture.md` | **新建**（ADR 編號撞的話往後推，先 ls `docs/decisions/` 確認編號） |
| `docs/research/seo-prior-art-2026-04-24.md` | 只讀（ADR 要 cite 它當 supporting research） |
| `memory/claude/project_seo_solution_scope.md` | 只讀 + ADR 凍結後更新一行 status |
| `agents/brook/compose.py` | 只讀（`_build_compose_system_prompt` 整合點 §4.2） |
| `agents/usopp/publisher.py` | 只讀（SEOPress 寫入點 §2.6） |
| `.claude/skills/keyword-research/SKILL.md` | 只讀（frontmatter schema §2.1） |

**不碰**：`agents/brook/compose.py`、`agents/robin/*`、`thousand_sunny/routers/*`（桌機在動）。不寫 skill 實作、不改 Brook compose 程式碼、不動 SEOPress。純 doc。

### 3. 輸入

- Prior-art doc：`docs/research/seo-prior-art-2026-04-24.md`（§4、§5、§6 是 ADR 的主要素材）
- Nakama principles：`docs/principles/{schemas,reliability,observability}.md` — ADR 要援引
- 既有 ADR 格式參考：`docs/decisions/ADR-007-*.md`（最近的一份 ADR，格式對齊）
- Brook compose 整合點：`agents/brook/compose.py:476`（`compose_and_enqueue` signature）
- keyword-research output schema：`.claude/skills/keyword-research/SKILL.md` frontmatter 區塊

### 4. 輸出

一份 ADR（`ADR-008-seo-solution-architecture.md`），結構：

```
# ADR-008: SEO Solution Architecture

## Status
Proposed — 2026-04-24

## Context
（2-3 段：為什麼現在要 SEO solution；既有 gap；prior-art 摘要 3-5 行）

## Decision

### D1. Skill 家族切法
採 Option A：3 skill（seo-audit-post / seo-keyword-enrich / seo-optimize-draft）
（引 prior-art §5.2；列出每個 skill 的 trigger / IO / 非目標）

### D2. 數據源組合
（列表：GSC API / PageSpeed Insights / DataForSEO Labs / firecrawl / 既有 keyword-research）
（每個附 capability + cost + fallback 策略）

### D3. SEOContextV1 schema 凍結
（列出 pydantic class 完整欄位 + 每個欄位的用途與可選性）
（位置：shared/schemas/publishing.py）

### D4. Phase 1 / Phase 2 界線
Phase 1（先做）：
  - seo-audit-post 基本版（~25 script check + 10 LLM check）
  - seo-keyword-enrich（GSC + DataForSEO Labs）
  - SEOContextV1 schema + Brook compose opt-in 整合
  - Cannibalization detection（§6 第 8 點 — prior-art 建議含在 phase 1）

Phase 2（之後）：
  - seo-optimize-draft（重寫 draft）
  - Cron-driven 整站 GSC 體檢
  - SurferSEO API 評估
  - GEO/AEO optimization

### D5. Brook compose 整合契約
（精準說明：compose_and_enqueue 新增 seo_context: SEOContextV1 | None 參數，
  default None = fallback 到現狀；compose prompt 如何塞 SEO block）

### D6. LLM 模型選擇
seo-audit-post semantic check 用 Sonnet（§6 第 4 點，~$0.02/audit）；
seo-keyword-enrich synth 用 Haiku（純 merge/rank，cheap）

### D7. 觸發詞與 skill frontmatter
（列出 3 個 skill 的 description / trigger phrases，避免與 keyword-research 衝突）

## Consequences

### 正面
- Health vertical 最佳化（GSC 主數據源）
- 月成本 < $3 + 一次性 $50（vs Ahrefs $129/月）
- 三個 skill 都可單獨開源（符合 feedback_open_source_ready.md）
- Brook compose opt-in，不破現有對話式 flow

### 負面 / Risk
- 需 OAuth 設定 GSC API（一次性 setup cost）
- Skill 串接靠 frontmatter contract，schema drift 要 guard
- DataForSEO $50 下限（不做就沒 keyword difficulty 數據）

## Alternatives Considered
（列 prior-art §5.3 「明確不建議走的路線」摘要 3-4 條，每條一句話為何 reject）

## Open Items（留給實作階段的 follow-up）
- 列出 prior-art §6 已回答問題 vs 還沒回答的
- 預期的 PR 切分順序（Slice A/B/C，給下游實作者參考但 ADR 不凍結）

## References
- docs/research/seo-prior-art-2026-04-24.md
- memory/claude/project_seo_solution_scope.md
- memory/claude/feedback_skill_design_principle.md
- memory/claude/feedback_open_source_ready.md
- agents/brook/compose.py:476
- .claude/skills/keyword-research/SKILL.md
```

**補一份 memory 更新**：`memory/claude/project_seo_solution_scope.md` 加一行 "ADR-008 (2026-04-24) — architecture frozen"。

### 5. 驗收（Definition of Done）

- [ ] ADR 8 個 decision（D1-D7）全部明確凍結，沒留 TBD
- [ ] D3 SEOContextV1 pydantic schema 完整列出（field name / type / description / default）
- [ ] D4 Phase 1 / Phase 2 界線清楚（每條 item 明確屬於哪個 phase）
- [ ] D5 Brook compose 整合點精準（含 line number，如 `compose.py:476`）
- [ ] Alternatives Considered 至少列 3 條（Ahrefs / Surfer phase-1 / all-in-one skill），每條一句 reject reason
- [ ] Cross-machine multi-model review：D1 → Zen `codereview` by `gemini-2.5-pro` + `grok-4-fast-reasoning` + `claude-opus-4-6` 的 single-ADR triangulation（參考 `memory/claude/project_multi_model_panel_methodology.md`），找 inconsistencies 並修
- [ ] Memory 更新：`project_seo_solution_scope.md` 加 ADR-008 status
- [ ] PR 標題 `docs(seo): ADR-008 SEO solution architecture` + P7-COMPLETION 格式交付

### 6. 邊界

- ❌ 不寫 skill 實作（skill markdown files 留給後續 PR）
- ❌ 不改 `agents/brook/compose.py`（SEOContextV1 整合留給下一個 PR）
- ❌ 不改 `shared/schemas/publishing.py`（schema 凍結在 ADR，實作在下一個 PR）
- ❌ 不動 `.claude/skills/keyword-research/`（已 production，不得 scope creep）
- ❌ 不動桌機 touching 的檔案（見上方清單）
- ❌ 不自己決定要不要做 Phase 1 — ADR 只凍結架構，實作排程由修修決定

### 7. Multi-model review（2026-04-24 新加的 best practice）

參考 `memory/claude/project_multi_model_panel_methodology.md`：ADR 完成後跑一輪 triangulate
- Gemini 2.5 Pro（吹哨者）— 找細節矛盾 / 假設漏洞
- Claude Opus 4.6（仲裁）— 評估 trade-off 選擇
- Grok 4 fast reasoning（啦啦隊）— 驗證技術可行性

三家若有共識 blocker → 改 ADR；若只 Gemini 吹哨但另外兩家都 OK → 在 ADR 下方 "Open Items" 記一筆，不阻擋 merge。

---

## 任務 T1：agent_memory.py tech debt（快速收尾）

### 1. 目標

`shared/agent_memory.py` 的 3 項 tech debt（PR #42 當時留的）：
1. `update()` 的 `sqlite3.IntegrityError` 路徑沒有 `conn.rollback()`，髒 transaction 可能洩漏到下一個 commit
2. `type` 欄位是 `str`，應該用 `Literal[...]` 收斂合法值
3. 掃過 module 所有 public function 的 docstring，對齊實作準確度

### 2. 範圍

| 路徑 | 動作 |
|---|---|
| `shared/agent_memory.py` | 3 項修正 |
| `tests/shared/test_agent_memory.py`（或相關測試路徑，grep 定位） | 加對應 regression test |
| `agents/nami/memory_extractor.py` / `agents/*/` | 只讀 — 確認 type 欄位實際出現的值集合 |

**不碰**：桌機 touching 的 agent 檔（brook/compose、robin/kb_search、routers/brook、routers/zoro）。

### 3. 輸入

- 當前實作：`shared/agent_memory.py:226-276`（`update` function）
- 當前 type 欄位合法值：grep `"preference"|"fact"|"decision"|...` 在 `agents/` 下找所有 caller 實際傳了什麼
- 既有測試：`tests/shared/test_agent_memory.py`（如果沒有就建；grep 先定位）

### 4. 輸出

#### 4.1 `update()` rollback 修正

現狀 `shared/agent_memory.py:265-273`：
```python
try:
    cur = conn.execute(
        f"UPDATE user_memories SET {', '.join(sets)} WHERE id = ?",
        params,
    )
except sqlite3.IntegrityError as e:
    # subject 改到跟同 (agent, user_id) 的其他 row 撞 UNIQUE
    raise ValueError(f"subject collision: {e}") from e
conn.commit()
```

改成：
```python
try:
    cur = conn.execute(
        f"UPDATE user_memories SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    conn.commit()
except sqlite3.IntegrityError as e:
    conn.rollback()
    raise ValueError(f"subject collision: {e}") from e
```

**為什麼**：sqlite3 Python driver 對 DML 有 implicit transaction，`execute()` raise 後未 commit、未 rollback，下一個 `conn.commit()` 可能把髒 state flush。`sqlite_python_pitfalls` memory 記過同類坑。

順便掃 `add()` / `forget()` / `decay()`，同樣 pattern 要 audit 一次。

#### 4.2 Type Literal 化

1. 先 grep：`grep -rh "memory.add\|agent_memory.add" agents/` 找所有 caller 實際傳的 `type=` 值
2. 收斂成 `Literal[...]`（估計 4-6 種：`"preference"`, `"fact"`, `"decision"`, `"pattern"`, 等）
3. 在 module 頂部定義：
   ```python
   from typing import Literal
   MemoryType = Literal["preference", "fact", "decision", "pattern"]  # 實際值 audit 後填
   ```
4. `add()` / `update()` / `search()` 的 `type` 參數改用 `MemoryType | None`（update / search allow None）
5. `UserMemory` dataclass 的 `type: str` 保留 str（DB 取出不強制 Literal，向後相容 legacy rows）
6. 如果 caller 傳了不在 Literal 裡的值，現在會 static-fail — 列出所有違規 caller 並修齊

**注意**：如果實際 type 值 >8 種或語意鬆散，改 Literal 反而綁手 — 這時候 flag 給修修決定是否值得做。

#### 4.3 Docstring 準確度

掃過 `shared/agent_memory.py` 所有 public function docstring：
- `add()` docstring 第 2 行說「命中則 update content+confidence」— 確認是否真的只 update 這兩個欄位（看實作），不準確就修
- `update()` docstring 說「只更新非 None 的欄位」— 但 `confidence=0.0` 是合法值也是 non-None，實作對但描述容易誤解，考慮補一句「`confidence=0.0` 視為合法更新」
- `decay()` docstring 說「confidence * factor」— 但沒說如果 confidence 已經是 0 會一直停在 0；補一句
- `format_as_context()`、`search()`、`list_*` 同樣逐個 audit

### 5. 驗收

- [ ] `update()` IntegrityError 路徑有 `conn.rollback()`，有 regression test 驗證「UNIQUE collision → rollback → 後續操作看不到髒 state」
- [ ] `add()` / `forget()` / `decay()` 同類 try/except 掃過，該加 rollback 的都加了（或確認 commit 前 raise 已自然 rollback 無需補）
- [ ] `MemoryType` Literal 定義完整，現有 caller 全部符合；若改動有擋人 → 在 PR description 列出全部 caller 遷移清單
- [ ] Docstring 修正項目列出（PR body 列 "before/after"）
- [ ] `pytest tests/shared/test_agent_memory.py -v` 全綠
- [ ] `pytest` 全 repo 綠（baseline 1035 passed / 1 skipped）
- [ ] `ruff check` + `ruff format` 綠
- [ ] `feedback_dep_manifest_sync.md` — 沒加新 dep，skip
- [ ] P7 完工格式交付

### 6. 邊界

- ❌ 不重構 `shared/state.py` 的 `_get_conn()`（SQLite 連線管理不在本任務）
- ❌ 不改 `agents/*/memory_extractor*.py` 的 extractor 邏輯（只做 signature 對齊）
- ❌ 不改 Bridge UI（`thousand_sunny/routers/bridge_memory.py`）— 只動 shared 層
- ❌ 不動 DB schema（`CREATE TABLE user_memories` 不變）

---

## Handoff 注意事項

1. **衝突預防**：
   - 桌機 open PR 三份都在 `agents/brook/` / `agents/robin/` / `thousand_sunny/routers/`，D1 + T1 都不碰這些檔
   - 若桌機延伸到 `agents/robin/ingest.py` / `thousand_sunny/routers/{franky,robin}.py`，Mac 也不碰
   - 各自開 feature branch from `main`

2. **PR 命名建議**：
   - D1: `docs/adr-008-seo-solution-architecture`
   - T1: `refactor/agent-memory-rollback-literal-docstring`

3. **完工後**：兩個 PR 都走 `feedback_pr_review_merge_flow.md`：
   - D1：自動 code-review + multi-model triangulation（§7）→ 報告 → 等修修授權 → squash merge
   - T1：自動 code-review → 報告 → 等修修授權 → squash merge

4. **順序**：T1 先做（<45min 快速清掉 tech debt） → D1 再做（design work，1.5-2hr）。T1 清完 main 會比較乾淨。

5. **若 Mac 跑完還有餘裕**：看 `project_pending_tasks.md` 的 PR #111 follow-ups（GHA quota / curl retry / dedupe / simulate_down 擴展），純 infra 也零衝突。

---

## 為什麼選這兩個

- **D1**：prior-art 已完成（桌機 2026-04-24），blocker 在「決策」而不是「研究」；ADR 是純 doc work，零檔案衝突；ADR 完成後 Phase 1 實作任務才能拆（給下一次 Mac / 桌機手段）
- **T1**：`agent_memory.update()` rollback 是 reliability 風險（雖然實務上被觸發機率低，但一旦 UNIQUE collision 會產生 confusing state）；Literal 化 + docstring 是小重構，Mac 熟這個 module（PR #42 當時 Mac 寫的）；<45min 收一件 tech debt 清單

桌機 coverage work 估計 1-2 小時內可完成或回報，Mac D1 + T1 預估 2-2.5 小時並行。
