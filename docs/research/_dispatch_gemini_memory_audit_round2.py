"""Gemini panel audit dispatch for Memory System Redesign v2 (Round 2).

Multi-agent panel step 4-prime (second round):
  v1 → Codex audit + Gemini audit (Round 1 — done)
  v2 → Codex audit Round 2 + Gemini audit Round 2 ← THIS SCRIPT
  → Convergence check → user sign-off → implementation

Run from any cwd:
  E:/nakama/.venv/Scripts/python.exe E:/nakama-design/docs/research/_dispatch_gemini_memory_audit_round2.py > E:/nakama-design/docs/research/2026-05-08-gemini-memory-redesign-audit-round2.md
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path("E:/nakama-design")
ARTIFACT_PATH = REPO_ROOT / "docs/research/2026-05-08-memory-system-redesign-v2.md"
INTEGRATION_MATRIX_PATH = REPO_ROOT / "docs/research/2026-05-08-panel-integration-matrix.md"
PRIOR_GEMINI_AUDIT_PATH = REPO_ROOT / "docs/research/2026-05-08-gemini-memory-redesign-audit.md"
CODEX_ROUND2_PATH = REPO_ROOT / "docs/research/2026-05-08-codex-memory-redesign-audit-round2.md"
TOPIC = "Memory System Redesign v2 (Round 2)"

sys.path.insert(0, str(Path("E:/nakama")))


def _load_env():
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(Path("E:/nakama/.env"))
    except ImportError:
        pass


def _ask_via_project_client(prompt: str, system: str) -> str:
    from shared.gemini_client import ask_gemini  # type: ignore
    return ask_gemini(
        prompt,
        system=system,
        model="gemini-2.5-pro",
        max_tokens=8192,
        thinking_budget=2048,
        temperature=0.3,
    )


def _ask_via_genai(prompt: str, system: str) -> str:
    import google.generativeai as genai  # type: ignore
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not in env. Source E:/nakama/.env first.")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        "gemini-2.5-pro",
        system_instruction=system,
        generation_config={"max_output_tokens": 8192, "temperature": 0.3},
    )
    response = model.generate_content(prompt)
    return response.text


def ask_gemini(prompt: str, system: str) -> str:
    try:
        return _ask_via_project_client(prompt, system)
    except (ImportError, ModuleNotFoundError) as e:
        print(f"# project gemini_client unavailable ({e}), falling back to genai SDK", file=sys.stderr)
        return _ask_via_genai(prompt, system)


SYSTEM = (
    "You are the Gemini auditor in Round 2 of a multi-agent panel review. "
    "You previously audited v1 of this memory-system redesign. Claude has now produced v2 "
    "incorporating your Round 1 push-back (and Codex's). Your task is a focused Round 2 review "
    "checking whether v2 actually addressed your concerns and whether new issues appeared. "
    "Be brief, focused, and specific. Do not rehash Round 1 unless v2 ignored a major point."
)


def build_prompt(artifact: str, matrix: str, prior_gemini: str, codex_round2: str) -> str:
    return f"""# Memory System Redesign v2 Audit — Multi-Agent Panel Round 2 (Gemini)

You previously audited v1. Claude wrote v2 incorporating panel feedback. Codex has just completed Round 2 audit. Now you audit v2 from Gemini perspective Round 2.

## Your task — be brief (~800-1200 words)

### Section 1 — DID v2 ADDRESS YOUR ROUND 1 CONCERNS?

For each of your Round 1 push-backs, mark:
- **Adopted faithfully** — v2 incorporated as you intended
- **Adopted with modification** — v2 took your point differently (better or worse?)
- **Rejected with reasoning** — assess whether reasoning holds
- **Ignored** — flag as still-open

