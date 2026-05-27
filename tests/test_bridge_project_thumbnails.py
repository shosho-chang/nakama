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

SAMPLE_BRAINSTORM_LLM_RESPONSE = """\
Here are 3 distinct thumbnail idea blocks:

Idea 1
大字：妙用解密
我的表情：驚訝
視覺：左罐右腦黃閃電
數字/圖示：⚡
背景：實驗室白底

Idea 2
大字：65 歲來得及
我的表情：思考
視覺：老人 + 大腦掃描
數字/圖示：65
背景：醫院走廊

Idea 3
大字：每天 5g
我的表情：解釋
視覺：湯匙 + 化學分子式
數字/圖示：5g
背景：實驗室桌面
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

    # B1 cutout library — populate `surprised`, `thoughtful`, `explaining` for
    # the 3 emotions in SAMPLE_BRAINSTORM_LLM_RESPONSE.
    for emo in ("surprised", "thoughtful", "explaining"):
        d = tmp_path / "Attachments" / "cutouts" / "shosho" / emo
        d.mkdir(parents=True)
        (d / "1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    # Reference library — at least one image so brainstorm payload includes
    # an image block (otherwise the user_message branch with no images runs).
    ref_dir = tmp_path / "Attachments" / "cutouts" / "reference" / "youtube" / "mine"
    ref_dir.mkdir(parents=True)
    (ref_dir / "ref1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-ref")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_project_thumbnails as bpt_module

    importlib.reload(auth_module)
    importlib.reload(bpt_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


class TestBrainstorm:
    def test_brainstorm_persists_three_ideas(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_LLM_RESPONSE,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 200, r.text
        body = r.text
        assert "Idea 1" in body
        assert "妙用解密" in body
        assert "65 歲來得及" in body
        assert "每天 5g" in body
        # Director's Notes textarea wired in
        assert "director_notes" in body

        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        ideas = fm["thumbnail_ideas"]
        assert isinstance(ideas, list)
        assert len(ideas) == 3
        # Round-trip parseability — feed each back through parser
        from shared.thumbnail_idea import parse_idea

        parsed = [parse_idea(i) for i in ideas]
        assert {p.emotion_key for p in parsed} == {"surprised", "thoughtful", "explaining"}

    def test_brainstorm_writes_audit_scope(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_LLM_RESPONSE,
        )
        captured: list[dict] = []
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.record_api_call",
            lambda **kw: captured.append(kw),
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 200, r.text

        assert any(
            json.loads(c.get("scope_json", "{}")).get("scope") == "thumbnail_brainstorm"
            for c in captured
        )

    def test_brainstorm_rejects_unknown_content_type(self, client, tmp_path, monkeypatch):
        """ADR-033 PR4-B opened podcast; other types still 400."""
        path = tmp_path / "Projects" / "肌酸的妙用.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace("content_type: youtube", "content_type: article")
        path.write_text(text, encoding="utf-8")
        import thousand_sunny.routers.bridge_project_thumbnails as bpt_mod

        bpt_mod._indexer_singleton = None  # noqa: SLF001

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 400
        assert "youtube|podcast" in r.text or "youtube" in r.text.lower()

    def test_brainstorm_502_on_unparseable_llm_response(self, client, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: "totally unstructured text with no 大字 anywhere",
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 502


class TestTitleBrainstorm:
    def test_titles_persists_three_candidates(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: (
                "肌酸不只練肌肉：3 個你沒聽過的妙用\n"
                "65 歲開始吃肌酸？最新研究說：來得及\n"
                "每天 5g，改變你大腦的化學反應\n"
            ),
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm-titles")
        assert r.status_code == 200, r.text
        # Returned partial is the textarea with 3 lines
        assert "肌酸不只練肌肉" in r.text
        assert "65 歲開始吃肌酸" in r.text
        assert "每天 5g" in r.text

        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        assert len(fm["title_candidates"]) == 3
        assert fm["title_candidates"][0] == "肌酸不只練肌肉：3 個你沒聽過的妙用"

    def test_titles_strips_numbered_prefix(self, client, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: "1. 肌酸的真相\n2) 你不知道的事\n(3) 65 歲的選擇\n",
        )
        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm-titles")
        assert r.status_code == 200, r.text
        assert "1. 肌酸" not in r.text
        assert "肌酸的真相" in r.text
        assert "你不知道的事" in r.text
        assert "65 歲的選擇" in r.text

    def test_titles_strips_bullet_markers(self, client, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: "- A 候選\n• B 候選\n* C 候選\n",
        )
        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm-titles")
        assert r.status_code == 200, r.text
        assert "- A 候選" not in r.text
        assert "A 候選" in r.text

    def test_titles_caps_at_3(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: "title 1\ntitle 2\ntitle 3\ntitle 4\ntitle 5\n",
        )
        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm-titles")
        assert r.status_code == 200, r.text
        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        assert len(fm["title_candidates"]) == 3
        assert fm["title_candidates"] == ["title 1", "title 2", "title 3"]

    def test_titles_skips_preamble_lines(self, client, tmp_path, monkeypatch):
        """Lines starting with 'Here', '以下', 'Title' etc are LLM preamble — never stored."""
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: (
                "Here are 3 title candidates:\n"
                "以下三個候選：\n"
                "肌酸的真相\n"
                "65 歲還來得及嗎\n"
                "每天 5g 的改變\n"
            ),
        )
        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm-titles")
        assert r.status_code == 200, r.text
        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        assert fm["title_candidates"] == ["肌酸的真相", "65 歲還來得及嗎", "每天 5g 的改變"]
        assert all("Here are" not in t for t in fm["title_candidates"])
        assert all("以下" not in t for t in fm["title_candidates"])

    def test_titles_502_on_empty_response(self, client, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: "",
        )
        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm-titles")
        assert r.status_code == 502


class TestRender:
    @pytest.fixture
    def with_ideas(self, client, tmp_path, monkeypatch):
        """Seed the project with 3 brainstormed ideas (no LLM needed for render tests)."""
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_LLM_RESPONSE,
        )
        client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
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

    def test_funnel_400_when_video_path_escapes_repo_root(self, podcast_client, tmp_path):
        """Defense-in-depth: frontmatter host_video_path that resolves outside
        repo root must be rejected (post-review hardening 2026-05-26)."""
        path = tmp_path / "Projects" / "王醫師專訪.md"
        text = path.read_text(encoding="utf-8")
        # Replace with traversal attempt
        text = text.replace(
            "host_video_path: data/podcasts/wang/host_angle.mp4",
            "host_video_path: ../../../../../etc/passwd",
        )
        path.write_text(text, encoding="utf-8")
        import thousand_sunny.routers.bridge_project_thumbnails as bpt_mod

        bpt_mod._indexer_singleton = None  # noqa: SLF001

        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/podcast/funnel/host")
        assert r.status_code == 400
        assert "escapes" in r.text.lower() or "repo root" in r.text.lower()

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


class TestPodcastBrainstormHappyPath:
    def test_podcast_brainstorm_uses_podcast_prompt(self, podcast_client, monkeypatch):
        prompts_seen: list[str] = []

        def fake_llm(messages, *, system=None, model=None, max_tokens=2048):
            prompts_seen.append(system or "")
            return SAMPLE_BRAINSTORM_LLM_RESPONSE

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            fake_llm,
        )
        r = podcast_client.post("/bridge/projects/王醫師專訪/thumbnail/brainstorm")
        assert r.status_code == 200, r.text
        # Podcast prompt has the DOAC / two-person framing — verify it was loaded.
        assert "DOAC" in prompts_seen[0] or "兩人" in prompts_seen[0] or "host" in prompts_seen[0]


# ── v1.1 playbook integration + B-min refinement endpoints ───────────────────


SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS = """\
Idea 1
archetype: [T-A1, T-V10, JP-4]
大字：8 個習慣
我的表情：驚訝
視覺：圖示列表
數字/圖示：8
背景：白色

