---
name: Script-Driven Video Slice 1 SHIPPED 2026-05-03
description: PR #320 squash merged bb5d54d 2026-05-03 — DSL parser + clap removal + FCPXML 1.10 emit + Hyperframes swap (5d2dcf6) + path traversal guard (71f2ba6) + DaVinci import smoke 桌機通過；issue #313 closed；Slice 2 #314 unblocked 等修修 Claude Design hands-on
type: project
created: 2026-05-02
updated: 2026-05-03
---

修修 2026-05-02 grill 凍結「腳本式 YouTube 影片自動化」workflow。Phase 0-6 全 closed，Slice 1 ship 進 main 2026-05-03。

## Slice 1 ship 戰績

PR #320 squash merged `bb5d54d` 2026-05-03 02:05 UTC（10:05 CST）。

### Phase 進度

- ✅ **Phase 0 grill** — `/grill-with-docs` 走 7 分岔
- ✅ **Phase 1 PRD** — `/to-prd` 提交 #310
- ✅ **Phase 2a** — PR #311 merged：ADR-015 Accepted + Plan + CONTEXT-MAP + memory
- ✅ **Phase 2b** — `/to-issues` 5 slice issue 建好（#313/#314/#315/#316/#317）+ blocked-by chain
- ✅ **Phase 2c** — triage：#313/#316 → sandcastle + ready-for-agent；#314/#315/#317 → ready-for-human
- ✅ **Phase 3** — sandcastle dispatch round 2 success → Slice 1 PR #320 opened
- ✅ **Phase 4** — multi-agent review w/ isolation worktree (replace ultrareview)
- ✅ **Phase 5** — mac e2e session 抓 5 ship-blocker bug 全修 + cut 語意翻轉
- ✅ **Phase 6** — Hyperframes swap (5d2dcf6) + path traversal guard (71f2ba6) + DaVinci smoke 桌機通過 + squash merge

### Slice 1 final commit chain (PR #320)

```
5d2dcf6 chore(brook): swap Remotion → Hyperframes for video subproject
71f2ba6 fix(brook): script_video — path traversal guard + doc drift + load_config
1d7ad8d fix(brook): mistake_removal — flip cut semantics to keep retake, drop NG take
987ef15 fix(brook): script_video pipeline — fix 4 ship-blockers + add e2e CI test
7d4a4f3 fix(brook): address multi-agent review findings on Slice 1
95a7661 feat(brook): Slice 1 骨幹 — DSL parser + clap removal + FCPXML 1.10 emit
```

### DaVinci import smoke 結果（修修桌機 2026-05-03）

- ✅ Import 無 schema error popup
- ✅ Timeline 載入成功
- ✅ Source media 連得到（absolute `file:///E:/nakama/...` URL）
- ⚠️ Razor cut 視覺看不到（fixture 限制 — `clap_marker_audio.wav` 無 voice content，cut 演算法把整段除 4 frame lead-in 全砍）— 但這是設計如此不是 bug
- ✅ Ripple delete 生效（4/30s << 240/30s）
- ✅ 30fps exact (`frameDuration="1/30s"`)

### 桌機跑 smoke 跟 Mac runbook 差異（已記）

- xmllint 沒裝 → Python `xml.etree.ElementTree.parse()` 替代
- video/ 子專案首次 build：`npm install`（47 套件 vs Remotion 200+，因 Hyperframes swap）+ `npx tsc`
- system Python 沒 yaml dep → 用 venv `E:/nakama/.venv/Scripts/python.exe`
- DaVinci Resolve 路徑 `C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe`
- 桌機 GPU RTX 5070 Ti 跑 DaVinci 比 Mac 流暢

## Hyperframes swap 重要決定

`5d2dcf6` swap Remotion → Hyperframes（Apache-2.0、AI-agent ergonomic 更好、library-clock animation seekable、no build step、HeyGen 50+ pre-built block catalog）。Slice 1 Remotion-specific code 為零（parse.ts/types.ts/validate.ts engine-agnostic），swap 零 migration cost。Slice 2+ 開始 render 時 add `hyperframes` + `@hyperframes/core` deps。

詳細 7 條理由見 commit 5d2dcf6 message。

## 5 Slice 拆分進度