Specifically check:
1. **Multilingual & i18n concerns** (your Round 1 §1) — did v2 address with bilingual frontmatter? Is it sufficient or papering-over?
2. **Tool-driven vs doc-driven** (your Round 1 §2) — v2 deferred CLI to Phase 2+. Acceptable trade or capitulation?
3. **Markdown-as-database anti-pattern** (your Round 1 §5) — v2 deferred SQLite hybrid to Phase 4+. Right call given current scale?
4. **Append-only doesn't solve update conflicts** (your Round 1 §5) — v2 added "update precedence: last-write-wins" + "shared is rare-write curated". Is this sufficient or just delays the problem?
5. **`memory-trunk` is git-flow re-spelled** (your Round 1 §5) — v2 dropped it. Adopted faithfully.
6. **Anchoring to existing memory/ directory** (your Round 1 §3 — "should it be a separate repo?") — v2 stayed in main repo, citing C3 Sandcastle. Reasoning hold?
7. **Long-tail memos are low-TTL signals** (your Round 1 §2) — v2 split Tier 1 / Tier 2 (`.nakama/session_handoff.md`). Faithful adoption.
8. **L3 medicalization concern** (your Round 1 §3) — v2 replaced with ephemeral handoff + agent judgment. Adopted with modification.

### Section 2 — NEW v2 ISSUES FROM GEMINI LENS

What did Round 1 not foresee but v2's specific design decisions now raise?
- Tier 1 / Tier 2 split — does this cleanly solve continuity, or create new failure modes?
- Bilingual frontmatter for `shared/` — implementation feasibility (who writes the second language)?
- Update precedence "last-write-wins" — multilingual implications when zh and en versions diverge?
- Transition-aware reindex producing single INDEX.md — agent confusion during transition?
- 30-day archive cutoff for `project_session_*` — appropriate, or should be type-aware?

### Section 3 — DOES v2 vs CODEX ROUND 2 AGREE?

Compare your Round 2 verdict with Codex Round 2 verdict (provided below).
- Where do you agree?
- Where do you disagree?
- Where Codex sees something you don't (or vice versa)?

### Section 4 — ROUND 2 VERDICT

Produce one of:
- **Approve v2 for Phase 0 implementation**
- **Approve v2 with X minor changes** (list specific)
- **Reject v2, do another round** (list blocking issues)

State whether PR A is shippable today as scoped.

## Style

- English
- Concrete, cite section names
- ~800-1200 words (briefer than Round 1)
- No verbatim copy of v1 audit — assume reader has seen it

## Codex Round 2 audit verbatim

{codex_round2}

## Integration matrix (Round 1 outcomes)

{matrix}

## Your prior Round 1 audit (for self-reference)

{prior_gemini}

## v2 artifact under review

{artifact}

---

Begin Round 2 audit now."""


def main() -> int:
    _load_env()

    if not CODEX_ROUND2_PATH.exists():
        print(f"# ERROR: Codex Round 2 audit not yet at {CODEX_ROUND2_PATH}", file=sys.stderr)
        print(f"# Wait for Codex Round 2 agent to complete, then re-run.", file=sys.stderr)
        return 1

    artifact = ARTIFACT_PATH.read_text(encoding="utf-8")
    matrix = INTEGRATION_MATRIX_PATH.read_text(encoding="utf-8")
    prior_gemini = PRIOR_GEMINI_AUDIT_PATH.read_text(encoding="utf-8")
    codex_round2 = CODEX_ROUND2_PATH.read_text(encoding="utf-8")

    prompt = build_prompt(artifact, matrix, prior_gemini, codex_round2)

    print("=== Gemini Round 2 audit dispatch ===", file=sys.stderr)
    print(f"v2 artifact: {len(artifact)} chars", file=sys.stderr)
    print(f"Matrix: {len(matrix)} chars", file=sys.stderr)
    print(f"Prior Gemini: {len(prior_gemini)} chars", file=sys.stderr)
    print(f"Codex Round 2: {len(codex_round2)} chars", file=sys.stderr)
    print(f"Total prompt: {len(prompt)} chars (~{len(prompt)//4} tokens)", file=sys.stderr)
    print(f"---", file=sys.stderr)

    response = ask_gemini(prompt, system=SYSTEM)
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
