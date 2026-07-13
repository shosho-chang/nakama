---
title: DaVinci import smoke — Script-Driven Video FCPXML acceptance gate
status: active（ADR-050 全案落地後更新 2026-07-04 — FCPXML 生成走 `shared/fcpxml/` builder；cleanup subcommand 已接通，指令見 §3）
created: 2026-05-02
updated: 2026-07-04
owner: 修修（Mac DaVinci Resolve operator）
applies_to: every PR that touches `shared/fcpxml/` 或 `agents/brook/script_video/cleanup/ripple_fcpxml.py`
related:
  - ADR-015 docs/decisions/ADR-015-script-driven-video-production.md
  - Plan docs/plans/2026-05-02-script-driven-video-production.md
  - Issue #313 (Slice 1 acceptance criterion #5)
---

# DaVinci import smoke — runbook

**Last updated: 2026-05-02 (Slice 1 / PR #320)**

Slice 1 的 35 個自動測試只驗 FCPXML 1.10 通過 `xmllint --schema` 結構有效，**沒驗 DaVinci Resolve 真認得**。本 runbook 是 hard acceptance gate，每次動 `fcpxml_emitter.py` 或 `video/src/parser/` 都要跑一次。

預估時間 5–10 min（含 DaVinci 開啟 cold start ~30s）。

---

## 0. 前置條件

- [ ] **DaVinci Resolve 18.0+** 已裝（Mac）— `/Applications/DaVinci Resolve/DaVinci Resolve.app` 存在
- [ ] **ffmpeg** 在 PATH（pipeline Stage 0 要 extract audio/video stream）
- [ ] **Python venv** 跑得起來（repo 主 venv 即可，無需 GPU / WhisperX）
- [ ] **Node.js + video/ subproject 已 build**（or 接受 Python fallback parser，aroll-full only）— Slice 1 階段兩條路都行

---

## 1. 取最新 PR branch

```bash
cd /Users/shosho/Documents/nakama
gh pr checkout 320
git log --oneline -1   # 預期看到 7d4a4f3 (或之後的 fix commit)
```

⚠️ 不要用 local stale `pr-320` branch（95a7661 是 pre-review-fix）— 一定 `gh pr checkout` 拉最新。

---

## 2. 造 dummy episode（首次跑）

Slice 1 沒附 fixture episode dir，需要手造：

```bash
mkdir -p data/script_video/smoke-001
```

**`data/script_video/smoke-001/raw_recording.mp4`** — 任何含拍掌音的 MP4 都行；最簡：把 fixture WAV 包成 MP4：

```bash
ffmpeg -i tests/fixtures/script_video/clap_marker_audio.wav \
       -f lavfi -i color=c=black:s=1920x1080:r=30 \
       -shortest -c:v libx264 -c:a aac \
       data/script_video/smoke-001/raw_recording.mp4
```

**`data/script_video/smoke-001/script.md`**：

```markdown
# Smoke Test Episode

[aroll-full]
這是一段測試錄音，包含拍掌標記。
```

---

## 3. 跑 pipeline（指令已隨 ADR-050 更新，2026-07-04）

```bash
# （script-anchored 主線，2026-07-04 起）先產 WhisperX 字級 timestamps（GPU，修修本機跑）
python scripts/run_whisperx_words.py data/script_video/<id>/aroll-audio.wav \
       --output data/script_video/<id>/words.json

# cleanup：words.json 存在 → 單擊掌 + retake 指紋回溯模式；
# 逐字稿（script.md）也在 → 另產文字全對、時間軸已重映射的 transcript.srt。
# 無 words.json → 退回 legacy double-clap 音訊模式。
# episode 目錄需有 episode.yaml，缺會 fail loud 附建立指引。
python -m agents.brook.script_video --episode smoke-001 cleanup
# 預期: "cleanup: N ripple-delete cuts → .../out/cleanup.fcpxml"

# 獨立字幕校正（不做 cut 重映射；時間軸沿用 words.json 的音檔）
python -m agents.brook.script_video --episode <id> correct-srt

# storyboard 主線（乾淨錄影 + transcript.srt 就緒後）
python -m agents.brook.script_video --episode <id> run
# 預期: out/episode.fcpxml + out/b_roll_<hash>.mp4
```

**自動檢查**：

```bash
xmllint --noout data/script_video/smoke-001/out/cleanup.fcpxml && echo "XML well-formed ✓"
ls -lh data/script_video/smoke-001/out/
# cleanup 產 cleanup.fcpxml；storyboard 主線產 episode.fcpxml（檔名區隔，ADR-050 D4）
# 各 stage 完成時間戳寫入 episode.yaml 的 stages: map
```

---

## 4. DaVinci import

1. 開 **DaVinci Resolve**
2. 新 Project（任何 timeline preset，Slice 1 emit 1080p / 30fps exact）
3. 選單列：**File → Import → Timeline...**
4. 選 `data/script_video/smoke-001/out/episode.fcpxml`
5. 觀察 import dialog：
   - **無 schema error popup** ✓
   - **無 missing media red icon**（若有，是 absolute path issue — 見 §6）
6. timeline 載入後：
   - V1 軌道有 1 條 A-roll clip
   - razor cut 在拍掌位置（fixture 內已知 t≈4.5s, 9.0s — 跟拍掌數對得上）
   - timeline 總長 ≈ source - cut 區段（ripple delete 已生效）
   - V2/V3/V4 軌道空（Slice 1 不渲 B-roll）

---

## 5. 成功條件（全勾才 PASS）

| # | 條件 | 怎麼驗 |
|---|---|---|
| 1 | Import 無 schema error popup | DaVinci 不跳 「Cannot import FCPXML」 紅框 |
| 2 | Timeline 載入完成 | 看到 V1 + audio track，非空 |
| 3 | Source media 連得到 | clip 不顯示紅色 missing icon |
| 4 | Razor cut 出現在拍掌位置 | 用 timeline cursor 走 cut point，跟 fixture 對 ±50ms |
| 5 | Ripple delete 生效（timeline 比 source 短） | timeline 總長 < raw_recording.mp4 length |
| 6 | 30fps exact（非 29.97） | Project Settings → Frame Rate 顯示 30 |

**任一條失敗 → FAIL**，回報格式見 §7。

---

## 6. 常見失敗模式 + 診斷

| 症狀 | 根因 | 處置 |
|---|---|---|
| Import dialog 跳 schema error | FCPXML 1.10 element/attribute 不對 | 截 error 字串 → grep `fcpxml_emitter.py` 對應 element → fix commit |
| Clip 紅色 missing media | `<asset>` 用 relative path 或 path 含中文/空格 | 確認 `asset-clip` `src=` 是 absolute path + URL-encoded |
| Timeline 空白 | razor cut 把全部 ripple delete | check `mistake_removal.detect_clap_markers` 回傳的 cuts 數量是否 > 預期 |
| Cut 偏移 > 50ms | Marker detection threshold 不對 | check `mistake_removal.py` peak detection params |
| DaVinci 顯示 29.97 fps | FCPXML format element fps 寫成 30000/1001 | check `fcpxml_emitter.py` `_FPS = 30` + format `frameDuration="1/30s"` |

---

## 7. 回報格式

**PASS**：

```
DaVinci import smoke ✓ — Slice <N> / PR #<NNN>
- timeline length: <X.X>s
- cuts applied: <K> at [t1, t2, ...]
- date: 2026-MM-DD
```

**FAIL**：

```
DaVinci import smoke ✗ — Slice <N> / PR #<NNN>
- failure mode: <#1-6 from §5>
- error string: <paste import dialog text>
- attached: data/script_video/smoke-001/out/episode.fcpxml (gist 或 PR comment)
- date: 2026-MM-DD
```

---

## 8. Cleanup（可選）

dummy episode 不需 commit；`data/script_video/` 已在 `.gitignore`（PR #320 增）。要清就 `rm -rf data/script_video/smoke-001/`（手動執行，不在 deny list 但 repo CLAUDE.md 規定 agent 用回收桶）。

---

## 變更歷史

| 日期 | Slice / PR | 變更 |
|---|---|---|
| 2026-05-02 | Slice 1 / PR #320 | 初版（V1 軌道 + razor cut + ripple delete，無 B-roll） |
| 2026-07-04 | ADR-050 全案 | 指令改 `python -m agents.brook.script_video`；FCPXML 走 `shared/fcpxml/` builder |
| 2026-07-04 | script-anchored cleanup | 單擊掌 + WhisperX 字級對稿回溯（ADR-015 §Q3 β 實現）；`correct-srt` 子命令；§3 指令更新 |
