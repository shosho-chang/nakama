---
name: project-foundry-phase1-complete
description: Foundry agent Phase 1 全結 2026-05-26 — 5 PR + DaVinci smoke ✅ + Phase 1.5 backlog
metadata:
  type: project
---

# Foundry Phase 1 全結 2026-05-26

ADR-032 Phase 1（Hyperframes 基底 script-to-B-roll pipeline）全部上線。SUPERSEDES
[[project-foundry-agent]] 內 #715/#716 in-flight 描述。

## Ship 紀錄

| PR  | 內容                                                            | Merged commit |
|-----|-----------------------------------------------------------------|---------------|
| 717 | PR-1 scaffold（agents/foundry/、5 layout YAML、guardrails 種子）| `5321a09`     |
| 720 | PR-2 Mandarin normalizer + SRT flattener + beat aligner         | `bdb9bb3`     |
| 723 | PR-3 LLM planner + storyboard.yaml schema（Pydantic）           | `53c8818`     |
| 719 | PR-4 partial（LINE Seed TW + DaVinci fixture + SSIM amend）     | `6c36186`     |
| 724 | PR-4 extend（render_dispatcher + hyperframes_worker + emitter）| `87165e1`     |
| 726 | PR-5 Bridge UI Tier 2（router + template + edit_log + 修 emitter file:// URI）| `9503052` |

**DaVinci import smoke ✅** 2026-05-26 by 修修（screenshot
`G:/OneDrive/Pictures/螢幕擷取畫面/Screenshot 2026-05-26 101637.png`）— V1 black10s +
V2 bigstat3s lane=1 @ 4s + A1 sync 全部 import 成 timeline。

## 兩個重大設計修正（不在原 ADR）

1. **Hyperframes 不 byte-deterministic**（含 `--docker` mode 也不）。ADR-032
   acceptance 從「mp4 hash 對得上」改成「SSIM ≥ 0.99」；byte-hash test 用
   `xfail(strict=True)` 留下追上游 marker。empirical SSIM 0.99977。
2. **FCPXML media-rep src 必須 absolute `file:///` URI**（DaVinci 對相對路徑靜默
   拒絕）。原 emitter 用 `mp4.name` 相對 — PR-5 內順手修 `Path.resolve().as_uri()`。
   絕對 URI smoke 2026-05-26 通過。

## Phase 1.5 backlog（已 defer，不要又當新功能拿來做）

- **拆 / 合 beat UI** — Tier 2 砍了，等真正 dogfood 觸發需求
- **Tier 3 inline `<video>` player** — file:// link 已夠，bumping 等真有人嫌
- **Single-beat re-plan helper**（CLI）— 目前 replan 動作只 clear render_status，
  實際 LLM re-plan 還要走整支 planner re-run；可加 `agents.foundry replan-beat`
- **Planner cold-start retrieval** — examples 目錄當下空，promote UI 開始累積，
  ≥5 條再開 few-shot retrieval（gate 已在 `planner._load_examples()`）
- **Reader + Web Playwright workers** — `render_dispatcher` 已 route `reader-playwright`
  / `web-playwright`，worker module 還沒 implement（dual-path b-roll [[project-broll-dual-path-architecture]]）

## Dogfood next

修修錄一支真 podcast 後，跑：

```bash
python -m agents.foundry --episode <ep-id> run
# plan → render → emit
# 然後 web: http://localhost:8000/foundry/<ep-id> 進 Bridge UI
```

真實使用會浮 Phase 1.5 backlog 中哪些先優先。

## 關聯

- [[project-foundry-agent]] — agent overview（會被本紀錄 supersede in-flight 部分）
- [[project-broll-dual-path-architecture]] — dual path（Reader+Playwright vs Hyperframes）
- [[feedback-grill-then-panel-for-big-adr]] — ADR-032 從 grill v1 → panel v2 的學到
- [[reference-sandcastle-token-rotation]] — PR-2/3/4 sandcastle 401 真根因（`--env-file` flag）
