# ruff: noqa: E501,F401
"""Gemini panel audit dispatch — multi-agent-panel skill step 3.

Reads:
  docs/research/2026-05-08-claude-textbook-ingest-v1-draft.md (Claude v1)
  docs/research/2026-05-08-codex-textbook-ingest-audit-round1.md (Codex r1)

Writes Gemini r1 audit (verbatim) to stdout — caller redirects to
  docs/research/2026-05-08-gemini-textbook-ingest-audit.md
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]  # E:/nakama-stage4
ARTIFACT_PATH = REPO_ROOT / "docs/research/2026-05-08-claude-textbook-ingest-v1-draft.md"
CODEX_AUDIT_PATH = REPO_ROOT / "docs/research/2026-05-08-codex-textbook-ingest-audit-round1.md"
TOPIC = "ADR-020 textbook ingest v3 — 5/8 3-patch effort + ship decision"

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
        thinking_budget=2048,
        temperature=0.3,
    )


SYSTEM = (
    "You are an independent third-party auditor providing a second opinion on "
    f"a {TOPIC}. The owner has explicitly asked for push-back from your unique "
    "perspective as a Gemini model — do NOT rubber-stamp existing analyses. "
    "Your value is your different reasoning chain, broader fact-recall, and "
    "stronger multilingual lens than Claude or GPT-5. Be concrete, cite "
    "specifics, and disagree where appropriate. Refuse 'looks good overall' "
    "as audit output — list 5+ things you would change if asked."
)


def build_prompt(artifact: str, codex_audit: str) -> str:
    return f"""# {TOPIC} Audit — Multi-Agent Panel Step 3

You are the THIRD reviewer in a multi-agent panel.

- **Step 1**: Claude (Opus 4.7 1M context) drafted the artifact under review (v1) — covers a 5/7 production burn ($45 wasted on 28-chapter textbook ingest batch shipped without Obsidian human-eyeball validation), 5/8 morning's 3 root cause patches (commit feee4d8), BSE ch1/3/6 validation results, plus 14 newly-found issues (B4-B17) that span: a single-chapter mode polluting live KB, ASCII-only classifier rules, hardcoded source_count=1 collapsing the L3 maturity tier, expensive update_merge LLM diff per repeat, dead frontmatter fields, concept page fragmentation (ATP + Adenosine Triphosphate as 2 pages), 96% incomplete reverse backlinks, dual-source-of-truth wikilinks already off-by-one in production, alias_map being downstream-useless, Patch 3 over-correction on inline wikilinks, YAML anchor latent risk.
- **Step 2**: Codex (round 1) audited the 3 patches and found additional issues (regex gaps, NFKC missing, Windows reserved names, empty concept_map_md silent regression, ADR-020 contract drift). Codex round 2 is running in parallel to you, with full v1 access.
- **Step 3 (YOU)**: Gemini's lens. You bring different priors than Claude/GPT-5.

## Background — what is this pipeline

ADR-020 textbook ingest v3 takes EPUB books → KB/Raw markdown → walker (Python H1 splitter) produces ChapterPayload(verbatim_body, section_anchors, figures, tables) → LLM Phase 1 emits ONLY metadata JSON (frontmatter + per-section concept_map_md + wikilinks list); body never passes through LLM → Python _assemble_body concatenates walker verbatim + figure transform + chapter-end appendix → Phase 2 dispatch routes each LLM-emitted wikilink term to L1 (alias_map entry only)/L2 (stub concept page)/L3 (active concept page) via 4 deterministic rules.

The vault target is Obsidian (markdown-based wiki). The user is a Chinese-speaking content creator (Health & Wellness / Longevity content). Result must be agent-readable AND human-readable for study. 28 chapters across 2 textbooks (Biochemistry for Sport and Exercise + Sport Nutrition Jeukendrup 4e) waiting to ingest. Two more textbooks may follow.

## What you should produce

A 1500-2500 word Gemini audit in 6 sections. Where you AGREE with Claude or Codex, acknowledge briefly and move on. Where you DISAGREE or have additional Gemini-specific insight, dig deep.

### Section 1 — MULTILINGUAL & CHINESE-LANGUAGE LENS

This is your strongest distinct lens. The user is Chinese-speaking; the eventual ingest queue includes Chinese-language sources. Specifically address:

