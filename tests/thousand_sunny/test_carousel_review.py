from __future__ import annotations

import hashlib
import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from agents.brook.podcast_carousel_render import _digest_files
from scripts.podcast_carousel_correction_job import claim_job, fail_job
from scripts.podcast_carousel_publish_job import (
    checkpoint_publish_target,
    claim_publish_job,
    complete_publish_job,
    publish_job_path,
    start_publish_target,
)
from shared.schemas.carousel_publish import CarouselPublishPlatformResult
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
    render_input_path.write_text(
        render_input_path.read_text(encoding="utf-8").replace(
            "</body>",
            "<script>window.applyEditorPatch=()=>{};window.__carouselRefit=()=>("
            '{status:"fit",regions:{},notes:[]});</script></body>',
        ),
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


def _make_manifest_manual_only(root: Path) -> str:
    package = root / EPISODE / "ig-carousel"
    current_path = package / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    manifest_path = Path(current["manifest"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    point = payload["pages"][2]
    expanded = payload["pages"][:3]
    for page_number in range(4, 10):
        clone = json.loads(json.dumps(point))
        clone["page_id"] = f"point-{page_number}"
        clone["page_number"] = page_number
        clone["copy_page"]["page_id"] = clone["page_id"]
        expanded.append(clone)
    for page_number, original in enumerate(payload["pages"][-2:], start=10):
        original["page_number"] = page_number
        expanded.append(original)
    payload["pages"] = expanded
    payload["publish_compatibility"] = "manual_only"
    manifest = CarouselReviewManifestV1.model_validate(payload)
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    manifest_sha256 = receipt_for(manifest_path).sha256
    current["manifest_sha256"] = manifest_sha256
    current_path.write_text(json.dumps(current), encoding="utf-8")
    return manifest_sha256


def _write_legacy_publish_job(root: Path, manifest_sha256: str) -> str:
    package = root / EPISODE / "ig-carousel"
    current = json.loads((package / "current.json").read_text(encoding="utf-8"))
    manifest = json.loads(Path(current["manifest"]).read_text(encoding="utf-8"))
    caption = "Legacy approved caption"
    fingerprint_payload = {
        "source_revision": "r001",
        "source_manifest_sha256": manifest_sha256,
        "caption": caption,
        "platforms": ["instagram"],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    job_id = "pj-" + "8" * 32
    payload = {
        "schema_name": "nakama.podcast_carousel_publish_job.v1",
        "job_id": job_id,
        "episode_id": "ep120",
        "source_revision": "r001",
        "source_manifest_sha256": manifest_sha256,
        "approval_revision_number": 1,
        "approved_at": "2026-08-19T03:00:00Z",
        "request_fingerprint": fingerprint,
        "caption": caption,
        "assets": [
            {
                "page_id": page["page_id"],
                "page_number": page["page_number"],
                "image": page["image"],
            }
            for page in manifest["pages"]
        ],
        "targets": [
            {
                "platform": "instagram",
                "strategy": "agent_browser",
                "configuration_state": "agent_browser_required",
                "required_executor_capabilities": ["browser_session"],
                "note": "Legacy browser target.",
            }
        ],
        "status": "queued",
        "created_at": "2026-08-19T03:00:00Z",
        "updated_at": "2026-08-19T03:00:00Z",
        "claim": None,
        "progress": [],
        "results": [],
        "error": None,
    }
    path = package / "publish_jobs" / f"{job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return job_id


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    _seed(tmp_path)
    monkeypatch.setenv("PODCAST_EPISODES_ROOT", str(tmp_path))
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.delenv("META_CAROUSEL_PUBLISH_CONFIGURED", raising=False)
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


def test_manifest_receipt_and_parse_use_one_immutable_buffer(client, monkeypatch):
    app, root = client
    manifest_path = (
        root / EPISODE / "ig-carousel" / "revisions" / "r001" / "review_manifest.v1.json"
    ).resolve()
    original_read_bytes = Path.read_bytes
    reads = 0

    def swapping_read_bytes(path: Path) -> bytes:
        nonlocal reads
        payload = original_read_bytes(path)
        if path.resolve() == manifest_path:
            reads += 1
            path.write_bytes(b"{}")
        return payload

    monkeypatch.setattr(Path, "read_bytes", swapping_read_bytes)
    response = app.get(f"/bridge/ig-cards/{EPISODE}")

    assert response.status_code == 200
    assert reads == 1


def test_media_returns_the_same_verified_bytes_when_file_swaps_after_read(client, monkeypatch):
    app, root = client
    image_path = (
        root / EPISODE / "ig-carousel" / "revisions" / "r001" / "pages" / "01.png"
    ).resolve()
    original_payload = image_path.read_bytes()
    original_read_bytes = Path.read_bytes
    reads = 0

    def swapping_read_bytes(path: Path) -> bytes:
        nonlocal reads
        payload = original_read_bytes(path)
        if path.resolve() == image_path:
            reads += 1
            path.write_bytes(b"unverified replacement")
        return payload

    monkeypatch.setattr(Path, "read_bytes", swapping_read_bytes)
    response = app.get(f"/bridge/ig-cards/{EPISODE}/media/cover")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == original_payload
    assert reads == 1


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


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("queued", "等待執行者認領"),
        ("claimed", "執行者已認領"),
        ("in_progress", "發布進行中"),
        ("completed", "發布已完成"),
        ("failed", "發布未完成，可重試"),
        ("superseded", "發布核准已撤回"),
    ],
)
def test_approved_review_board_links_latest_matching_publish_status(
    client,
    monkeypatch,
    status: str,
    label: str,
):
    app, _ = client
    manifest_sha = _manifest_sha(app)
    app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    import thousand_sunny.routers.carousel_review as review_module

    monkeypatch.setattr(
        review_module,
        "list_publish_jobs",
        lambda _: [
            SimpleNamespace(
                job_id="pj-" + "a" * 32,
                source_revision="r001",
                source_manifest_sha256=manifest_sha,
                status=status,
            )
        ],
    )

    board = app.get(f"/bridge/ig-cards/{EPISODE}")

    assert board.status_code == 200
    assert label in board.text
    assert f'data-state="{status}"' in board.text
    assert f'href="/bridge/ig-cards/{EPISODE}/publish"' in board.text
    assert "pj-" + "a" * 32 in board.text
    assert "readonly disabled" in board.text


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
        "publish_url": f"/bridge/ig-cards/{EPISODE}/publish",
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


def test_historical_approval_does_not_bypass_active_correction(client):
    app, _ = client
    manifest_sha = _manifest_sha(app)
    assert (
        app.post(
            f"/bridge/ig-cards/{EPISODE}/approve",
            data={"manifest_sha256": manifest_sha},
        ).status_code
        == 200
    )
    correction = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={"manifest_sha256": manifest_sha, "feedback_cover": "new correction"},
    )

    repeated = app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )

    assert correction.status_code == 201
    assert repeated.status_code == 409
    assert repeated.json()["detail"] == "correction job is still active"


