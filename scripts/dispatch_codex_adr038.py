"""Codex panel audit dispatch — multi-agent-panel skill step 2 for ADR-038.

Reads:
  docs/decisions/ADR-038-foundry-phase2-resolve-driver-and-borrowings.md
  docs/decisions/ADR-032-hyperframes-broll-pipeline.md (sibling context)

Writes Codex audit (verbatim) to:
  docs/research/2026-05-28-codex-adr038-audit.md

Uses `codex exec` CLI for non-interactive execution.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = REPO_ROOT / "docs/decisions/ADR-038-foundry-phase2-resolve-driver-and-borrowings.md"
ADR032_PATH = REPO_ROOT / "docs/decisions/ADR-032-hyperframes-broll-pipeline.md"
OUTPUT_PATH = REPO_ROOT / "docs/research/2026-05-28-codex-adr038-audit.md"

TOPIC = "ADR-038 Foundry Phase 2 — Resolve Lua driver + course-video-manager borrowings"


def build_prompt(artifact: str, adr032: str) -> str:
    return f"""# {TOPIC} Audit — Multi-Agent Panel Step 2 (Codex)

You are an independent third-party auditor providing a second opinion on ADR-038. The owner (修修) has explicitly asked for push-back from your perspective as Codex/GPT-5 — do **NOT** rubber-stamp Claude's analysis.

