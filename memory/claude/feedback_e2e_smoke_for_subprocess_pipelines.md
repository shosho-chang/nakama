---
name: Subprocess-stitched pipeline 必加 e2e fixture test 進 CI
description: Python orchestrator ↔ Node/外部 binary 縫合的 pipeline，per-stage unit test 全綠不代表 e2e 通；CI 必加端到端 fixture test
type: feedback
originSessionId: 2026-05-02-mac-pr320-e2e
created: 2026-05-02
---

**規則：subprocess-stitched pipeline（Python orchestrator ↔ Node / 外部 binary / ffmpeg）必加 e2e fixture test 進 CI，不能只靠 per-stage unit test。**

**Why:** PR #320 sandcastle agent 寫 35 unit test 全綠 + multi-agent review (3 reviewer) 過 + ultrareview 也不會抓 — 但端到端從沒跑通。修修在 mac 第一次跑 `python -m agents.brook.script_video --episode smoke-001` 連環 hit 5 個 bug：

1. **tsc emit path mismatch** — `tsconfig rootDir="."` → tsc 編到 `dist/src/parser/parse.js`，但 pipeline.py 找 `dist/parser/parse.js`，subprocess silently exit 0 但 manifest.json 不存在
2. **codec ↔ reader 不相容** — Stage 0 ffmpeg 出 mp3 (`libmp3lame`)，Stage 1 用 stdlib `wave` 只認 RIFF/WAV，`wave.Error: file does not start with RIFF id`
3. **subprocess CLI no-op** — TS 只 export pure function `parseScript()` 沒 CLI main，`node parse.js --script x --out y` 進 module top-level 沒處理 argv → silent no-op exit 0
4. **schema field placeholder leak** — parser 用 word-count 估 `total_frames` (~30 frames placeholder)，pipeline 沒 overwrite 用 source media duration → emitter 算 timeline = `total_frames / fps = 1s` 但 cuts 在 source-time seconds (0-8s) → timeline 縮 8s → 0.5s
5. **algorithm 反語意** — cut 範圍跟用戶 workflow 相反（這個是 design 不是 bug，但 unit test 沒 catch 因為 fixture 純合成 clap pulses 沒 voice）

**共同根因**：unit test 各自 mock subprocess / mock filesystem / 用 numpy array 直接 feed function — **subprocess boundary、stdin/stdout/file IO、副檔名 ↔ codec contract、parser CLI argv 都是 unit test 不 cover 的 surface**。

**How to apply:**

每個 subprocess-stitched pipeline 必有一個 e2e fixture test：

1. **真實 subprocess invocation**：不 mock subprocess，真跑 `node parse.js` / `ffmpeg` / `python parse.py`
2. **真實 fixture 檔案**：tmp_path 內合成最小可重現案例（合成 audio numpy → wave 寫 wav → ffmpeg 包 mp4），驗 codec/副檔名 contract
3. **pytest skip guard**：`shutil.which("node")` / `shutil.which("ffmpeg")` / `dist/.../parse.js exists` 任一 missing 就 skip — local 全裝可跑、CI 也可跑
4. **CI workflow 配齊 dependency**：`.github/workflows/*.yml` 加 `actions/setup-node@v4` / `apt-get install ffmpeg` / `npm install --prefix subproject` / `npm run build`，否則 e2e test 永遠 skip 等於沒加
5. **斷言不只「跑得起來」**：assert output 結構（XML well-formed / schema field 對位 source media / 跨 module data flow 對齊）— bug 4 是 unit test 各 stage 對自己負責的 field 都對，但「上游欄位被下游 trust」沒 cross-test

**Anti-pattern**：
- ❌ 只 mock 各 subprocess + assert function call args — 假 confidence，subprocess 真 invoke 路徑沒驗
- ❌ Unit test 用空 / 合成 fixture — `clap_marker_audio.wav` 純 clap pulse 沒 voice，VAD 邏輯 bug 沒 catch
- ❌ Multi-agent review 只看 code diff — 不真 invoke 整管線

**配套**：fixture 自帶生成器（`__init__.py` 或 helper script），CI / local 都能 reproduce，不靠 binary blob check-in。

**相關記憶**：
- [feedback_test_realism.md](feedback_test_realism.md) — mock 形狀對齊真實 contract（同類教訓，不同階）
- [feedback_structural_vs_functional_validation.md](feedback_structural_vs_functional_validation.md) — 結構不變量 ≠ 用戶實際使用
- [feedback_acceptance_target_clarity.md](feedback_acceptance_target_clarity.md) — 驗收 LLM artifact 要分清驗收對象