def test_latest_matching_draft_requires_a_new_approval_after_correction_fails(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    assert (
        app.post(
            f"/bridge/ig-cards/{EPISODE}/approve",
            data={"manifest_sha256": manifest_sha},
        ).status_code
        == 200
    )
    correction = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={"manifest_sha256": manifest_sha, "feedback_cover": "new correction"},
    ).json()
    job_path = root / EPISODE / "ig-carousel" / "correction_jobs" / f"{correction['job_id']}.json"
    claim_job(
        job_path,
        executor="codex",
        executor_id="review-worker",
        claim_token="claim-review-0001",
    )
    fail_job(
        job_path,
        claim_token="claim-review-0001",
        error="correction could not converge",
    )

    approved = app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )

    assert approved.status_code == 200
    feedback = json.loads(
        (root / EPISODE / "ig-carousel" / "review_feedback.v1.json").read_text(encoding="utf-8")
    )
    matching_approvals = [
        revision
        for revision in feedback["revisions"]
        if revision["carousel_revision"] == "r001"
        and revision["manifest_sha256"] == manifest_sha
        and revision["decision"] == "approved"
    ]
    assert len(matching_approvals) == 2


@pytest.mark.parametrize(
    ("approval_endpoint", "approval_status"),
    [("approve", 200), ("decide", 303)],
)
def test_approval_and_feedback_race_is_serialized_by_release_lock(
    client,
    monkeypatch,
    approval_endpoint: str,
    approval_status: int,
):
    app, root = client
    manifest_sha = _manifest_sha(app)
    import thousand_sunny.routers.carousel_review as review_module

    original_lock = review_module.publish_release_lock
    original_append = review_module._append_feedback_revision
    lock_attempts = 0
    attempts_guard = threading.Lock()
    second_lock_attempted = threading.Event()
    approval_append_entered = threading.Event()
    release_approval = threading.Event()

    @contextmanager
    def observed_release_lock(package_root):
        nonlocal lock_attempts
        with attempts_guard:
            lock_attempts += 1
            if lock_attempts == 2:
                second_lock_attempted.set()
        with original_lock(package_root):
            yield

    def delayed_append(**kwargs):
        if kwargs["decision"] == "approved":
            approval_append_entered.set()
            assert release_approval.wait(timeout=5)
        return original_append(**kwargs)

    monkeypatch.setattr(review_module, "publish_release_lock", observed_release_lock)
    monkeypatch.setattr(review_module, "_append_feedback_revision", delayed_append)
    approval_data = {"manifest_sha256": manifest_sha}
    if approval_endpoint == "decide":
        approval_data.update(
            {
                f"status_{page_id}": "approved"
                for page_id in ("cover", "hook", "point-one", "quote", "cta")
            }
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            approval_future = pool.submit(
                app.post,
                f"/bridge/ig-cards/{EPISODE}/{approval_endpoint}",
                data=approval_data,
            )
            assert approval_append_entered.wait(timeout=5)
            feedback_future = pool.submit(
                app.post,
                f"/bridge/ig-cards/{EPISODE}/feedback",
                data={
                    "manifest_sha256": manifest_sha,
                    "feedback_cover": "race-safe correction",
                },
            )
            assert second_lock_attempted.wait(timeout=5)
            assert not feedback_future.done()
            release_approval.set()
            approval = approval_future.result(timeout=5)
            feedback = feedback_future.result(timeout=5)
    finally:
        release_approval.set()

    assert approval.status_code == approval_status
    assert feedback.status_code == 201
    audit = json.loads(
        (root / EPISODE / "ig-carousel" / "review_feedback.v1.json").read_text(encoding="utf-8")
    )
    assert [revision["decision"] for revision in audit["revisions"][-2:]] == [
        "approved",
        "draft",
    ]


def test_legacy_decide_draft_supersedes_queued_publish(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    publish = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Queued before legacy draft save",
            "platforms": "instagram",
        },
    ).json()

    saved = app.post(
        f"/bridge/ig-cards/{EPISODE}/decide",
        data={"manifest_sha256": manifest_sha},
    )

    assert saved.status_code == 303
    job = json.loads(
        publish_job_path(root / EPISODE / "ig-carousel", publish["job_id"]).read_text(
            encoding="utf-8"
        )
    )
    assert job["status"] == "superseded"
    assert "review draft" in job["superseded_reason"]


