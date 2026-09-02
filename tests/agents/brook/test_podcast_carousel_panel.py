from __future__ import annotations

import json
from threading import Lock

import pytest

from agents.brook.podcast_carousel_copy import build_transcript_index, generate_copy_spec
from agents.brook.podcast_carousel_panel import PanelResult, run_panel
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


def _finding(finding_id: str) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "severity": "medium",
        "page_id": None,
        "claim": "reviewer found an editorial issue",
        "page_copy_quote": None,
        "evidence_ids": ["B0001"],
        "suggested_change": "revise the unsupported copy",
    }


def _panel_payload_with_reviews(reviews: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "episode_id": "ep120",
        "revision": "r002",
        "status": "converged",
        "reviews": reviews,
        "verified_findings": [],
        "verification_rejections": [],
        "synthesis": {
            "accepted_finding_ids": [],
            "rejected": [],
            "revision_instructions": [],
            "blockers": [],
        },
    }


def test_panel_result_rejects_unreconciled_reviewer_findings():
    reviews = {
        lens: {
            "lens": lens,
            "verdict": "revise",
            "findings": [_finding(f"{lens}-01")],
        }
        for lens in ("ig_audience", "episode_editorial", "brand_evidence")
    }

    with pytest.raises(ValueError, match="reconcile every reviewer finding"):
        PanelResult.model_validate(_panel_payload_with_reviews(reviews))


def test_panel_result_rejects_verification_outcomes_not_present_in_reviews():
    reviews = {
        lens: {"lens": lens, "verdict": "pass", "findings": []}
        for lens in ("ig_audience", "episode_editorial", "brand_evidence")
    }
    payload = _panel_payload_with_reviews(reviews)
    payload["verified_findings"] = [_finding("invented-01")]
    payload["synthesis"] = {
        "accepted_finding_ids": [],
        "rejected": [{"finding_id": "invented-01", "reason": "not actionable"}],
        "revision_instructions": [],
        "blockers": [],
    }

    with pytest.raises(ValueError, match="reconcile every reviewer finding"):
        PanelResult.model_validate(payload)


# --- 修修 2026-09-02 裁決 ---------------------------------------------------
# 「Agent review 審的是 AI 的生成內容，人類 review 之後的成果根本不應該再觸發
# 這個 review。」以下兩條把那句話釘在契約上。


def _high_brand_finding() -> dict[str, object]:
    return {
        "finding_id": "brand-01",
        "severity": "high",
        "page_id": "cover",
        "claim": "「總經理」在逐字稿出現 0 次，封面把它掛在來賓名下。",
        "page_copy_quote": "台灣大哥大 總經理",
        "evidence_ids": ["B0002"],
        "suggested_change": "改用逐字稿撐得起的說法。",
    }


def test_high_brand_finding_can_be_rejected_when_it_targets_an_editor_decision():
    """修修指定的值，lens 不能否決——但 finding 全文照樣留在紀錄裡。"""
    reviews = {
        lens: {"lens": lens, "verdict": "pass", "findings": []}
        for lens in ("ig_audience", "episode_editorial")
    }
    reviews["brand_evidence"] = {
        "lens": "brand_evidence",
        "verdict": "revise",
        "findings": [_high_brand_finding()],
    }
    payload = _panel_payload_with_reviews(reviews)
    payload["verified_findings"] = [_high_brand_finding()]
    payload["synthesis"] = {
        "accepted_finding_ids": [],
        "rejected": [
            {
                "finding_id": "brand-01",
                "reason": "修修在 Review Gate 指定的職稱；出處記在 editorial_direction.md",
                "editor_decision": True,
            }
        ],
        "revision_instructions": [],
        "blockers": [],
    }
    panel = PanelResult.model_validate(payload)
    assert panel.status == "converged"
    # 記錄而非消音：finding 與駁回理由都還在
    assert panel.reviews["brand_evidence"].findings[0].finding_id == "brand-01"
    assert panel.synthesis.rejected[0].editor_decision is True


def test_high_brand_finding_still_cannot_be_rejected_as_agent_judgement():
    """沒有標成編輯裁決時，那道護欄要照擋——它擋掉的是「我覺得沒關係」。"""
    reviews = {
        lens: {"lens": lens, "verdict": "pass", "findings": []}
        for lens in ("ig_audience", "episode_editorial")
    }
    reviews["brand_evidence"] = {
        "lens": "brand_evidence",
        "verdict": "revise",
        "findings": [_high_brand_finding()],
    }
    payload = _panel_payload_with_reviews(reviews)
    payload["verified_findings"] = [_high_brand_finding()]
    payload["synthesis"] = {
        "accepted_finding_ids": [],
        "rejected": [{"finding_id": "brand-01", "reason": "我判斷影響不大"}],
        "revision_instructions": [],
        "blockers": [],
    }
    with pytest.raises(ValueError, match="editor_decision"):
        PanelResult.model_validate(payload)


def test_panel_may_be_inherited_when_the_spec_declares_it(tmp_path):
    """人類只改了自己指定的欄位時，沿用上一版的 panel，不重跑三個 agent。"""
    from agents.brook.podcast_carousel_panel import assert_panel_renderable

    _index, spec = _index_and_spec(tmp_path)
    reviews = {
        lens: {"lens": lens, "verdict": "pass", "findings": []}
        for lens in ("ig_audience", "episode_editorial", "brand_evidence")
    }
    payload = _panel_payload_with_reviews(reviews)
    payload["episode_id"] = spec.episode_id
    payload["revision"] = "r002"
    panel = PanelResult.model_validate(payload)

    inherited = spec.model_copy(update={"revision": "r003", "panel_inherited_from": "r002"})
    assert_panel_renderable(panel, spec=inherited)  # 宣告了就放行

    not_declared = spec.model_copy(update={"revision": "r003"})
    with pytest.raises(ValueError, match="panel revision"):
        assert_panel_renderable(panel, spec=not_declared)

    wrong_source = spec.model_copy(update={"revision": "r003", "panel_inherited_from": "r001"})
    with pytest.raises(ValueError, match="panel revision"):
        assert_panel_renderable(panel, spec=wrong_source)


def test_inherited_panel_may_come_from_earlier_in_the_chain(tmp_path):
    """繼承會成鏈：r004 沿用 r003，而 r003 那份 panel 本身是從 r002 沿用來的。

    沿用時 panel 是原樣複製的，內容仍自報 r002。宣告指向**來源版本**（完成驗收
    也是這樣比對），所以出圖端必須接受鏈上更早的那一版，否則第二次沿用就卡死
    （2026-09-03 實際卡住 r004）。
    """
    from agents.brook.podcast_carousel_panel import assert_panel_renderable

    _index, spec = _index_and_spec(tmp_path)
    reviews = {
        lens: {"lens": lens, "verdict": "pass", "findings": []}
        for lens in ("ig_audience", "episode_editorial", "brand_evidence")
    }

    def _panel(revision: str) -> PanelResult:
        payload = _panel_payload_with_reviews(reviews)
        payload["episode_id"] = spec.episode_id
        payload["revision"] = revision
        return PanelResult.model_validate(payload)

    chained = spec.model_copy(update={"revision": "r004", "panel_inherited_from": "r003"})
    assert_panel_renderable(_panel("r002"), spec=chained)

    # 往後不行——拿比來源還新的 panel 來治理這一版沒有意義
    with pytest.raises(ValueError, match="panel revision"):
        assert_panel_renderable(_panel("r005"), spec=chained)
