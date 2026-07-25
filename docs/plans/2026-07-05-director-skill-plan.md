# Director 分鏡 skill 實施計畫（ADR-051）

2026-07-05 grill 定案。目標：修修新拍 RAW 到位時，Director 可跑第一次真集數 E2E。

## PR 切片

| PR | 內容 | 依賴 | 估量 |
|----|------|------|------|
| **A（本 PR）** | ADR-051 ＋ `agents/brook/script_video/CONTEXT.md` ＋ CONTEXT-MAP 指標 | — | docs only |
| **B schema** | `render_target` 增 `asset`（`BRollSpec.asset` 子結構：path / source_url / source_span / candidates[] / attribution）；component 增 `transition_title` `book_cover` `quote_card` `doc_highlight`；guardrails 詞彙同步；`fcpxml_emitter` 支援 asset 類 beat（直接檔案，不走 render hash）；`render_dispatcher` 對 asset 類驗檔案存在即 done | — | ~1d |
| **C compositions** | 前置：STYLE.md 增 video visual grammar（外部素材處理/字幕禁飛區/幀率 conform，panel v2 §5）。`video/compositions/` 新增 3 個：`transition_title`（滿版章節卡，duration=max(語音,可讀)）、`book_cover`、`quote_card`（中英混排 typography 規則）；全部套 `--sho-*` tokens。doc_highlight + doc_locate.py **緩到 v1.1**（panel v2 §3/§4） | B（component 詞彙） | ~1.5d |
| **D alpha spike** | Hyperframes 透明輸出（ProRes 4444 / WebM alpha）→ DaVinci import 驗證；PASS → 開 `keypoint_overlay`（v1.5），FAIL → 記 runbook 死路 | — | ~0.5d |
| **E skill 手冊** | `.claude/skills/brook-director/SKILL.md`：流程（分型→節奏預算(heuristic)→逐 beat 決策→跨語言搜尋詞生成→素材獲取→asset_requests/asset_manifest 雙檔→驗收(sha256+幀率)→Bridge 審核→render/emit）；run log 留痕（panel v2 §1）；KOL 護欄；文獻三層 fallback（英文原句配對，v1.1 起用）；Codex prompt 模板；resume 點；「每集教訓寫回手冊」章節 | B、C 完成後定稿（草稿可先行） | ~1d |
| **F Bridge UI 小改** | 審核頁顯示 asset 類 beat 的來源連結／候選預覽／出處；螢幕錄影外供槽位顯示 | B | ~0.5d |

| **G validate-storyboard** | guardrails hard limits 的 code 強制（panel v2 §11）＋ guardrail/prompt 節奏數字對齊 | B | ~0.5d |

## E2E（修修 RAW 到位後）

cleanup（單擊掌＋對稿）→ transcript.srt → **Director skill 首跑** →
Bridge 兩層審核 → asset_requests/asset_manifest 交接下載 → 驗收 → render → emit → DaVinci import smoke。
完成即產出手冊 v1 的第一批教訓與 few-shot example #1。

## 明確不做（本輪）

- side_overlay / pip layout（仍卡 DaVinci transform fixture，Phase 1.5 原案不變）
- 螢幕錄影自動化（外供槽位）
- 配樂/音效（Envato music/SFX 可搜，但不在 Director v1 範圍）
- 判斷結晶回 `plan` prompt（等手冊磨 ~10 集）