def test_legacy_decide_cannot_approve_during_active_correction(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    correction = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={"manifest_sha256": manifest_sha, "feedback_cover": "active correction"},
    )
    data = {"manifest_sha256": manifest_sha}
    data.update(
        {
            f"status_{page_id}": "approved"
            for page_id in ("cover", "hook", "point-one", "quote", "cta")
        }
    )

    response = app.post(f"/bridge/ig-cards/{EPISODE}/decide", data=data)

    assert correction.status_code == 201
    assert response.status_code == 409
    audit = json.loads(
        (root / EPISODE / "ig-carousel" / "review_feedback.v1.json").read_text(encoding="utf-8")
    )
    assert audit["revisions"][-1]["decision"] == "draft"


def test_publish_page_requires_current_manifest_approval(client):
    app, _ = client

    response = app.get(f"/bridge/ig-cards/{EPISODE}/publish")

    assert response.status_code == 403
    assert "has not passed the Review Gate" in response.json()["detail"]


def test_approved_manifest_opens_stage6_publish_page(client):
    app, _ = client
    manifest_sha = _manifest_sha(app)
    approved = app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )

    response = app.get(approved.json()["publish_url"])

    assert response.status_code == 200
    assert "stage 6" in response.text.lower()
    assert "Instagram" in response.text
    assert "YouTube Community" in response.text
    assert "瀏覽器＋人工確認" in response.text
    assert "沒有可用的自動發布端點" in response.text


