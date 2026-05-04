# Robin — Knowledge Base Context

Robin 是 nakama 的 KB agent：吸收 source（article / paper / book / podcast）→ 抽 concept / entity → 寫 wiki page。本 context 涵蓋 source 進 KB 的完整 lifecycle 與 annotation 處理規則。

> Status: 2026-05-04 grill ADR-017 重設計中。本檔 lazy 凍結已確認 term，未凍結部分標 `(待 grill)`。

## Language

**Source page**:
持久存儲於 `KB/Wiki/Sources/{slug}.md` 的純 source 內容；身份是「**cross-source reference 的公正資料來源**」，**不嵌 annotation / 個人觀點**（污染禁止）。
_Avoid_: source file（太泛）, source markdown（太實作）

**Annotation**:
Reader 中對 source 段落的 ref+note 對（schema: `{type: "annotation", ref, note}`）；**不持久進 source page**（per Source page 公正原則）；持久化於 `KB/Annotations/{slug}.md` 獨立檔。
_Avoid_: comment（修修日常用詞，但 schema 不叫 comment，避免雙詞並存）

**Highlight**:
Reader 中對 source 段落的純標記，無 note（schema: `{type: "highlight", text}`）。同 annotation 不嵌 source page，跟 annotation 一起住 `KB/Annotations/{slug}.md`。

**Ingest**:
Robin pipeline — 把 Inbox 檔案處理成 `KB/Wiki/Sources/{slug}.md` + 抽 concept / entity → 寫 / 更新 concept / entity wiki page。**只動 source 內容，不嵌 annotation**（污染禁止）。
_Avoid_: 把「annotation 進 KB」也叫 ingest（不同 path 不同詞彙）

**Concept page**:
`KB/Wiki/Concepts/{slug}.md`，cross-source evidence aggregator（per `memory/claude/feedback_kb_concept_aggregator_principle.md`）。`## 個人觀點` section 接收 annotation push，per-source HTML comment boundary 隔離。

**「同步到 KB」按鈕**（行將砍除）:
PR #344 在 reader header 加的獨立按鈕，按 → annotation push 到 concept page `## 個人觀點`。grill 結論將砍按鈕、改自動觸發（觸發點待 grill）。
_Avoid_: 任何將「sync」當持久存在概念用的詞彙；按鈕廢除後此詞退場

## Annotation lifecycle 凍結原則

| # | 原則 | 來源 |
|---|---|---|
| 1 | annotation 必須持久化 | 修修：之後寫文章要 reference |
| 2 | source page 不嵌 annotation | 修修：source 是公正 reference |
| 3 | annotation 拆獨立檔（`KB/Annotations/{slug}.md`） | ADR-017 §Decision（保留） |
| 4 | annotation push 到 concept page `## 個人觀點` 機制保留 | 修修 Scenario (i) reference 用 |
| 5 | 進 KB 不該獨立按鈕、應自動觸發 | 修修：今天的 frustration |
| 6 | 觸發點 = (X) source ingest 結束 + (Y) annotation save 後 debounce，兩者並行 | grill Q5 凍結 |
| 7 | source 重 ingest 時 annotation 跟著走丟 OK | grill 確認 0-1 次/年 + 可接受 |
| 8 | Reader header 加 Ingest button、ingest 從 reader 也能觸發 | grill Q5 凍結 |

## Reference scenario（修修寫文章時翻 annotation 的 frequency）

| Scenario | Frequency | Annotation 存哪最有用 |
|---|---|---|
| (i) 寫單一 concept 文章 | **主要** | concept page `## 個人觀點` push 機制 |
| (ii) 寫 cross-source 主題 | **主要** | `KB/Annotations/{slug}.md` 個別檔 |
| (iii) Quote 具體某條 annotation | 偶爾 | 全域 search（待設計） |

## Relationships

- **Reader 標 annotation**（Thousand Sunny / Robin reader） → `KB/Annotations/{slug}.md`（auto save，已 ADR-017）
- **Source ingest**（`/start` from inbox） → `KB/Wiki/Sources/{slug}.md` + 抽 concept → `KB/Wiki/Concepts/{slug}.md`
- **Annotation push to concept page** → concept page `## 個人觀點`，per-source boundary
  - **(X)** source ingest 結束自動 push 已存全部 annotation
  - **(Y)** annotation save 後 debounce auto-push（若 source 已 ingest）

## Flagged ambiguities

- **「ingest」泛意 vs 嚴格意義** — 修修原 mental model 用泛意（「進 KB 這件事」），grill 凍結用**嚴格意義**（只指 source pipeline）。annotation 進 KB 是另一條 path、不歸 ingest 詞彙
- **「comment」vs「annotation」** — 修修日常用「comment」，schema / 程式碼 / 本 CONTEXT 凍結用「annotation」，避免雙詞並存
- **「同步到 KB」這個動作** — 按鈕廢除後，動作改稱「**annotation push to concept**」，避免 sync 一詞汎濫

## Example dialogue

> **修修**：「我剛在 reader 標了 5 條 annotation，這些會跟 source 一起 ingest 進 KB 嗎？」
> **Robin domain expert**：「Annotation 不會嵌進 source page — source page 是公正 reference，不污染。Annotation 拆存在 `KB/Annotations/`，並會自動 push 到對應 concept page 的 `## 個人觀點` section（具體觸發點待凍結）。Source ingest 跟 annotation push 是兩條 path、走 `/start` flow + lifecycle event 各自跑。」
