from __future__ import annotations

import json

import pytest

from agents.brook.podcast_carousel_copy import build_transcript_index, generate_copy_spec
from shared.schemas.podcast_carousel import EpisodeMetadata


def _write_transcript(tmp_path):
    prose = tmp_path / "transcript_prose.md"
    prose.write_text(
        "**張修修**：為什麼大家只看到成功？\n\n"
        "**鄭國威**：因為演算法會把失敗的作品沉到海平面下面。\n\n"
        "**鄭國威**：所以一致性其實是大量試錯後被看見的結果。\n",
        encoding="utf-8",
    )
    srt = tmp_path / "transcript.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n為什麼大家只看到成功\n\n"
        "2\n00:00:03,000 --> 00:00:07,000\n因為演算法會把失敗的作品沉到海平面下面\n\n"
        "3\n00:00:07,000 --> 00:00:11,000\n所以一致性其實是大量試錯後被看見的結果\n",
        encoding="utf-8",
    )
    return prose, srt


def _valid_response():
    return {
        "episode_topic": "一致性背後的失敗",
        "variant_override_reason": None,
        "pages": [
            {
                "page_id": "cover",
                "role": "cover",
                "headline": "一致性背後藏著大量失敗",
                "emphasis": "大量失敗",
                "cutout": "guest_v5_laughing.png",
                "evidence_ids": ["B0002", "B0003"],
            },
            {
                "page_id": "hook",
                "role": "hook",
                "question": "為什麼你只看到別人成功？",
                "emphasis": "只看到別人成功",
                "bridge": "這集拆開演算法替創作者藏起來的另一面。",
                "evidence_ids": ["B0001", "B0002"],
            },
            {
                "page_id": "point-algorithm",
                "role": "point",
                "headline": "演算法會埋掉失敗作品",
                "emphasis": "埋掉失敗",
                "body": "我們看到的一致，是大量作品被淘汰後留下的結果。",
                "evidence_ids": ["B0002", "B0003"],
            },
            {
                "page_id": "quote",
                "role": "quote",
                "variant": "B",
                "text": "一致性是大量試錯後被看見的結果。",
                "emphasis": "大量試錯",
                "cutout": "guest_v4_excited.png",
                "host_question": "為什麼大家只看到成功？",
                "host_question_evidence_ids": ["B0001"],
                "host_cutout": "host_v2_explaining.png",
                "evidence_ids": ["B0003"],
            },
            {
                "page_id": "cta",
                "role": "cta",
                "emphasis": "失敗",
                "engagement_question": "哪一次失敗讓你改變做法？",
                "evidence_ids": ["B0002", "B0003"],
            },
        ],
    }


def test_build_transcript_index_projects_prose_to_srt_times(tmp_path):
    prose, srt = _write_transcript(tmp_path)
    index = build_transcript_index(prose, srt)
    assert len(index.blocks) == 3
    assert index.blocks[0].speaker == "張修修"
    assert (index.blocks[0].t0, index.blocks[0].t1) == (1.0, 3.0)
    assert (index.blocks[-1].t0, index.blocks[-1].t1) == (7.0, 11.0)
    assert "[B0002 00:00:03.000–00:00:07.000] 鄭國威" in index.prompt_text()


def test_build_transcript_index_fails_closed_on_text_drift(tmp_path):
    prose, srt = _write_transcript(tmp_path)
    prose.write_text("**張修修**：逐字稿裡沒有這句。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fails closed"):
        build_transcript_index(prose, srt)


def test_generate_copy_spec_materialises_evidence_and_even_b_variant(tmp_path):
    prose, srt = _write_transcript(tmp_path)
    index = build_transcript_index(prose, srt)
    seen_prompts = []

    def fake_llm(prompt: str) -> str:
        seen_prompts.append(prompt)
        return json.dumps(_valid_response(), ensure_ascii=False)

    value = generate_copy_spec(
        transcript=index,
        episode_id="ep120",
        episode=EpisodeMetadata(
            number=120,
            topic="待生成",
            guest_name="鄭國威",
            guest_title="泛科學共同創辦人",
        ),
        host="張修修",
        cutouts=["guest_v5_laughing.png", "guest_v4_excited.png", "host_v2_explaining.png"],
        llm_call=fake_llm,
    )
    assert value.episode.topic == "一致性背後的失敗"
    assert value.pages[-2].variant == "B"
    assert value.pages[2].evidence[0].evidence_id == "B0002"
    assert value.pages[2].evidence[0].t0 == 3.0
    assert "沒有 social_brief" in seen_prompts[0]


def test_generate_copy_spec_retries_invalid_model_output(tmp_path):
    prose, srt = _write_transcript(tmp_path)
    index = build_transcript_index(prose, srt)
    invalid = _valid_response()
    invalid["pages"][1]["emphasis"] = "逐字不存在"
    replies = iter(
        [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(_valid_response(), ensure_ascii=False),
        ]
    )
    prompts = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    value = generate_copy_spec(
        transcript=index,
        episode_id="ep120",
        episode=EpisodeMetadata(
            number=120,
            topic="待生成",
            guest_name="鄭國威",
            guest_title="泛科學共同創辦人",
        ),
        host="張修修",
        cutouts=["guest_v5_laughing.png", "guest_v4_excited.png", "host_v2_explaining.png"],
        llm_call=fake_llm,
    )
    assert value.pages[1].emphasis == "只看到別人成功"
    assert len(prompts) == 2
    assert "前次錯誤" in prompts[1]