def test_publish_page_and_status_route_load_handwritten_legacy_v1_job(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    approved = app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    job_id = _write_legacy_publish_job(root, manifest_sha)

    page = app.get(approved.json()["publish_url"])
    status = app.get(f"/bridge/ig-cards/{EPISODE}/publish/jobs/{job_id}")

    assert page.status_code == 200
    assert job_id in page.text
    assert status.status_code == 200
    assert status.json()["job_id"] == job_id
    assert status.json()["source_publish_compatibility"] is None


def test_manual_only_manifest_never_advertises_meta_api(client, monkeypatch):
    app, root = client
    monkeypatch.setenv("META_CAROUSEL_PUBLISH_CONFIGURED", "1")
    manifest_sha = _make_manifest_manual_only(root)
    approved = app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    assert approved.status_code == 200

    page = app.get(approved.json()["publish_url"])
    job = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Manual-only carousel",
            "platforms": "instagram",
        },
    )
    youtube = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Manual-only carousel",
            "platforms": "youtube_community",
        },
    )

    assert page.status_code == 200
    assert "meta_api" not in page.text
    youtube_input = page.text.split('value="youtube_community"', 1)[1].split(">", 1)[0]
    assert "disabled" in youtube_input
    assert "最多接受 10 張圖片" in page.text
    assert job.status_code == 201
    assert job.json()["targets"][0]["strategy"] == "agent_browser"
    assert youtube.status_code == 400
    assert "最多接受 10 張圖片" in youtube.json()["detail"]


def test_publish_submit_validates_caption_platforms_and_manifest_drift(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )

    empty_caption = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={"manifest_sha256": manifest_sha, "platforms": "instagram"},
    )
    no_platform = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={"manifest_sha256": manifest_sha, "caption": "Approved caption"},
    )
    _advance_manifest_revision(root)
    stale = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Approved caption",
            "platforms": "instagram",
        },
    )

    assert empty_caption.status_code == 400
    assert no_platform.status_code == 400
    assert stale.status_code == 409
    assert app.get(f"/bridge/ig-cards/{EPISODE}/publish").status_code == 403


def test_instagram_caption_boundary_does_not_limit_other_platforms(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )

    instagram_at_limit = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "字" * 2200,
            "platforms": "instagram",
        },
    )
    instagram_over_limit = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "字" * 2201,
            "platforms": "instagram",
        },
    )
    facebook_over_instagram_limit = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "字" * 2201,
            "platforms": "facebook_page",
        },
    )
    youtube_over_instagram_limit = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "字" * 2201,
            "platforms": "youtube_community",
        },
    )

    assert instagram_at_limit.status_code == 201
    assert instagram_over_limit.status_code == 400
    assert "2,200" in instagram_over_limit.json()["detail"]
    assert facebook_over_instagram_limit.status_code == 201
    assert youtube_over_instagram_limit.status_code == 201
    jobs = list((root / EPISODE / "ig-carousel" / "publish_jobs").glob("pj-*.json"))
    assert len(jobs) == 3


def test_publish_submit_is_idempotent_and_refresh_restores_job(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    data = {
        "manifest_sha256": manifest_sha,
        "caption": "本集整理四個讓內容活得更久的策略。",
        "platforms": ["youtube_community", "instagram"],
    }

    first = app.post(f"/bridge/ig-cards/{EPISODE}/publish/jobs", data=data)
    duplicate = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={**data, "platforms": ["instagram", "youtube_community"]},
    )
    overlapping = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            **data,
            "caption": "Different caption while the first job is active",
            "platforms": "instagram",
        },
    )

    assert first.status_code == 201
    assert duplicate.status_code == 200
    assert duplicate.json()["idempotent"] is True
    assert duplicate.json()["job_id"] == first.json()["job_id"]
    assert overlapping.status_code == 409
    assert "active publish job" in overlapping.json()["detail"]
    assert [target["platform"] for target in first.json()["targets"]] == [
        "instagram",
        "youtube_community",
    ]
    assert first.json()["targets"][0]["strategy"] == "agent_browser"
    assert first.json()["targets"][1]["strategy"] == "agent_browser_manual"
    assert "--executor codex" in first.json()["claim_commands"]["codex"]
    assert "--executor claude_code" in first.json()["claim_commands"]["claude_code"]
    assert "--capability browser_session" in first.json()["claim_commands"]["codex"]
    jobs = list((root / EPISODE / "ig-carousel" / "publish_jobs").glob("pj-*.json"))
    assert len(jobs) == 1
    assert not (root / EPISODE / "ig-carousel" / "published.json").exists()

    status = app.get(first.json()["status_url"])
    refreshed = app.get(f"/bridge/ig-cards/{EPISODE}/publish")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert refreshed.status_code == 200
    assert first.json()["job_id"] in refreshed.text
    assert "本集整理四個讓內容活得更久的策略。" in refreshed.text


