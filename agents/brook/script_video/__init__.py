"""agents.brook.script_video — Brook 影片線共用模組。

2026-11-15 起，舊的照稿影片分鏡流程（storyboard.yaml → plan / render / emit → FCPXML，
`python -m agents.brook.script_video` 與 Bridge `/brook/video`）整條刪除——修修：
「舊的完全沒有執行過，可以直接砍掉了」。YouTube 照稿影片改走重新規劃中的新流程。

本套件留下的是 Podcast 長精華／短片還在用的部分：
- finished_cut_production/ — 長精華成片引擎（ADR-066）
- editorial_master / subtitle_handoff — Stage 5 字幕交接
- highlight_* / identity_placement / long_highlight_* / transition_cold_read — 精華選段與視覺
- render_workers/thumbnail_worker — 封面 still render
"""
