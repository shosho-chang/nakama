from __future__ import annotations

import importlib
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from agents.brook.podcast_carousel_render import _digest_files
from shared.schemas.podcast_carousel import (
    CarouselReviewManifestV1,
    CarouselReviewPage,
    PageFitDiagnostic,
    PodcastCarouselCopySpecV1,
    TemplateSnapshot,
    receipt_for,
)

SHA = "a" * 64


class _ReviewFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, str | None]] = []
        self.nested_form = False
        self.submit_form: dict[str, str | None] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            if self.forms:
                self.nested_form = True
            self.forms.append(attributes)
        elif tag == "button" and attributes.get("type") == "submit":
            self.submit_form = self.forms[-1] if self.forms else None

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.forms:
            self.forms.pop()


EPISODE = "20260721 鄭國威"


def _spec() -> PodcastCarouselCopySpecV1:
    evidence = {
        "evidence_id": "ev-1",
        "source_path": "transcript_prose.md",
        "source_sha256": SHA,
        "speaker": "鄭國威",
        "text": "大家只會看到你做得還 OK 的那一面。",
        "t0": 100,
        "t1": 112,
    }
    pages = [
        {
            "role": "cover",
            "page_id": "cover",
            "headline": "看不見的失敗",
            "emphasis": "失敗",
            "guest_name": "鄭國威",
            "guest_title": "共同創辦人",
            "cutout": "guest.png",
            "evidence": [evidence],
        },
        {
            "role": "hook",
            "page_id": "hook",
            "question": "為什麼只看見成功？",
            "emphasis": "只看見成功",
            "bridge": "從演算法背後看答案。",
            "evidence": [evidence],
        },
        {
            "role": "point",
            "page_id": "point-one",
            "headline": "失敗會沉下去",
            "emphasis": "沉下去",
            "body": "留下來的通常是表現較好的內容。",
            "evidence": [evidence],
        },
        {
            "role": "quote",
            "page_id": "quote",
            "variant": "B",
            "text": "大家只會看到做得還 OK 的那一面。",
            "emphasis": "做得還 OK",
            "guest_name": "鄭國威",
            "guest_cutout": "guest.png",
            "host_question": "怎麼保持一致？",
            "host_question_evidence": [{**evidence, "evidence_id": "host", "speaker": "張修修"}],
            "host_cutout": "host.png",
            "evidence": [evidence],
        },
        {
            "role": "cta",
            "page_id": "cta",
            "episode_topic": "看不見的失敗",
            "emphasis": "看不見的失敗",
            "evidence": [evidence],
        },
    ]
    return PodcastCarouselCopySpecV1.model_validate(
        {
            "episode_id": "ep120",
            "revision": "r001",
            "episode": {
                "number": 120,
                "topic": "內容創作",
                "guest_name": "鄭國威",
                "guest_title": "共同創辦人",
            },
            "pages": pages,
            "publish_compatibility": "api_compatible",
        }
    )


def _seed(root: Path) -> Path:
    package = root / EPISODE / "ig-carousel"
    revision = package / "revisions" / "r001"
    pages_dir = revision / "pages"
    pages_dir.mkdir(parents=True)
    spec = _spec()
    copy_path = revision / "copy_spec.v1.json"
    copy_path.write_text(spec.model_dump_json(), encoding="utf-8")
    template_root = package / "templates" / SHA
    template_root.mkdir(parents=True)
    (template_root / "preview.css").write_text("#canvas{width:1080px}", encoding="utf-8")
    (template_root / "preview.js").write_text("window.previewLoaded=true", encoding="utf-8")
    (template_root / "preview.png").write_bytes(b"trusted preview image")
    template_files = [
        (path.relative_to(template_root).as_posix(), path)
        for path in template_root.rglob("*")
        if path.is_file()
    ]
    template_sha256 = _digest_files(template_files)
    render_input_path = revision / "render_input.html"
    render_input_path.write_text(
        '<!doctype html><html><head><base href="file:///snapshot/">'
        '<link rel="stylesheet" href="preview.css"></head>'
        '<body><div id="canvas" class="cover"><img class="guest" alt="來賓">'
        '<h1 class="cover-title">看不見的失敗</h1></div></body></html>',
        encoding="utf-8",
    )
    review_pages = []
    for index, page in enumerate(spec.pages, start=1):
        image_path = pages_dir / f"{index:02d}.png"
        Image.new("RGB", (1080, 1350), "white").save(image_path)
        review_pages.append(
            CarouselReviewPage(
                page_id=page.page_id,
                page_number=index,
                role=page.role,
                content_sha256=SHA,
                image=receipt_for(image_path),
                fit=PageFitDiagnostic(status="fit"),
                copy_page=page,
            )
        )
    manifest = CarouselReviewManifestV1(
        episode_id=spec.episode_id,
        revision=spec.revision,
        copy_spec=receipt_for(copy_path),
        render_input=receipt_for(render_input_path),
        template=TemplateSnapshot(root=str(template_root), sha256=template_sha256),
        publish_compatibility="api_compatible",
        pages=review_pages,
    )
    manifest_path = revision / "review_manifest.v1.json"
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    manifest_receipt = receipt_for(manifest_path)
    (package / "current.json").write_text(
        json.dumps(
            {
                "episode_id": "ep120",
                "revision": "r001",
                "manifest": str(manifest_path.resolve()),
                "manifest_sha256": manifest_receipt.sha256,
            }
        ),
        encoding="utf-8",
    )
    return package


