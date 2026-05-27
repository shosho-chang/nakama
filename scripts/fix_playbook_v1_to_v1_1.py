"""Apply 3-way panel v1.1 fixes to playbook_v1.md.

Fixes the universal + 2-of-3 confidence items from
``docs/research/2026-05-27-playbook-3way-panel-integration.md``.

Universal-confidence fixes (apply mechanically):
- I-2: §1.X anchor citation corrections (29 instances)
- I-3: §1 "NOT ad-hoc" hedge softening

2-of-3 confidence fixes (apply with explicit edits):
- I-1: numerical/frequency recomputation flag
- I-4: T-A9 / T-A10 → modifier flag
- I-5: MC-3 conflation expansion
- I-7: T-V6 grade C → D
- I-8: zh-Hant rewrite flag
- I-12: mechanism softening ("hypothesised", "consistent with")

Gemini-unique fixes:
- I-9: §5.4 expansion (5 new rows)
- I-10: §1.4 collectivist supplement
- I-13: T-V5 mechanism rewrite
- I-14: JP-8 reframe

Backlog (documented in §8, not modifying):
- I-6, I-11, I-15, I-16, I-17, I-18

Run from worktree root:
    python -m scripts.fix_playbook_v1_to_v1_1

Writes diff summary to stderr. Modifies prompts/thumbnail/playbook_v1.md in place.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLAYBOOK = _REPO_ROOT / "prompts" / "thumbnail" / "playbook_v1.md"

logger = logging.getLogger(__name__)


# I-2: anchor fixes. Map (framework_name_lowercase, wrong_anchor) → correct_anchor
_ANCHOR_FIXES = {
    # framework: correct_§1.X
    "loewenstein": "§1.1",
    "mrbeast pvp": "§1.2",
    "cialdini authority": "§1.3",
    "cialdini social-proof": "§1.3",
    "cialdini scarcity": "§1.3",
    "cialdini commitment-consistency": "§1.3",
    "cialdini reciprocity": "§1.3",
    "cialdini liking": "§1.3",
    "identity-based hook": "§1.4",
    "loss aversion": "§1.5",
    "specificity bias": "§1.6",
    "specificity": "§1.6",
    "pattern interrupt": "§1.7",
    "face emotion contagion": "§1.8",
    "numerical anchor": "§1.9",
    "familiarity scaffolding": "§1.10",
    "mere-exposure": "§1.10",
    "cognitive ease": "§1.11",
    "insider knowledge frame": "§1.12",
    "status signaling": "§1.4",
}


def apply_anchor_fixes(text: str) -> tuple[str, int]:
    """Find `Framework (§1.X)` patterns; fix where §1.X is wrong."""
    pattern = re.compile(r"([A-Z][A-Za-z\- ]+?)\s+\(§(1\.\d+)\)")

    def _repl(m: re.Match) -> str:
        fw = m.group(1).strip()
        cited = "§" + m.group(2)
        # case-insensitive lookup
        key = fw.lower()
        correct = _ANCHOR_FIXES.get(key)
        if correct is None:
            # Fallback: substring match
            for k, v in _ANCHOR_FIXES.items():
                if k in key:
                    correct = v
                    break
        if correct and correct != cited:
            return f"{fw} ({correct})"
        return m.group(0)

    new_text, count = pattern.subn(_repl, text)
    fixed = sum(1 for m in pattern.finditer(text) if _repl(m) != m.group(0))
    return new_text, fixed


# I-3: §1 "NOT ad-hoc" softening
_I3_OLD = "Click-driver attributions in this playbook are NOT ad-hoc."
_I3_NEW = "Click-driver attributions in this playbook are hypothesised by matching observed title/thumbnail structure to established cognitive frameworks. They are **hypotheses, not causal claims** — see §7 caveats MC-2 and the panel integration matrix at `docs/research/2026-05-27-playbook-3way-panel-integration.md`."


# I-7: T-V6 grade C → D
_I7_OLD = "| T-V6 | Surprised / Excited Face with Question Overlay | C |"
_I7_NEW = "| T-V6 | Surprised / Excited Face with Question Overlay | **D / Avoid** |"

# I-7 supplementary: T-V6 archetype grade line
_I7_ARCH_OLD = """### T-V6. Surprised / Excited Face with Question-Mark Overlay"""
# We'll add a regulatory warning block right after the heading (search/insert)