- B5 in v1: classifier rules use `\\b<term>\\b` word boundaries which are undefined for CJK. Confirm severity. Also examine `_rule_definition_phrase` which has 2 Chinese patterns (稱為, 定義為) — is this enough? What other Chinese definitional constructs commonly appear in academic Chinese (科學中文 / 學術寫作)?
- Frontmatter convention is "key=English, value=mixed" per CLAUDE.md vault rule. Concept page bodies are 100% English (textbook excerpts). Body has Chinese placeholder `_(尚無內容)_` mixed in. Mixed registers — what's the right design?
- Wikilink targets for Chinese terms — does the ATP slug logic break for 腺苷三磷酸? Does Obsidian wikilink resolution have ASCII-vs-CJK issues?
- B12 concept fragmentation will compound when Chinese sources arrive: [[ATP]] + [[腺苷三磷酸]] + [[Adenosine Triphosphate]] becomes 3 fragmented pages. What should the alias dictionary look like to handle EN-EN + EN-ZH + ZH-ZH equivalence?

### Section 2 — DIFFERENT REASONING PRIOR

Where does your training prior disagree with Claude's or GPT-5's framing?

- Claude framed the issue as 14 separate Bs needing 4-7 patches.
- Codex round 1 framed it as patch-4-then-go.
- What FRAME do you want to push back with? Is the right unit "patches" or is it "the contract between dispatch and assembly is wrong, so any number of patches won't fix this"?

### Section 3 — CLAUDE/CODEX BLIND SPOTS

Claude wrote v1; Codex round 1 reviewed; Codex round 2 is running. Where do they likely SHARE the same bias?

- Both Claude and Codex are heavy on code-locality reasoning ("file:line"). What ABSTRACT failure modes might both miss?
  - Information-theoretic: is the wikilink graph as-designed actually conveying useful information density per token of LLM output?
  - Game-theoretic: when LLM is told to emit `wikilinks: [...]` per section, what's its incentive? Does it over-emit (since cost is 0 for it) leading to noise?
  - Persistence-theoretic: this is a knowledge base intended for years of writing. What does the design look like in 18 months when the user has 50 books ingested?
  - Distillation: is the Path B "walker = literal copy, LLM = metadata" split actually buying anything when the user can't use the metadata reliably?

### Section 4 — DECISION FRAMING

The v1 §8 lists 3 options: SHIP-NOW / PATCH-4-THEN-SHIP / ARCHITECTURAL-PAUSE. Critique this framing. Specifically:

- Is "ship 28 chapters" the right outcome to optimize? Maybe ship 1 book (BSE) only and validate user actually uses it before SN ingest?
- The user has $10k OpenAI free credit — how does that change cost calculus for review iterations and re-ingests?
- The 5/7 burn lesson was "validate-with-eyeball before scale". Is that lesson being honored or is panel review being substituted for eyeball validation?

### Section 5 — ARCHITECTURAL CONCERNS

Big-picture issues that escape line-level audit. Push back on the architecture itself if warranted:

- Is the 4-rule deterministic classifier (section_heading / bolded_define / freq_multi_section / definition_phrase) the right tool? Or should classifier itself be an LLM call?
- The `_alias_map.md` artifact: should it be DELETED entirely as a concept, replaced with always-create-stub-concept-page?
- Path B (walker verbatim + LLM metadata) was a deliberate move from Path A (LLM emits body). Was that the right call given the body verbatim test is now self-validating? What does Path C look like?
- Does the system have any TEST PYRAMID? E.g. fixture chapter that round-trips to known-good output across every change. There's lots of unit tests but no end-to-end golden chapter.

### Section 6 — FINAL VERDICT

- **Approve / approve with modifications / reject**.
- **Top 5 specific changes** ranked by P(future-burn) × cost-to-fix.
- **Ship decision**: ship 28 / ship 1 book / patch then ship / architectural pause / fundamental rewrite.
- Cite v1 sections by number (§1, §3, etc.); cite Codex round 1 by section letter (A, B, C, etc.); cite specific Bs (B4, B12 etc.) when responding.

## Required style

- English (matches Codex audit, helps panel comparison)
- Concrete and specific. Push back where you disagree. Refuse to rubber-stamp.
- This will be read directly by the project owner (Chinese-speaking) to make the final call.

## Codex round 1 audit (verbatim)

{codex_audit}

## Claude v1 draft (the artifact under review)

{artifact}

---

Begin your 6-section Gemini audit now."""


def main() -> int:
    _load_env()

    artifact = ARTIFACT_PATH.read_text(encoding="utf-8")
    codex_audit = CODEX_AUDIT_PATH.read_text(encoding="utf-8")

    prompt = build_prompt(artifact, codex_audit)

    print("=== Gemini panel audit dispatch ===", file=sys.stderr)
    print(f"Topic: {TOPIC}", file=sys.stderr)
    print(f"Prompt size: {len(prompt)} chars (~{len(prompt) // 4} tokens)", file=sys.stderr)
    print("Model: gemini-2.5-pro", file=sys.stderr)
    print("---", file=sys.stderr)

    response = ask_gemini(prompt, system=SYSTEM)
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
