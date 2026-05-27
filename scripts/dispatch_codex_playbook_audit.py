"""Codex panel audit dispatch — multi-agent-panel skill step 2.

Reads:
  prompts/thumbnail/playbook_v1.md (Claude draft under review)

Writes Codex audit (verbatim) to:
  docs/research/2026-05-27-codex-thumbnail-playbook-audit.md

Uses the `codex exec` CLI for non-interactive execution. Stdin carries the
audit prompt + artifact verbatim; codex returns the audit text on stdout.

Required: `codex` CLI installed and authenticated (ChatGPT subscription).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = REPO_ROOT / "prompts" / "thumbnail" / "playbook_v1.md"
CATALOG_PATH = REPO_ROOT / "prompts" / "thumbnail" / "playbook_data_v1.json"
EXTRACTION_PATH = REPO_ROOT / "data" / "thumbnail_reference_extraction_v1.json"
DESIGN_DOC_PATH = REPO_ROOT / "docs" / "research" / "2026-05-26-thumbnail-playbook-design.md"
OUTPUT_PATH = REPO_ROOT / "docs" / "research" / "2026-05-27-codex-thumbnail-playbook-audit.md"

TOPIC = "Title × Thumbnail Playbook v1 for 修修 (Health & Wellness / Longevity YouTube creator, zh-Hant audience)"


PROMPT_PREFIX = f"""# {TOPIC} Audit — Multi-Agent Panel Step 2 (Codex)

You are an independent third-party auditor providing a second opinion on a Title × Thumbnail Playbook. The owner (修修) has explicitly asked for push-back from your perspective as Codex/GPT-5 — do **NOT** rubber-stamp Claude's analysis.

Your value-add as Codex:
- Fact verification: verify numerical claims, hard counts, percentages (corpus is 140 thumbnails)
- Cross-document drift detection: playbook prose vs catalog JSON vs raw extraction data
- Push-back posture: willing to disagree where Claude is hand-waving
- Concrete grounding: cite playbook section IDs (T-A1...T-A10, T-V1...T-V10, JP-1...JP-8, MC-1...MC-6) verbatim

## Background

修修 runs a Health & Wellness / Longevity content channel in Traditional Chinese (Taiwan / Hong Kong audience). The playbook v1 was built from 140 high-CTR thumbnails sampled from 4 English-language creators (Ali Abdaal, Alex Hormozi, Cleo Abram, Jeff Su = 35 each). It will guide future LLM brainstorm calls when 修修 plans new videos.

Claude executed a 5-phase pipeline:
1. Per-image vision extraction (Sonnet 4.6) → 140 structured JSON rows
2. Cluster analysis → 10 title archetypes + 10 thumbnail archetypes + 8 joint pairings + 6 caveats
3. LLM composition → §2/§3/§4/§5.2/§7 markdown body
4. Brand-fit grading (S/A/B/C/F) per archetype with Chinese-language adaptations
5. Splice into hand-curated scaffold (§0/§1 theory anchors / §5.1 voice principles / §5.3 Hormozi adaptation / §5.4 bilingual table / §6 integration spec)

Total cost ~$10 across ~145 LLM calls. Claude graded the work as "max effort" deliverable.

## Stakes for 修修

Per the owner: "Title + Thumbnail accounts for 33-50%+ of a video's success — this is among the most important deliverables in the entire project". Bad recommendations cost weeks of failed videos. The playbook will be loaded into brainstorm prompts that 修修 will trust without re-verification on most calls.

## What you should produce

A 1500-2500 word audit in 6 sections. Where you agree with Claude, acknowledge briefly. Where you disagree or have unique insight, dig in.

### Section 1 — NUMERICAL / FREQUENCY GROUNDING

The playbook makes specific frequency claims (e.g. "T-A2 frequency 33/140 = 23.6%", "Loewenstein in 133/140 titles = 95%"). Spot-check at least 3 of these against the catalog (`prompts/thumbnail/playbook_data_v1.json`) — do the numbers match? Are creator_distribution sums correct? Are the universal_patterns frequencies plausible given the sample? Cite exact discrepancies.

### Section 2 — ARCHETYPE COHERENCE & SPLITS

Caveat MC-3 admits T-A4 (Story-Confession) and T-V1 (Face-Right Text-Left) likely conflate two distinct patterns. Read T-A1 through T-A10 and T-V1 through T-V10 critically — which other archetypes might be conflations? Which might be unnecessary splits (two near-identical patterns that should merge)? Be specific: cite §2.{{ID}} and §3.{{ID}} sections.

### Section 3 — FRAMEWORK ATTRIBUTION RIGOR

