from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.schemas.podcast_carousel import (
    CAROUSEL_DISPLAY_COPY_FIELDS,
    ArtifactReceipt,
    CarouselCopyEdit,
    CarouselCoverLayoutEdit,
    CarouselEditorApplyRequest,
    CarouselFeedbackRevision,
    CarouselPageDecision,
    CarouselReviewFeedbackV1,
    CarouselReviewManifestV1,
    CarouselReviewPage,
    CarouselTextLayoutEdit,
    CoverLayoutOverride,
    CoverPage,
    CTAPage,
    EpisodeMetadata,
    HookPage,
    PageFitDiagnostic,
    PodcastCarouselCopySpecV1,
    PointPage,
    QuotePage,
    TemplateSnapshot,
    TextLayoutOverrideV1,
    TranscriptEvidence,
    receipt_for,
)

SHA = "a" * 64


def test_display_copy_fields_are_allowlisted_in_visual_reading_order():
    assert CAROUSEL_DISPLAY_COPY_FIELDS == {
        "cover": ("headline", "emphasis", "guest_name", "guest_title"),
        "hook": ("question", "emphasis", "bridge"),
        "point": ("headline", "emphasis", "body"),
        "quote": ("host_question", "text", "emphasis", "guest_name"),
        "cta": ("episode_topic", "emphasis"),
    }


def evidence(evidence_id: str = "ev-1", *, speaker: str = "鄭國威") -> TranscriptEvidence:
    return TranscriptEvidence(
        evidence_id=evidence_id,
        source_path="transcript_prose.md",
        source_sha256=SHA,
        speaker=speaker,
        text="演算法會讓表現不好的影片沉到海平面下",
        t0=100.0,
        t1=112.0,
        line_start=79,
        line_end=79,
    )


def pages(*, quote_variant: str = "B"):
    ev = [evidence()]
    result = [
        CoverPage(
            page_id="cover",
            headline="演算法也會幫你藏起失敗",
            emphasis="藏起失敗",
            guest_name="鄭國威",
            guest_title="泛科學共同創辦人",
            cutout="guest_v5_laughing.png",
            evidence=ev,
        ),
        HookPage(
            page_id="hook",
            question="為什麼你只看見別人成功？",
            emphasis="只看見別人成功",
            bridge="這集拆解內容創作看不見的淘汰機制。",
            evidence=ev,
        ),
        PointPage(
            page_id="point-algorithm",
            headline="演算法會埋掉失敗作品",
            emphasis="埋掉失敗",
            body="觀眾自然只看得到表現比較好的那一面。",
            evidence=ev,
        ),
    ]
    quote_kwargs = {
        "page_id": "quote",
        "variant": quote_variant,
        "text": "大家只會看到你做得還 OK 的那一面。",
        "emphasis": "做得還 OK",
        "guest_name": "鄭國威",
        "guest_cutout": "guest_v4_excited.png",
        "evidence": ev,
    }
    if quote_variant == "B":
        quote_kwargs.update(
            {
                "host_question": "你們怎麼一直保持一致？",
                "host_question_evidence": [evidence("ev-host", speaker="張修修")],
                "host_cutout": "host_v2_explaining.png",
            }
        )
    result.extend(
        [
            QuotePage(**quote_kwargs),
            CTAPage(
                page_id="cta",
                episode_topic="創作者看不見的失敗",
                emphasis="看不見的失敗",
                evidence=ev,
            ),
        ]
    )
    return result


def spec(*, episode_number: int = 120, quote_variant: str = "B"):
    return PodcastCarouselCopySpecV1(
        episode_id="20260721-zheng-guowei",
        revision="r001",
        episode=EpisodeMetadata(
            number=episode_number,
            topic="內容創作的失敗與一致性",
            guest_name="鄭國威",
            guest_title="泛科學共同創辦人",
        ),
        pages=pages(quote_variant=quote_variant),
        publish_compatibility="api_compatible",
    )


