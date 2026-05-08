"""Gemini v2 sign-off — multi-agent-panel skill step 5b.

Reads the v2 draft + integration matrix; asks Gemini for a SHORT sign-off
(under 800 words) on the panel-adjudicated v2 plan.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
V2_PATH = REPO_ROOT / "docs/research/2026-05-08-textbook-ingest-v2-draft.md"
MATRIX_PATH = REPO_ROOT / "docs/research/2026-05-08-integration-matrix.md"
GEMINI_R1_PATH = REPO_ROOT / "docs/research/2026-05-08-gemini-textbook-ingest-audit.md"
TOPIC = "ADR-020 textbook ingest v3 — v2 sign-off"

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
        thinking_budget=1024,
        temperature=0.3,
    )


SYSTEM = (
    "You are doing a short v2 SIGN-OFF on a panel review you participated in earlier "
    "(your v1 audit is included for self-reference). The v2 draft was written by Claude "
    "after integrating your audit + Codex's two-round audit + Claude's own findings. "
    "Sign off / sign off with mods / reject. Push back on places where v2 mis-adjudicated "
    "your earlier pushback. Do NOT redo the full audit. Keep this under 800 words."
)


def build_prompt(v2: str, matrix: str, gemini_r1: str) -> str:
    return f"""# {TOPIC}

You audited v1 earlier with a REJECT verdict + Architectural Pause recommendation + Path C proposal. Claude has now written a v2 incorporating panel adjudication. Several of your pushbacks were ADOPTED, several were REJECTED in favor of Codex's framing, several were MODIFIED.

**User clarification post-v1 (2026-05-08)**: All future textbook ingest sources are English-language only. Chinese sources will enter via separate pipelines (general ebook ingest + web article ingest), NOT via this textbook path. This permanently defers B5 (CJK classifier failure) for THIS pipeline. v2 reflects this in §3.

# What you should produce

A SHORT sign-off (under 800 words). Three sections:

## §1 — Sign off / sign off with mods / reject v2

Single explicit verdict. If sign-off: state any micro-corrections. If sign-off-with-mods: list which v2 §1/§2/§3 items to add/remove/reorder + concrete reason. If reject: state the structural disagreement and what v3 should do differently.

## §2 — Where v2 rejected your earlier pushback — push back if you still disagree

The integration matrix (in your reading list) shows where your r1 audit was overridden. Specifically:

- **Row 9**: v2 keeps Path B; rejects your Path C re-architecture. Codex r1+r2 said the failures are in unverified contracts BETWEEN components, not in body-vs-metadata split. v2 adopts your Path C SUBSTANCE (canonicalize, graph-aware dispatch, appendix from dispatch result) but as targeted patches in Path B framing, not as architectural rewrite. Do you still demand the rewrite? Or does substance-adopted-without-relabel work?

- **Row 4**: v2 demotes L1 wikilinks to plain text in body; rejects your "always-create-stub". Codex r2 warned 50 books × 20 L1/chapter = thousands of empty stubs poison search. Do you still demand stub creation? Or accept plain-text demotion?

- **Row 8 (CJK BLOCKER)**: now MOOT given user clarification (textbook path is English-only forever). Your expanded zh patterns (`是指`, `即`, `也就是說`, `又稱`, `的定義是`) recorded for separate ebook/web pipelines — confirm this is acceptable for THIS pipeline.

- **Row 26 (decision frame)**: v2 adopts Codex r2's "patch P0 → ship 1 book → UAT → patch P1 → ship SN" instead of your "architectural pause + 1-week refactor + ingest 1 book". Do you still demand pause? Or accept phased ship after P0 patches?

## §3 — Final ship recommendation

One paragraph. State your ship recommendation + cite which v2 sections it traces to.

# Constraints

- Hard cap 800 words.
- Push back specifically on rejected items if you still disagree. Don't relitigate adopted items.
- This is the final round before user makes ship decision — be decisive.

## v2 draft (the artifact)

{v2}

## Integration matrix (panel adjudication)

{matrix}

## Your own r1 audit (for self-reference; do NOT repeat it)

{gemini_r1}
"""


def main() -> int:
    _load_env()

    v2 = V2_PATH.read_text(encoding="utf-8")
    matrix = MATRIX_PATH.read_text(encoding="utf-8")
    gemini_r1 = GEMINI_R1_PATH.read_text(encoding="utf-8")

    prompt = build_prompt(v2, matrix, gemini_r1)

    print("=== Gemini v2 sign-off dispatch ===", file=sys.stderr)
    print(f"Topic: {TOPIC}", file=sys.stderr)
    print(f"Prompt size: {len(prompt)} chars (~{len(prompt)//4} tokens)", file=sys.stderr)
    print("Model: gemini-2.5-pro", file=sys.stderr)
    print("---", file=sys.stderr)

    response = ask_gemini(prompt, system=SYSTEM)
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
