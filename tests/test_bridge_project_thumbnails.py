"""ADR-033 PR4-A — sibling router endpoints (mocked LLM + render).

We don't shell out to ``npx hyperframes`` or call Sonnet here; both are mocked.
Tests verify the routing, frontmatter writes, candidate-serving file safety,
commit archive rotation, and audit log emission.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

SAMPLE_PROJECT = """\
---
type: project
content_type: youtube
created: 2026-04-10
status: active
priority: high
area: work
search_topic: 肌酸
one_sentence: 探討肌酸對非運動族群的妙用
title_candidates:
  - 肌酸不只練肌肉：3 個你沒聽過的妙用
  - 65 歲開始吃肌酸？最新研究說：來得及
  - 每天 5g，改變你大腦的化學反應
tags:
  - project
  - youtube
---

## 專案描述

肌酸 ep
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NAKAMA_THUMBNAILS_DATA_DIR", str(tmp_path / "data_thumbs"))

    proj_dir = tmp_path / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "肌酸的妙用.md").write_text(SAMPLE_PROJECT, encoding="utf-8")

    # Cutout library — populate emotions used by the render tests.
    for emo in ("surprised", "thoughtful", "explaining"):
        d = tmp_path / "Attachments" / "cutouts" / "shosho" / emo
        d.mkdir(parents=True)
        (d / "1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_project_thumbnails as bpt_module

    importlib.reload(auth_module)
    importlib.reload(bpt_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


class TestRender:
    @pytest.fixture
    def with_ideas(self, client, tmp_path):
        """Seed project with 3 parseable thumbnail ideas (no LLM call)."""
        ideas = [
            "大字：妙用解密\n我的表情：驚訝\n視覺：左罐右腦黃閃電\n數字/圖示：⚡\n背景：白色",
            "大字：65 歲來得及\n我的表情：思考\n視覺：老人加大腦掃描\n數字/圖示：65\n背景：灰藍色",
            "大字：每天 5g\n我的表情：解釋\n視覺：湯匙加化學分子式\n"
            "數字/圖示：5g\n背景：實驗室桌面",
        ]
        path = tmp_path / "Projects" / "肌酸的妙用.md"
        raw = path.read_text(encoding="utf-8")
        parts = raw.split("---", 2)
        fm = yaml.safe_load(parts[1])
        fm["thumbnail_ideas"] = ideas
        fm_yaml = yaml.dump(fm, allow_unicode=True, default_flow_style=False)
        new_raw = "---\n" + fm_yaml + "---" + parts[2]
        path.write_text(new_raw, encoding="utf-8")
        import thousand_sunny.routers.bridge_project_thumbnails as bpt_mod

        bpt_mod._indexer_singleton = None  # noqa: SLF001
        return client

    def test_render_calls_worker_and_serves_partial(self, with_ideas, tmp_path, monkeypatch):
        """Mock the worker; verify the render endpoint returns the partial."""

        async def fake_render(*, out_png, **_kw):
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(b"\x89PNG\r\n\x1a\nrendered")
            return out_png

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_youtube_still",
            fake_render,
        )

        r = with_ideas.post(
            "/bridge/projects/肌酸的妙用/thumbnail/render",
            data={"idea_index": "0", "director_notes": "darken bg"},
        )
        assert r.status_code == 200, r.text
        assert "/thumbnail/candidate/" in r.text
        assert "v0.png" in r.text or "v0" in r.text

    def test_render_writes_manifest_with_director_notes(self, with_ideas, tmp_path, monkeypatch):
        async def fake_render(*, out_png, **_kw):
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(b"\x89PNG\r\n\x1a\nx")
            return out_png

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_youtube_still",
            fake_render,
        )

        r = with_ideas.post(
            "/bridge/projects/肌酸的妙用/thumbnail/render",
            data={"idea_index": "1", "director_notes": "shift face left"},
        )
        assert r.status_code == 200, r.text

        # Locate the run directory and verify manifest.json contents
        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        _DATA_THUMBNAILS_DIR = _thumbnails_dir()

        slug_dir = _DATA_THUMBNAILS_DIR / "肌酸的妙用" / "runs"
        ts_dirs = list(slug_dir.iterdir())
        assert len(ts_dirs) == 1
        manifest = json.loads((ts_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["renders"][0]["director_notes"] == "shift face left"
        assert manifest["renders"][0]["idea_index"] == 1

    def test_render_index_out_of_range_400(self, with_ideas):
        r = with_ideas.post(
            "/bridge/projects/肌酸的妙用/thumbnail/render",
            data={"idea_index": "99"},
        )
        assert r.status_code == 400
        assert "out of range" in r.text


class TestCandidateServing:
    def test_candidate_serves_existing_png(self, client, tmp_path):
        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        _DATA_THUMBNAILS_DIR = _thumbnails_dir()

        run_dir = _DATA_THUMBNAILS_DIR / "肌酸的妙用" / "runs" / "20260526T140000"
        run_dir.mkdir(parents=True)
        (run_dir / "v0.png").write_bytes(b"\x89PNG\r\n\x1a\nserved")

        r = client.get("/bridge/projects/肌酸的妙用/thumbnail/candidate/20260526T140000/v0.png")
        assert r.status_code == 200
        assert r.content == b"\x89PNG\r\n\x1a\nserved"

    def test_candidate_rejects_traversal_filename(self, client):
        r = client.get(
            "/bridge/projects/肌酸的妙用/thumbnail/candidate/20260526T140000/..%2Fevil.png"
        )
        # Either 400 from validator or 404 from the route not matching
        assert r.status_code in (400, 404)

    def test_candidate_rejects_bad_ts_shape(self, client):
        r = client.get("/bridge/projects/肌酸的妙用/thumbnail/candidate/not-a-ts/v0.png")
        assert r.status_code == 400

    def test_candidate_404_when_missing(self, client):
        r = client.get(
            "/bridge/projects/肌酸的妙用/thumbnail/candidate/20260526T140000/missing.png"
        )
        assert r.status_code == 404


class TestPackagingCandidateServing:
    """Tests for the packaging PNG serving endpoint (ADR-054 S6)."""

    def _seed_packaging_png(self, tmp_path: Path, episode_slug: str, filename: str) -> Path:
        d = tmp_path / "Attachments" / "packaging" / episode_slug
        d.mkdir(parents=True)
        f = d / filename
        f.write_bytes(b"\x89PNG\r\n\x1a\npkg-bytes")
        return f

    def test_packaging_candidate_serves_existing_png(self, client, tmp_path):
        self._seed_packaging_png(tmp_path, "20260723-xieboran", "pkg-L1-1.png")
        r = client.get(
            "/bridge/projects/肌酸的妙用/thumbnail/packaging/20260723-xieboran/pkg-L1-1.png"
        )
        assert r.status_code == 200
        assert r.content == b"\x89PNG\r\n\x1a\npkg-bytes"

    def test_packaging_candidate_rejects_traversal_filename(self, client):
        r = client.get(
            "/bridge/projects/肌酸的妙用/thumbnail/packaging/20260723-xieboran/..%2Fevil.png"
        )
        assert r.status_code in (400, 404)

    def test_packaging_candidate_rejects_bad_ep_slug(self, client):
        r = client.get("/bridge/projects/肌酸的妙用/thumbnail/packaging/謝伯讓/pkg-L1-1.png")
        assert r.status_code == 400

    def test_packaging_candidate_404_when_missing(self, client):
        r = client.get(
            "/bridge/projects/肌酸的妙用/thumbnail/packaging/20260723-xieboran/missing.png"
        )
        assert r.status_code == 404

    def test_packaging_candidate_rejects_cjk_filename(self, client):
        r = client.get("/bridge/projects/肌酸的妙用/thumbnail/packaging/20260723-xieboran/封面.png")
        assert r.status_code in (400, 404)


class TestCommit:
    def _seed_candidate(self, tmp_path: Path, ts: str = "20260526T140000") -> Path:
        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        _DATA_THUMBNAILS_DIR = _thumbnails_dir()

        run_dir = _DATA_THUMBNAILS_DIR / "肌酸的妙用" / "runs" / ts
        run_dir.mkdir(parents=True, exist_ok=True)
        cand = run_dir / "v0.png"
        cand.write_bytes(b"\x89PNG\r\n\x1a\nchosen-bytes")
        return cand

    def test_commit_copies_to_vault_and_updates_frontmatter(self, client, tmp_path):
        self._seed_candidate(tmp_path)

        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/commit",
            data={"run_ts": "20260526T140000", "filename": "v0.png"},
        )
        assert r.status_code == 200, r.text

        vault_thumb = tmp_path / "Attachments" / "projects" / "肌酸的妙用" / "thumbnail.png"
        assert vault_thumb.exists()
        assert vault_thumb.read_bytes() == b"\x89PNG\r\n\x1a\nchosen-bytes"

        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        assert fm["thumbnail"] == "Attachments/projects/肌酸的妙用/thumbnail.png"
        assert fm["thumbnail_run"] == "20260526T140000/v0.png"
        assert fm.get("thumbnail_chosen_at")  # ISO timestamp present

    def test_commit_archives_existing_thumbnail(self, client, tmp_path):
        # Seed an existing chosen thumbnail
        existing_dir = tmp_path / "Attachments" / "projects" / "肌酸的妙用"
        existing_dir.mkdir(parents=True)
        old_path = existing_dir / "thumbnail.png"
        old_path.write_bytes(b"\x89PNG\r\n\x1a\nold-thumb")

        self._seed_candidate(tmp_path)

        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/commit",
            data={"run_ts": "20260526T140000", "filename": "v0.png"},
        )
        assert r.status_code == 200, r.text

        archive_dir = existing_dir / "_archive"
        assert archive_dir.is_dir()
        archived = list(archive_dir.glob("*.png"))
        assert len(archived) == 1
        assert archived[0].read_bytes() == b"\x89PNG\r\n\x1a\nold-thumb"

        # New thumbnail in place
        assert old_path.read_bytes() == b"\x89PNG\r\n\x1a\nchosen-bytes"

    def test_commit_404_when_candidate_missing(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/commit",
            data={"run_ts": "20260526T140000", "filename": "v0.png"},
        )
        assert r.status_code == 404

    def test_commit_400_on_bad_filename(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/commit",
            data={"run_ts": "20260526T140000", "filename": "../etc/passwd"},
        )
        assert r.status_code == 400

    def test_commit_writes_audit_scope(self, client, tmp_path, monkeypatch):
        self._seed_candidate(tmp_path)
        captured: list[dict] = []
        with patch(
            "thousand_sunny.routers.bridge_project_thumbnails.record_api_call",
            lambda **kw: captured.append(kw),
        ):
            r = client.post(
                "/bridge/projects/肌酸的妙用/thumbnail/commit",
                data={"run_ts": "20260526T140000", "filename": "v0.png"},
            )
        assert r.status_code == 200, r.text
        scopes = [json.loads(c.get("scope_json", "{}")).get("scope") for c in captured]
        assert "thumbnail_commit" in scopes