# I-4: T-A9 / T-A10 → modifier flag (add a leading note paragraph at start of §2)
_I4_NOTE = """
> **v1.1 panel integration note** (from `docs/research/2026-05-27-playbook-3way-panel-integration.md` items I-4 + I-5): both Codex and Gemini flag that T-A9 (Year-Anchor) and T-A10 (Cost-Risk-Reframe) function as **modifier tags** rather than primary archetypes — they attach to other archetypes rather than standing alone. v1.1 retains them in §2 for traceability but treat them as modifiers in brainstorm calls (combine with T-A1/T-A2/T-A8 rather than using standalone). Codex also notes that T-A2, T-A3, T-A5, T-A8 likely conflate 2-3 distinct sub-patterns each — v2 will split. See MC-3 (expanded).

"""

# I-5: MC-3 conflation expansion
_I5_OLD = "**Implication for 修修**: 在使用 T-A4 時，修修 應分別測試「回顧告白版」（「9 件我希望 30 歲前就知道的事」）和「假設藍圖版」（「如果我想逆轉生理年齡，我會這樣做」）兩種子型態——它們啟動的心理機制不同，對不同觀眾狀態（已有行動意願 vs. 尚在探索）的效力也可能大相徑庭，切勿混為一談。"

_I5_NEW = (
    _I5_OLD
    + """

**v1.1 panel update** (Codex audit §2): MC-3 undercounted the conflation problem. Additional archetypes flagged for v2 split:
- **T-A2 How-To** conflates: (a) procedural how-to (steps), (b) personal workflow story ("How I Manage My Time"), (c) explainer ("AI Agents, Clearly Explained") — these create different click expectations.
- **T-A3 Contrarian Reversal** conflates: (a) accusation frames ("you're doing it wrong"), (b) scientific reframe ("Dinosaurs Were Weirder Than We Thought"), (c) social-comparison ("99% of People…") — trust-implication differs sharply.
- **T-A5 Exclusive Secret** overlaps T-A3 — "The Real Reason…" (deep explanation, trust-building) vs "what they don't tell you" (suspicion/conspiracy-adjacent, trust-risky) need separation in health context.
- **T-A8 Authority-Research** is too broad — named-expert-guest / named-institution / quantified-credential / "I-read-N-books" are different trust mechanisms (external vs self-authority).
- **T-V1 / T-V4** are production orientations of the same dual-zone face+payload layout — v2 should merge unless CTR data proves left/right matters."""
)


# I-13: T-V5 mechanism rewrite (Proof of Work / Complexity Signaling)
_I13_FIND_NEAR = "### T-V5. Whiteboard / Diagram Reveal with Creator"
_I13_OLD_MECH_PATTERN = re.compile(
    r"(### T-V5\. Whiteboard / Diagram Reveal with Creator.*?- \*\*Mechanism\*\*: )([^\n]+)",
    re.DOTALL,
)
_I13_NEW_MECH = (
    "**(v1.1 panel revision — Gemini audit §4)** The dense, near-unreadable-at-thumbnail-scale diagram works via **Proof of Work / Complexity Signaling** "
    "(consistent with §1.10 Familiarity Scaffolding and §1.6 Specificity), not via Loewenstein's information gap. The viewer cannot read the diagram, but its existence signals that the creator has done extensive synthesis to compress complexity into a system — this raises credibility before the viewer commits to clicking. The mechanism is **the existence of the system, not the contents** of the diagram."
)


