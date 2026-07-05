# Director 分鏡 skill 實施計畫（ADR-051）

2026-07-05 grill 定案。目標：修修新拍 RAW 到位時，Director 可跑第一次真集數 E2E。

## PR 切片

| PR | 內容 | 依賴 | 估量 |
|----|------|------|------|
| **A（本 PR）** | ADR-051 ＋ `agents/brook/script_video/CONTEXT.md` ＋ CONTEXT-MAP 指標 | — | docs only |
| **B schema** | `render_target` 增 `asset`（`BRollSpec.asset` 子結構：path / source_url / source_span / candidates[] / attribution）；component 增 `transition_title` `book_cover` `quote_card` `doc_highlight`；guardrails 詞彙同步；`fcpxml_emitter` 支援 asset 類 beat（直接檔案，不走 render hash）；`render_dispatcher` 對 asset 類驗檔案存在即 done | — | ~1d |
| **C compositions** | `video/compositions/` 新增 4 個：`transition_title`（滿版章節卡，錨定語音時長）、`book_cover`、`quote_card`、`doc_highlight`（吃頁面 PNG＋bbox，縮圖→推近→黃 highlight）；＋ `cleanup/doc_locate.py`（PyMuPDF：引用句→頁碼+bbox+頁面 PNG）；全部套 `--sho-*` tokens | B（component 詞彙） | ~1.5d |
| **D alpha spike** | Hyperframes 透明輸出（ProRes 4444 / WebM alpha）→ DaVinci import 驗證；PASS → 開 `keypoint_overlay`（v1.5），FAIL → 記 runbook 死路 | — | ~0.5d |
| **E skill 手冊** | `.claude/skills/brook-director/SKILL.md`：流程（分型→節奏預算→逐 beat 決策→素材獲取→assets_queue→驗收→Bridge 審核→render/emit）；頻道分析節奏規則；觸發語意；KOL 護欄；文獻三層 fallback；Codex prompt 模板；resume 點；「每集教訓寫回手冊」章節 | B、C 完成後定稿（草稿可先行） | ~1d |
| **F Bridge UI 小改** | 審核頁顯示 asset 類 beat 的來源連結／候選預覽／出處；螢幕錄影外供槽位顯示 | B | ~0.5d |

## E2E（修修 RAW 到位後）

cleanup（單擊掌＋對稿）→ transcript.srt → **Director skill 首跑** →
Bridge 兩層審核 → assets_queue 交接 Codex → 驗收 → render → emit → DaVinci import smoke。
完成即產出手冊 v1 的第一批教訓與 few-shot example #1。

## 明確不做（本輪）

- side_overlay / pip layout（仍卡 DaVinci transform fixture，Phase 1.5 原案不變）
- 螢幕錄影自動化（外供槽位）
- 配樂/音效（Envato music/SFX 可搜，但不在 Director v1 範圍）
- 判斷結晶回 `plan` prompt（等手冊磨 ~10 集）
