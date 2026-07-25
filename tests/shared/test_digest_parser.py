# ruff: noqa: E501  # markdown fixture strings include CJK + long DOI URLs
"""Tests for shared.digest_parser — PubMed + AI digest schema parsing."""

from __future__ import annotations

from shared.digest_parser import (
    AI_DIM_LABELS,
    PUBMED_DIM_LABELS,
    dim_label,
    parse_ai_digest,
    parse_pubmed_digest,
)

PUBMED_SAMPLE = """
# PubMed 每日精選 — 2026-05-26

> 今日精選以腸道微生物與老化、神經健康關聯的研究為主軸。

**候選總數**：35　**入選**：12　**Editor's picks**：5

## ⭐ Editor's Picks

### 1. Microbiome functional gene pathways and cognitive performance

- **Journal**: Gut microbes (Q1 · SJR 4.109)
- **Domain**: `longevity`
- **Score**: 3.6  (R4/I4/C3/A2/F4/N4)
- **Verdict**: 特定腸道菌代謝路徑的活躍程度，與認知能力下降有關。
- **Why**: 這篇研究超越了單純討論「某菌種」多寡。
- **全文**: ⚠️ 非 OA — [DOI: 10.1080/19490976.2026.2676162](https://doi.org/10.1080/19490976.2026.2676162) (需手動取得)
- **→** [[pubmed-42178714]] · [PubMed](https://pubmed.ncbi.nlm.nih.gov/42178714/)

### 2. Second editor pick title

- **Journal**: Nature (Q1 · SJR 18.500)
- **Domain**: `nutrition`
- **Score**: 3.5  (R5/I4/C3/A3/F3/N3)
- **Verdict**: 第二個 pick verdict。
- **Why**: 第二個 pick 的 why 解釋。
- **全文**: 🌐 網頁全文（doi.org）— [[KB/Attachments/12345.md|本機 markdown]]
- **→** [[pubmed-12345678]] · [PubMed](https://pubmed.ncbi.nlm.nih.gov/12345678/)

## 其他精選

### 3. Non-pick entry title

- **Journal**: PLoS One (Q2 · SJR 0.500)
- **Domain**: `exercise`
- **Score**: 2.8  (R3/I3/C2/A3/F2/N3)
- **Verdict**: 一個沒被選為 editor pick 的條目。
- **Why**: 也有 why 段。
- **全文**: ⚠️ 非 OA — [DOI: 10.0000/xxx](https://doi.org/10.0000/xxx) (需手動取得)
- **→** [[pubmed-99999999]] · [PubMed](https://pubmed.ncbi.nlm.nih.gov/99999999/)
"""


AI_SAMPLE = """
# AI 每日情報 — 2026-05-26

> 今日以產業動態為主。

**候選總數**：13　**精選**：1

---

## 1. Harness, Scaffold, and the AI Agent Terms Worth Getting Right

- **Publisher**: Hugging Face
- **Category**: `agent_framework`
- **Published**: 2026-05-25T00:00:00+00:00 (22.5h ago)
- **Score**: 3.53 (5-dim) / 3.68 (4-dim)  (S3/N3/A4/Q5/R3)
- **Verdict**: HF 釋出 AI agent 術語標準化指南。
- **Why**: 對多 agent 系統架構有參考價值。
- **Key**: HF 正式發布 agent 領域術語標準。
- **Noise note**: 無明顯炒作
- **→** [https://huggingface.co/blog/agent-glossary](https://huggingface.co/blog/agent-glossary)

"""


