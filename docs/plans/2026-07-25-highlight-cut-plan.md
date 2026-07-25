# highlight-cut — 訪談集精華選段（2026-07-25 grill 凍結）

修修需求：整集長訪談切出 ~5 個 10 分鐘段落（橫式長片上 YT）+ ~5 個 1–2 分鐘段落
（直式短影片），交三個 persona 盲審評分，各選 top 3；精彩就多提。

Pipeline 位置：CONTENT-PIPELINE **Stage 5 影片 channel**，podcast-pipeline 鏈最後一段
（audio-prep → subtitle-gen → subtitle-correct → speaker-split → gap-fill →
resolve-project → **highlight-cut**）。

## Grill 凍結決策（2026-07-25，逐題修修拍板）

| # | 決策 | 內容 |
|---|------|------|
| Q1 | 段落定義 | **連續時間範圍**，吸附 cue 邊界；cold-open 重排/去句間停頓留 v2 |
| Q2 | 物化形態 | 當選段 = 獨立 Resolve timeline；全部候選在主 timeline 打 marker（當選紅/落選藍） |
| Q3 | 短片畫框 | 直接建 1080×1920 直式 timeline；字幕樣式先橫式，修修調一次後 `--make-template` 存直式模板 |
| Q4 | 開採派工 | 3 視角 miner（故事弧/金句爆點/實用價值）平行提案 → 重疊 >50% 去重留強者 |
| Q4b | 長度 | 三層制：目標帶（長 8–12min／短 60–120s）+ 容忍帶（6–18min／40–180s）+ 唯一硬上限 Shorts 180s（平台）。**內容邊界優先於長度**，絕不論述中間切 |
| Q5 | persona | 阿哲/凱文/淑芬 YT 化（`set: yt-audience`，源自修修凍結的 p0-audience-personas + YT top20 真實留言）+ brand-lens（斷章取義否決權）+ Renee（僅長片，留存曲線 lens） |
| Q6 | 評選 | **各選 3**（長 3 + 短 3）；中位數排名（不用平均）；同分新觀眾分優先；lens 不計分但 brand-lens 有否決權（標紅交修修裁決，不自動排除）；落選全留報告 + marker |
| Q7 | 流程 | **一鍵到底**：開採 → 盲審 → 物化 → 報告；修修只看最終報告，換段 = 重物化（冪等） |

## 產物

```
G:\footages\<ep>\highlights\
├── candidates.json       # 全部候選 {id, format, t_start, t_end, title, hook, rationale, miner}
├── winners.json          # 評選結果（分數、排名、否決標記）
├── TA審稿回饋-*.md        # persona-review 報告
└── 選段企劃-<ep>.md       # 各 3 當選段（3 標題候選+hook+理由+persona 摘要）+ 落選短評
```

Resolve：`Highlights` bin、timeline 命名 `長1 - <標題>`／`短1 - <標題>`。

## 實作件

- `scripts/run_highlight_cut.py`：candidates 驗證（cue 吸附/長度帶/重疊去重）+ `--materialize`
  （建 timeline + marker，冪等）+ `--dry-run`
- persona ×4 + rubric ×2（本 PR，`status: draft` 待修修過目）
- `.claude/skills/highlight-cut/SKILL.md`：Cowork 編排手冊
- 零 API 錢：miner + persona 全走 Cowork subagent（subscription quota）

## v2 路線圖（記錄不做）

- 短片進階剪輯：cold-open 重排、詞級時間戳自動去句間停頓（jump-cut 密度）
- 訪談類影片留言補掃（top20 掃描裡訪談只有 Yuki 一支，persona 訪談樣本偏薄）
- 直式字幕樣式模板（等修修調完第一支）
- YT Analytics 留存曲線回饋校準 rubric 權重