# I-14: JP-8 recipe replace (cautionary case + non-confrontational reframe)
_I14_OLD_HEADER = (
    "### JP-8. Exclusive Secret Title + 'YOU'RE BEING LIED TO' Confrontational Overlay Thumbnail"
)
_I14_NEW_HEADER = "### JP-8. Exclusive Secret Title + Confrontational Overlay Thumbnail (v1.1: cautionary use only)"

_I14_PANEL_NOTE = """

> **v1.1 panel warning** (Gemini audit §4): This pairing is a **brand-suicide risk** for an evidence-based health channel. Confrontational tone applied to established medical metrics (e.g. "BMI 是錯的") creates an adversarial relationship with mainstream medical consensus, which conflicts with 修修's positioning. Use this pairing ONLY when the contrarian claim is backed by published research the channel can cite, AND the language is reframed from confrontation ("X is wrong") to collective re-examination ("we may have misunderstood X"). Frequency in corpus: 4 — already near the ≥3 threshold floor; consider treating as creator-specific (Hormozi) signature rather than generalisable archetype.

"""


# I-9: §5.4 expansion — 5 new rows
_I9_INSERT_AFTER = (
    "| Numbers | 100K subs, $100M | 阿拉伯數字 + 中文單位「10 萬訂閱」「破億營收」(避免直接美元) |"
)
_I9_NEW_ROWS = """
| Modal particles | (absent in English titles) | 「喔 / 啊 / 啦 / 耶 / 嘛」 可大幅軟化命令式語氣 — 例：「5 個睡眠殺手」→「原來這 5 件事才是睡眠殺手喔！」(Gemini panel §1b) |
| Lenticular brackets | (n/a) | 【】(全形 lenticular brackets) 是 Taiwan YT 主流 framing convention — 「【完整版】/【新手必看】/【醫師審訂】」放在標題尾或中段標記 promise (Gemini panel §1b) |
| TW vs HK terminology | (single English term) | 健康領域用詞兩岸三地有差，例：優格(TW) vs 乳酪(HK)；高麗菜(TW) vs 椰菜(HK)；單車(HK) vs 腳踏車(TW)。需建健康詞彙 glossary 並依目標受眾選擇 (Gemini panel §1b) |
| Simplified char contamination | (n/a, alphabet-based) | 中文輸入法或剪貼板易混入簡體字（台→台/臺、里→里/裡、发→發/髮）— 損害品牌信度，必須加入發布前 QA step (Gemini panel §1b) |
| Numerical unit cultural connotation | "10K / 100M" | 用「10K / 100K」signal Western tech/finance 影響，可能疏離傳統受眾；「10 萬 / 十萬」signal 在地正式調性。視 segment 選擇 (Gemini panel §1b) |"""


# I-10: §1.4 collectivist supplement
_I10_INSERT_AFTER = "**修修-relevant variant**: 健康/長壽受眾的 identity hooks 通常是「重視健康的科學派」「中年要起來保養」「不想老化得太快的高敏感族」。"
_I10_NEW = """

**v1.1 collectivist supplement (Gemini panel §1d)**: Berger's framework was developed in individualistic North American context. In Taiwan / Hong Kong Confucian-influenced collectivist culture, status-driven hooks like "Get Ahead of 99% of People" land weaker — and a **family-and-social-responsibility identity hook** is often more powerful:

- **The Responsible Provider/Child**: 「如何健康地陪伴孩子成長到 30 歲」/ 「別成為家人的負擔：中年後必須做的 3 個健康準備」
- **The Prudent Planner**: 「為自己的老年生活做好準備」(self-reliance for harmony, not competition)
- **The In-Group Knowledge Sharer**: 「值得分享給父母 / 伴侶看的 X 件事」(identity = "I have valuable information for my loved ones", not "I'm smarter than everyone")

These hooks should be over-indexed in §2 Chinese adaptations vs the corpus's individualistic frames."""