class TestParsePubmedDigest:
    def test_returns_three_entries(self):
        studies = parse_pubmed_digest(PUBMED_SAMPLE)
        assert len(studies) == 3

    def test_editor_pick_flag(self):
        studies = parse_pubmed_digest(PUBMED_SAMPLE)
        by_idx = {s.idx: s for s in studies}
        assert by_idx[1].is_editor_pick is True
        assert by_idx[2].is_editor_pick is True
        assert by_idx[3].is_editor_pick is False

    def test_pick_sort_order(self):
        # Picks first then others; idx order within each
        studies = parse_pubmed_digest(PUBMED_SAMPLE)
        assert [s.idx for s in studies] == [1, 2, 3]
        assert [s.is_editor_pick for s in studies] == [True, True, False]

    def test_journal_meta_parsed(self):
        s = parse_pubmed_digest(PUBMED_SAMPLE)[0]
        assert s.journal_name == "Gut microbes"
        assert s.journal_quartile == "Q1"
        assert s.journal_sjr == 4.109

    def test_domain_score_and_breakdown(self):
        s = parse_pubmed_digest(PUBMED_SAMPLE)[0]
        assert s.domain == "longevity"
        assert s.score == 3.6
        assert s.score_breakdown == "R4/I4/C3/A2/F4/N4"
        assert s.score_dims == (
            ("R", 4),
            ("I", 4),
            ("C", 3),
            ("A", 2),
            ("F", 4),
            ("N", 4),
        )

    def test_verdict_and_why(self):
        s = parse_pubmed_digest(PUBMED_SAMPLE)[0]
        assert "認知能力下降" in s.verdict
        assert "某菌種" in s.why

    def test_full_text_link(self):
        s = parse_pubmed_digest(PUBMED_SAMPLE)[0]
        assert s.full_text_url == "https://doi.org/10.1080/19490976.2026.2676162"
        assert "非 OA" in s.full_text_label

    def test_kb_wikilink_and_pubmed_url(self):
        s = parse_pubmed_digest(PUBMED_SAMPLE)[0]
        assert s.kb_wikilink == "pubmed-42178714"
        assert s.external_id == "42178714"
        assert s.external_url == "https://pubmed.ncbi.nlm.nih.gov/42178714/"

    def test_title_extracted_cleanly(self):
        studies = parse_pubmed_digest(PUBMED_SAMPLE)
        assert studies[0].title.startswith("Microbiome functional gene pathways")
        assert "###" not in studies[0].title

    def test_empty_body_returns_empty(self):
        assert parse_pubmed_digest("") == []

    def test_only_header_returns_empty(self):
        assert parse_pubmed_digest("# Title only\n\n no entries\n") == []


class TestParseAiDigest:
    def test_returns_one_entry(self):
        studies = parse_ai_digest(AI_SAMPLE)
        assert len(studies) == 1

    def test_publisher_and_category(self):
        s = parse_ai_digest(AI_SAMPLE)[0]
        assert s.publisher == "Hugging Face"
        assert s.category == "agent_framework"

    def test_published_at_and_age(self):
        s = parse_ai_digest(AI_SAMPLE)[0]
        assert s.published_at == "2026-05-25T00:00:00+00:00"
        assert s.published_age == "22.5h ago"

    def test_dual_score_parsed(self):
        s = parse_ai_digest(AI_SAMPLE)[0]
        assert s.score == 3.53
        assert s.score_secondary == 3.68
        assert s.score_breakdown == "S3/N3/A4/Q5/R3"
        assert s.score_dims == (
            ("S", 3),
            ("N", 3),
            ("A", 4),
            ("Q", 5),
            ("R", 3),
        )

    def test_key_and_noise_note(self):
        s = parse_ai_digest(AI_SAMPLE)[0]
        assert "agent 領域術語標準" in s.key_point
        assert s.noise_note == "無明顯炒作"

    def test_external_url(self):
        s = parse_ai_digest(AI_SAMPLE)[0]
        assert s.external_url == "https://huggingface.co/blog/agent-glossary"

    def test_no_pubmed_fields_on_ai_entry(self):
        s = parse_ai_digest(AI_SAMPLE)[0]
        assert s.is_editor_pick is False
        assert s.journal_name is None
        assert s.journal_quartile is None
        assert s.kb_wikilink is None

    def test_empty_body_returns_empty(self):
        assert parse_ai_digest("") == []