Idea 2
archetype: [T-A8, T-V3]
大字：12 週實證
我的表情：思考
視覺：左右對照
數字/圖示：12
背景：醫院走廊

Idea 3
archetype: [T-A6, T-V4]
大字：真的嗎
我的表情：解釋
視覺：問句覆字
數字/圖示：?
背景：深藍科學感
"""


class TestPlaybookIntegration:
    """v1.1: playbook archetype index injected, vision images removed."""

    def test_brainstorm_user_message_includes_playbook_index_no_images(self):
        from thousand_sunny.routers.bridge_project_thumbnails import (
            _brainstorm_user_message,
        )

        parts = _brainstorm_user_message(
            title_candidates=["Test title"],
            one_sentence="test sentence",
            search_topic="test",
        )
        # All parts are text now — no image attachments
        assert all(p.get("type") == "text" for p in parts)
        # Playbook index present
        combined = "\n".join(p["text"] for p in parts)
        assert "Playbook archetype index" in combined
        assert "T-A1" in combined and "T-V6" in combined
        # Brief preserved
        assert "Test title" in combined and "test sentence" in combined

    def test_brainstorm_persists_archetype_tags(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 200, r.text

        # Meta JSON written with archetype tags per idea
        meta_path = tmp_path / "data_thumbs" / "肌酸的妙用" / "brainstorm_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["schema_version"] == "v1"
        runs = meta["runs"]
        assert len(runs) == 1
        idea_tags = [i["archetype_tags"] for i in runs[0]["ideas"]]
        assert ["T-A1", "T-V10", "JP-4"] in idea_tags
        assert ["T-A8", "T-V3"] in idea_tags
        assert ["T-A6", "T-V4"] in idea_tags

    def test_brainstorm_emits_archetype_tags_in_audit(self, client, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS,
        )
        captured: list[dict] = []
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.record_api_call",
            lambda **kw: captured.append(kw),
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 200, r.text

        brainstorm_audit = next(
            c
            for c in captured
            if json.loads(c.get("scope_json", "{}")).get("scope") == "thumbnail_brainstorm"
        )
        scope = json.loads(brainstorm_audit["scope_json"])
        assert scope["n_references"] == 0  # v1.1: no image few-shot
        assert "archetype_tags" in scope
        assert ["T-A1", "T-V10", "JP-4"] in scope["archetype_tags"]


class TestIdeaSaveEdit:
    """B-min.1: editable idea card + save endpoint."""

    def _seed_three_ideas(self, client, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_LLM_RESPONSE,
        )
        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 200, r.text

    def test_save_edit_updates_frontmatter_at_index(self, client, tmp_path, monkeypatch):
        self._seed_three_ideas(client, monkeypatch)

        new_value = (
            "archetype: [T-A2, T-V4]\n"
            "大字：手動編輯版\n"
            "我的表情：解釋\n"
            "視覺：edited visual\n"
            "數字/圖示：5\n"
            "背景：edited bg"
        )
        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/idea/1",
            data={"value": new_value},
        )
        assert r.status_code == 200, r.text
        assert "手動編輯版" in r.text

        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        # idx=1 replaced, idx=0 and idx=2 unchanged
        assert "手動編輯版" in fm["thumbnail_ideas"][1]
        assert "妙用解密" in fm["thumbnail_ideas"][0]
        assert "每天 5g" in fm["thumbnail_ideas"][2]

    def test_save_edit_out_of_range_400(self, client, monkeypatch):
        self._seed_three_ideas(client, monkeypatch)
        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/idea/99",
            data={"value": "anything"},
        )
        assert r.status_code == 400
        assert "out of range" in r.text.lower()

    def test_save_edit_empty_value_400(self, client, monkeypatch):
        self._seed_three_ideas(client, monkeypatch)
        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/idea/0",
            data={"value": "   "},
        )
        assert r.status_code == 400

    def test_save_edit_invalid_idea_surfaces_parse_error_inline(self, client, monkeypatch):
        self._seed_three_ideas(client, monkeypatch)
        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/idea/0",
            data={"value": "broken — missing required lines"},
        )
        # Save succeeds (200) but partial surfaces parse error inline
        assert r.status_code == 200
        assert "解析失敗" in r.text or "parse" in r.text.lower()


class TestIdeaIndividualReroll:
    """B-min.2: re-roll a single idea slot, keep others verbatim."""

    def test_idea_reroll_swaps_only_target_idx(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_LLM_RESPONSE,
        )
        # Seed
        client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")

        # Stub a fresh LLM that produces a single fresh idea different from kept
        fresh = (
            "Idea 1\n"
            "archetype: [T-A3, T-V8]\n"
            "大字：新版 hook\n"
            "我的表情：認真\n"
            "視覺：新視覺\n"
            "數字/圖示：新\n"
            "背景：新背景\n"
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: fresh,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm/idea/0")
        assert r.status_code == 200, r.text
        # Response is the full 3-card grid swap
        assert "新版 hook" in r.text
        assert "每天 5g" in r.text  # idx=2 preserved

        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        assert "新版 hook" in fm["thumbnail_ideas"][0]
        assert "65 歲來得及" in fm["thumbnail_ideas"][1]
        assert "每天 5g" in fm["thumbnail_ideas"][2]


class TestTitleIndividualReroll:
    """B-min.3: re-roll a single title row, keep others verbatim."""

    def test_title_reroll_with_textarea_value(self, client, tmp_path, monkeypatch):
        # Provide current textarea content; LLM returns one new title
        new_title = "5g 肌酸是大腦的祕密武器（哈佛研究）"
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: new_title + "\n",
        )

        current = (
            "肌酸不只練肌肉：3 個你沒聽過的妙用\n"
            "65 歲開始吃肌酸？最新研究說：來得及\n"
            "每天 5g，改變你大腦的化學反應\n"
        )
        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/brainstorm-titles/idea/1",
            data={"value": current},
        )
        assert r.status_code == 200, r.text
        # New title shows up; original titles 0 + 2 still there
        assert new_title in r.text
        assert "肌酸不只練肌肉" in r.text
        assert "每天 5g，改變你大腦" in r.text

        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        assert fm["title_candidates"][0] == "肌酸不只練肌肉：3 個你沒聽過的妙用"
        assert fm["title_candidates"][1] == new_title
        assert fm["title_candidates"][2] == "每天 5g，改變你大腦的化學反應"

    def test_title_reroll_out_of_range_400(self, client, monkeypatch):
        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/brainstorm-titles/idea/99",
            data={"value": "a\nb\nc"},
        )
        assert r.status_code == 400