def test_partial_publish_failure_requires_republish_confirmation_in_ui_and_server(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    created = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Initial cross-platform release",
            "platforms": ["instagram", "youtube_community"],
        },
    ).json()
    context_before = app.get(f"/bridge/ig-cards/{EPISODE}/publish/context")
    assert context_before.status_code == 200
    assert context_before.json()["published_platforms"] == []
    path = publish_job_path(root / EPISODE / "ig-carousel", created["job_id"])
    claim_publish_job(
        path,
        executor="codex",
        executor_id="release-worker",
        executor_capabilities=["browser_session"],
        claim_token="claim-partial-ui-0001",
    )
    completed_at = datetime.now(UTC)
    results = [
        CarouselPublishPlatformResult(
            platform="instagram",
            strategy="agent_browser",
            status="published",
            receipt_id="ig-partial-ui-receipt",
            completed_at=completed_at,
        ),
        CarouselPublishPlatformResult(
            platform="youtube_community",
            strategy="agent_browser_manual",
            status="failed",
            error="manual confirmation declined",
            completed_at=completed_at,
        ),
    ]
    for index, result in enumerate(results):
        started = start_publish_target(
            path,
            claim_token="claim-partial-ui-0001",
            platform=result.platform,
        )
        state = next(state for state in started.target_states if state.platform == result.platform)
        result = result.model_copy(
            update={
                "idempotency_key": state.idempotency_key,
                "attempt_id": state.attempt_id,
            }
        )
        results[index] = result
        checkpoint_publish_target(
            path,
            claim_token="claim-partial-ui-0001",
            result=result,
        )
    complete_publish_job(path, claim_token="claim-partial-ui-0001", results=results)
    retry_preflight = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/preflight",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Initial cross-platform release",
            "platforms": ["instagram", "youtube_community"],
        },
    )
    repost_preflight = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/preflight",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Different Instagram release",
            "platforms": ["instagram"],
        },
    )

    page = app.get(f"/bridge/ig-cards/{EPISODE}/publish")
    context_after = app.get(f"/bridge/ig-cards/{EPISODE}/publish/context")
    unfinished_only = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Retry only the unfinished YouTube release",
            "platforms": "youtube_community",
        },
    )
    blocked = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Different Instagram release",
            "platforms": "instagram",
        },
    )
    confirmed = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Different Instagram release",
            "platforms": "instagram",
            "confirm_republish": "true",
        },
    )

    assert page.status_code == 200
    assert retry_preflight.json()["republish_required_platforms"] == []
    assert repost_preflight.json()["republish_required_platforms"] == ["instagram"]
    assert 'name="confirm_republish" value="true"' in page.text
    assert context_after.status_code == 200
    assert context_after.json()["published_platforms"] == ["instagram"]
    assert unfinished_only.status_code == 201
    assert blocked.status_code == 409
    assert "explicit confirmation" in blocked.json()["detail"]
    assert confirmed.status_code == 201


def test_new_feedback_supersedes_queued_publish_before_revoking_approval(client):
    app, _ = client
    manifest_sha = _manifest_sha(app)
    app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    publish = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Approved release caption",
            "platforms": "instagram",
        },
    ).json()

    correction = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={"manifest_sha256": manifest_sha, "feedback_cover": "change the cover"},
    )
    status = app.get(publish["status_url"])

    assert correction.status_code == 201
    assert status.status_code == 200
    assert status.json()["status"] == "superseded"
    assert "revoked the release approval" in status.json()["superseded_reason"]


def test_new_feedback_fails_closed_while_publish_is_claimed(client):
    app, root = client
    manifest_sha = _manifest_sha(app)
    app.post(
        f"/bridge/ig-cards/{EPISODE}/approve",
        data={"manifest_sha256": manifest_sha},
    )
    publish = app.post(
        f"/bridge/ig-cards/{EPISODE}/publish/jobs",
        data={
            "manifest_sha256": manifest_sha,
            "caption": "Approved release caption",
            "platforms": "instagram",
        },
    ).json()
    path = publish_job_path(root / EPISODE / "ig-carousel", publish["job_id"])
    claim_publish_job(
        path,
        executor="codex",
        executor_id="release-worker",
        executor_capabilities=["browser_session"],
        claim_token="claim-release-0001",
    )

    correction = app.post(
        f"/bridge/ig-cards/{EPISODE}/feedback",
        data={"manifest_sha256": manifest_sha, "feedback_cover": "change the cover"},
    )

    assert correction.status_code == 409
    assert "publish job is active" in correction.json()["detail"]
    assert app.get(publish["status_url"]).json()["status"] == "claimed"


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


