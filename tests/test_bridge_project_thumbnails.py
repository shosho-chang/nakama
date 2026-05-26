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

    def test_brainstorm_rejects_non_youtube(self, client, tmp_path, monkeypatch):
        # Flip the fixture project to podcast
        path = tmp_path / "Projects" / "肌酸的妙用.md"
        text = path.read_text(encoding="utf-8")
        text = text.replace("content_type: youtube", "content_type: podcast")
        path.write_text(text, encoding="utf-8")
        # Re-init indexer (singleton clears across requests within reload — but
        # safe to monkeypatch a new singleton)
        import thousand_sunny.routers.bridge_project_thumbnails as bpt_mod

        bpt_mod._indexer_singleton = None  # noqa: SLF001

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 400
        assert "youtube" in r.text.lower() or "podcast" in r.text.lower()

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