def test_copy_spec_rejects_rehook_in_v1():
    payload = spec().model_dump(mode="json")
    payload["pages"].insert(
        -2,
        {
            "page_id": "rehook-trust",
            "role": "re_hook",
            "question": "但做得穩，真的只靠演算法嗎？",
            "emphasis": "只靠演算法",
            "bridge": "接下來看長期信任如何累積。",
            "evidence": [evidence().model_dump(mode="json")],
        },
    )
    with pytest.raises(ValidationError, match="re_hook"):
        PodcastCarouselCopySpecV1.model_validate(payload)


def test_emphasis_must_be_exact_substring():
    with pytest.raises(ValidationError, match="exact substring"):
        HookPage(
            page_id="hook",
            question="這是一個問題嗎？",
            emphasis="不存在",
            bridge="繼續看。",
            evidence=[evidence()],
        )


def test_manual_breaks_are_layout_lines_and_cannot_split_emphasis():
    source = spec()
    payload = source.model_dump(mode="json")
    question = source.pages[1].question
    emphasis = source.pages[1].emphasis
    emphasis_at = question.index(emphasis)
    payload["layout_overrides"]["text_regions"] = [
        {
            "page_id": "hook",
            "role": "hook",
            "region": "question",
            "values": {
                "x_px": 64,
                "y_px": 240,
                "width_px": 880,
                "font_start_px": 104,
                "lines": [question[:emphasis_at], question[emphasis_at:]],
            },
        }
    ]
    parsed = PodcastCarouselCopySpecV1.model_validate(payload)
    assert parsed.layout_overrides.text_regions[0].values.lines
    split_at = emphasis_at + max(1, len(emphasis) // 2)
    payload["layout_overrides"]["text_regions"][0]["values"]["lines"] = [
        question[:split_at],
        question[split_at:],
    ]
    with pytest.raises(ValidationError, match="split emphasis"):
        PodcastCarouselCopySpecV1.model_validate(payload)


def test_text_layout_contract_is_allowlisted_snapped_and_inside_safe_area():
    edit = CarouselTextLayoutEdit(
        page_id="hook",
        role="hook",
        region="bridge",
        artifact_sha256=SHA,
        values=TextLayoutOverrideV1(x_px=64, y_px=720, width_px=880, font_start_px=34),
    )
    assert edit.values.model_dump() == {
        "x_px": 64,
        "y_px": 720,
        "width_px": 880,
        "font_start_px": 34,
        "lines": None,
    }
    with pytest.raises(ValidationError, match="4px grid"):
        TextLayoutOverrideV1(x_px=65, y_px=720, width_px=880, font_start_px=34)
    with pytest.raises(ValidationError, match="2px step"):
        TextLayoutOverrideV1(x_px=64, y_px=720, width_px=880, font_start_px=35)
    with pytest.raises(ValidationError, match="safe rect"):
        CarouselTextLayoutEdit(
            page_id="hook",
            role="hook",
            region="bridge",
            artifact_sha256=SHA,
            values=TextLayoutOverrideV1(x_px=160, y_px=720, width_px=960, font_start_px=34),
        )


def test_cover_official_baseline_fits_safe_rect_and_repeated_emphasis_keeps_first_occurrence():
    baseline = CarouselTextLayoutEdit(
        page_id="cover",
        role="cover",
        region="headline",
        artifact_sha256=SHA,
        values=TextLayoutOverrideV1(x_px=64, y_px=168, width_px=976, font_start_px=106),
    )
    assert baseline.values.width_px == 976

    source = spec()
    payload = source.model_dump(mode="json")
    payload["pages"][0]["headline"] = "foofoo"
    payload["pages"][0]["emphasis"] = "foo"
    payload["layout_overrides"]["text_regions"] = [
        {
            "page_id": "cover",
            "role": "cover",
            "region": "headline",
            "values": {
                "x_px": 64,
                "y_px": 168,
                "width_px": 976,
                "font_start_px": 106,
                "lines": ["fo", "ofoo"],
            },
        }
    ]
    with pytest.raises(ValidationError, match="split emphasis"):
        PodcastCarouselCopySpecV1.model_validate(payload)
    with pytest.raises(ValidationError, match="not editable"):
        CarouselTextLayoutEdit(
            page_id="cta",
            role="cta",
            region="body",
            artifact_sha256=SHA,
            values=TextLayoutOverrideV1(x_px=64, y_px=720, width_px=880, font_start_px=34),
        )


def test_point_emphasis_must_be_in_headline_not_body():
    with pytest.raises(ValidationError, match="point emphasis must be in headline"):
        PointPage(
            page_id="point-format",
            headline="每種情境需要不同格式",
            emphasis="擴圈",
            body="短影音負責擴圈。",
            evidence=[evidence()],
        )


def test_cta_v1_has_no_engagement_line():
    with pytest.raises(ValidationError, match="engagement_question"):
        CTAPage.model_validate(
            {
                "page_id": "cta",
                "episode_topic": "完整收聽泛科學的內容生存策略",
                "emphasis": "內容生存策略",
                "engagement_question": "你要先改哪一項？",
                "evidence": [evidence().model_dump(mode="json")],
            }
        )


def test_even_episode_requires_b_by_default_but_allows_reasoned_fallback():
    with pytest.raises(ValidationError, match="variant_override_reason"):
        spec(quote_variant="A")

    value = PodcastCarouselCopySpecV1(
        episode_id="ep120",
        revision="r001",
        episode=EpisodeMetadata(
            number=120,
            topic="測試",
            guest_name="鄭國威",
            guest_title="來賓",
        ),
        pages=pages(quote_variant="A"),
        publish_compatibility="api_compatible",
        variant_override_reason="找不到可靠配對的主持人問題，降級 A",
    )
    assert value.pages[-2].variant == "A"


def test_quote_b_requires_host_question_evidence_and_cutout():
    with pytest.raises(ValidationError, match="variant B requires"):
        QuotePage(
            page_id="quote",
            variant="B",
            text="這是來賓回答。",
            emphasis="來賓回答",
            guest_name="鄭國威",
            guest_cutout="guest.png",
            evidence=[evidence()],
        )


def test_non_contiguous_evidence_cannot_be_a_quote():
    ev = evidence()
    ev.contiguous = False
    with pytest.raises(ValidationError, match="contiguous evidence"):
        QuotePage(
            page_id="quote",
            variant="A",
            text="這是來賓回答。",
            emphasis="來賓回答",
            guest_name="鄭國威",
            guest_cutout="guest.png",
            evidence=[ev],
        )


def test_publish_compatibility_is_derived_from_page_count():
    long_pages = pages()
    for index in range(7):
        long_pages.insert(
            -2,
            PointPage(
                page_id=f"point-extra-{index}",
                headline=f"額外重點 {index}",
                emphasis="額外重點",
                body="內容",
                evidence=[evidence(f"ev-extra-{index}")],
            ),
        )
    assert len(long_pages) == 12
    with pytest.raises(ValidationError, match="manual_only"):
        PodcastCarouselCopySpecV1(
            episode_id="ep120",
            revision="r001",
            episode=EpisodeMetadata(
                number=120,
                topic="測試",
                guest_name="鄭國威",
                guest_title="來賓",
            ),
            pages=long_pages,
            publish_compatibility="api_compatible",
        )


def test_review_manifest_requires_contiguous_page_numbers():
    copy_spec = spec()
    receipt = ArtifactReceipt(path="C:/tmp/a.json", bytes=10, sha256=SHA)
    review_pages = []
    for index, page in enumerate(copy_spec.pages, start=1):
        review_pages.append(
            CarouselReviewPage(
                page_id=page.page_id,
                page_number=index,
                role=page.role,
                content_sha256=SHA,
                image=ArtifactReceipt(path=f"C:/tmp/{index}.png", bytes=10, sha256=SHA),
                fit=PageFitDiagnostic(status="fit"),
                copy_page=page,
            )
        )
    review_pages[1].page_number = 9
    with pytest.raises(ValidationError, match="contiguous"):
        CarouselReviewManifestV1(
            episode_id="ep120",
            revision="r001",
            copy_spec=receipt,
            render_input=receipt,
            template=TemplateSnapshot(root="C:/tmp/template", sha256=SHA),
            publish_compatibility="api_compatible",
            pages=review_pages,
        )


def test_review_manifest_round_trips_optional_render_input_receipt():
    copy_spec = spec()
    receipt = ArtifactReceipt(path="C:/tmp/copy.json", bytes=10, sha256=SHA)
    render_input = ArtifactReceipt(path="C:/tmp/render-input.json", bytes=20, sha256="b" * 64)
    review_pages = [
        CarouselReviewPage(
            page_id=page.page_id,
            page_number=index,
            role=page.role,
            content_sha256=SHA,
            image=ArtifactReceipt(path=f"C:/tmp/{index}.png", bytes=10, sha256=SHA),
            fit=PageFitDiagnostic(status="fit"),
            copy_page=page,
        )
        for index, page in enumerate(copy_spec.pages, start=1)
    ]
    manifest = CarouselReviewManifestV1(
        episode_id="ep120",
        revision="r026",
        copy_spec=receipt,
        render_input=render_input,
        template=TemplateSnapshot(root="C:/tmp/template", sha256=SHA),
        publish_compatibility="api_compatible",
        pages=review_pages,
    )

    restored = CarouselReviewManifestV1.model_validate_json(manifest.model_dump_json())
    assert restored.render_input == render_input


def test_approved_feedback_requires_all_pages_approved():
    decisions = [
        CarouselPageDecision(page_id="cover", status="approved", artifact_sha256=SHA),
        CarouselPageDecision(
            page_id="hook",
            status="needs_changes",
            feedback="縮短",
            artifact_sha256=SHA,
        ),
    ]
    with pytest.raises(ValidationError, match="every page"):
        CarouselFeedbackRevision(
            revision_number=1,
            created_at=datetime.now(UTC),
            carousel_revision="r001",
            manifest_sha256=SHA,
            decision="approved",
            pages=decisions,
        )


def test_feedback_root_defaults_to_empty_revision_list():
    value = CarouselReviewFeedbackV1(episode_id="ep120")
    assert value.revisions == []


def test_structured_editor_rejects_identity_evidence_and_empty_copy_changes():
    base = {
        "page_id": "cover",
        "role": "cover",
        "artifact_sha256": SHA,
    }
    # 選哪一張去背照是編輯決定（修修 2026-09-02），現在可編輯——
    # 但值必須是 packaging/cutouts 裡的單純檔名，否則 `cutouts_dir / name`
    # 會被路徑穿越。
    assert CarouselCopyEdit(**base, fields={"cutout": "guest_v5_explaining.png"})
    for traversal in ("../../secret.png", "sub/dir.png", "x.svg"):
        with pytest.raises(ValidationError, match="bare cutout filename"):
            CarouselCopyEdit(**base, fields={"cutout": traversal})
    with pytest.raises(ValidationError, match="not editable"):
        CarouselCopyEdit(**base, fields={"evidence": "changed"})
    with pytest.raises(ValidationError, match="cannot be empty"):
        CarouselCopyEdit(**base, fields={"headline": "  "})


def test_structured_editor_layout_bounds_and_payload_roundtrip():
    edit = CarouselEditorApplyRequest(
        manifest_sha256=SHA,
        copy_edits=[
            CarouselCopyEdit(
                page_id="point-algorithm",
                role="point",
                artifact_sha256=SHA,
                fields={"headline": "新標題", "emphasis": "新標題"},
            )
        ],
        layout_overrides=CarouselCoverLayoutEdit(
            artifact_sha256=SHA,
            values=CoverLayoutOverride(
                guest_right_px=-180,
                guest_bottom_px=-80,
                guest_height_px=980,
                title_font_size_px=112,
            ),
        ),
    )
    assert CarouselEditorApplyRequest.model_validate_json(edit.model_dump_json()) == edit
    with pytest.raises(ValidationError, match="less than or equal to 1400"):
        CoverLayoutOverride(guest_height_px=1401)
    with pytest.raises(ValidationError, match="at least one structured"):
        CarouselEditorApplyRequest(manifest_sha256=SHA)


@pytest.mark.parametrize(
    ("role", "fields"),
    [
        ("cover", {"headline": "封面", "emphasis": "封面"}),
        ("hook", {"question": "問題？", "bridge": "承接"}),
        ("point", {"headline": "重點", "body": "內文"}),
        ("quote", {"text": "引言", "guest_name": "來賓"}),
        ("cta", {"episode_topic": "主題", "emphasis": "主題"}),
    ],
)
def test_each_role_display_copy_patch_roundtrips(role: str, fields: dict[str, str]):
    edit = CarouselCopyEdit(
        page_id=f"{role}-page",
        role=role,
        artifact_sha256=SHA,
        fields=fields,
    )
    assert CarouselCopyEdit.model_validate_json(edit.model_dump_json()) == edit


def test_every_registry_display_field_rejects_crlf_without_layout_override():
    source = spec()
    page_indexes = {page.role: index for index, page in enumerate(source.pages)}
    for role, fields in CAROUSEL_DISPLAY_COPY_FIELDS.items():
        for field in fields:
            payload = source.model_dump(mode="json")
            page = payload["pages"][page_indexes[role]]
            if page.get(field) is None:
                continue
            page[field] = f"{page[field]}\nforbidden"
            with pytest.raises(ValidationError, match="CR/LF"):
                PodcastCarouselCopySpecV1.model_validate(payload)


def test_copy_edit_rejects_crlf_before_prospective_spec_validation():
    with pytest.raises(ValidationError, match="CR/LF"):
        CarouselCopyEdit(
            page_id="cover",
            role="cover",
            artifact_sha256=SHA,
            fields={"guest_title": "title\r\nsecond line"},
        )


def test_receipt_for_hashes_file(tmp_path):
    path = tmp_path / "artifact.txt"
    path.write_text("carousel", encoding="utf-8")
    receipt = receipt_for(path)
    assert receipt.bytes == len("carousel")
    assert receipt.path == str(path.resolve())
    assert len(receipt.sha256) == 64


def test_quote_guest_geometry_has_no_default_and_is_bounded():
    """A 版與 B 版的算圖預設不同（A: -50/-10/440、B: -18/-20/430）。

    寫一組 default 必然對其中一版說謊，所以三個值都必須明講。
    """
    from shared.schemas.podcast_carousel import GuestLayoutOverride

    with pytest.raises(ValidationError):
        GuestLayoutOverride()
    with pytest.raises(ValidationError):
        GuestLayoutOverride(guest_right_px=-50, guest_bottom_px=-10)

    ok = GuestLayoutOverride(guest_right_px=-50, guest_bottom_px=-10, guest_height_px=440)
    assert ok.guest_height_px == 440
    for bad in (
        {"guest_right_px": 999, "guest_bottom_px": -10, "guest_height_px": 440},
        {"guest_right_px": -50, "guest_bottom_px": -10, "guest_height_px": 1},
        {"guest_right_px": -50, "guest_bottom_px": -10, "guest_height_px": 5000},
    ):
        with pytest.raises(ValidationError):
            GuestLayoutOverride(**bad)


def test_layout_overrides_carry_quote_geometry_alongside_cover():
    from shared.schemas.podcast_carousel import (
        CarouselLayoutOverridesV1,
        CoverLayoutOverride,
        GuestLayoutOverride,
    )

    overrides = CarouselLayoutOverridesV1(
        cover=CoverLayoutOverride(),
        quote=GuestLayoutOverride(guest_right_px=-80, guest_bottom_px=-30, guest_height_px=400),
    )
    assert overrides.quote.guest_height_px == 400
    assert CarouselLayoutOverridesV1().quote is None


def test_apply_request_rejects_an_empty_edit_even_with_quote_slot_present():
    from shared.schemas.podcast_carousel import CarouselEditorApplyRequest

    with pytest.raises(ValidationError):
        CarouselEditorApplyRequest(manifest_sha256="a" * 64)
