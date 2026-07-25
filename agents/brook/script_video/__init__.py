"""agents.brook.script_video — Brook Video Production Line（ADR-032 技術設計 / ADR-050 歸屬）.

Stage 5 production line：吃 clean SRT + talking-head video，產出 DaVinci-importable
FCPXML timeline + 個別 B-roll mp4 clips。原 `agents/foundry/`（ADR-032 引入），
ADR-050 D1 裁決歸 Brook 後整樹遷入。

Layered:
- cleanup/ — 拍掌 marker mistake removal（選配前置 stage，ADR-050 D3）
- srt_flattener + chinese_normalizer + beat_aligner — deterministic Python
- planner — single LLM call producing anchor-based beats
- render_dispatcher + render_workers — 3-path (hyperframes / reader-playwright / web-playwright)
- fcpxml_emitter — thin adapter over shared/fcpxml (ADR-050 D2)

Storyboard is the only LLM-produced artifact; Bridge UI (`/brook/video/<episode>`)
provides 3 per-row actions (approve / edit-fields / re-plan-with-note) + 3
batch actions, with two-layer approve (text → render → visual).

Sub-package 硬邊界（ADR-050 D1）：Brook 其他模組不得 import 本套件內部，
只准走 CLI（`python -m agents.brook.script_video`）或 pipeline 頂層 API。
"""
