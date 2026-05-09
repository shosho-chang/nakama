# ruff: noqa: E501, E402
"""Gemini panel audit for ADR-024 (cross-lingual concept alignment).

Step 3 of multi-agent-panel skill: Gemini reads Claude draft AND Codex audit,
brings different reasoning chain to surface what Claude+Codex both missed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = (
    REPO_ROOT / "docs/decisions/ADR-024-cross-lingual-concept-alignment.md"
)
CODEX_AUDIT_PATH = REPO_ROOT / "docs/research/2026-05-08-codex-adr-024-audit.md"
OUTPUT_PATH = REPO_ROOT / "docs/research/2026-05-08-gemini-adr-024-audit.md"
SUPPORTING = [
    REPO_ROOT / "docs/plans/2026-05-08-monolingual-zh-source-grill.md",
    REPO_ROOT / "docs/decisions/ADR-017-annotation-kb-integration.md",
    REPO_ROOT / "docs/decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md",
    REPO_ROOT / "docs/decisions/ADR-022-multilingual-embedding-default.md",
    REPO_ROOT / "agents/robin/CONTEXT.md",
    REPO_ROOT / "CONTEXT-MAP.md",
]

sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from shared.gemini_client import ask_gemini

SYSTEM = """You are Gemini 2.5 Pro performing the third lens in a multi-agent panel review of an architectural decision record (ADR-024 cross-lingual concept alignment for nakama monolingual-zh source ingest).

Your value add is a DIFFERENT REASONING CHAIN than Claude (the drafter) or Codex/GPT-5 (the first auditor). Both have already analyzed this document. Do NOT rubber-stamp either of them.