def _manifest_sha(app: TestClient) -> str:
    board = app.get(f"/bridge/ig-cards/{EPISODE}")
    marker = 'name="manifest_sha256" value="'
    return board.text.split(marker, 1)[1].split('"', 1)[0]


def _advance_manifest_revision(root: Path, revision: str = "r002") -> str:
    package = root / EPISODE / "ig-carousel"
    current_path = package / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    manifest_path = Path(current["manifest"])
    manifest = CarouselReviewManifestV1.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    copy_path = Path(manifest.copy_spec.path)
    copy_spec = PodcastCarouselCopySpecV1.model_validate_json(copy_path.read_text(encoding="utf-8"))
    copy_path.write_text(
        copy_spec.model_copy(update={"revision": revision}).model_dump_json(), encoding="utf-8"
    )
    manifest = manifest.model_copy(
        update={"revision": revision, "copy_spec": receipt_for(copy_path)}
    )
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    manifest_sha256 = receipt_for(manifest_path).sha256
    current.update({"revision": revision, "manifest_sha256": manifest_sha256})
    current_path.write_text(json.dumps(current), encoding="utf-8")
    return manifest_sha256


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    _seed(tmp_path)
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.carousel_review as review_module

    importlib.reload(auth_module)
    importlib.reload(review_module)
    app = FastAPI()
    app.include_router(review_module.page_router)
    return TestClient(app, follow_redirects=False), tmp_path


def test_board_shows_all_cards_and_evidence_drawers(client):
    app, _ = client
    response = app.get(f"/bridge/ig-cards/{EPISODE}")
    assert response.status_code == 200
    assert response.text.count('class="carousel-card"') == 5
    assert "grid-template-columns:repeat(5" not in response.text
    assert "逐字稿證據" in response.text
    assert "核准" in response.text


def test_review_submit_button_belongs_to_post_form_without_nested_forms(client):
    app, _ = client
    response = app.get(f"/bridge/ig-cards/{EPISODE}")
    parser = _ReviewFormParser()
    parser.feed(response.text)

    assert parser.nested_form is False
    assert parser.submit_form is not None
    assert parser.submit_form["method"] == "post"
    assert parser.submit_form["action"].endswith(f"/bridge/ig-cards/{EPISODE}/decide")


def test_saved_board_names_review_round_and_exposes_saving_state(client):
    app, _ = client
    board = app.get(f"/bridge/ig-cards/{EPISODE}")
    marker = 'name="manifest_sha256" value="'
    manifest_sha = board.text.split(marker, 1)[1].split('"', 1)[0]

    response = app.post(
        f"/bridge/ig-cards/{EPISODE}/decide",
        data={"manifest_sha256": manifest_sha},
    )
    assert response.status_code == 303

    saved_board = app.get(f"/bridge/ig-cards/{EPISODE}?saved=1")
    assert "第 1 輪檢查已儲存" in saved_board.text
    assert 'data-saving-label="儲存中…"' in saved_board.text


def test_review_board_persists_refresh_draft_and_submits_without_navigation(client):
    app, _ = client
    response = app.get(f"/bridge/ig-cards/{EPISODE}")

    assert "sessionStorage.setItem" in response.text
    assert "sessionStorage.getItem" in response.text
    assert "fetch(reviewForm.action" in response.text
    assert 'id="review-save-status"' in response.text
    assert 'id="review-count"' in response.text


