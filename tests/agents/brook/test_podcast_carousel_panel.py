from __future__ import annotations

import json
from threading import Lock

import pytest

from agents.brook.podcast_carousel_copy import build_transcript_index, generate_copy_spec
from agents.brook.podcast_carousel_panel import run_panel
from shared.schemas.podcast_carousel import EpisodeMetadata


def _index_and_spec(tmp_path):
    prose = tmp_path / "transcript_prose.md"
    prose.write_text(
        "**張修修**：為什麼大家只看到成功？\n\n"
        "**鄭國威**：演算法會把失敗作品沉下去，所以大家只看到成功。\n",
        encoding="utf-8",
    )
    srt = tmp_path / "transcript.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n為什麼大家只看到成功\n\n"
        "2\n00:00:03,000 --> 00:00:08,000\n"
        "演算法會把失敗作品沉下去所以大家只看到成功\n",
        encoding="utf-8",
    )
    index = build_transcript_index(prose, srt)
    draft = {
        "episode_topic": "演算法藏起來的失敗",
        "variant_override_reason": None,
        "pages": [
            {
                "page_id": "cover",
                "role": "cover",
                "headline": "演算法藏起來的失敗",
                "emphasis": "藏起來的失敗",
                "cutout": "guest.png",
                "evidence_ids": ["B0002"],
            },
            {
                "page_id": "hook",
                "role": "hook",
                "question": "為什麼你只看到別人成功？",
                "emphasis": "只看到別人成功",
                "bridge": "這集拆解看不見的淘汰。",
                "evidence_ids": ["B0001", "B0002"],
            },
            {
                "page_id": "point-algorithm",
                "role": "point",
                "headline": "演算法會埋掉失敗作品",
                "emphasis": "埋掉失敗",
                "body": "大家自然只看得到成功的一面。",
                "evidence_ids": ["B0002"],
            },
            {
                "page_id": "quote",
                "role": "quote",
                "variant": "B",
                "text": "大家只看到成功。",
                "emphasis": "只看到成功",
                "cutout": "guest.png",
                "host_question": "為什麼大家只看到成功？",
                "host_question_evidence_ids": ["B0001"],
                "host_cutout": "host.png",
                "evidence_ids": ["B0002"],
            },
            {
                "page_id": "cta",
                "role": "cta",
                "emphasis": "失敗",
                "engagement_question": "哪次失敗改變了你？",
                "evidence_ids": ["B0002"],
            },
        ],
    }
    spec = generate_copy_spec(
        transcript=index,
        episode_id="ep120",
        episode=EpisodeMetadata(
            number=120,
            topic="待生成",
            guest_name="鄭國威",
            guest_title="泛科學共同創辦人",
        ),
        host="張修修",
        cutouts=["guest.png", "host.png"],
        llm_call=lambda _prompt: json.dumps(draft, ensure_ascii=False),
    )
    return index, spec


def test_panel_runs_three_independent_lenses_and_verifies_findings(tmp_path):
    index, spec = _index_and_spec(tmp_path)
    prompts = {}
    lock = Lock()
    replies = {
        "ig_audience": {
            "lens": "ig_audience",
            "verdict": "revise",
            "findings": [
                {
                    "finding_id": "ig-01",
                    "severity": "medium",
                    "page_id": "hook",
                    "claim": "問題有吸引力，但 bridge 太抽象。",
                    "page_copy_quote": "這集拆解看不見的淘汰。",
                    "evidence_ids": ["B0002"],
                    "suggested_change": "把 payoff 寫得更具體。",
                }
            ],
        },
        "episode_editorial": {
            "lens": "episode_editorial",
            "verdict": "revise",
            "findings": [
                {
                    "finding_id": "episode-01",
                    "severity": "low",
                    "page_id": None,
                    "claim": "可以補強失敗如何形成一致性的關係。",
                    "page_copy_quote": None,
                    "evidence_ids": ["B0002"],
                    "suggested_change": "增加因果橋接。",
                }
            ],
        },
        "brand_evidence": {
            "lens": "brand_evidence",
            "verdict": "revise",
            "findings": [
                {
                    "finding_id": "brand-01",
                    "severity": "high",
                    "page_id": "point-algorithm",
                    "claim": "引用字串並不存在，應作廢。",
                    "page_copy_quote": "這句 Copy Spec 沒有",
                    "evidence_ids": ["B0002"],
                    "suggested_change": "不要採用。",
                }
            ],
        },
    }

    def reviewer(lens, prompt):
        with lock:
            prompts[lens] = prompt
        return json.dumps(replies[lens], ensure_ascii=False)

    synthesis_seen = {}

    def synthesize(prompt):
        synthesis_seen["prompt"] = prompt
        return json.dumps(
            {
                "accepted_finding_ids": ["ig-01", "episode-01"],
                "rejected": [],
                "revision_instructions": ["具體化 Hook payoff，補上因果橋接。"],
                "blockers": [],
            },
            ensure_ascii=False,
        )

    result = run_panel(
        spec=spec,
        transcript=index,
        reviewer_call=reviewer,
        synthesis_call=synthesize,
    )
    assert set(prompts) == {"ig_audience", "episode_editorial", "brand_evidence"}
    assert "ig-01" not in prompts["episode_editorial"]
    assert [value.finding_id for value in result.verified_findings] == ["ig-01", "episode-01"]
    assert result.verification_rejections[0].finding_id == "brand-01"
    assert "brand-01" not in synthesis_seen["prompt"]


def test_panel_skips_synthesis_when_all_lenses_pass(tmp_path):
    index, spec = _index_and_spec(tmp_path)

    def reviewer(lens, _prompt):
        return json.dumps({"lens": lens, "verdict": "pass", "findings": []})

    result = run_panel(
        spec=spec,
        transcript=index,
        reviewer_call=reviewer,
        synthesis_call=lambda _prompt: pytest.fail("synthesis should not run"),
    )
    assert result.verified_findings == []
    assert result.synthesis.revision_instructions == []


def test_synthesis_cannot_reject_verified_high_brand_finding(tmp_path):
    index, spec = _index_and_spec(tmp_path)

    def reviewer(lens, _prompt):
        if lens != "brand_evidence":
            return json.dumps({"lens": lens, "verdict": "pass", "findings": []})
        return json.dumps(
            {
                "lens": lens,
                "verdict": "revise",
                "findings": [
                    {
                        "finding_id": "brand-high",
                        "severity": "high",
                        "page_id": "quote",
                        "claim": "金句把演算法改成了觀眾。",
                        "page_copy_quote": "大家只看到成功。",
                        "evidence_ids": ["B0002"],
                        "suggested_change": "保持原本因果主詞。",
                    }
                ],
            },
            ensure_ascii=False,
        )

    def synthesize(_prompt):
        return json.dumps(
            {
                "accepted_finding_ids": [],
                "rejected": [{"finding_id": "brand-high", "reason": "不想改"}],
                "revision_instructions": [],
                "blockers": [],
            },
            ensure_ascii=False,
        )

    with pytest.raises(ValueError, match="cannot be rejected"):
        run_panel(
            spec=spec,
            transcript=index,
            reviewer_call=reviewer,
            synthesis_call=synthesize,
        )
