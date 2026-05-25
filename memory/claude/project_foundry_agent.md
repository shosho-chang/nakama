---
name: Foundry — script-to-B-roll pipeline agent（ADR-032 引入 2026-05-25）
description: 新 agent 寄居 agents/foundry/，獨立於 Brook（ADR-027 narrow 後）。Phase 1 5 PRs：PR-1 scaffold（done #717）/ PR-2 SRT+Mandarin+aligner / PR-3 planner+schema / PR-4 dispatcher+FCPXML+DaVinci fixture / PR-5 Bridge UI Tier 2
type: project
---

ADR-032 引入新 agent `foundry`，**獨立於 Brook**（ADR-001 amendment）。

**Why**：ADR-027 narrow Brook 到 Scaffold + Repurpose + SEO Audit 後，video pipeline 的複雜度（multi-worker render queue + realtime Bridge UI + 跨 episode learning corpus + Hyperframes/Playwright 兩個重 stack）超出 Brook mandate。Panel（Gemini）push-back：「forcing it into agents/brook/ risks bloating Brook into a monolith」。

**Scope**：script + clean SRT + talking-head mp4 → DaVinci-importable FCPXML + 個別 B-roll mp4 clips。Stage 5 production agent. Brook 仍可在 RepurposeEngine 鏈中 invoke foundry，**call-not-host pattern**。

**Architecture**（已 ship to main 2026-05-25 PR #717）：

```
agents/foundry/
├── pipeline.py             # CLI entry: python -m agents.foundry --episode <id> {plan|render|emit|run}
├── srt_flattener.py        # SRT → flat text + char↔time index           [PR-2 stub]
├── chinese_normalizer.py   # Mandarin pre-proc: cn2an + 全形/半形 + 「」  [PR-2 stub]
├── planner.py              # LLM call → beats with exact-copy anchors    [PR-3 stub]
├── beat_aligner.py         # str.find primary + AnchorNotFoundError      [PR-2 stub]
├── render_dispatcher.py    # 3-path dispatch                             [PR-4 stub]
├── render_workers/
│   ├── hyperframes_worker.py            [PR-4 stub, only path Phase 1]
│   ├── reader_playwright_worker.py      [Phase 1.5]
│   └── web_playwright_worker.py         [Phase 1.5]
├── fcpxml_emitter.py       # FCPXML 1.10 + --fcpxml-version fallback    [PR-4 stub]
├── layouts/                # 5 named YAML recipes
│   ├── full_aroll.yaml + full_broll.yaml   ★ Phase 1 active
│   └── side_overlay_{left,right}.yaml + pip_corner_br.yaml  PENDING fixture
├── prompts/broll_planner.md      [PR-3 placeholder]
├── STYLE.md                # editorial rubric — Phase 1 vocab subset enforced
├── guardrails.yaml         # allow/deny hardlimits
├── examples/_index.yaml    # empty Phase 1; gated by len >= 5
├── edit_log/.gitkeep
├── lib/web_highlight_record.py  # promoted from spike (was E:/nakama-reader-record-spike/)
└── README.md               # episode dir layout + storyboard schema + invariants

video/compositions/bigstat/  # promoted from spike worktree (was E:/nakama-hyperframes-bigstat/)
```

**Critical invariants**：

- **Talking head sacred for grade**（v2 修正字眼，不是「永不 encode」— DaVinci 出片本就 encode；invariant 是 pipeline 不破壞 grade latitude）
- **SRT-first input** — not script-first (decoupled from recording)
- **Mistake removal out of scope** — upstream tool 責任
- **LLM/Python decomposition** — LLM 看自然 prose，Python 算 timing；exact-copy anchor + AnchorNotFoundError hard fail
- **`docs/design-system.md` 唯一 brand source**（planner load，不複製 token）
- **Phase 1 layout vocab**：full_aroll + full_broll only。side_overlay_* / pip_corner_br 待 DaVinci import fixture 驗證 adjust-transform 單位後 ship（panel 警告 Apple 規範非 pixel）

**Phase 1 PR map**（estimated 13.5d，v1 9.5d 經 panel 上修）：

| PR | Issue | 狀態 |
|---|---|---|
| PR-1 scaffold | #712 ✅ closed | Merged #717 5321a09 |
| PR-2 SRT/Mandarin/aligner | #713 | Open, sandcastle-tagged，blocked on GH_TOKEN rotation |
| PR-3 planner+schema | #714 | Open, sandcastle-tagged, blocked by #713 |
| PR-4 dispatcher+FCPXML+font+fixture | #715 | Open, no sandcastle (multi-file + GPU + DaVinci 實測) |
| PR-5 Bridge UI Tier 2 | #716 | Open, no sandcastle (UI work) |

**Phase 1.5 backlog**：Reader+Web Playwright 真接通（含 Robin URL scheme 定義） / side_overlay layouts / TransitionTitle component / 拆/合 beat UI / examples retrieval 啟用（corpus ≥ 5）

**References**：
- ADR-032 [docs/decisions/ADR-032-hyperframes-broll-pipeline.md] — full 600 行
- ADR-015 [Superseded] — 保留 Remotion → Hyperframes 改變脈絡
- ADR-001 — amended with foundry agent row（ADR-032 同 PR）
- ADR-027 — narrow Brook（foundry 獨立的依據）
- Codex/Gemini audit verbatim in docs/research/2026-05-25-{codex,gemini}-adr032-audit.md
- [project_broll_dual_path_architecture] — PR #710 decision memory（dispatcher 3-path 為何不統一 Hyperframes）
- [feedback_cdp_screencast_over_recordvideo] — Playwright recording 技術選擇