class TestDimLabel:
    def test_pubmed_dim_label(self):
        assert "Rigor" in dim_label("pubmed", "R")
        assert "Impact" in dim_label("pubmed", "I")
        # C/A/F must match the real rubric (prompts/robin/pubmed_digest/score.md),
        # not the old clarity/audience/freshness mislabels.
        assert "Clinical Relevance" in dim_label("pubmed", "C")
        assert "Actionability" in dim_label("pubmed", "A")
        assert "Red Flags" in dim_label("pubmed", "F")
        assert "Novelty" in dim_label("pubmed", "N")

    def test_ai_dim_label(self):
        assert "Signal" in dim_label("ai", "S")
        assert "Novelty" in dim_label("ai", "N")

    def test_unknown_dim_returns_code(self):
        assert dim_label("pubmed", "Z") == "Z"

    def test_label_tables_cover_canonical_dims(self):
        assert set(PUBMED_DIM_LABELS) == set("RICAFN")
        assert set(AI_DIM_LABELS) == set("SNAQR")


class TestPubmedRefFormats:
    """The `- **→**` reference line has two historical shapes; both must yield
    a usable PMID + PubMed URL (regression: ADR-042 dropped the wikilink and
    the old regex silently stopped matching, so links vanished on real data)."""

    _ADR042 = """
### 1. Skin-innervating glutamatergic neurons modulate aging.

- **Journal**: Cell (Q1 · SJR 24.592)
- **Domain**: `longevity`
- **Score**: 3.6  (R4/I4/C2/A2/F4/N5)
- **Verdict**: 皮膚神經元調節膠原蛋白生成。
- **Why**: 開創神經-皮膚抗老新領域。
- **→** [PubMed](https://pubmed.ncbi.nlm.nih.gov/42468523/)
"""

    _LEGACY = """
### 1. Old format study

- **Journal**: NEJM (Q1 · SJR 18.500)
- **Domain**: `metabolic`
- **Score**: 3.6  (R4/I4/C3/A2/F4/N4)
- **Verdict**: v
- **Why**: w
- **→** [[pubmed-42174253]] · [PubMed](https://pubmed.ncbi.nlm.nih.gov/42174253/)
"""

    def test_adr042_format_yields_pmid_and_url(self):
        s = parse_pubmed_digest(self._ADR042)[0]
        assert s.external_id == "42468523"
        assert s.external_url == "https://pubmed.ncbi.nlm.nih.gov/42468523/"
        assert s.kb_wikilink is None  # no wikilink in the new format

    def test_legacy_format_still_parses(self):
        s = parse_pubmed_digest(self._LEGACY)[0]
        assert s.external_id == "42174253"
        assert s.external_url == "https://pubmed.ncbi.nlm.nih.gov/42174253/"
        assert s.kb_wikilink == "pubmed-42174253"


class TestRealDigestFixtures:
    """Smoke test against the actual vault digests — confirms the parser
    handles the real schema, not just the hand-built sample above.

    Skipped if the vault isn't present (CI / other devs).
    """

    def _vault_digest(self, type_: str, date_: str):
        from pathlib import Path

        p = Path("E:/Shosho LifeOS/KB/Wiki/Digests")
        f = p / type_ / f"{date_}.md"
        if not f.exists():
            import pytest

            pytest.skip(f"vault digest fixture not present: {f}")
        raw = f.read_text(encoding="utf-8")
        # strip frontmatter
        parts = raw.split("---", 2)
        return parts[2] if len(parts) >= 3 else raw

    def test_real_pubmed_parses_multiple_studies(self):
        body = self._vault_digest("PubMed", "2026-05-26")
        studies = parse_pubmed_digest(body)
        assert len(studies) >= 8
        assert any(s.is_editor_pick for s in studies)
        assert any(s.journal_quartile == "Q1" for s in studies)

    def test_real_ai_parses_at_least_one(self):
        body = self._vault_digest("AI", "2026-05-26")
        studies = parse_ai_digest(body)
        assert len(studies) >= 1
        assert studies[0].publisher