| Slice | Issue | PR | Status |
|---|---|---|---|
| 1 骨幹 | #313 | #320 ✅ merged | shipped 2026-05-03 |
| 2 6 components | #314 | — | unblocked，**修修主導 Claude Design hands-on（非 sandcastle）** |
| 3 PDF + DocumentQuote | #315 | — | blocked by #314 |
| 4 Embedding (BGE-M3) | #316 | — | blocked by #315；最佳 sandcastle 候選 |
| 5 端到端 dry-run | #317 | — | blocked by #316；修修主導 |

## Sandcastle 戰績整理

| Round | Image | 結果 | 教訓 |
|---|---|---|---|
| Round 1 (#240) | v1 | ✅ | base setup |
| Round 2 (#239) | v1 | ✅ | sonnet-4-6 sufficient for protocol-compliant code |
| Round 3 (#270 Mac) | v2 dev tools | ✅ | Mac UID 502 vs image 1000 chmod fix → PR #318 |
| Round 4 (#313) | v3 chmod | ✅ | agent 自 install scipy/librosa + npm install local tsc，25 tests pass |
| Round 5+ (planned) | v4 + xmllint+tsc | 待 dispatch | Slice 4 #316 unblock 後跑 |

Image v4 含預裝 dev tools (pytest 9.0.3 + ruff 0.15.12 + xmllint libxml 20914 + tsc 6.0.3)，2026-05-03 桌機 sync + rebuild 完成（見 [project_session_2026_05_03_pr320_ship.md](project_session_2026_05_03_pr320_ship.md)）。

## Slice 1 (#313) 範圍實際 ship

`agents/brook/script_video/` Python pipeline 5-stage scaffold：cuts.py / manifest.py / mistake_removal.py / fcpxml_emitter.py / srt_emitter.py / pipeline.py / __main__.py
`video/` Node.js TS scaffold（Hyperframes engine-agnostic deps）：src/parser/{parse,validate,types}.ts + tests/parser.test.ts + package.json + tsconfig.json
`tests/brook/script_video/` 51 tests pass（Phase 6 path traversal +9）
`tests/fixtures/script_video/clap_marker_audio.wav` synthetic fixture
`scipy>=1.11` → pyproject.toml + requirements.txt
`video/node_modules/` + `video/dist/` → .gitignore

## 文件 / artifacts 索引

- PRD：[#310](https://github.com/shosho-chang/nakama/issues/310)
- ADR：[docs/decisions/ADR-015-script-driven-video-production.md](../../docs/decisions/ADR-015-script-driven-video-production.md)
- Plan：[docs/plans/2026-05-02-script-driven-video-production.md](../../docs/plans/2026-05-02-script-driven-video-production.md)
- 5 slice issue：#313 (closed) / #314 / #315 / #316 / #317
- DaVinci smoke runbook：[docs/runbooks/2026-05-02-davinci-import-smoke.md](../../docs/runbooks/2026-05-02-davinci-import-smoke.md)
- PR #311（Phase 2a 凍結）/ PR #312/#318/#319/#320（Phase 2b/2c/3-6）

## 跟既有專案的 cross-ref

- 跟 `project_podcast_theme_video_repurpose.md` 不同 — 那條「訪談抽亮點」，這條「腳本式照稿 + 自動 B-roll」
- 跟 `project_three_content_lines.md` Line 1/2/3 不同 — 那是 RepurposeEngine fan-out；這條 sequential pipeline，**不是 Line 4**
- 跟 ADR-014 RepurposeEngine — sibling 不繼承不擴展
- 跟 ADR-001 Brook = Composer — 仍合理
- 跟 ADR-013 transcribe — Stage 2+ 重用 WhisperX

## Slice 2 #314 dispatch 起手點

下次 session 起手做 Slice 2，**修修主導不是 sandcastle**：
1. 開 Claude Design 視覺探索 6 個 Hyperframes component（ARollFull / TransitionTitle / ARollPip / DocumentQuote / QuoteCard / BigStat）
2. 美學迭代到滿意 → 「交付套件 → Claude Code」單指令 handoff 落地
3. Hyperframes deps add：`npm install hyperframes @hyperframes/core` 到 video/package.json
4. DSL parser 5 directive 擴展（aroll-pip / transition / quote / big-stat / 第 5 個 TBD）
