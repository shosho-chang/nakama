# ruff: noqa: E501, E402
"""Gemini panel audit for ADR-021 (annotation substance store + Brook synthesize).

Step 3 of multi-agent-panel skill: Gemini reads Claude draft AND Codex audit,
brings different reasoning chain to surface what Claude+Codex both missed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_PATH = REPO_ROOT / "docs/decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md"
CODEX_AUDIT_PATH = REPO_ROOT / "docs/research/2026-05-07-codex-adr-021-audit.md"
OUTPUT_PATH = REPO_ROOT / "docs/research/2026-05-07-gemini-adr-021-audit.md"
SUPPORTING = [
    REPO_ROOT / "docs/decisions/ADR-017-annotation-kb-integration.md",
    REPO_ROOT / "agents/robin/CONTEXT.md",
    REPO_ROOT / "CONTENT-PIPELINE.md",
]

sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from shared.gemini_client import ask_gemini

SYSTEM = """You are Gemini 2.5 Pro performing the third lens in a multi-agent panel review of an architectural decision record.

Your value add is a DIFFERENT REASONING CHAIN than Claude (the drafter) or Codex/GPT-5 (the first auditor). Both have already analyzed this document. Do NOT rubber-stamp either of them.

Specifically bring these lenses Claude and Codex tend to miss:
- Multilingual / cross-locale concerns (the user writes in 繁體中文; KB has zh + en mixed content)
- Cross-domain analogues (similar problems solved in other systems / fields)
- Long-horizon / lifecycle concerns (5 years from now, what calcifies?)
- Behavioral / user-psychology angles (what could go wrong with how a real human interacts with this design?)
- Information-theoretic concerns (what info is being lost, what's being duplicated, what's the actual signal?)

Output in English. Cite file paths verbatim. Refuse to say 'looks good overall' — if you can't find issues list 5 things you would change anyway."""

ARTIFACT = ARTIFACT_PATH.read_text(encoding="utf-8")
CODEX_AUDIT = CODEX_AUDIT_PATH.read_text(encoding="utf-8")
SUPPORTING_TEXT = "\n\n---\n\n".join(
    f"# {p.relative_to(REPO_ROOT)}\n\n{p.read_text(encoding='utf-8')}"
    for p in SUPPORTING if p.exists()
)

PROMPT = f"""Audit the ADR draft below. Codex has already done a thorough code-grounded push-back. Your job is to find what BOTH Claude and Codex missed using a different reasoning chain.

# ADR-021 Claude draft

{ARTIFACT}

# Codex audit (verdict: do not ship as-is, 9 amendments required)

{CODEX_AUDIT}

# Supporting context (ADR-017, Robin CONTEXT.md, CONTENT-PIPELINE.md)

{SUPPORTING_TEXT}

# Required output (6 sections, English)

1. **What Codex missed (drift detection beyond code grounding)** — Codex audited code carefully. What architectural / lifecycle / behavioral concerns are absent from Codex's lens? Cite specific ADR-021 lines.

2. **Multilingual / cross-locale concerns** — The user writes in 繁中, KB has zh + en mixed content, EPUB books may be bilingual. Specifically scrutinize:
   - File 2 prose chapter-grouped structure when chapters are in mixed languages
   - kb_search query (topic + Zoro keywords) when topic is 繁中 and corpus has English papers
   - Wikilink `[[Concepts/X]]` resolution when X has both zh and en aliases (see _alias_map.md in KB)
   - LLM concept extraction sensitivity to language mode shifts in user reflections

3. **Lifecycle / 5-year concerns** — What calcifies and becomes hard to undo over years of accumulation?
   - File 1 / File 2 contract drift as schemas evolve
   - kb_index.db rebuild cost as KB grows
   - Project page mutation contract entropy as Brook adds more sections
   - Reject sticky list growth and stale-rejection problem

4. **Behavioral / user-psychology angles** — What goes wrong with how a real human interacts with this?
   - β HITL gate fatigue: user faces 30 evidence items per Project, will they actually review all?
   - Frozen slug list + live content: what happens when user adds a NEW reflection that contradicts an existing in-pool reflection? Does live render expose this?
   - Web UI panel as "review mode + writing mode same surface": context switching cost?
   - Project page as Obsidian/Web UI dual-write target: which is the true author when they disagree?

5. **Cross-domain analogues — has this problem been solved elsewhere better?**
   - Reading apps (Readwise, Matter, Hypothesis): how do they split position-data vs substance? What did they get right/wrong?
   - Knowledge management systems (Roam, Logseq, Tana): how do they handle reflection vs annotation distinction?
   - Academic literature management (Zotero, Citavi): how do they handle "evidence pool review" before writing?
   - Citation workflows (LaTeX bibtex, Pandoc): what mutation contract patterns generalize?

6. **Verdict** — does Gemini agree with Codex's "do not ship as-is, 9 amendments"? Add / remove / sharpen items. Be specific.

# Hard rules

- English output
- File paths verbatim
- No rubber-stamping — if you can't find issues, list 5 you would change anyway
- 1500-3000 words
- Save the output to {OUTPUT_PATH} (the calling script will write it)
"""

print(f"[gemini-panel] Dispatching audit on ADR-021... (artifact: {len(ARTIFACT)} chars, codex: {len(CODEX_AUDIT)} chars)", file=sys.stderr)

audit = ask_gemini(
    PROMPT,
    system=SYSTEM,
    model="gemini-2.5-pro",
    max_tokens=8192,
    thinking_budget=2048,
    temperature=0.3,
)

OUTPUT_PATH.write_text(audit, encoding="utf-8")
print(f"[gemini-panel] Written: {OUTPUT_PATH}", file=sys.stderr)
print(OUTPUT_PATH)
