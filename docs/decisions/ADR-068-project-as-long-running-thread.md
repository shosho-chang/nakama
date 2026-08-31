# ADR-068: Project = 長期戰線；ADR-031 工作區全面退役

- Status: Accepted
- Date: 2026-08-31
- Owner: Thousand Sunny / Bridge
- Supersedes: ADR-031（project workspace 全部）；ADR-021 §4 synthesize review surface（`/projects/{slug}` 頁 + `/api/projects` store；annotation store 本體不受影響，仍歸 Reader/ADR-017）
- Grill: 2026-08-31，逐題拍板（粒度 → 頁面存廢 → vault schema → ADR-021 範圍 → 建立入口 → Weekly 分組）

## Context

ADR-031 把「Project」定義為**單支影片的七道工序工作區**（Brief / Title & Thumbnail / Research /
Hook / Script / Review / Publish）。上線後三個月的實況：

- vault 裡的真實任務全是**跨週反覆推進的長期戰線**（自由艦隊社群文章、26W35 電子報、AI Agent
  百人體驗會），沒有一個掛得進「一支影片」的模型 — 頁面上僅有的兩筆 project 是測試假資料
- 單件產出的工序真相已搬家：packaging / podcast pipeline（ADR-063~067）+ composer skills
  接管了七道工序的實際執行，project 頁的 stage 勾選變成無人維護的裝飾
- 🍅 統計存 frontmatter 快照，正式站顯示「3/11」但底下零任務 — 快照與現實脫節的必然結果

同時，Task→Project 歸屬機制（檔名前綴 `{project} - {task}` + frontmatter `projects:` 雙寫，
PR #1234 接通 reassign）是 Weekly Dashboard 實際在用的活功能。

**根因不是介面爛，是分類單位與工作方式錯配。**

## Decision

1. **Project 重定義為長期戰線**：把跨週任務綁在一起的容器、任務的分組鍵（已凍入
   CONTEXT-MAP.md glossary）。單件產出語意作廢。
2. **Vault schema 極簡化**：`Projects/{名稱}.md` frontmatter 只有 `type: project`、
   `status: active|archived`、`created`；body 自由書寫。**不存任何統計快照** — 一切數字
   read 時從任務即時算。歸屬讀取以 frontmatter 為準、檔名前綴為 legacy fallback；雙寫慣例保留。
3. **新表面（薄）**：`/bridge/projects` 清單頁（active 戰線 + live 統計 + 單欄「+ 新戰線」
   表單；archived 摺疊）+ `/bridge/projects/{slug}` 詳情頁（跨週全任務 + 🍅 實際/規劃 +
   封存/復原）。task 頁下拉的「＋ 新戰線…」inline 建立列為後續小 PR。
4. **Weekly 任務列表自動按戰線分組**（無切換）：同戰線任務聚攏、戰線名視覺區隔、獨立任務
   殿後。獨立 PR 落地，隔離 Weekly 回歸風險。
5. **退役清單**：
   | 資產 | 處置 |
   |---|---|
   | `bridge_projects.py`（2,014 行）+ `templates/bridge/projects/` 18 檔 | 刪，換薄 router + 2 template |
   | `bridge_project_thumbnails.py`（824 行） | packaging S7 gate 現用的 3 條唯讀 serving 路由搬入 `packaging.py`（URL 正名，移除假 slug `gate` hack），其餘刪 |
   | `projects.py`（ADR-021 §4，308 行）+ `templates/projects/` | 刪 |
   | `project_indexer.py` / `project_reviews.py` | 刪，換薄 indexer |
   | `project_writer.py` | 保留任務歸屬函式（rename / reassign / task_project），刪 workspace 專用部分 |
   | `lifeos_templates/project_*.tpl` + `render_project` / `create_project_with_tasks` | 刪，換極簡 stub renderer |
   | `Brook 風格訓練.md` | `type: agent-workspace`，新 indexer 自然忽略，檔案不動 |

## Considered options

- **Project 頁面完全移除、降級為 Weekly 篩選** — 被否：Weekly 只看一週，「這條戰線至今
  累積什麼」的跨週視角在系統裡會沒有家。
- **保留七 stage tab 改唯讀、從 packaging 推導狀態** — 被否：需打通兩套系統，成本遠高於
  價值；產線真相已有自己的 surface。
- **統計續存快照** — 被否：「3/11 幽靈快照」就是這條路的終點。

## Consequences

- `/projects` namespace 清空；`/bridge/projects` 語意全換
- ADR-033 縮圖 pipeline 的 project-tab 入口消失；縮圖產製走 packaging skill（現況如此）
- 未來要重做「單件產出工作區」時，從 packaging line 長出來，不要復活本次退役的程式碼
