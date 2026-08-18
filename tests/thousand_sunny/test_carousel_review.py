from __future__ import annotations

import importlib
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

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
        template=TemplateSnapshot(root=str(package / "templates" / SHA), sha256=SHA),
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
    assert "Approve" in response.text


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
    assert "第 1 輪 Review 已儲存" in saved_board.text
    assert 'data-saving-label="儲存中…"' in saved_board.text


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
