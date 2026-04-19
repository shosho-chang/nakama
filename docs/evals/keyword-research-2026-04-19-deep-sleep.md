---
eval_target: keyword-research skill
skill_version: 3f153a1  # PR #32 merge commit
eval_date: 2026-04-19
eval_topic: 深度睡眠
eval_topic_en: Deep sleep
content_types: [youtube, blog]
eval_route: real-case (transcribe-style, not synthetic assertion framework)
eval_plan: C:\Users\Shosho\.claude\plans\gleaming-swinging-pancake.md
result: pass-with-notes
---

# keyword-research Skill First Real Eval — 深度睡眠

## Environment

- Skill: `f:/nakama/.claude/skills/keyword-research/` @ commit `3f153a1`
- CLI: `f:/nakama/scripts/run_keyword_research.py`
- `trendspy` 0.1.6 (PR #31 after pytrends archival)
- `YOUTUBE_API_KEY` ✓, `ANTHROPIC_API_KEY` ✓
- Non-fatal import warnings: `urllib3 1.26.14 / chardet 7.4.3` vs `requests`; `google.api_core` Python 3.10 EOL notice
- Git: clean on `main`
- Scope (Step 2 gate resolution): 廣角度 — 深眠原理 + 實用混合

## Run Artifacts

| Run | content_type | Output | Size | Duration | sources_used / failed | Result |
|-----|--------------|--------|------|----------|----------------------|--------|
| 1   | youtube      | [keyword-research-deep-sleep-youtube.md](artifacts/keyword-research-deep-sleep-youtube.md) | 13,253 B | 44.4s | 10/10 · [] | ✅ clean |
| 2   | blog         | [keyword-research-deep-sleep-blog.md](artifacts/keyword-research-deep-sleep-blog.md) | 12,735 B | 41.0s | 7/10 · [twitter_zh, twitter_en, trends_zh] | ✅ degraded gracefully |

## Skill UX observations (6 steps + 2 gates)

### Step 2 — Topic clarification gate
- ✅ 觸發正常。SKILL.md 明確要求「即使 fast mode 也不跳過」— 行為符合。
- ⚠️ 術語表 [taiwan-health-terminology.md](../../.claude/skills/keyword-research/references/taiwan-health-terminology.md) **沒有收錄「深度睡眠」這個條目**（只有「睡眠」general / 失眠 / OSA / 時差）。Claude 必須自己提合理的 disambiguation（我這次出的三個選項是：N3 NREM 科普 / 實用導向 / 廣角度）。→ 小改進：可在 terminology 表加「深度睡眠 → 深睡階段 vs 提升深睡實作」。

### Step 4 — Cost go/no-go gate
- ✅ 觸發正常，給了時間/成本/API 配額三項估算。行為一致。

### Step 5 — Pipeline invocation
- ✅ 預期 30–60s，兩次都在範圍內（44.4s / 41.0s）
- ✅ 串流 log 清楚，sources_used / sources_failed / elapsed 三件事都出
- ✅ Auto-translate 正常（深度睡眠 → `Deep sleep`）
- ⚠️ Auto-translate 首字母大寫 `Deep sleep`（而非 `deep sleep`）— 無功能影響，但一致性不佳。EN 資料源 query 用大寫 query 不會出事；仍可考慮 lowercase normalize。

## Output contract compliance

### Run 1 (youtube) — 全綠
- `type: keyword-research` ✓
- `topic` + `topic_en` 俱全 ✓
- `core_keywords = 10`（target 8–12）✓，全部 7 個 required fields 齊全 ✓
- `trend_gaps = 3`（target ≥2）✓
- `youtube_title_seeds = 10` ✓
- `blog_title_seeds = 10` ✓（**彩蛋發現**：兩種 title seeds 無論 content_type 都會產出，content_type 只影響 body prose + 優先序，frontmatter 永遠兩種都有 → 下游 composability 好）

### Run 2 (blog) — 全綠
- 同上，`core_keywords = 10`、`trend_gaps = 4`、`*_title_seeds = 10`
- `sources_failed = [twitter_zh, twitter_en, trends_zh]`，仍達成 sources_used=7 門檻（≥7 per error-recovery.md）

## Quality (subjective)

### ✅ 做得好
- **繁中術語大致正確**：褪黑激素（非褪黑素）、腦脊髓液、雙耳節拍、Delta 腦波（δ波）、脈輪（traditional form）
- **trend_gaps 有實料**：`glymphatic system / 大腦夜間清潔` 是 2013 Nedergaard 以來真實的 EN-leading 睡眠科學趨勢；`Apple Watch / Oura Ring 深睡百分比優化`也是現行 biohacker 主流話題 — 精準。
- **sources fanout 廣**：2 runs 合計觸及 10 個獨立信號源，coverage 健康。
- **graceful degrade 經實測驗證**：Trends_zh 在連續第二跑被 quota 擋，但 pipeline 照常產 markdown，frontmatter 誠實標記 `sources_failed`。
- **雙語 gap 分析正確**：「音樂導向（中文） vs 科學教育（英文）」描述符合實情。

### ⚠️ 可接受但值得改進（不 block）
1. **同 topic 兩 run 之間的 keyword 漂移**（中度）
   - Run 1: `腦脊髓液清洗系統`（準確醫學術語）
   - Run 2: `睡眠清潔系統`（直譯 "sleep cleaning system"，不是自然繁中）
   - 正解應是 `膠狀淋巴系統 / 腦膠淋巴系統 (glymphatic system)`
   - 反映 LLM synthesis 對醫學 niche 術語的穩定性不夠
2. **Reddit/zh 信號品質弱**：兩次 run 都撈到 `r/moneyfengcn「意识是什么」` — 與 deep sleep 毫無關聯。疑似 reddit_zh collector 的 query 太寬。
3. **Twitter/zh 只撈到一篇 張朝陽 簡中貼文**（來自中國大陸 KOL），觸發 Run 1 YouTube title `張朝陽每天只睡4小時的秘密…` — 台灣觀眾覆蓋有限。反映 Twitter/zh 源無法區分 zh-TW vs zh-CN。
4. **時間感知問題**：Run 2 blog title 出現 `2024 最新研究…`（今天 2026-04-19）— Claude synthesis 沒被注入 current date 或被 cache/prior 偏誤。→ 在 synthesis prompt 加 `{today}` 變數可解。
5. **特定數值聲明未驗證**：Run 2 trend_gap `3.2Hz Delta 腦波治療 … 5700 萬觀看` — Delta 波帶是 0.5–4Hz 所以 3.2Hz 合理，但具體觀看數字可能是 hallucination（沒 citation）。

### 🐛 Bug（blocking 性）
**無**。所有「blocking」定義（crash / schema 破 / 全源失敗 / 輸出空）都未觸發。

## Cross-run comparison (stateless verification)

- ✅ 確認 stateless：兩次 run 都完整重跑 auto-translate + 10 源並行收集。
- ✅ content_type=youtube → youtube_title_seeds 明顯更 clickbait（`★` 符號、誇張承諾）；content_type=blog → blog_title_seeds 較長、SEO-friendly（`完整指南`、`7 個方法`、`2024 最新研究`）。差異化明顯。
- `core_keywords` 重疊率 ~70%（10 個中有 7 個同名或近似），3 個差異來自 LLM 變異 + Run 2 缺 trends_zh 訊號改由 zh autocomplete 補位。

## Downstream usability (Brook compose / SEO audit)

- ✅ Frontmatter schema 穩定、可 `yaml.safe_load` parse、欄位完整
- ✅ `core_keywords[].keyword_en` + `trend_gaps[].topic` 方便 SEO skill 串 DataForSEO 查 volume/difficulty
- ✅ 兩種 title_seeds 都在 frontmatter → Brook compose 可同時吃 YouTube (video script) 和 blog 角度
- 💡 建議 [output-contract.md](../../.claude/skills/keyword-research/references/output-contract.md) 新增「下游 consumer 範例 snippet」— 給 Brook / SEO skill 接手者看

## Verification checklist (from plan)

- ✅ Both md files exist, ≥ 3 KB each (13.3 KB / 12.7 KB)
- ✅ `yaml.safe_load` succeeds on both frontmatters
- ✅ `type: keyword-research`, `core_keywords ≥ 8` (10/10), `trend_gaps ≥ 2` (3/4), `*_title_seeds ≥ 8` (10/10)
- ✅ `sources_used ≥ 7` (Run 1: 10; Run 2: 7 — exactly at threshold)
- ✅ Human quality spot-check ≥50% titles worth clicking — **pass**（~7/10 YouTube titles 有點擊感，~8/10 blog titles 可用）
- ✅ Findings note 結論明確：**pass-with-notes**

## Conclusion

**PASS-WITH-NOTES** — Skill V1 可收斂。Pipeline 端到端穩定、graceful degrade 驗證成功、schema 合約乾淨、下游可組合性良好。有 5 個中度可改進項，但都不 block SEO solution 接棒工作。

## Follow-up actions

### Immediate (不 block SEO solution)
- [ ] 更新 memory [project_pending_tasks.md](../../memory/claude/project_pending_tasks.md) → keyword-research eval 完成標記
- [ ] 開一個 GitHub issue 列 improvement backlog（以下 5 項），不另開 PR — 作 backlog：
  1. terminology 表補「深度睡眠」條目
  2. auto-translate lowercase normalize
  3. synthesis prompt 注入 `{today}` 變數防止 `2024 最新研究` 年代錯誤
  4. reddit_zh collector query 精度檢查（`r/moneyfengcn 意识是什么` 怎麼撈進來的）
  5. twitter_zh zh-TW vs zh-CN 分流策略
- [ ] output-contract.md 加 downstream consumer snippet 範例

### Next (立即推進 — 這是 eval 通過後的主線)
- ⏭ **SEO solution prior-art-research**：DataForSEO MCP / Ahrefs MCP / blog audit workflow 選型調研（per [project_seo_solution_scope.md](../../memory/claude/project_seo_solution_scope.md)）

### Not needed
- ❌ 不 re-eval 單一 topic（無 bug 要驗）
- ❌ 不建立 keyword-research-workspace/ eval framework（per transcribe route feedback）