def test_editor_pages_preserve_visual_field_order(client):
    app, _ = client
    response = app.get(f"/bridge/ig-cards/{EPISODE}")
    marker = "const editorPages = "
    editor_pages = json.loads(response.text.split(marker, 1)[1].split(";", 1)[0])

    assert [page["field_order"] for page in editor_pages] == [
        ["headline", "emphasis", "guest_name", "guest_title"],
        ["question", "emphasis", "bridge"],
        ["headline", "emphasis", "body"],
        ["host_question", "text", "emphasis", "guest_name"],
        ["episode_topic", "emphasis"],
    ]


def test_media_returns_verified_png(client):
    app, _ = client
    response = app.get(f"/bridge/ig-cards/{EPISODE}/media/cover")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_all_approved_closes_gate_and_appends_audit_revision(client):
    app, root = client
    board = app.get(f"/bridge/ig-cards/{EPISODE}")
    marker = 'name="manifest_sha256" value="'
    manifest_sha = board.text.split(marker, 1)[1].split('"', 1)[0]
    data = {"manifest_sha256": manifest_sha}
    for page_id in ("cover", "hook", "point-one", "quote", "cta"):
        data[f"status_{page_id}"] = "approved"
        data[f"feedback_{page_id}"] = ""
    response = app.post(f"/bridge/ig-cards/{EPISODE}/decide", data=data)
    assert response.status_code == 303
    payload = json.loads(
        (root / EPISODE / "ig-carousel" / "review_feedback.v1.json").read_text(encoding="utf-8")
    )
    assert payload["revisions"][-1]["decision"] == "approved"
    assert len(payload["revisions"][-1]["pages"]) == 5


def test_needs_changes_requires_feedback(client):
    app, _ = client
    board = app.get(f"/bridge/ig-cards/{EPISODE}")
    marker = 'name="manifest_sha256" value="'
    manifest_sha = board.text.split(marker, 1)[1].split('"', 1)[0]
    response = app.post(
        f"/bridge/ig-cards/{EPISODE}/decide",
        data={"manifest_sha256": manifest_sha, "status_cover": "needs_changes"},
    )
    assert response.status_code == 400


def test_old_revision_feedback_does_not_pollute_new_manifest(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    saved = app.post(
        f"/bridge/ig-cards/{EPISODE}/decide",
        data={
            "manifest_sha256": manifest_sha,
            "status_cover": "needs_changes",
            "feedback_cover": "只屬於舊 revision 的意見",
        },
    )
    assert saved.status_code == 303

    _advance_manifest_revision(root)
    board = app.get(f"/bridge/ig-cards/{EPISODE}")

    assert board.status_code == 200
    assert "只屬於舊 revision 的意見" not in board.text
    assert '<dd id="review-count">0</dd>' in board.text


def test_feedback_submit_creates_revision_bound_queued_job(client):
    app, root = client
    manifest_sha = _manifest_sha(app)

    response = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={
            "manifest_sha256": manifest_sha,
            "feedback_cover": "放大來賓",
            "feedback_hook": "",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["source_revision"] == "r001"
    assert payload["source_manifest_sha256"] == manifest_sha
    assert payload["feedback_items"] == [
        {
            "page_id": "cover",
            "artifact_sha256": receipt_for(
                root / EPISODE / "ig-carousel" / "revisions" / "r001" / "pages" / "01.png"
            ).sha256,
            "feedback": "放大來賓",
        }
    ]
    job_path = root / EPISODE / "ig-carousel" / "correction_jobs" / f"{payload['job_id']}.json"
    assert job_path.is_file()
    assert json.loads(job_path.read_text(encoding="utf-8"))["status"] == "queued"

    status = app.get(f"/bridge/ig-cards/{EPISODE}/jobs/{payload['job_id']}")
    assert status.status_code == 200
    assert status.json() == payload


def test_feedback_submit_rejects_duplicate_active_job(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    data = {"manifest_sha256": manifest_sha, "feedback_cover": "make guest larger"}

    first = app.post(f"/bridge/ig-cards/{EPISODE}/feedback", data=data)
    duplicate = app.post(f"/bridge/ig-cards/{EPISODE}/feedback", data=data)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    jobs = list((root / EPISODE / "ig-carousel" / "correction_jobs").glob("cj-*.json"))
    assert len(jobs) == 1


def test_feedback_submit_rejects_empty_and_stale_requests(client):
    app, root = client
    manifest_sha = _manifest_sha(app)

    empty = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={"manifest_sha256": manifest_sha},
    )
    assert empty.status_code == 400

    _advance_manifest_revision(root)
    stale = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={"manifest_sha256": manifest_sha, "feedback_cover": "舊圖意見"},
    )
    assert stale.status_code == 409


def test_approve_is_separate_from_correction_feedback(client):
    app, root = client
    manifest_sha = _manifest_sha(app)

    mixed = app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha, "feedback_cover": "仍需修改"},
    )
    assert mixed.status_code == 400

    correction = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={"manifest_sha256": manifest_sha, "feedback_cover": "仍需修改"},
    )
    assert correction.status_code == 201
    blocked = app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    assert blocked.status_code == 409
    assert not (root / EPISODE / "ig-carousel" / "published.json").exists()