# Podcast endpoints — ADR-033 PR4-B


SAMPLE_PODCAST_PROJECT = """\
---
type: project
content_type: podcast
created: 2026-04-10
status: active
priority: high
area: work
search_topic: 長壽訪談
one_sentence: 訪問 Dr. 王 — 老年肌力訓練
title_candidates:
  - 70 歲還能練？醫師告訴你
host_video_path: data/podcasts/wang/host_angle.mp4
guest_video_path: data/podcasts/wang/guest_angle.mp4
tags:
  - project
  - podcast
---

## 專案描述

訪談 ep
"""


@pytest.fixture
def podcast_client(monkeypatch, tmp_path):
    """Fresh TestClient with a podcast-flavored project fixture."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NAKAMA_THUMBNAILS_DATA_DIR", str(tmp_path / "data_thumbs"))

    proj_dir = tmp_path / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "王醫師專訪.md").write_text(SAMPLE_PODCAST_PROJECT, encoding="utf-8")

    # Reference library for podcast
    ref_dir = tmp_path / "Attachments" / "cutouts" / "reference" / "podcast" / "mine"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-ref")

    # Funnel uses repo_root for video resolution; tests that need a real video
    # file override _resolve_video_path via monkeypatch (see _resolve_to_tmp).
    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_project_thumbnails as bpt_module

    importlib.reload(auth_module)
    importlib.reload(bpt_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _mock_funnel_run(monkeypatch, *, candidates: list[dict]):
    """Patch ``thumbnail_funnel.run`` to return synthesised candidates."""
    from shared.thumbnail_funnel import FrameCandidate

    async def fake_run(video_path, out_dir, *, mode="conversation", top_pct=0.5, seed=42):
        out_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for c in candidates:
            p = out_dir / c["filename"]
            p.write_bytes(b"\x89PNG\r\n\x1a\nfake-frame")
            result.append(
                FrameCandidate(
                    path=p,
                    timestamp_sec=c["timestamp_sec"],
                    sample_kind=c["sample_kind"],
                    sharpness=c["sharpness"],
                )
            )
        return result

    monkeypatch.setattr(
        "thousand_sunny.routers.bridge_project_thumbnails.thumbnail_funnel.run",
        fake_run,
    )


def _resolve_to_tmp(tmp_path):
    """Override _resolve_video_path so frontmatter-relative paths land inside tmp_path."""
    import thousand_sunny.routers.bridge_project_thumbnails as bpt_module

    original = bpt_module._resolve_video_path

    def fake_resolve(raw):
        return tmp_path / raw

    return original, fake_resolve


class TestPodcastFunnel:
    def test_funnel_rejects_invalid_role(self, podcast_client):
        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/bystander")
        assert r.status_code == 400
        assert "role" in r.text.lower()

    def test_funnel_rejects_non_podcast_project(self, client):
        # YouTube fixture (content_type=youtube) — funnel must reject.
        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/podcast/funnel/host")
        assert r.status_code == 400

    def test_funnel_412_when_video_path_missing_from_frontmatter(self, podcast_client, tmp_path):
        # Strip host_video_path from frontmatter
        path = tmp_path / "Projects" / "王醫師專訪.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace("host_video_path: data/podcasts/wang/host_angle.mp4\n", "")
        path.write_text(text, encoding="utf-8")
        import thousand_sunny.routers.bridge_project_thumbnails as bpt_mod

        bpt_mod._indexer_singleton = None  # noqa: SLF001

        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host")
        assert r.status_code == 412
        assert "host_video_path" in r.text

    def test_funnel_404_when_video_file_missing_on_disk(
        self, podcast_client, monkeypatch, tmp_path
    ):
        # Frontmatter has the path but no file exists at the resolved location.
        _, fake = _resolve_to_tmp(tmp_path)
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails._resolve_video_path",
            fake,
        )
        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host")
        assert r.status_code == 404

    def test_funnel_400_when_video_path_outside_allowlist(self, podcast_client, tmp_path):
        """Defense-in-depth: path outside ALL allowlist roots (repo root + FOOTAGE_ROOT)
        must be rejected. ADR-054 S6 — allowlist replaces single-root constraint."""
        path = tmp_path / "Projects" / "王醫師專訪.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "host_video_path: data/podcasts/wang/host_angle.mp4",
            "host_video_path: ../../../../../etc/passwd",
        )
        path.write_text(text, encoding="utf-8")
        import thousand_sunny.routers.bridge_project_thumbnails as bpt_mod

        bpt_mod._indexer_singleton = None  # noqa: SLF001

        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host")
        assert r.status_code == 400
        assert "outside allowed roots" in r.text.lower() or "allowed" in r.text.lower()

    def test_funnel_allows_footage_root_path(self, podcast_client, monkeypatch, tmp_path):
        """FOOTAGE_ROOT env expands the allowlist: absolute paths under it must be
        accepted and proceed to the funnel run step (not 400). ADR-054 S6."""
        footage_dir = tmp_path / "footages"
        footage_dir.mkdir()
        video_path = footage_dir / "謝伯讓" / "host_angle.mp4"
        video_path.parent.mkdir(parents=True)
        video_path.write_bytes(b"fake mp4")

        monkeypatch.setenv("FOOTAGE_ROOT", str(footage_dir))

        proj_path = tmp_path / "Projects" / "王醫師專訪.md"
        text = proj_path.read_text(encoding="utf-8")
        text = text.replace(
            "host_video_path: data/podcasts/wang/host_angle.mp4",
            f"host_video_path: {video_path}",
        )
        proj_path.write_text(text, encoding="utf-8")
        import thousand_sunny.routers.bridge_project_thumbnails as bpt_mod

        bpt_mod._indexer_singleton = None  # noqa: SLF001

        _mock_funnel_run(
            monkeypatch,
            candidates=[
                {
                    "filename": "frame_000.png",
                    "timestamp_sec": 5.0,
                    "sample_kind": "periodic",
                    "sharpness": 800.0,
                }
            ],
        )
        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host")
        assert r.status_code == 200, r.text
        assert "frame_000.png" in r.text

    def test_funnel_happy_path(self, podcast_client, monkeypatch, tmp_path):
        # 1. Create a fake video at the resolved path
        video_dir = tmp_path / "data" / "podcasts" / "wang"
        video_dir.mkdir(parents=True)
        (video_dir / "host_angle.mp4").write_bytes(b"fake mp4 bytes")
        _, fake = _resolve_to_tmp(tmp_path)
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails._resolve_video_path",
            fake,
        )
        # 2. Mock the funnel
        _mock_funnel_run(
            monkeypatch,
            candidates=[
                {
                    "filename": "frame_000.png",
                    "timestamp_sec": 12.5,
                    "sample_kind": "periodic",
                    "sharpness": 850.0,
                },
                {
                    "filename": "frame_001.png",
                    "timestamp_sec": 33.0,
                    "sample_kind": "audio_peak",
                    "sharpness": 1200.0,
                },
            ],
        )

        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host")
        assert r.status_code == 200, r.text
        # Partial includes both candidate cells + the role marker
        assert "frame_000.png" in r.text
        assert "frame_001.png" in r.text
        assert "host" in r.text.lower()

    def test_funnel_writes_audit_scope(self, podcast_client, monkeypatch, tmp_path):
        video_dir = tmp_path / "data" / "podcasts" / "wang"
        video_dir.mkdir(parents=True)
        (video_dir / "host_angle.mp4").write_bytes(b"fake")
        _, fake = _resolve_to_tmp(tmp_path)
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails._resolve_video_path",
            fake,
        )
        _mock_funnel_run(
            monkeypatch,
            candidates=[
                {
                    "filename": "frame_000.png",
                    "timestamp_sec": 12.5,
                    "sample_kind": "periodic",
                    "sharpness": 850.0,
                }
            ],
        )
        captured: list[dict] = []
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.record_api_call",
            lambda **kw: captured.append(kw),
        )
        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host")
        assert r.status_code == 200
        scopes = [json.loads(c.get("scope_json", "{}")).get("scope") for c in captured]
        assert "thumbnail_funnel" in scopes


class TestPodcastFunnelCandidate:
    def _seed_funnel_candidate(self, tmp_path, role: str = "host"):
        """Write a fake candidate under data_thumbs/{slug}/funnel/{role}/{ts}/."""
        d = tmp_path / "data_thumbs" / "王醫師專訪" / "funnel" / role / "20260526T140000"
        d.mkdir(parents=True)
        (d / "frame_000.png").write_bytes(b"\x89PNG\r\n\x1a\ncandidate")
        return d

    def test_candidate_serves_png(self, podcast_client, tmp_path):
        self._seed_funnel_candidate(tmp_path, role="host")
        r = podcast_client.get(
            "/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host/20260526T140000/frame_000.png"
        )
        assert r.status_code == 200
        assert r.content == b"\x89PNG\r\n\x1a\ncandidate"

    def test_candidate_rejects_path_traversal(self, podcast_client, tmp_path):
        self._seed_funnel_candidate(tmp_path, role="host")
        r = podcast_client.get(
            "/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host/"
            "20260526T140000/..%2Fpasswd.png"
        )
        # Not found → 404 because path normalisation happens or invalid filename
        assert r.status_code in (400, 404)

    def test_candidate_rejects_bad_role(self, podcast_client, tmp_path):
        self._seed_funnel_candidate(tmp_path, role="host")
        r = podcast_client.get(
            "/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/intruder/"
            "20260526T140000/frame_000.png"
        )
        assert r.status_code == 400


class TestPodcastActiveCutouts:
    def _seed_funnel(self, tmp_path, role: str = "host"):
        d = tmp_path / "data_thumbs" / "王醫師專訪" / "funnel" / role / "20260526T140000"
        d.mkdir(parents=True)
        for i in range(3):
            (d / f"frame_{i:03d}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return d

    def _patch_u2net(self, monkeypatch, *, fail: bool = False):
        async def fake_u2net(src: Path, dst: Path):
            if fail:
                from thousand_sunny.routers.bridge_project_thumbnails import U2NetError

                raise U2NetError(f"simulated u2net failure for {src.name}")
            dst.write_bytes(b"\x89PNG\r\n\x1a\ntransparent")

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails._u2net_cutout",
            fake_u2net,
        )

    def test_active_cutouts_happy_path(self, podcast_client, monkeypatch, tmp_path):
        self._seed_funnel(tmp_path, role="host")
        self._patch_u2net(monkeypatch)
        r = podcast_client.post(
            "/bridge/projects/王醫師專訪/thumbnail/podcast/active-cutouts",
            data={
                "role": "host",
                "run_ts": "20260526T140000",
                "selected": ["frame_000.png", "frame_001.png"],
            },
        )
        assert r.status_code == 200, r.text
        # Vault cutouts created
        vault_dir = tmp_path / "Attachments" / "cutouts" / "podcast" / "王醫師專訪"
        assert (vault_dir / "host_v1.png").is_file()
        assert (vault_dir / "host_v2.png").is_file()
        # Frontmatter updated
        fm = yaml.safe_load(
            (tmp_path / "Projects" / "王醫師專訪.md").read_text(encoding="utf-8").split("---")[1]
        )
        active = fm["thumbnail_active_cutouts"]
        assert active["host"] == [
            "Attachments/cutouts/podcast/王醫師專訪/host_v1.png",
            "Attachments/cutouts/podcast/王醫師專訪/host_v2.png",
        ]

    def test_active_cutouts_replaces_only_one_role(self, podcast_client, monkeypatch, tmp_path):
        """Confirming host must not wipe existing guest entries."""
        # Pre-seed frontmatter with existing guest list
        proj = tmp_path / "Projects" / "王醫師專訪.md"
        text = proj.read_text(encoding="utf-8")
        text = text.replace(
            "tags:\n",
            "thumbnail_active_cutouts:\n"
            "  guest:\n"
            "    - Attachments/cutouts/podcast/王醫師專訪/guest_v1.png\n"
            "tags:\n",
        )
        proj.write_text(text, encoding="utf-8")
        # Refresh indexer
        import thousand_sunny.routers.bridge_project_thumbnails as bpt_mod

        bpt_mod._indexer_singleton = None  # noqa: SLF001

        self._seed_funnel(tmp_path, role="host")
        self._patch_u2net(monkeypatch)
        r = podcast_client.post(
            "/bridge/projects/王醫師專訪/thumbnail/podcast/active-cutouts",
            data={
                "role": "host",
                "run_ts": "20260526T140000",
                "selected": ["frame_000.png"],
            },
        )
        assert r.status_code == 200, r.text
        fm = yaml.safe_load(proj.read_text(encoding="utf-8").split("---")[1])
        active = fm["thumbnail_active_cutouts"]
        # Guest preserved
        assert active["guest"] == ["Attachments/cutouts/podcast/王醫師專訪/guest_v1.png"]
        # Host populated
        assert len(active["host"]) == 1

    def test_active_cutouts_rejects_too_many_selected(self, podcast_client, monkeypatch, tmp_path):
        self._seed_funnel(tmp_path, role="host")
        self._patch_u2net(monkeypatch)
        r = podcast_client.post(
            "/bridge/projects/王醫師專訪/thumbnail/podcast/active-cutouts",
            data={
                "role": "host",
                "run_ts": "20260526T140000",
                "selected": [
                    "frame_000.png",
                    "frame_001.png",
                    "frame_002.png",
                    "frame_003.png",  # 4th — over limit
                ],
            },
        )
        assert r.status_code == 400
        assert "1-3" in r.text or "3" in r.text

    def test_active_cutouts_400_on_invalid_filename(self, podcast_client, monkeypatch, tmp_path):
        self._seed_funnel(tmp_path, role="host")
        self._patch_u2net(monkeypatch)
        r = podcast_client.post(
            "/bridge/projects/王醫師專訪/thumbnail/podcast/active-cutouts",
            data={
                "role": "host",
                "run_ts": "20260526T140000",
                "selected": ["../etc/passwd"],
            },
        )
        assert r.status_code == 400

    def test_active_cutouts_500_on_u2net_failure(self, podcast_client, monkeypatch, tmp_path):
        self._seed_funnel(tmp_path, role="host")
        self._patch_u2net(monkeypatch, fail=True)
        r = podcast_client.post(
            "/bridge/projects/王醫師專訪/thumbnail/podcast/active-cutouts",
            data={
                "role": "host",
                "run_ts": "20260526T140000",
                "selected": ["frame_000.png"],
            },
        )
        assert r.status_code == 500
        assert "u2net" in r.text.lower() or "remove-background" in r.text.lower()
