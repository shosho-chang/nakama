"""Gemini panel audit dispatch — multi-agent-panel skill step 3.

Reads:
  prompts/thumbnail/playbook_v1.md (Claude draft)
  docs/research/2026-05-27-codex-thumbnail-playbook-audit.md (Codex audit)

Writes Gemini audit (verbatim) to:
  docs/research/2026-05-27-gemini-thumbnail-playbook-audit.md

Uses shared/gemini_client (gemini-2.5-pro).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = REPO_ROOT / "prompts" / "thumbnail" / "playbook_v1.md"
CODEX_AUDIT_PATH = REPO_ROOT / "docs" / "research" / "2026-05-27-codex-thumbnail-playbook-audit.md"
OUTPUT_PATH = REPO_ROOT / "docs" / "research" / "2026-05-27-gemini-thumbnail-playbook-audit.md"

TOPIC = "Title × Thumbnail Playbook v1 for 修修 (Health & Wellness / Longevity YouTube creator, zh-Hant audience)"

sys.path.insert(0, str(REPO_ROOT))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        for env_candidate in (REPO_ROOT / ".env", Path("E:/nakama/.env")):
            if env_candidate.exists():
                load_dotenv(env_candidate)
                break
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
    "You are an independent third-party auditor providing a second opinion on a "
    f"{TOPIC}. The owner (修修) has explicitly asked for push-back from your "
    "unique perspective as a Gemini model — do NOT rubber-stamp Claude or Codex "
    "analyses. Your value is your different reasoning chain, broader fact-recall, "
    "and STRONGER MULTILINGUAL LENS than Claude or GPT-5 — this playbook will be "
    "deployed on a Traditional Chinese (Taiwan / Hong Kong) channel, making your "
    "zh-Hant cultural and linguistic recall mission-critical. Be concrete, cite "
    "specifics, and disagree where appropriate. Refuse 'looks good overall' as "
    "audit output — list 5+ things you would change if asked."
)


def build_prompt(artifact: str, codex_audit: str) -> str:
    return f"""# {TOPIC} Audit — Multi-Agent Panel Step 3 (Gemini)

You are the THIRD reviewer in a multi-agent panel. Claude drafted the playbook. Codex (GPT-5) audited it. Now you audit BOTH — adding a Gemini-specific perspective.

## Background

修修 runs a Health & Wellness / Longevity content channel in Traditional Chinese (Taiwan / Hong Kong audience). The playbook v1 was built from 140 high-CTR thumbnails sampled from 4 English-language creators (Ali Abdaal, Alex Hormozi, Cleo Abram, Jeff Su = 35 each). It will guide future LLM brainstorm calls when 修修 plans new videos.

Stakes per the owner: "Title + Thumbnail accounts for 33-50%+ of a video's success — this is among the most important deliverables in the entire project". Bad recommendations cost weeks of failed videos.

## What you should produce

A 1500-2500 word audit in 6 sections. Focus on what's UNIQUELY valuable from Gemini perspective — especially the bilingual / cross-cultural lens. Where you agree with Claude or Codex, acknowledge briefly. Where you disagree or have additional insight, dig in.

### Section 1 — MULTILINGUAL / CROSS-CULTURAL ADAPTATION

This is your strongest lens. The playbook is built from 100% English-language reference data but will be deployed in zh-Hant. Specific things to assess:

(a) The Chinese adaptation examples in §2 (e.g. "5 個研究證實的習慣，讓你的生理年齡年輕 10 歲" / "如何在 12 週內，把你的靜息心率降到運動員水準"). Spot-check 5-8 of these — do they read as PUBLISHABLE Taiwan/Hong Kong YouTube titles, or as English-to-Chinese MTL artifacts? Are character counts realistic for mobile YT feed truncation (zh-Hant typically truncates at 24-32 chars on mobile)?

(b) §5.4 Bilingual considerations table — what zh-Hant title/thumbnail conventions did Claude miss? Examples to consider: 全形標點 vs ASCII, 量詞 conventions, 「呢 / 喔 / 啊」 modal particles in casual titles, 「啦 / 嘛」 audience signals, 數字表記 (一萬 vs 10K vs 10,000), parenthetical credibility markers different from English「（醫師審訂）」「（最新研究）」「（XX年更新）」, traditional vs simplified character contamination risks, Taiwan vs Hong Kong terminology splits.