Your value-add as Codex:
- Code grounding: cross-check file paths, line numbers, code symbols (agents/foundry/*, shared/*)
- Fact verification: verify numerical claims (LOC estimates, day estimates, hash widths)
- Push-back posture: willing to disagree where Claude is hand-waving
- Drift detection: ADR-038 vs ADR-032 sibling-ADR contract; vs actual code already shipped

## Background — what is ADR-038

Foundry is a Python agent at `agents/foundry/` that turns a clean SRT + talking-head mp4 into a DaVinci-importable FCPXML + individual B-roll mp4 clips. Phase 1 shipped 2026-05-26 across PRs #717/720/723/719/724/726. Pipeline modules: pipeline.py 206 LOC, srt_flattener.py 106, chinese_normalizer.py 135, beat_aligner.py 131, planner.py 114, render_dispatcher.py 60, fcpxml_emitter.py 230, edit_log.py 66. Hyperframes is the HTML→mp4 renderer (npx CLI, visual SSIM determinism 0.99977). Bridge UI Tier 2 lives at `/foundry/<ep-id>` (`thousand_sunny/routers/foundry.py`).

ADR-038 proposes Phase 2 = adopt 7 patterns from `mattpocock/course-video-manager` (a 25KB TS/React app that drives DaVinci Resolve via Lua). The 3 headline borrowings:
- **D1**: Lua scripts (6 files, 12-169 LOC each) invoked via `fuscript.exe -q script.lua` from Python wrapper. Closes "manual FCPXML import" UX gap.
- **D2**: SHA256 hash-named b-roll renders (`b_roll_<hash[:16]>.mp4`) + `EXPORT_VERSION` constant kill-switch. Re-plan becomes O(changed beats).
- **D3**: LLM tool-call edit agent (6 tools: replace_anchor / set_broll / shift_anchor / mark_aroll / split_beat / merge_beats). Replaces full planner re-run.

Plus D4 Resolve ASR for English captions, D5 silencedetect parser for beat hints, D6 [N] clip-index anchors, D7 LCS storyboard diff.

PR slicing: 7 PRs (A-G), ~12.5d total. 5/7 sandcastle-eligible.

## Stakes for 修修

Foundry is the single most expensive Nakama agent to develop (Phase 1 was ~13.5d of estimated work). Phase 2 misadoption = weeks of churn. Real podcast cadence is ~1/week so dogfood feedback loop is slow.

## What you should produce

A 1500-2500 word audit in 6 sections. Where you agree with Claude, acknowledge briefly. Where you disagree or have unique insight, dig in. The owner specifically wants you to stress-test these 5 questions (raised in ADR §Panel Integration):

1. **D1** — Is `fuscript.exe`-subprocess pattern actually robust? Or is there a cleaner Python-native Resolve API path (DaVinciResolveScript Python module) that ADR-038 dismissed too quickly?
2. **D2** — Is 16-char SHA prefix enough? Are the chosen minimal hash inputs (`broll_decision, layout, broll.component, broll.params, broll.render_target`) actually complete? Does layout YAML *content* need to be in the hash so layout changes invalidate cache?
3. **D3** — Is the 6-op tool surface complete? What edit ops are likely missing that 修修 will actually want once he uses this for a real episode?
4. **PR slicing** — Is PR-D (3d for 6 Lua scripts + driver + tests) realistic, or optimistic? Are dependencies between PRs correctly identified?
5. **§OQ1 license question** — Matt's repo has no LICENSE file. ADR-038 proposes "file GitHub issue, fallback rewrite in 1-2d if no response". Is this sound, or should we rewrite from scratch upfront?

### Section 1 — CODE GROUNDING

Verify file paths, line numbers, LOC estimates cited in ADR-038. Specifically:
- Does `agents/foundry/render_workers/` already exist (Phase 1 has `hyperframes_worker.py`)? Will adding `resolve_driver.py` + `resolve/*.lua` subdir collide with anything?
- The "Phase 1 caveat" claim that "Bridge UI Re-plan action currently only clears `render_status`, doesn't actually re-run LLM" — verify by checking `thousand_sunny/routers/foundry.py` or similar.
- D2 says FCPXML emitter "reads from `out/b_roll_<hash>.mp4` via storyboard lookup" — is current `fcpxml_emitter.py` ready for this rename, or does it glob `b_roll_*.mp4`?
- ADR-032 §1 specified 3-path dispatcher (hyperframes / reader_playwright / web_playwright) with the latter two as stubs. ADR-038 doesn't mention finishing those. Is that a drift / silent regression?

### Section 2 — DRIFT DETECTION (ADR-038 vs ADR-032 vs CLAUDE.md vs prior memory)

- ADR-032 §Phase 1 acceptance §Functional item 5 = "修修真實一集 end-to-end" — NOT satisfied. ADR-038 §Acceptance §Functional repeats this. Is ADR-038 sneaking acceptance closure that ADR-032 already owed? Is it ok to ship Phase 2 PRs before Phase 1 acceptance closes?
- CLAUDE.md says "新 agent 寫入 vault 必須 vault rules" — Foundry doesn't write vault now. D1 adds Resolve render output writing somewhere — does it touch vault? (Spoiler: ADR says no; verify.)
- Sandcastle defaults in nakama (per memory_sandcastle_default) — ADR-038 says PR-D is sandcastle-tagged "via Lua-mock". Can sandcastle actually run Lua mock tests, or does sandcastle have no Lua runtime?

### Section 3 — NUMERICAL / FACTUAL CLAIMS

- LOC estimates: 250 for resolve_driver.py, 200 for beat_editor.py, 150 for replan_agent.py, 100 for storyboard_diff.py, 80 for export_hash.py, 50+30 for silence_detection. Spot-check 2-3 against Matt's source equivalents (e.g. compare to `app/services/video-processing-service.ts:408-501` line range).
- Day estimates per PR-A through PR-G (1.5/1/1/3/1.5/1/3.5 = 12.5d). Spot-check 2-3 for plausibility. ADR-032 Phase 1 estimate was 13.5d for 5 PRs and shipped close to that — what's the prior on this team's estimation accuracy?
- "16-char SHA prefix is 2^64 space" — verify. Is collision probability actually material given expected ~5K rendered b-rolls/year per 修修's cadence?
- "Re-plan cost ~$0.20 per re-plan in tokens (Sonnet 4.6, ~12k input × 25 beats episode)" — verify the math. Sonnet 4.6 input pricing ~$3/M; 12k × 25 = 300k tokens ≠ $0.20 alone. Numbers seem off.

### Section 4 — ASSUMPTION PUSH-BACK

What is Claude assuming without evidence? Specific assumptions to challenge:
- "修修 already uses DaVinci for grade + final encode; the friction is the manual import step" — is the manual import step really the dominant friction? Or is the bigger friction something else (e.g. Hyperframes not having the layout 修修 wants)?
- "D1 ROI" framing — ADR-038 §Risks admits "修修 doesn't actually dogfood D1 → ROI zero" is a Medium-probability risk. Should this be a HARD GATE not just a risk-table entry?
- D3 framing: "tool-call pattern is dramatically more reliable than diff parsing for sub-document edits" cites Matt as evidence. Matt's domain is courseware (English, short clips); Foundry's domain is Mandarin SRT (longer, character-offset-anchored). Does the analogy actually hold?
- "Phase 2 work and dogfood can proceed in parallel" — is this dual-track plan realistic? Will 修修 actually dogfood Phase 1 while Claude builds Phase 2?

### Section 5 — ALTERNATIVES NOT CONSIDERED

What approaches did Claude not evaluate?
- DaVinciResolveScript Python API (mentioned briefly in D1 alternatives as "deferred Phase 2.5") — should this be the PRIMARY path instead of Lua-via-subprocess? Lua scripts can call the same Resolve API; the choice is purely about subprocess overhead vs in-process call.
- Skip D1 entirely, ship D2 + D3 only (lowest risk, highest immediate value). What does that smaller Phase 2 look like?
- Don't borrow from `course-video-manager` at all; design from scratch. What's the actual cost difference?
- Postpone Phase 2 until Phase 1 acceptance closes (修修 real episode shipped). What's the cost of that delay?

### Section 6 — FINAL VERDICT

- Approve as-is / approve with modifications / reject.
- If modifications: top 3-5 specific changes with reasoning, citing ADR-038 sections.
- If reject: alternative architecture you'd propose.
- Address the 5 numbered owner questions explicitly.

## Required style

- English. Push back where you disagree. Avoid hedging ("consider", "perhaps") — say "do X, here's why" or "don't do X".
- Cite ADR-038 sections (§0, §D1, §D2, §OQ1, §Risks, etc.). Cite file paths verbatim (no translation).
- This will be read directly by the project owner to make the final call.

## Sibling ADR for context (ADR-032 Phase 1 base, 508 lines — read for drift detection)

{adr032}

## ADR-038 v1 draft (the artifact under review)

{artifact}

---

Begin your 6-section audit now."""


def main() -> int:
    artifact = ARTIFACT_PATH.read_text(encoding="utf-8")
    adr032 = ADR032_PATH.read_text(encoding="utf-8")
    prompt = build_prompt(artifact, adr032)

    print("=== Codex panel audit dispatch ===", file=sys.stderr)
    print(f"Topic: {TOPIC}", file=sys.stderr)
    print(f"Prompt size: {len(prompt)} chars (~{len(prompt) // 4} tokens)", file=sys.stderr)
    print(f"Output: {OUTPUT_PATH}", file=sys.stderr)
    print("---", file=sys.stderr)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    import shutil
    codex_bin = shutil.which("codex") or shutil.which("codex.cmd") or r"C:\Users\Shosho\AppData\Roaming\npm\codex.cmd"
    result = subprocess.run(
        [codex_bin, "exec", "--skip-git-repo-check", "-"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
        shell=False,
    )

    if result.returncode != 0:
        print(f"codex exec failed (rc={result.returncode})", file=sys.stderr)
        print(f"stderr:\n{result.stderr}", file=sys.stderr)
        return result.returncode

    OUTPUT_PATH.write_text(result.stdout, encoding="utf-8")
    print(f"Wrote {len(result.stdout)} chars to {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
