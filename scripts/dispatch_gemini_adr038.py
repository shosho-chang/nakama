# ruff: noqa: E501
"""Gemini panel audit dispatch — multi-agent-panel skill step 3 for ADR-038.

Reads:
  docs/decisions/ADR-038-foundry-phase2-resolve-driver-and-borrowings.md
  docs/research/2026-05-28-codex-adr038-audit.md (Codex step 2)

Writes Gemini audit (verbatim) to:
  docs/research/2026-05-28-gemini-adr038-audit.md
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = REPO_ROOT / "docs/decisions/ADR-038-foundry-phase2-resolve-driver-and-borrowings.md"
CODEX_AUDIT_PATH = REPO_ROOT / "docs/research/2026-05-28-codex-adr038-audit.md"
OUTPUT_PATH = REPO_ROOT / "docs/research/2026-05-28-gemini-adr038-audit.md"
TOPIC = "ADR-038 Foundry Phase 2 — Resolve Lua driver + course-video-manager borrowings"

sys.path.insert(0, str(REPO_ROOT))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path("E:/nakama/.env"))
    except ImportError:
        pass


def ask_gemini(prompt: str, system: str) -> str:
    from shared.gemini_client import ask_gemini as _ask

    return _ask(
        prompt,
        system=system,
        model="gemini-2.5-pro",
        max_tokens=8192,
        thinking_budget=4096,
        temperature=0.3,
    )


SYSTEM = (
    f"You are an independent third-party auditor providing a second opinion on "
    f"{TOPIC}. The owner (修修) has explicitly asked for push-back from your "
    "unique perspective as a Gemini model — do NOT rubber-stamp Claude's or "
    "Codex's analysis. Your value is your different reasoning chain, broader "
    "fact-recall across the DaVinci/video-editing ecosystem, and willingness "
    "to push back on framing. Be concrete, cite specifics, disagree where "
    "appropriate. Refuse 'looks good overall' as audit output — list 5+ "
    "things you would change if asked."
)


def build_prompt(artifact: str, codex_audit: str) -> str:
    return f"""# {TOPIC} Audit — Multi-Agent Panel Step 3 (Gemini)

You are the THIRD reviewer in a multi-agent panel.

- **Step 1**: Claude (Opus 4.7) drafted ADR-038 v1 (the artifact under review)
- **Step 2**: Codex (GPT-5) audited Claude's draft. Verbatim Codex audit included below.
- **Step 3 (YOU)**: Gemini lens. Different priors than Claude/Codex.

## Background — what is Foundry / ADR-038

Foundry is a Python agent at `agents/foundry/` that turns clean SRT + talking-head mp4 into a DaVinci-importable FCPXML + individual B-roll mp4 clips. Phase 1 shipped 2026-05-26 (6 PRs); SSIM determinism 0.99977 verified; LINE Seed TW @font-face shipped; Bridge UI Tier 2 lives at `/foundry/<ep-id>`.

