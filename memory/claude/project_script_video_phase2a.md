---
name: Script-Driven Video Production Phase 1-4 closed → Phase 5 等 DaVinci smoke + merge
description: 修修最高價值 workflow 自動化專案 — Phase 0-4 全 closed（PRD/ADR/Plan/5 issue/sandcastle round 2 ship Slice 1/multi-agent review 修 8 findings），Phase 5 squash merge 等修修 DaVinci import smoke (#320 HITL gate)
type: project
created: 2026-05-02
updated: 2026-05-02
---

修修 2026-05-02 grill 凍結「腳本式 YouTube 影片自動化」workflow。Phase 0-4 全 closed，Phase 5 等修修 DaVinci import smoke 後 squash merge。

## Phase 進度

- ✅ **Phase 0 grill** — `/grill-with-docs` 走 7 分岔
- ✅ **Phase 1 PRD** — `/to-prd` 提交 #310，修修 approved
- ✅ **Phase 2a** — PR #311 merged：ADR-015 Accepted + Plan + CONTEXT-MAP + memory
- ✅ **Phase 2b** — `/to-issues` 5 slice issue 建好（#313/#314/#315/#316/#317）+ blocked-by chain；quiz 4 自答（granularity ✅ / 依賴鏈 ✅ / Slice 1 不拆 ✅ / 1 AFK + 4 HITL ✅）
- ✅ **Phase 2c** — triage：#313/#316 → sandcastle + ready-for-agent；#314/#315/#317 → ready-for-human；PRD #310 tracking comment
- ✅ **Phase 3** — sandcastle dispatch round 2 success → Slice 1 PR #320 opened
  - **Round 1 fail**：Dockerfile chmod 在 pre-install 之前跑，UID 1000 own 的 dev tools 對 host UID 501 permission denied → readability-lxml transitive `sgmllib.py` rewrite fail
  - Fix：PR #318 merged — chmod after pre-install + memory Phase 2b/2c
  - V3 image：PR #319 merged — pre-install libxml2-utils (xmllint) + npm install -g typescript (tsc)
  - **Round 2 success**：agent (sonnet-4-6) 自 install scipy/librosa + npm install local typescript + 寫 25 tests + commit `feat(brook): Slice 1 骨幹` + sandcastle merge-to-host-HEAD
- ✅ **Phase 4 multi-agent review** — 3 parallel general-purpose agent w/ isolation worktree（替代 ultrareview）
  - Reviewer A（correctness）：0 blocker / 4 major / 6 minor
  - Reviewer B（test-gap）：2 blocker / 2 major / 3 minor
  - Reviewer C（API-design）：0 blocker / 4 major / 2 minor
  - Convergence on 3 finding（fps cross-lang drift / mistake_removal silent loss / stub style inconsistency）
  - **Commit 7d4a4f3 解 2 blocker + 6 major**：CLI test added / lxml 結構驗證 / `<asset-clip>` / fps Literal[30] / source_id+match_index / Citation+Slide / `detect_alignment_cuts` raises / Stage 1 fallback fix / 砍 `_python_fallback_parser`
  - Tests 25 → 35 passed（+5 CLI +3 lxml +1 alignment +1 asset-clip rename）
- 🔄 **Phase 5** — 等修修 DaVinci import smoke (HITL gate) → squash merge PR #320 → close #313

## 5 Slice 拆分（Phase 2b 落 issue + Phase 3 進度）

| Slice | Issue | PR | Status |
|---|---|---|---|
| 1 骨幹 | #313 | #320 | ✅ ready，等 DaVinci smoke + merge |
| 2 6 components | #314 | — | ⏸ blocked by #313 merge；hands-on dispatch |
| 3 PDF + DocumentQuote | #315 | — | ⏸ blocked by #314；hands-on dispatch |
| 4 Embedding (BGE-M3) | #316 | — | ⏸ blocked by #315；**最佳 sandcastle 候選** |
| 5 端到端 dry-run | #317 | — | ⏸ blocked by #316；修修主導 |

## Sandcastle 戰績 + image evolution

| Round | Image | 結果 | 教訓 |
|---|---|---|---|
| Round 1 (#270 fix) | v1 | 通過 | base |
| Round 2 (#288/#289) | v1 | 通過 | sonnet-4-6 sufficient for protocol-compliant code |
| Round 3 (#270 retry) | v2（dev tools 預裝） | 通過 | dev tools pre-install saves ~10-15s wall per iter |
| Round 4 (#313) | v2 | **fail** at pip install | UID 501 vs UID 1000 chmod 漏跑 → PR #318 |
| Round 5 (#313 retry) | v2 chmod fix | **success** | agent 自 install scipy/librosa + npm install local tsc，25 tests pass |
| Round 6+ (planned) | v3（+xmllint+tsc） | 待 dispatch | Slice 4 #316 unblock 後跑 |

## Slice 1 (#313) 範圍實際 ship

`agents/brook/script_video/` Python pipeline 5-stage scaffold：cuts.py / manifest.py / mistake_removal.py / fcpxml_emitter.py / srt_emitter.py / pipeline.py / __main__.py
`video/` Node.js TS scaffold：src/parser/{parse,validate,types}.ts + tests/parser.test.ts + package.json + tsconfig.json
`tests/brook/script_video/` 35 tests（25 sandcastle + 10 review fix）
`tests/fixtures/script_video/clap_marker_audio.wav` synthetic fixture
`scipy>=1.11` → pyproject.toml + requirements.txt
`video/node_modules/` + `video/dist/` → .gitignore

不在範圍（Slice 2-5）：
- 5 個 DSL directive（aroll-pip / transition / quote / big-stat）
- 6 Remotion components 實作
- PyMuPDF + DocumentQuote 渲染
- BGE-M3 + sqlite-vec
- 端到端 dry-run

## 下次 session 接手起手點

1. 看 PR #320 狀態 — `gh pr view 320 --json state,mergeStateStatus,statusCheckRollup`
2. 修修若已 DaVinci import smoke pass → squash merge + close #313 + 開 Slice 2 #314 hands-on
3. 修修若 DaVinci 抓 schema warning → diagnose + 開 fix commit
4. Worktree leak 清：`.claude/worktrees/agent-aed8564fc242aa3a3` locked，git worktree remove --force / unlock 都被 deny rule 擋；需 修修 settings.json 暫解 deny / 或 rm -rf 走回收桶 PowerShell
5. Slice 2 #314 dispatch 走 Claude Design 視覺探索 + Claude Code 落地（hands-on，非 sandcastle）

## 文件 / artifacts 索引

- PRD：[#310](https://github.com/shosho-chang/nakama/issues/310)
- ADR：[docs/decisions/ADR-015-script-driven-video-production.md](../../docs/decisions/ADR-015-script-driven-video-production.md)
- Plan：[docs/plans/2026-05-02-script-driven-video-production.md](../../docs/plans/2026-05-02-script-driven-video-production.md)
- 5 slice issue：#313 / #314 / #315 / #316 / #317
- PR #311 merged（Phase 2a：ADR + Plan + memory）
- PR #312 merged（memory update）
- PR #318 merged（Dockerfile chmod fix + Phase 2b/2c memory）
- PR #319 merged（Dockerfile xmllint + tsc pre-install）
- PR #320 open（Slice 1 sandcastle + multi-agent review fix）

## 跟既有專案的 cross-ref

- 跟 `project_podcast_theme_video_repurpose.md` 不同 — 那條「訪談抽亮點」，這條「腳本式照稿 + 自動 B-roll」
- 跟 `project_three_content_lines.md` Line 1/2/3 不同 — 那是 RepurposeEngine fan-out；這條 sequential pipeline
- 跟 ADR-014 RepurposeEngine — sibling 不繼承不擴展
- 跟 ADR-001 Brook = Composer — 仍合理
- 跟 ADR-013 transcribe — Stage 2+ 重用 WhisperX