Specifically bring these lenses Claude and Codex tend to miss:
- Multilingual / cross-locale concerns (the user writes 繁體中文; KB has zh + en mixed content; ingest of Chinese source is FIRST cross-lingual ingest into English-named KB)
- Cross-domain analogues (Wikipedia interlanguage links, MediaWiki redirects, ICD-10 multilingual, Unicode CLDR, BabelNet / WordNet — how have other systems handled this exact problem?)
- Long-horizon / lifecycle concerns (5 years from now, what calcifies? alias map drift, Concept page rename cascade, Brook output rendering breakage)
- Behavioral / user-psychology angles (what could go wrong with how 修修 actually interacts? when LLM picks wrong canonical_en, what's the recovery loop?)
- Information-theoretic concerns (alias map redundancy, frontmatter aliases vs alias_map dict — duplication or different signal? grounding pool token / context tradeoff)

Output in English. Cite file paths verbatim. Refuse to say 'looks good overall' — if you can't find issues list 5 things you would change anyway."""

ARTIFACT = ARTIFACT_PATH.read_text(encoding="utf-8")
CODEX_AUDIT = CODEX_AUDIT_PATH.read_text(encoding="utf-8") if CODEX_AUDIT_PATH.exists() else "[Codex audit not yet generated — proceed without it but flag in your verdict]"
SUPPORTING_TEXT = "\n\n---\n\n".join(
    f"# {p.relative_to(REPO_ROOT)}\n\n{p.read_text(encoding='utf-8')}"
    for p in SUPPORTING
    if p.exists()
)

PROMPT = f"""Audit the ADR draft below. Codex has already done a thorough code-grounded push-back. Your job is to find what BOTH Claude and Codex missed using a different reasoning chain.

# ADR-024 Claude draft

{ARTIFACT}

# Codex audit

{CODEX_AUDIT}

# Supporting context (grill summary, ADR-017, ADR-021, ADR-022, Robin CONTEXT, CONTEXT-MAP)

{SUPPORTING_TEXT}

# Required output (6 sections, English)

1. **What Codex missed (architectural / lifecycle / behavioral lens beyond code grounding)** — Codex audited code carefully. What architectural / lifecycle / behavioral concerns are absent from Codex's lens? Cite specific ADR-024 lines or section headers.

2. **MULTILINGUAL CONSIDERATIONS — cross-lingual retrieval, term mapping, disambiguation**:
   - When LLM抽 zh-source extraction picks `canonical_en` from grounding pool, what's the failure mode where it picks WRONG English term (e.g. 「肌酸」mapped to "Creatinine" instead of "Creatine" — sound-alikes, false friends, semantic drift)? What's the recovery loop?
   - The `is_zh_native: bool` escape hatch — what's the LLM's calibration for this? When 「自由基」(free radicals) is hesitantly assigned `is_zh_native=true`, what happens?
   - alias map shipped 5/8 (NFKC + casefold + plural + seed alias dict): is `casefold` even meaningful for Chinese? Is plural-stemming for English causing zh-EN alias drift?
   - When Brook synthesizes a 繁中 article and references `[[Concepts/Mitophagy|粒線體自噬]]`, does the wikilink display alias mechanism actually work in markdown→HTML pipeline or just in Obsidian native? Verify against `agents/brook/synthesize/_outline.py` if accessible.
   - What about variant Chinese forms — 簡體 (粒线体自噬) appearing in scraped articles, 港澳 conventions, etc.? Does ADR-024 even acknowledge zh-Hans / zh-Hant split?

3. **CROSS-DOMAIN ANALOGUES — has this exact problem been solved better?**
   - **Wikipedia interlanguage links** (langlinks db table) — pages have language-tagged sister pages, not aliases. Why didn't ADR-024 consider language-as-first-class versus alias-as-second-class?
   - **MediaWiki redirects** vs aliases — what's the model? When does MW use redirect vs alias category?
   - **ICD-10 / SNOMED CT multilingual** — medical code namespaces with multiple language labels per code, hierarchical. Closest analogue to a multilingual KB.
   - **WordNet / BabelNet synsets** — language-agnostic synset id, language-specific lemmas. Drastically different model.
   - **Obsidian wikilink alias** — what does Obsidian's pipe-alias actually support? Pipe is rendering only; underlying link target is canonical filename. Does ADR-024 over-rely on this for cross-lingual UX?
   - **Roam Logseq block-based** — alternate identity model entirely.

4. **5-YEAR LIFECYCLE / CALCIFICATION CONCERNS**:
   - What calcifies after 100+ Chinese source aliases accumulate in 100+ Concept page frontmatters?
   - What's the rename cascade cost when an English canonical needs to change (e.g. "Mitophagy" deemed wrong, should be "Selective Autophagy")?
   - alias_map seed dict in `shared/alias_map.py` (or wherever) — does it sync with frontmatter aliases automatically or are they two truth sources?
   - When ADR-022 multilingual embedding gets re-trained / replaced (post-BGE-M3), does Concept namespace need rebuild?
   - Long-horizon: what if 修修 starts ingesting Japanese books? Korean? The schema is `monolingual-zh` mode, not `monolingual-{{lang}}`. Premature lock-in?

5. **BEHAVIORAL / USER-PSYCHOLOGY ANGLES**:
   - When LLM picks WRONG canonical_en during ingest, modifying alias_map.py + frontmatter aliases array is fixable but tedious. Is the recovery flow visible to 修修 in the Reader UI / Bridge UI? Or silent until Brook synthesize fails?
   - The ADR mentions "lazy build" — 修修 ingests a Chinese book, expects KB to be queryable. But existing English Concept pages won't have zh aliases until many books later. Will 修修 be confused / give up?
   - The grounding pool injection — every ingest run sees the existing 100+ Concept names. As corpus grows past 500 Concept pages, this token budget bloats. When does 修修 notice?
   - The frontmatter `aliases` array is human-editable in Obsidian. What if 修修 manually edits an alias and then textbook-ingest auto-merges a different one? Conflict resolution unclear in ADR-024.

6. **VERDICT** — does Gemini agree with Codex's verdict (refer to Codex audit Section 6)? Add / remove / sharpen items. Be specific about which Codex items you uplift / downgrade and why. Cite ADR-024 sections by header name.

# Style requirements

- English
- Concrete and specific. Cite ADR-024 sections by header. Cite file paths verbatim.
- Push back where you disagree with Claude OR Codex.
- This will be read directly by the project owner (修修) for ship/no-ship call.
"""


def main() -> None:
    print(f"Dispatching Gemini panel audit for ADR-024…")
    print(f"  artifact: {ARTIFACT_PATH.relative_to(REPO_ROOT)}")
    print(f"  codex audit: {CODEX_AUDIT_PATH.relative_to(REPO_ROOT)}")
    print(f"  output: {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(f"  supporting: {len([p for p in SUPPORTING if p.exists()])}/{len(SUPPORTING)} files found")
    print()

    response = ask_gemini(
        prompt=PROMPT,
        system=SYSTEM,
        model="gemini-2.5-pro",
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(response, encoding="utf-8")
    print(f"Wrote audit ({len(response)} chars) → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