(c) Cleo Abram's pattern T-V6 (Question Overlay) is graded C for 修修. Is this grade right? In English science-explainer context the pattern works; what about zh-Hant health-explainer? Push back on the grade with cultural reasoning.

(d) Identity-Based Hook examples — does Berger's "Contagious" framework actually port across cultures? Are status/identity hooks calibrated the same in collectivist (zh-Hant Confucian-influenced) audience vs individualist Western audience? Cite specific archetypes where this matters.

### Section 2 — DIFFERENT PRIOR

Where does your training prior differ from Claude's (Anthropic Constitutional AI) or GPT-5's (RLHF + GPT lineage)? Specific dimensions you see that they wouldn't. Don't say "I have different training data" generically — point to specific places in the playbook where your prior would generate a different recommendation.

### Section 3 — CLAUDE/CODEX BLIND SPOTS

Where do Claude and Codex likely share the same bias? What did both miss? Use specific evidence from the playbook text or Codex audit. Don't say "they might miss X" — say "they both missed X, here's the section."

Examples to probe:
- Both Claude and Codex are trained on heavily-English YouTube data; what zh-Hant high-CTR creator patterns are completely absent from this analysis?
- Both have engagement biases toward thumbnail aesthetics popular in English-speaking markets; what aesthetic conventions popular in Taiwan/Hong Kong YouTube (e.g. KOL beauty/wellness creator style) might be missing?

### Section 4 — RAW DATA SANITY CHECK

The playbook references 140 raw rows in `data/thumbnail_reference_extraction_v1.json` and a catalog in `prompts/thumbnail/playbook_data_v1.json`. Without re-extracting, can you point to specific rows or catalog entries where the LLM extraction likely hallucinated? (Common modes: invented colour hex codes, framework attributions that match generic theory not the specific example, post-hoc story-confession framings that aren't in the actual thumbnail.) Use the catalog's `canonical_example_id` field — pick 3-5 and assess whether the canonical example actually exemplifies the archetype.

### Section 5 — METHODOLOGY GAPS BEYOND §7 CAVEATS

§7 has 6 caveats. What's structurally missing? Examples to consider:
- No A/B framework recommendation for 修修 to validate adoptions (Claude says "test patterns" generically; what specific A/B test design — sample size, duration, metric — would actually answer the question?)
- No version-decay strategy (when does v1 become stale? what triggers v2 — corpus size? CTR data accumulation? algorithm change?)
- No false-positive guardrails: which archetypes, if 修修 over-applies them, would harm channel-level metrics not just video-level CTR?

### Section 6 — FINAL VERDICT

- Approve / approve with modifications / reject
- If modifications: top 3-5 specific changes ordered by priority, citing playbook section IDs (T-AN, T-VN, JP-N, MC-N, §X.Y) and Codex audit section numbers (1-6) verbatim
- For the SINGLE highest-priority change: provide concrete spec for what the fix looks like (not just "improve X" but "replace section §X.Y paragraph 3 with: ...")

## Required style

- English (matches Codex audit, helps panel comparison)
- Concrete and specific. Push back where you disagree.
- This will be read directly by 修修 to make the final call.
- Refuse "looks good overall" as audit output — list 5+ things you would change.

## Codex (GPT-5) audit verbatim

{codex_audit}

## Artifact under review (playbook_v1.md verbatim)

{artifact}

---

Begin your 6-section Gemini audit now."""


def main() -> int:
    _load_env()

    if not CODEX_AUDIT_PATH.exists():
        print(f"ERROR: Codex audit not found: {CODEX_AUDIT_PATH}", file=sys.stderr)
        print("Run scripts/dispatch_codex_playbook_audit.py first.", file=sys.stderr)
        return 2

    artifact = ARTIFACT_PATH.read_text(encoding="utf-8")
    codex_audit = CODEX_AUDIT_PATH.read_text(encoding="utf-8")
    prompt = build_prompt(artifact, codex_audit)

    print("=== Gemini panel audit dispatch ===", file=sys.stderr)
    print(f"Topic: {TOPIC}", file=sys.stderr)
    print(f"Prompt size: {len(prompt)} chars (~{len(prompt) // 4} tokens)", file=sys.stderr)
    print(f"Output: {OUTPUT_PATH}", file=sys.stderr)
    print("---", file=sys.stderr)

    response = ask_gemini(prompt, system=SYSTEM)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(response, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(response)} chars)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