Each archetype's `mechanism_writeup` cites cognitive frameworks (Loewenstein, Cialdini, MrBeast PVP, etc.). For 3-5 randomly chosen archetypes: is the framework attribution earned by the structure of the example, or is it post-hoc rationalization? Where would a behavioural economist push back? Caveat MC-2 already concedes "post-hoc rationalisation" — push harder: which specific attributions are weakest?

### Section 4 — 修修-ADAPTATION REALISM

§5.2 grades each archetype S/A/B/C/F for 修修's brand. The single S grade is T-A8 (Authority-Research). Stress-test the brand-fit grades — are any A-grades over-optimistic given 修修's actual channel size (sub-50K, per MC-6)? Are any C-grades unfairly harsh? For the published Chinese adaptation examples in §2 (e.g. "5 個研究證實的習慣，讓你的生理年齡年輕 10 歲"), spot-check 3-5: would 修修's evidence-based audience react well? Would Taiwan/Hong Kong YouTube CTR algorithms surface them? Push back if any read as English-translation rather than zh-Hant native.

### Section 5 — METHODOLOGY GAPS

§7 has 6 self-critique caveats. What's missing? Sample-selection bias is acknowledged but: (a) no creator from the actual 修修-relevant niche (Attia, Huberman, Bryan Johnson, Saladino, Rhonda Patrick), (b) no zh-Hant high-CTR baseline, (c) survivorship bias acknowledged but no countermeasure proposed beyond "topic-driven archetypes for <50K". What other methodology gaps did Claude miss?

### Section 6 — FINAL VERDICT

- Approve as-is / approve with modifications / reject
- If modifications: top 3-5 specific changes ordered by priority, with rationale citing playbook sections
- If reject: alternative architecture you'd propose
- Bonus: identify 1-2 places where Claude's writing crosses from "useful pattern catalog" into "false confidence" — where 修修 should be most cautious applying recommendations

## Required style

- English (matches Gemini audit, helps panel comparison)
- Concrete and specific. Cite §N.M sections by name. Quote exact phrases.
- Push back where you disagree. Avoid hedging language ("consider", "perhaps", "might want to") — say "do X, here's why" or "don't do X, here's why".
- This will be read directly by 修修 to make the final call.
- Refuse "looks good overall" as audit output — list 5+ things you would change.

## Reference files (read these from the file system)

- `prompts/thumbnail/playbook_v1.md` — main playbook (1083 lines)
- `prompts/thumbnail/playbook_data_v1.json` — machine catalog
- `data/thumbnail_reference_extraction_v1.json` — 140 raw rows
- `docs/research/2026-05-26-thumbnail-playbook-design.md` — design rationale + framework anchors

Read these directly from the worktree (you are running with `codex exec` in this repo's cwd).

---

Begin your 6-section audit now. Output plain markdown — no preamble or code fence around the audit body.
"""


def main() -> int:
    if not ARTIFACT_PATH.exists():
        print(f"ERROR: artifact not found: {ARTIFACT_PATH}", file=sys.stderr)
        return 2

    print("=== Codex panel audit dispatch ===", file=sys.stderr)
    print(f"Topic: {TOPIC}", file=sys.stderr)
    print(f"Artifact: {ARTIFACT_PATH} ({ARTIFACT_PATH.stat().st_size} bytes)", file=sys.stderr)
    print(f"Output: {OUTPUT_PATH}", file=sys.stderr)
    print("---", file=sys.stderr)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Build the full prompt: prefix + artifact content embedded
    artifact_text = ARTIFACT_PATH.read_text(encoding="utf-8")
    full_prompt = (
        PROMPT_PREFIX
        + "\n\n## Artifact under review (playbook_v1.md, verbatim)\n\n"
        + artifact_text
    )

    print(
        f"Prompt size: {len(full_prompt)} chars (~{len(full_prompt) // 4} tokens)", file=sys.stderr
    )
    print("Dispatching codex exec...", file=sys.stderr)

    # Find codex executable (Windows: need .cmd; *nix: bare name)
    codex_cmd = "codex"
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd",
            Path("C:/Users/Shosho/AppData/Roaming/npm/codex.cmd"),
        ]
        for c in candidates:
            if c.exists():
                codex_cmd = str(c)
                break

    # Run codex exec with prompt via stdin
    proc = subprocess.run(
        [codex_cmd, "exec", "--skip-git-repo-check", "-"],
        input=full_prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        shell=(os.name == "nt"),  # Windows .cmd dispatch needs shell
    )

    if proc.returncode != 0:
        print(f"codex exec failed (exit {proc.returncode}):", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    output = proc.stdout
    OUTPUT_PATH.write_text(output, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(output)} chars)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
