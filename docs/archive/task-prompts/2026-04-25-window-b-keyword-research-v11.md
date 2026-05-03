# Window B 交接 — 2026-04-25 夜（keyword-research v1.1 backlog）

**Window A 同時飛中**：3 個 open PR
- **PR #139**（feat/seo-slice-c-brook-integration）— `agents/brook/{compose,seo_block,seo_narrow}.py` + 1 test
- **PR #141**（feat/robin-reader-ui-polish）— `tests/test_robin_router.py` + `thousand_sunny/routers/robin.py` + `thousand_sunny/templates/robin/reader.html`
- **PR #143**（feat/nami-pubmed-lookup）— `gateway/handlers/nami.py` + `shared/pubmed_client.py` + 2 tests

**Window B 絕對不能碰**：
- `agents/brook/**`
- `agents/robin/**`
- `thousand_sunny/routers/{robin,bridge}.py`
- `thousand_sunny/templates/{robin,bridge}/**`
- `gateway/handlers/nami.py`、`shared/pubmed_client.py`
- `shared/approval_queue.py`（PR #140 剛 merged，避免再碰同檔）

可以 grep / 讀取上述任何檔案當 reference，但**不要修改**。

---

## §1 目標

收掉 GH issue [#33 keyword-research skill: v1 eval backlog (6 items)](https://github.com/anthropics/claude-code/issues/33) 的 **Phase A 四項**（Item 6 + 1 + 3 + 2），讓 keyword-research skill 進入 v1.1：CLI 印實測 token + $、terminology 表補「深度睡眠」、synthesis prompt 注入 `{today_iso}`、auto-translate 結果 lowercase。Item 4 / 5（reddit_zh / twitter_zh query 精度）留 Phase B（要實際跑真 API 看 query 結果，較重）。

## §2 範圍

| 路徑 | 動作 |
|---|---|
| `scripts/run_keyword_research.py` | **改**：Step 5/6 之後加 cost summary 區段（input/output/thinking tokens + $ 換算 + 「N runs averaged」） |
| `agents/zoro/keyword_research.py` | **改**：(a) `_auto_translate()` return 前 `.lower()` normalize（Item 2）；(b) synthesis Claude call 注入 `today_iso` 變數（Item 3）；(c) 累積 token usage 回吐給 caller（Item 6） |
| `.claude/skills/keyword-research/references/taiwan-health-terminology.md` | **改**：「睡眠」段補「深度睡眠 / Deep sleep（N3 NREM / 慢波睡眠）」條目（Item 1） |
| `.claude/skills/keyword-research/references/cost-estimation.md` | **改**：cost 段從 a-priori 改成「量測自 v\<commit\>, \<date\>, N=X 次平均」（Item 6 配套） |
| `.claude/skills/keyword-research/SKILL.md` | **改**：Step 4 cost 預估 + Step 6 summary 反映實測值（Item 6 配套） |
| `tests/agents/zoro/test_keyword_research.py`（或既有對應檔） | **改**：補 lowercase normalize / today_iso 注入 / cost emit 三項 unit test |
| `shared/anthropic_client.py` | **只讀**（line 81-85 / 152-156 / 219-223 已 expose `input_tokens` / `output_tokens` / `cache_*`；Window B 確認 thinking token 是否在 usage object 內，沒有就先 0） |

**不碰**：見上「絕對不能碰」段。

## §3 輸入

| 來源 | 內容 | 狀態 |
|---|---|---|
| GH issue #33 body | 6 項 backlog 描述 + 優先序 6 > 1 ≈ 3 > 4 ≈ 5 > 2 | ✅ |
| `docs/evals/keyword-research-2026-04-19-deep-sleep.md` | 首次 eval findings（PASS-WITH-NOTES） | ✅ |
| `shared/anthropic_client.py:33` `ask_claude()` signature | 已抓 `response.usage.{input_tokens, output_tokens, cache_*}`；thinking token 看 SDK | ✅ |
| `agents/zoro/keyword_research.py:23-31` `_auto_translate()` | Item 2 lowercase 點位 | ✅ |
| `.claude/skills/keyword-research/SKILL.md` Step 4 / 6 | 既有 cost 段 a-priori 公式（要替換） | ✅ |
| Capability card 原則 | [feedback_open_source_ready.md](../../memory/claude/feedback_open_source_ready.md) 第 8 點 — 量測自 v\<commit\>, \<date\>, N 次平均 | ✅ |
| Skill 化開發四坑 | [feedback_skill_scaffolding_pitfalls.md](../../memory/claude/feedback_skill_scaffolding_pitfalls.md) — `python -m` shim / 嵌套 fence / `now_fn` forward / `(x or fallback)` 守衛 | ✅ |
| 範例 capability card | [docs/capabilities/](../capabilities/)（已有 kb-search / seo-keyword-enrich 範本，cost 段可對齊） | ✅ |

## §4 輸出

**Item 6 — CLI cost emit**（重點，capability card 原則示範）：

`scripts/run_keyword_research.py` 結尾印：

```
完成！耗時 XX.Xs

成本（實測）：
  Claude tokens:
    input    XXX
    output   XXX
    thinking XXX
  $ 換算（rate card 對齊 reference_llm_provider_cost_quirks.md）：$0.0XXX
  累積 N=X 次平均：見 references/cost-estimation.md
```

**Item 1 — terminology 補條目**：

`.claude/skills/keyword-research/references/taiwan-health-terminology.md` 「睡眠」段加：

```markdown
### 深度睡眠 / Deep sleep

- 學術名：**N3 NREM 睡眠** / **slow-wave sleep (SWS)** / **delta sleep**
- 台灣常見譯名：深度睡眠、深層睡眠（中國大陸常見：深度睡眠、深睡眠）
- Disambiguation：科普角度（什麼是深睡）vs 實作角度（如何提升深睡比例）vs 病理角度（深睡缺乏與失智 / 代謝風險）
- 相鄰術語：REM 快速動眼期、入睡潛時 sleep onset latency、睡眠效率 sleep efficiency
```

**Item 3 — `{today_iso}` 注入**：

合成 prompt template 加 `{today_iso}` 佔位（`agents/zoro/keyword_research.py` synthesis Claude call site，timezone 用 `ZoneInfo("Asia/Taipei")` 對齊 [reference_vps_timezone.md](../../memory/claude/reference_vps_timezone.md)）：

```python
from datetime import datetime
from zoneinfo import ZoneInfo

today_iso = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
prompt = SYNTHESIS_TEMPLATE.format(..., today_iso=today_iso)
```

驗收：跑一次 `--dry-run`（or 真跑）出來 prompt body 有 `today: 2026-04-25`，title seed 不再生「2024 最新」這種過期年份。

**Item 2 — auto-translate lowercase**：

```python
def _auto_translate(topic: str) -> str:
    en = ask_claude(...).strip()
    return en.lower()  # ← 加這行
```

`tests/agents/zoro/test_keyword_research.py` 補 case：「深度睡眠」 → `"deep sleep"`（不是 `"Deep sleep"`）。

**Cost summary 寫進 references/cost-estimation.md**：

```markdown
## 實測成本（v1.1, 2026-04-25, N=3 次平均）

- input tokens: ~XXX
- output tokens: ~XXX
- thinking tokens: ~XXX（若 model 用 extended thinking）
- $ 換算（Claude Sonnet rate card）：$0.0XX
- wall time: XXs
```

## §5 驗收

- [ ] GH issue #33 Item 6 / 1 / 3 / 2 全做完，Item 4 / 5 留 Phase B（PR 描述明寫）
- [ ] `python scripts/run_keyword_research.py "深度睡眠"` 結尾印 cost summary（input/output/thinking + $）
- [ ] `references/taiwan-health-terminology.md` 含「深度睡眠」條目
- [ ] synthesis prompt body grep 得到 `today: 2026-04-25`（或當天日期）
- [ ] `_auto_translate("深度睡眠")` 回傳 `"deep sleep"`（lower）
- [ ] `references/cost-estimation.md` 從 a-priori 改成實測，含 `v<commit>, <date>, N=X` 標記
- [ ] `SKILL.md` Step 4 / 6 cost 段反映實測值（不再是 ~$0.05 a-priori）
- [ ] 全 repo `pytest` pass，無 regression
- [ ] `ruff check` + `ruff format` 綠
- [ ] [feedback_skill_scaffolding_pitfalls.md](../../memory/claude/feedback_skill_scaffolding_pitfalls.md) 四坑全避（`python -m` shim / 嵌套 fence / `now_fn` forward / `(x or fallback)` 守衛）
- [ ] [feedback_dep_manifest_sync.md](../../memory/claude/feedback_dep_manifest_sync.md) — 加新 dep 時 requirements.txt + pyproject.toml 同步（這次應該不會加新 dep）
- [ ] PR 描述列「closes #33（partial — Item 6/1/3/2）」+ Item 4/5 backlog 留下
- [ ] P7 完工格式

## §6 邊界

- ❌ 不動 Item 4 / 5（reddit_zh / twitter_zh query 精度）— Phase B，要實跑 API 對 query 結果，本 PR 範圍外
- ❌ 不重新設計 synthesis prompt 整體架構（issue #33 §不在範圍）
- ❌ 不改 output contract schema（frontmatter 已穩定、下游 Brook compose 會接）
- ❌ 不建 synthetic eval framework
- ❌ 不碰 `agents/brook/**`、`agents/robin/**`、`gateway/handlers/nami.py`、`shared/pubmed_client.py`、`thousand_sunny/routers/{robin,bridge}.py`、`thousand_sunny/templates/{robin,bridge}/**`
- ❌ 不改 `shared/anthropic_client.py` 簽名（已 expose `usage`，只是 wrapper 層接出來；要動 caller side）
- ❌ 不在 PR body 宣稱「N=10 平均」如果只實測 N=2 — 寫實際數字

---

## 開工前 checklist

1. **`pwd` 確認獨立 worktree**：理想是 `cd /Users/shosho/Documents/nakama-window-b`（如已 `git worktree add`），否則跟 Window A 同 tree 會踩到 PR #139 / #141 / #143 in-flight
2. **開 feature branch**：`git checkout -b feat/keyword-research-v11-cost-emit main`（從乾淨 main 切）
3. **`git status` 確認 clean** 才動手
4. commit 前再 `git diff main --stat` 一次，確認檔案範圍對齊本檔 §2，沒看到 `agents/brook/` `agents/robin/` `gateway/handlers/nami.py` 等 Window A 範圍

## Phase B（不在本 PR、留 backlog）

Item 4 reddit_zh query 精度 + Item 5 twitter_zh zh-TW vs zh-CN 分流 — 要：
- 實跑 reddit_api / twitter_api 對若干 health topic（不只 deep sleep）抓 query 結果
- 看 `r/moneyfengcn` 之類無關 hit 是 query 太寬還是排序 bug
- twitter_zh 加 `lang:zh-tw` 或台灣網域偏好
- 各自至少 5 個 topic 對照前後

留給下一輪。