def test_structured_editor_queues_allowlisted_text_region_layout(client):
    app, root = client
    hook = receipt_for(root / EPISODE / "ig-carousel/revisions/r001/pages/02.png")
    response = app.post(
        f"/bridge/ig-cards/{EPISODE}/apply-edits",
        json={
            "manifest_sha256": _manifest_sha(app),
            "text_layout_overrides": [
                {
                    "page_id": "hook",
                    "role": "hook",
                    "region": "bridge",
                    "artifact_sha256": hook.sha256,
                    "values": {"x_px": 64, "y_px": 720, "width_px": 880, "font_start_px": 34},
                }
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["text_layout_overrides"][0]["region"] == "bridge"


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


def test_receipt_verified_precontract_render_input_disables_editor_and_requires_new_revision(
    client,
):
    app, root = client
    package = root / EPISODE / "ig-carousel"
    manifest_path = package / "revisions/r001/review_manifest.v1.json"
    render_input = package / "revisions/r001/render_input.html"
    source = render_input.read_text(encoding="utf-8")
    render_input.write_text(source.replace("window.applyEditorPatch=()=>{};", ""), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["render_input"] = receipt_for(render_input).model_dump(mode="json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    current_path = package / "current.json"
    current = json.loads(current_path.read_text(encoding="utf-8"))
    current["manifest_sha256"] = receipt_for(manifest_path).sha256
    current_path.write_text(json.dumps(current), encoding="utf-8")

    board = app.get(f"/bridge/ig-cards/{EPISODE}")
    assert board.status_code == 200
    assert "canonical editor API" in board.text
    edit = board.text.split('data-edit-page="cover"', 1)[1].split("</button>", 1)[0]
    assert "disabled" in edit

    preview = app.get(
        f"/bridge/ig-cards/{EPISODE}/preview/cover",
        params={"manifest_sha256": current["manifest_sha256"]},
    )
    assert preview.status_code == 409
    assert "render a new revision" in preview.json()["detail"]
    direct_apply = app.post(
        f"/bridge/ig-cards/{EPISODE}/apply-edits",
        json={"manifest_sha256": current["manifest_sha256"], "copy_edits": []},
    )
    assert direct_apply.status_code == 409
    assert "render a new revision" in direct_apply.json()["detail"]


def test_structured_editor_rejects_prospective_manual_lines_against_copy_edits(client):
    app, root = client
    package = root / EPISODE / "ig-carousel"
    cover = receipt_for(package / "revisions/r001/pages/01.png")
    response = app.post(
        f"/bridge/ig-cards/{EPISODE}/apply-edits",
        json={
            "manifest_sha256": _manifest_sha(app),
            "copy_edits": [
                {
                    "page_id": "cover",
                    "role": "cover",
                    "artifact_sha256": cover.sha256,
                    "fields": {"headline": "foofoo", "emphasis": "foo"},
                }
            ],
            "text_layout_overrides": [
                {
                    "page_id": "cover",
                    "role": "cover",
                    "region": "headline",
                    "artifact_sha256": cover.sha256,
                    "values": {
                        "x_px": 64,
                        "y_px": 168,
                        "width_px": 976,
                        "font_start_px": 106,
                        "lines": ["fo", "ofoo"],
                    },
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "prospective structured carousel edits" in response.json()["detail"]


def test_copy_only_editor_post_cannot_bypass_display_copy_crlf_contract(client):
    app, root = client
    cover = receipt_for(root / EPISODE / "ig-carousel/revisions/r001/pages/01.png")
    response = app.post(
        f"/bridge/ig-cards/{EPISODE}/apply-edits",
        json={
            "manifest_sha256": _manifest_sha(app),
            "copy_edits": [
                {
                    "page_id": "cover",
                    "role": "cover",
                    "artifact_sha256": cover.sha256,
                    "fields": {"guest_title": "first line\nsecond line"},
                }
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid structured carousel edits"


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