ADR-038 proposes Phase 2 = adopt 7 patterns from `mattpocock/course-video-manager` (TS/React app driving DaVinci Resolve via Lua):
- **D1 Resolve Lua driver** via `fuscript.exe`-subprocess (6 Lua scripts ported verbatim from Matt's repo)
- **D2 content-addressed export hash** + `EXPORT_VERSION` constant kill-switch
- **D3 LLM tool-call edit pattern** for single-beat re-plan (6 tools)
- D4 Resolve ASR for English captions, D5 silencedetect beat hints, D6 [N] clip-index anchors, D7 LCS storyboard diff

PR slicing: 7 PRs (A-G), ~12.5d total. 5/7 sandcastle-eligible.

The user (修修) is a Traditional Chinese (Taiwan) Health & Wellness content creator. Episodes are Mandarin SRT (FunASR Paraformer-zh upstream); YouTube + podcast bilingual content occasionally. Owns DaVinci Resolve Studio.

## What you should produce

A 1500-2500 word audit in 6 sections. Where you AGREE with Claude or Codex, acknowledge briefly. Where you DISAGREE or have additional Gemini-specific insight, dig deep.

### Section 1 — DAVINCI RESOLVE ECOSYSTEM LENS (your strongest distinct lens)

This is where your training prior likely sees most. ADR-038's load-bearing assumption is that `fuscript.exe -q script.lua` is the right driver protocol. Stress-test this:

- DaVinciResolveScript Python module (bundled with Resolve install). Is this strictly better than `fuscript.exe` for a Python-side caller? Latency, reliability, error handling, scriptability, multi-call session reuse?
- Resolve's Lua scripting environment vs Python scripting environment — which has better API coverage? Which is better documented? Which breaks less often across Resolve major versions?
- Resolve 19 vs 20 API stability — concrete known breakages? Did Matt's Lua scripts target a specific version (any version markers in Matt's `package.json` / docs)?
- Resolve Studio license: does the API surface differ between Resolve Free and Resolve Studio? Does headless/render-queue API require Studio?
- Resolve's project / session model: ADR-038 says "drop into the user's open Resolve session" — what happens if Resolve isn't open? Auto-launch? Error? Background headless?
- Real-world experience: how often do people actually use `fuscript` vs `DaVinciResolveScript` Python module for production tooling?

### Section 2 — MULTILINGUAL / MANDARIN LENS

Foundry's pipeline is Mandarin-first. ADR-038 D4 says "Resolve ASR is English-only; Mandarin sticks with FunASR". But:

- Resolve's `CreateSubtitlesFromAudio` actually supports multiple languages — is it really useless for Mandarin? Has it improved in recent Resolve versions?
- Frontmatter `lang` field becomes load-bearing in D4. Is `zh-TW / en / bilingual` the right enum, or should it be richer (zh-CN, ja, etc.)?
- D3 tool-call edit pattern: the tool surface (replace_anchor, set_broll, shift_anchor) takes character-offset arguments. Mandarin character offsets are well-defined but the LLM may emit them inconsistently (full-width vs half-width, normalized vs raw). Is the protocol robust to this?
- D6 `[N]` clip-index anchors: any subtleties for Mandarin storyboard rendering when N gets large (>99) or when LLM might confuse `[12]` with `[1, 2]` in CJK punctuation context?
- The `course-video-manager` source is English / single-creator — what borrowings might NOT generalize to the Mandarin/Nakama context that Claude+Codex would not flag?

### Section 3 — CLAUDE/CODEX BLIND SPOTS

Where do Claude and Codex likely SHARE the same bias?

- Both Claude and Codex are heavy on code-locality reasoning. What ABSTRACT failure modes might both miss?
  - **Workflow-theoretic**: ADR-038 assumes "DaVinci is open during edit" workflow. What if 修修's actual workflow involves switching projects, closing Resolve, etc.? Does the driver model handle this?
  - **Information-flow-theoretic**: D3 tool-call agent fragments the planner's output into 6 atomic ops. Does this fragmentation actually preserve the LLM's creative intent, or does it introduce a new translation loss?
  - **Persistence-theoretic**: This is a pipeline 修修 will use for years. What does Phase 2 look like in 18 months when Resolve has shipped v21/v22? When Hyperframes has churned 3 major versions?
  - **Cost dynamics**: ADR-038 cites "$0.02 per re-plan" for D3 vs "$0.20" for full re-plan. Did anyone check the math? Sonnet 4.6 input is ~$3/MTok output ~$15/MTok; with thinking budget the actual cost might be 3-5× higher.
- Both probably failed to notice that ADR-038 §Acceptance §Functional reuses Phase 1 acceptance gate ("修修真實一集") that ADR-032 already owned. Is this hidden scope creep or honest gate-reuse?

### Section 4 — ARCHITECTURAL / DESIGN CONCERNS

