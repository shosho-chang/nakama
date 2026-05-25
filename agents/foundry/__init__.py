"""foundry — script-to-B-roll pipeline agent (ADR-032).

Stage 5 production agent that consumes clean SRT + talking-head video and
emits a DaVinci-importable FCPXML timeline plus individual B-roll mp4 clips.

Layered:
- srt_flattener + chinese_normalizer + beat_aligner — deterministic Python
- planner — single LLM call producing anchor-based beats
- render_dispatcher + render_workers — 3-path (hyperframes / reader-playwright / web-playwright)
- fcpxml_emitter — FCPXML 1.10 (with --fcpxml-version fallback)

Storyboard is the only LLM-produced artifact; Bridge UI (`/foundry/<episode>`)
provides 3 per-row actions (approve / edit-fields / re-plan-with-note) + 3
batch actions, with two-layer approve (text → render → visual).
"""