def test_approve_records_latest_hash_without_publishing(client):
    app, root = client
    manifest_sha = _manifest_sha(app)

    response = app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )

    assert response.status_code == 200
    assert response.json() == {
        "approved": True,
        "revision": "r001",
        "manifest_sha256": manifest_sha,
        "published": False,
    }
    feedback = json.loads(
        (root / EPISODE / "ig-carousel" / "review_feedback.v1.json").read_text(encoding="utf-8")
    )
    assert feedback["revisions"][-1]["decision"] == "approved"
    assert not (root / EPISODE / "ig-carousel" / "published.json").exists()


def test_approve_is_idempotent_for_current_manifest(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    data = {"manifest_sha256": manifest_sha}

    first = app.post(f"/bridge/ig-cards/{EPISODE}/approve", data=data)
    second = app.post(f"/bridge/ig-cards/{EPISODE}/approve", data=data)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    feedback = json.loads(
        (root / EPISODE / "ig-carousel" / "review_feedback.v1.json").read_text(encoding="utf-8")
    )
    matching = [
        revision
        for revision in feedback["revisions"]
        if revision["carousel_revision"] == "r001"
        and revision["manifest_sha256"] == manifest_sha
        and revision["decision"] == "approved"
    ]
    assert len(matching) == 1


def test_structured_editor_apply_queues_copy_and_cover_layout_without_mutating_artifacts(client):
    app, root = client
    package = root / EPISODE / "ig-carousel"
    manifest_sha = _manifest_sha(app)
    copy_path = package / "revisions" / "r001" / "copy_spec.v1.json"
    image_path = package / "revisions" / "r001" / "pages" / "01.png"
    copy_before = copy_path.read_bytes()
    image_before = receipt_for(image_path)

    response = app.post(
        f"/bridge/ig-cards/{EPISODE}/apply-edits",
        json={
            "manifest_sha256": manifest_sha,
            "copy_edits": [
                {
                    "page_id": "cover",
                    "role": "cover",
                    "artifact_sha256": image_before.sha256,
                    "fields": {"headline": "更清楚地看見失敗"},
                }
            ],
            "layout_overrides": {
                "page_id": "cover",
                "artifact_sha256": image_before.sha256,
                "values": {
                    "guest_right_px": -180,
                    "guest_bottom_px": -90,
                    "guest_height_px": 980,
                    "title_font_size_px": 112,
                },
            },
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["copy_edits"][0]["fields"] == {"headline": "更清楚地看見失敗"}
    assert payload["layout_overrides"]["values"]["guest_height_px"] == 980
    assert payload["required_reviews"] == [
        "ig_audience",
        "episode_editorial",
        "brand_evidence",
    ]
    assert copy_path.read_bytes() == copy_before
    assert receipt_for(image_path) == image_before
    assert not (package / "published.json").exists()


def test_structured_editor_fails_closed_on_noop_illegal_fields_bounds_and_manifest_drift(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    cover = receipt_for(root / EPISODE / "ig-carousel/revisions/r001/pages/01.png")
    base = {
        "manifest_sha256": manifest_sha,
        "copy_edits": [
            {
                "page_id": "cover",
                "role": "cover",
                "artifact_sha256": cover.sha256,
                "fields": {"headline": "看不見的失敗"},
            }
        ],
    }
    assert app.post(f"/bridge/ig-cards/{EPISODE}/apply-edits", json=base).status_code == 400
    illegal = json.loads(json.dumps(base))
    illegal["copy_edits"][0]["fields"] = {"cutout": "other.png"}
    assert app.post(f"/bridge/ig-cards/{EPISODE}/apply-edits", json=illegal).status_code == 422
    bounds = {
        "manifest_sha256": manifest_sha,
        "layout_overrides": {
            "page_id": "cover",
            "artifact_sha256": cover.sha256,
            "values": {"guest_height_px": 2000},
        },
    }
    assert app.post(f"/bridge/ig-cards/{EPISODE}/apply-edits", json=bounds).status_code == 422
    stale = json.loads(json.dumps(base))
    stale["manifest_sha256"] = "b" * 64
    stale["copy_edits"][0]["fields"] = {"headline": "另一個失敗"}
    assert app.post(f"/bridge/ig-cards/{EPISODE}/apply-edits", json=stale).status_code == 409


def test_structured_editor_preview_uses_real_render_dom_and_scoped_snapshot_assets(client):
    app, _ = client
    manifest_sha = _manifest_sha(app)
    preview = app.get(
        f"/bridge/ig-cards/{EPISODE}/preview/cover",
        params={"manifest_sha256": manifest_sha},
    )
    assert preview.status_code == 200
    assert 'id="canvas" class="cover"' in preview.text
    assert "file:///snapshot/" not in preview.text
    assert "/preview-assets/" in preview.text
    assert "nakama-carousel-editor-v1" in preview.text
    assert "connect-src 'none'" in preview.headers["content-security-policy"]
    base = preview.text.split('<base href="', 1)[1].split('"', 1)[0]
    asset = app.get(f"{base}preview.css")
    assert asset.status_code == 200


@pytest.mark.parametrize("asset_name", ["preview.css", "preview.js", "preview.png"])
def test_editor_preview_rejects_snapshot_asset_mutation_after_verification(client, asset_name):
    app, root = client
    manifest_sha = _manifest_sha(app)
    preview = app.get(
        f"/bridge/ig-cards/{EPISODE}/preview/cover",
        params={"manifest_sha256": manifest_sha},
    )
    base = preview.text.split('<base href="', 1)[1].split('"', 1)[0]
    assert app.get(f"{base}preview.css").status_code == 200

    template_root = root / EPISODE / "ig-carousel" / "templates" / SHA
    (template_root / asset_name).write_bytes(b"tampered snapshot asset")

    mutated = app.get(f"{base}{asset_name}")
    assert mutated.status_code == 409
    assert mutated.json()["detail"] == "carousel template snapshot changed"


def test_legacy_manifest_keeps_review_open_but_disables_untrusted_editor(client):
    app, root = client
    package = root / EPISODE / "ig-carousel"
    manifest_path = package / "revisions/r001/review_manifest.v1.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.pop("render_input")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    current_path = package / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["manifest_sha256"] = receipt_for(manifest_path).sha256
    current_path.write_text(json.dumps(current), encoding="utf-8")

    board = app.get(f"/bridge/ig-cards/{EPISODE}")
    assert board.status_code == 200
    assert "此舊版本仍可檢查與填寫修改意見" in board.text
    assert "需先產生含安全預覽收據的新版本" in board.text
    preview = app.get(
        f"/bridge/ig-cards/{EPISODE}/preview/cover",
        params={"manifest_sha256": current["manifest_sha256"]},
    )
    assert preview.status_code == 409
    assert "no trusted editor preview" in preview.json()["detail"]


def test_editor_preview_rejects_tampered_render_input_receipt(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    render_input = root / EPISODE / "ig-carousel/revisions/r001/render_input.html"
    render_input.write_text("<html>tampered</html>", encoding="utf-8")

    preview = app.get(
        f"/bridge/ig-cards/{EPISODE}/preview/cover",
        params={"manifest_sha256": manifest_sha},
    )
    assert preview.status_code == 409
    assert preview.json()["detail"] == "carousel render input changed"


def test_auth_redirect_uses_same_bridge_login_boundary(tmp_path: Path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("WEB_PASSWORD", "swordfish")
    monkeypatch.setenv("WEB_SECRET", "secret")
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.carousel_review as review_module

    importlib.reload(auth_module)
    importlib.reload(review_module)
    fastapi_app = FastAPI()
    fastapi_app.include_router(review_module.page_router)
    app = TestClient(fastapi_app, follow_redirects=False)
    response = app.get(f"/bridge/ig-cards/{EPISODE}")
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?next=/bridge/ig-cards/")
    structured = app.post(
        f"/bridge/ig-cards/{EPISODE}/apply-edits",
        json={"manifest_sha256": SHA, "copy_edits": []},
    )
    assert structured.status_code == 401
    preview = app.get(
        f"/bridge/ig-cards/{EPISODE}/preview/cover",
        params={"manifest_sha256": SHA},
    )
    assert preview.status_code == 401