- Is the Foundry → Resolve dependency direction healthy? ADR-038 adds runtime dependency on a proprietary Mac/Windows-only video editor. Lock-in cost?
- D1 alternatives mention "headless Resolve Engine via DaVinciResolveScript Python API (vs `fuscript.exe`)" as Phase 2.5 deferral. This feels backwards — the Python-native path should be EVALUATED FIRST, then chosen Lua only if Python path is worse. Audit this ordering.
- D2 hash design: minimal beat fields chosen, but what about layout YAML content? If 修修 edits `layouts/full_broll.yaml`, hashes don't change → stale renders silently served. Catastrophic silent failure.
- D7 LCS storyboard diff: does this primitive really enable "multi-episode history view" or is it just a nice-to-have that doesn't move the needle?
- Test pyramid: ADR-038 mentions unit tests per module. Is there end-to-end Resolve-driven integration test? PR-D / PR-E unit tests run "against headless Resolve fixture" — is that fixture actually feasible to build (Resolve is GUI-heavy)?
- Phase 2.5 vs Phase 3 split: are the items correctly bucketed, or is some Phase 3 item actually Phase 2-blocking?

### Section 5 — LICENSE / ATTRIBUTION DEEP-DIVE (§OQ1)

ADR-038 §OQ1: Matt's repo has no LICENSE file. ADR proposes "file GitHub issue, fallback rewrite in 1-2d if no response". Push back:

- No-LICENSE = "all rights reserved" by default under US copyright. Adopting Lua scripts "verbatim" is a copyright violation absent explicit permission. Is the "fair-use reference" framing in ADR-038 §D1 implication legally sound?
- The GitHub-issue-then-fallback path: 1 week is too short for a busy maintainer to respond. What's the realistic timeline?
- Alternative: contact Matt directly (Twitter / email) for permission BEFORE writing any code that ports his scripts. Or rewrite from scratch from day 1 using only Resolve's public API docs (which are themselves Blackmagic Design's IP but published API surface is fair to use). Which is cleaner?
- The 1-2d "fallback rewrite" cost: realistic for 6 Lua scripts (12-169 LOC each) by a Python developer who hasn't written Lua before? Smell test.
- What does adoption look like in the contrapositive: if Claude rewrites the Lua from scratch using only Resolve API docs, is the result actually similar to Matt's, or quite different? The similarity tells you something about how much IP is being borrowed.

### Section 6 — FINAL VERDICT

- **Approve / approve with modifications / reject**.
- **Top 5 specific changes** ranked by P(future-burn) × cost-to-fix.
- **Address the 5 numbered owner questions** explicitly (D1 fuscript vs Python module; D2 hash inputs; D3 tool surface; PR-D 3d realism; §OQ1 license).
- Cite ADR-038 sections (§D1, §D2, §OQ1, etc.) and Codex audit sections (§1, §2, etc.) when responding.

## Required style

- English (matches Codex audit, helps panel integration)
- Concrete, specific. Push back. Refuse "looks good overall".
- The owner will read this DIRECTLY to make the final call.

## Codex audit (verbatim, step 2)

{codex_audit}

## ADR-038 v1 draft (the artifact under review)

{artifact}

---

Begin your 6-section Gemini audit now."""


def main() -> int:
    _load_env()

    artifact = ARTIFACT_PATH.read_text(encoding="utf-8")
    codex_audit = CODEX_AUDIT_PATH.read_text(encoding="utf-8") if CODEX_AUDIT_PATH.exists() else "[Codex audit not yet available — running parallel]"

    prompt = build_prompt(artifact, codex_audit)

    print("=== Gemini panel audit dispatch ===", file=sys.stderr)
    print(f"Topic: {TOPIC}", file=sys.stderr)
    print(f"Prompt size: {len(prompt)} chars (~{len(prompt) // 4} tokens)", file=sys.stderr)
    print(f"Codex audit present: {CODEX_AUDIT_PATH.exists()}", file=sys.stderr)
    print(f"Output: {OUTPUT_PATH}", file=sys.stderr)
    print("---", file=sys.stderr)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    response = ask_gemini(prompt, system=SYSTEM)
    OUTPUT_PATH.write_text(response, encoding="utf-8")
    print(f"Wrote {len(response)} chars to {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
