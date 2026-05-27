"""Dispatch Gemini audit on ADR-035 video reader vertical.

Run from worktree root:
    python scripts/audit_adr_035_gemini.py > docs/research/2026-05-27-gemini-video-reader-adr-035-audit.md
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make project shared module importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(Path("E:/nakama/.env"))

from shared.gemini_client import ask_gemini  # noqa: E402

ADR_PATH = ROOT / "docs/decisions/ADR-035-video-reader-vertical.md"
adr_text = ADR_PATH.read_text(encoding="utf-8")

SYSTEM = """You are auditing an architecture decision record (ADR) for the Nakama project,
a multi-agent AI system for a Health/Wellness content creator. The ADR proposes a YouTube
Video Reader vertical that consumes the project's just-shipped Entity Promotion infrastructure
(ADR-034 v2).

Your value-add over Claude (the original author) is that you bring a DIFFERENT REASONING CHAIN.
You have NOT seen the grill conversation that produced this ADR. You see only the final draft.

DO NOT rubber-stamp. The author has stated strong preferences in many places — those are
exactly where confirmation bias bites. Your job is to push back, identify blind spots, and
test alternatives the author may have dismissed too quickly.

Apply your specific lenses:
- Multilingual realities: the user is Taiwanese and consumes mixed zh-TW / en content. The ADR
  notes Chinese auto-caption quality issues but may under-weight this.
- Multimodal: video has audio + visual track. The ADR is text-anchored. Is anything left on
  the table by ignoring visual cues (slides, faces, on-screen text)?
- Long-context fact recall: web platform conventions (WebVTT, YouTube IFrame API, podcast
  RSS feeds, ToS-compliance for caption fetching) — flag any factual claims you can verify
  or contradict.
- Independent reasoning: where would your priors diverge from a typical LLM trained on the
  same text? Push there.
"""

USER = f"""Audit this ADR. Produce a structured 6-section report:

# 1. Code/Spec grounding
Are claims in the ADR concrete enough that a future implementer can act on them? Where is the
draft hand-wavy?

# 2. Drift / inconsistency
Internal contradictions, places where two decisions don't compose, or where the ADR claims
"share with X" but X isn't well-defined.

# 3. Numerical / factual claims
List every numerical or factual claim (e.g., "Whisper 0.1× realtime on RTX 4070", "1.5×
playback default", "YouTube auto-caption is free and predictable", "yt-dlp captions in
grey-area"). For each: verify, contradict, or call out as unverifiable.

# 4. Assumption push-back
At least 5 assumptions the ADR makes that you would challenge. For each: state the assumption,
state your push-back, and assess how load-bearing the assumption is.

# 5. Alternatives not considered (or rejected too fast)
What did the ADR's Considered Options miss? Which rejection reasoning is weakest?

# 6. Verdict
Is this ADR ready to ship as Accepted? If not, what 3 things would unblock it?

Be specific. Reference section IDs (D1-D8) when commenting on decisions. Output English.

---

ADR text:

```markdown
{adr_text}
```
"""

if __name__ == "__main__":
    response = ask_gemini(
        USER,
        system=SYSTEM,
        model="gemini-2.5-pro",
        max_tokens=8192,
        thinking_budget=2048,
    )
    print(response)