# I-8: zh-Hant rewrite flag — add a top-level warning at start of §2
_I8_BANNER = """
> **v1.1 panel-required rewrite flag** (Codex audit §4 + Gemini audit §1): All `中文化範例` strings in §2 / §3 / §4 below are **conceptual sketches**, not publishable titles. Gemini specifically called out catastrophic-translation cases (T-A2's 「在腦神經科學研究出現之前你可能已經損傷了它」 reads as machine translation; T-A4 violates own §5.4 length rule at 34 chars; T-A10 invents non-evidence metrics like 「睡眠恢復力」). Before deployment in brainstorm prompts, all 中文化範例 must be rewritten by 修修 or a native-zh-Hant copywriter familiar with Taiwan/HK YouTube conventions. v1.1 retains the English-translation versions for traceability; **do not load them into production prompts as-is**. See Gemini panel §6 item #1 spec for what a properly native T-A3 adaptation looks like — that block is reproduced in §A1 below.

"""

# I-12: causal language softening — replace "NOT ad-hoc" / "this confirms" / "non-negotiable"
_CAUSAL_SOFTEN = [
    # (old, new)
    ("NOT ad-hoc", "hypothesised from"),
    ("This confirms", "This is consistent with"),
    ("**non-negotiable**", "important"),
    ("the framework fires", "the framework is hypothesised to fire"),
    ("audited data", "credibility signal"),
    ("幾乎零改編直接使用", "可直接套用，但仍需逐次驗證該主張是否真實有研究支持"),
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    text = _PLAYBOOK.read_text(encoding="utf-8")
    original_len = len(text)

    # I-2 anchor fixes
    text, anchor_fixes = apply_anchor_fixes(text)
    logger.info("I-2: fixed %d §1.X anchor citations", anchor_fixes)

    # I-3 NOT ad-hoc softening
    if _I3_OLD in text:
        text = text.replace(_I3_OLD, _I3_NEW)
        logger.info("I-3: softened 'NOT ad-hoc' framing in §1")
    else:
        logger.warning("I-3: target string not found")

    # I-4 T-A9/T-A10 modifier note (insert at start of §2 body, after the §2 heading)
    if "## 2. Title Archetypes\n" in text:
        text = text.replace(
            "## 2. Title Archetypes\n\n",
            "## 2. Title Archetypes\n" + _I4_NOTE + _I8_BANNER + "\n",
            1,
        )
        logger.info("I-4 + I-8: added §2 v1.1 modifier-tag note + zh-Hant rewrite-required banner")
    else:
        logger.warning("I-4/I-8: §2 anchor not found")

    # I-5 MC-3 expansion
    if _I5_OLD in text:
        text = text.replace(_I5_OLD, _I5_NEW)
        logger.info("I-5: expanded MC-3 with T-A2/T-A3/T-A5/T-A8/T-V1 conflation flags")

    # I-7 T-V6 grade C → D in matrix table
    if _I7_OLD in text:
        text = text.replace(_I7_OLD, _I7_NEW)
        logger.info("I-7: T-V6 grade C → D in §5.2 matrix")

    # I-7 regulatory caveat at T-V6 archetype heading
    t_v6_regulatory = """### T-V6. Surprised / Excited Face with Question-Mark Overlay

> **v1.1 panel downgrade** (Gemini audit §1c, mission-critical): This archetype's grade is changed from C to **D / Avoid** for 修修's health channel. The Cleo Abram-style "found it? / solved it?" question overlay maps to **content-farm clickbait aesthetic in Taiwan/Hong Kong**, where such phrasing on health/medical topics aligns with miracle-cure scam content. There is also non-trivial regulatory risk: phrases implying medical breakthrough may run afoul of Taiwan's Health Food Control Act (健康食品管理法) and HK's equivalent advertising guidelines. The pattern's emotional cliff-hanger mechanism turns into a life-or-death question when applied to health — that's a credibility liability, not a click asset. **Use only with truly novel research the channel can cite, AND non-clickbait phrasing.**

"""
    if "### T-V6. Surprised / Excited Face with Question-Mark Overlay\n\n- **One-line**" in text:
        text = text.replace(
            "### T-V6. Surprised / Excited Face with Question-Mark Overlay\n",
            t_v6_regulatory,
            1,
        )
        logger.info("I-7: T-V6 archetype heading regulatory caveat added")

    # I-13 T-V5 mechanism rewrite
    m = _I13_OLD_MECH_PATTERN.search(text)
    if m:
        text = text[: m.start(2)] + _I13_NEW_MECH + text[m.end(2) :]
        logger.info("I-13: T-V5 mechanism replaced (Proof of Work / Complexity Signaling)")

    # I-14 JP-8 reframe header + panel note
    if _I14_OLD_HEADER in text:
        text = text.replace(_I14_OLD_HEADER, _I14_NEW_HEADER)
        # insert panel note right after the new header
        text = text.replace(
            _I14_NEW_HEADER + "\n",
            _I14_NEW_HEADER + _I14_PANEL_NOTE,
            1,
        )
        logger.info("I-14: JP-8 header reframed + panel-warning block added")

    # I-9 §5.4 expansion
    if _I9_INSERT_AFTER in text:
        text = text.replace(
            _I9_INSERT_AFTER,
            _I9_INSERT_AFTER + _I9_NEW_ROWS,
        )
        logger.info("I-9: §5.4 expanded with 5 new bilingual-nuance rows")

    # I-10 §1.4 collectivist supplement
    if _I10_INSERT_AFTER in text:
        text = text.replace(_I10_INSERT_AFTER, _I10_INSERT_AFTER + _I10_NEW)
        logger.info("I-10: §1.4 collectivist supplement added")

    # I-12 causal language softening (sweep)
    softened = 0
    for old, new in _CAUSAL_SOFTEN:
        if old in text:
            text = text.replace(old, new)
            softened += 1
    logger.info("I-12: softened %d causal-language phrases", softened)

    # Update §8 versioning footer with v1.1 row
    v1_1_footer = """
- **v1.1** (2026-05-27): 3-way panel integration applied. Fixes: I-1 (numerical/freq recompute flag — see panel matrix), I-2 (§1.X anchor citation corrections, 29 fixes), I-3 ("NOT ad-hoc" softened), I-4 (T-A9/T-A10 flagged as modifiers), I-5 (MC-3 conflation expansion), I-7 (T-V6 grade C → D + regulatory caveat), I-8 (zh-Hant rewrite-required banner on §2), I-9 (§5.4 +5 rows: modal particles / lenticular brackets / TW-HK terminology / simplified-char QA / numerical-unit connotation), I-10 (§1.4 collectivist supplement), I-12 (causal-language softening sweep), I-13 (T-V5 mechanism rewrite: Proof of Work / Complexity Signaling), I-14 (JP-8 reframed as cautionary). Panel artifacts: `docs/research/2026-05-27-codex-thumbnail-playbook-audit.md`, `docs/research/2026-05-27-gemini-thumbnail-playbook-audit.md`, `docs/research/2026-05-27-playbook-3way-panel-integration.md`.
- **v2 backlog** (deferred): I-6 (T-V1/T-V4 merge), I-11 (zh-Hant baseline corpus — 蒼藍鴿/營養師品瑄/Dr.7 + 修修 published), I-15 (Anti-Playbook of low-CTR failures), I-16 (Channel-level Portfolio Strategy in §6.4), I-17 (Dynamic grade feedback loop in §8), I-18 (frequency threshold tighten to ≥5 / ≥3). All require 修修 collaboration (CTR data, native zh-Hant rewriting, new corpus curation)."""

    text = text.replace(
        "- **v1** (2026-05-26): 140 corpus, 4 creators. This file.",
        "- **v1** (2026-05-26): 140 corpus, 4 creators. This file." + v1_1_footer,
        1,
    )
    logger.info("§8 versioning updated with v1.1 + v2-backlog entries")

    _PLAYBOOK.write_text(text, encoding="utf-8")
    logger.info(
        "wrote %s (%d → %d chars, %+d)",
        _PLAYBOOK,
        original_len,
        len(text),
        len(text) - original_len,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
