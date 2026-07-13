"""ADR-033 PR4-A — sibling router endpoints (mocked LLM + render).

We don't shell out to ``npx hyperframes`` or call Sonnet here; both are mocked.
Tests verify the routing, frontmatter writes, candidate-serving file safety,
commit archive rotation, and audit log emission.
"""

from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

PROJECT_SLUG = "\u808c\u9178\u7684\u5999\u7528"
PROJECT_ROUTE = "/bridge/projects/%E8%82%8C%E9%85%B8%E7%9A%84%E5%A6%99%E7%94%A8"

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
archetype: [T-A2, T-V1, JP-3]
lane: Jeff Clean Tutorial
recipe: jeff_clean_tutorial_dual_zone
reference_template: jeff_tool_header_panel
title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用
component: ui_panel
component_text: 妙用解密 / 護腦 / 抗老
host: one third, friendly, gaze toward panel
viewer_promise: 觀眾會快速知道肌酸除了肌肉之外還有什麼實用好處
evidence_fit: 影片會用研究整理支持大腦與日常健康的範圍
trust_risk: 避免暗示肌酸能治療疾病
大字：妙用解密
我的表情：驚訝
視覺：template=jeff_tool_header_panel; component=ui_panel; text=妙用解密/護腦/抗老; host=one third
數字/圖示：⚡
背景：實驗室白底
素材需求：creatine jar cutout; clean lab table; lightning icon

Idea 2
archetype: [T-A8, T-V3]
lane: Ali Warm Explainer
recipe: ali_warm_evidence_list
reference_template: shosho_benefit_list_card
title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用
component: benefit_list_card
component_text: 護腦 / 抗老 / 認知
host: left third, face large, gaze toward card
viewer_promise: 觀眾會感覺這是有研究支撐的溫和整理
evidence_fit: 影片有研究脈絡可以支撐長者與認知角度
trust_risk: 不把年齡當成醫療承諾
大字：65 歲來得及
我的表情：思考
視覺：template=shosho_benefit_list_card; component=benefit_list_card; host=left
數字/圖示：65
背景：醫院走廊
素材需求：older adult lifestyle photo; brain scan abstract; warm paper texture

Idea 3
archetype: [T-A1, T-V10, JP-4]
lane: Jeff Clean Tutorial
recipe: jeff_80_percent_protocol
reference_template: shosho_benefit_list_card
title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用
component: benefit_list_card
component_text: 5g / 護腦 / 增力
host: left third, explaining, gaze toward card
viewer_promise: 觀眾會期待一個簡單劑量與效益框架
evidence_fit: 影片會講每日 5g 作為常見研究劑量
trust_risk: 不把 5g 包裝成每個人的處方
大字：每天 5g
我的表情：解釋
視覺：template=shosho_benefit_list_card; component=benefit_list_card; host=left
數字/圖示：5g
背景：實驗室桌面
素材需求：supplement scoop photo; molecule icon; dark green metric background
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NAKAMA_THUMBNAILS_DATA_DIR", str(tmp_path / "data_thumbs"))
    monkeypatch.setenv("NAKAMA_TITLE_POOL_DATA_DIR", str(tmp_path / "title_pool"))

    proj_dir = tmp_path / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "肌酸的妙用.md").write_text(SAMPLE_PROJECT, encoding="utf-8")

    # B1 cutout library — populate `surprised`, `thoughtful`, `explaining` for
    # the 3 emotions in SAMPLE_BRAINSTORM_LLM_RESPONSE.
    from PIL import Image

    for emo in ("surprised", "thoughtful", "explaining"):
        d = tmp_path / "Attachments" / "cutouts" / "shosho" / emo
        d.mkdir(parents=True)
        Image.new("RGBA", (180, 260), (48, 120, 200, 255)).save(d / "1.png")

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

    def test_brainstorm_writes_asset_manifest(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_LLM_RESPONSE,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 200, r.text

        asset_path = tmp_path / "data_thumbs" / "肌酸的妙用" / "asset_manifest.json"
        manifest = json.loads(asset_path.read_text(encoding="utf-8"))
        assert manifest["schema_version"] == "thumbnail_asset_manifest.v1"
        assert manifest["policy"]["download_allowed"] is False
        assert manifest["policy"]["requires_manual_license_registration"] is True
        assert len(manifest["items"]) == 9
        assert manifest["items"][0]["query"] == "creatine jar cutout"
        assert manifest["items"][0]["provenance"]["provider"] == ""

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


class TestAssetManifestWorkflow:
    """Bridge UI surface for thumbnail asset sourcing provenance."""

    def _seed_manifest(self, client, monkeypatch):
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_LLM_RESPONSE,
        )
        r = client.post(f"{PROJECT_ROUTE}/thumbnail/brainstorm")
        assert r.status_code == 200, r.text
        assert r.headers.get("HX-Trigger") == "thumbnail-assets-changed"

    def test_assets_endpoint_renders_search_links_after_brainstorm(self, client, monkeypatch):
        self._seed_manifest(client, monkeypatch)

        r = client.get(f"{PROJECT_ROUTE}/thumbnail/assets")

        assert r.status_code == 200, r.text
        assert "creatine jar cutout" in r.text
        assert "Envato Elements" in r.text
        assert "download_allowed" in r.text

    def test_assets_update_records_candidate_provenance(self, client, tmp_path, monkeypatch):
        self._seed_manifest(client, monkeypatch)

        r = client.post(
            f"{PROJECT_ROUTE}/thumbnail/assets/idea01-asset01",
            data={
                "status": "candidate_found",
                "provider": "envato_elements",
                "asset_url": "https://elements.envato.com/creatine-jar",
                "provider_asset_id": "EV-123",
                "author": "Envato Creator",
                "notes": "clean product cutout candidate",
            },
        )

        assert r.status_code == 200, r.text
        assert "candidate recorded" in r.text
        manifest_path = tmp_path / "data_thumbs" / PROJECT_SLUG / "asset_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        item = manifest["items"][0]
        assert item["status"] == "candidate_found"
        assert item["provenance"]["provider"] == "envato_elements"
        assert item["provenance"]["provider_asset_id"] == "EV-123"

    def test_assets_update_requires_license_evidence_before_licensed(
        self, client, tmp_path, monkeypatch
    ):
        self._seed_manifest(client, monkeypatch)

        bad = client.post(
            f"{PROJECT_ROUTE}/thumbnail/assets/idea01-asset01",
            data={
                "status": "licensed",
                "provider": "envato_elements",
                "asset_url": "https://elements.envato.com/creatine-jar",
            },
        )
        assert bad.status_code == 400
        assert "licensed requires" in bad.text

        good = client.post(
            f"{PROJECT_ROUTE}/thumbnail/assets/idea01-asset01",
            data={
                "status": "licensed",
                "provider": "envato_elements",
                "asset_url": "https://elements.envato.com/creatine-jar",
                "license_registration": f"Project Use: {PROJECT_SLUG}",
                "local_path": f"Attachments/projects/{PROJECT_SLUG}/assets/creatine.png",
            },
        )

        assert good.status_code == 200, good.text
        manifest_path = tmp_path / "data_thumbs" / PROJECT_SLUG / "asset_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["items"][0]["status"] == "licensed"

    def test_assets_rebuild_from_current_thumbnail_ideas(self, client, tmp_path, monkeypatch):
        self._seed_manifest(client, monkeypatch)
        manifest_path = tmp_path / "data_thumbs" / PROJECT_SLUG / "asset_manifest.json"
        manifest_path.unlink()

        r = client.post(f"{PROJECT_ROUTE}/thumbnail/assets/rebuild")

        assert r.status_code == 200, r.text
        rebuilt = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert rebuilt["source"]["kind"] == "thumbnail_asset_manifest_rebuild"
        assert len(rebuilt["items"]) == 9

    def test_assets_rebuild_preserves_existing_provenance(self, client, tmp_path, monkeypatch):
        self._seed_manifest(client, monkeypatch)
        client.post(
            f"{PROJECT_ROUTE}/thumbnail/assets/idea01-asset01",
            data={
                "status": "candidate_found",
                "provider": "envato_elements",
                "asset_url": "https://elements.envato.com/creatine-jar",
                "provider_asset_id": "EV-123",
            },
        )

        r = client.post(f"{PROJECT_ROUTE}/thumbnail/assets/rebuild")

        assert r.status_code == 200, r.text
        manifest_path = tmp_path / "data_thumbs" / PROJECT_SLUG / "asset_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        first = manifest["items"][0]
        assert first["query"] == "creatine jar cutout"
        assert first["status"] == "candidate_found"
        assert first["provenance"]["provider_asset_id"] == "EV-123"

    def test_external_thumbnail_import_normalizes_and_records_candidate(
        self, client, tmp_path, monkeypatch
    ):
        self._seed_manifest(client, monkeypatch)

        from PIL import Image, ImageDraw

        image = Image.new("RGB", (1536, 1024), (28, 40, 48))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 760, 1024), fill=(240, 220, 120))
        draw.rectangle((760, 0, 1536, 1024), fill=(40, 115, 220))
        draw.ellipse((520, 220, 1010, 710), fill=(250, 250, 250))
        buf = io.BytesIO()
        image.save(buf, format="JPEG")
        buf.seek(0)

        r = client.post(
            f"{PROJECT_ROUTE}/thumbnail/import",
            data={
                "idea_index": "0",
                "provider_model": "gpt-image-2",
                "feedback": "first external generation",
            },
            files={"generated_image": ("gpt-image-2.jpg", buf.getvalue(), "image/jpeg")},
        )

        assert r.status_code == 200, r.text
        assert "Commit final thumbnail" in r.text
        runs_dir = tmp_path / "data_thumbs" / PROJECT_SLUG / "runs"
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1
        candidate = run_dirs[0] / "v0_external.png"
        assert candidate.is_file()
        with Image.open(candidate) as saved:
            assert saved.size == (1280, 720)

        manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        record = manifest["renders"][0]
        assert record["source"] == "external_image_model_import"
        assert record["stage"] == "full"
        assert record["provider_model"] == "gpt-image-2"
        assert record["feedback"] == "first external generation"
        assert record["upload"]["source_width"] == 1536
        assert record["upload"]["source_height"] == 1024
        assert record["prompt_package"]["model"] == "gpt-image-2"
        assert record["visual_qa"]["width"] == 1280
        assert record["visual_qa"]["height"] == 720

    def test_brainstorm_rejects_youtube_v2_contract_missing_metadata(self, client, monkeypatch):
        incomplete = """\
Idea 1
archetype: [T-A2, T-V1]
大字：a
我的表情：解釋
視覺：a
背景：a

Idea 2
archetype: [T-A2, T-V1]
大字：b
我的表情：解釋
視覺：b
背景：b

Idea 3
archetype: [T-A2, T-V1]
大字：c
我的表情：解釋
視覺：c
背景：c
"""
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: incomplete,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 502
        assert "contract" in r.text

    def test_brainstorm_rejects_unsupported_visual_tag(self, client, monkeypatch):
        bad = SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS.replace("[T-A2, T-V1]", "[T-A2, T-V4]")
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: bad,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 502
        assert "unsupported visual tag T-V4" in r.text

    def test_brainstorm_rejects_unknown_reference_template(self, client, monkeypatch):
        bad = SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS.replace(
            "reference_template: jeff_tool_header_panel",
            "reference_template: imaginary_template",
            1,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: bad,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 502
        assert "unknown reference_template imaginary_template" in r.text

    def test_brainstorm_rejects_component_that_does_not_match_template(self, client, monkeypatch):
        bad = SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS.replace(
            "component: benefit_list_card",
            "component: ui_panel",
            1,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: bad,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 502
        assert "does not match reference_template shosho_benefit_list_card" in r.text

    def test_brainstorm_retries_once_on_youtube_v2_contract_error(self, client, monkeypatch):
        bad = SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS.replace(
            "component: benefit_list_card",
            "component: ui_panel",
            1,
        )
        responses = [bad, SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS]
        calls: list[list[dict]] = []

        def fake_llm(messages, *, system=None, model=None, max_tokens=2048):
            calls.append(messages)
            return responses.pop(0)

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            fake_llm,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")

        assert r.status_code == 200, r.text
        assert len(calls) == 2
        second_content = calls[1][0]["content"]
        assert any("Contract repair request" in part["text"] for part in second_content)

    def test_brainstorm_normalizes_long_visual_brief(self, client, tmp_path, monkeypatch):
        bad = SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS.replace(
            ("視覺：template=shosho_benefit_list_card; component=benefit_list_card; host=left"),
            "視覺：" + ("這是一段過長的散文式構圖描述，" * 12),
            1,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: bad,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 200, r.text

        fm = yaml.safe_load(
            (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8").split("---")[1]
        )
        from shared.thumbnail_idea import parse_idea

        parsed = [parse_idea(raw) for raw in fm["thumbnail_ideas"]]
        assert all(len(idea.visual) <= 120 for idea in parsed)
        assert all("template=" in idea.visual and "component=" in idea.visual for idea in parsed)
        assert all("散文式構圖" not in raw for raw in fm["thumbnail_ideas"])

    def test_youtube_v2_normalizer_softens_shosho_policy_mismatch(self):
        from shared.thumbnail_idea import parse_ideas_batch
        import thousand_sunny.routers.bridge_project_thumbnails as bpt

        raw = """\
Idea 1
archetype: [T-A4, T-V1]
lane: Jeff Clean Tutorial
recipe: jeff_clean_tutorial_dual_zone
reference_template: shosho_benefit_list_card
title_pairing: Creatine benefits
component: benefit_list_card
component_text: brain / aging / brain
host: left or right third, face large
viewer_promise: clear health benefits
evidence_fit: evidence-backed summary
trust_risk: no medical claim
大字：not just muscle
emotion: serious
visual: template=shosho_benefit_list_card; component=benefit_list_card; host=left
accent: 6
background: warm study room
assets: note card; brain icon
"""

        ideas = parse_ideas_batch(raw)
        repaired_text, repaired_ideas = bpt._normalize_youtube_v2_response_text(raw, ideas)
        repaired = repaired_ideas[0]

        assert repaired.lane == "Ali Warm Explainer"
        assert repaired.recipe_id == "ali_warm_evidence_list"
        assert repaired.emotion_key == "explaining"
        assert repaired.emotion_input == "\u89e3\u91cb"
        assert repaired.host_directive == (
            "face large on a visual third; gaze or hand toward benefit card"
        )
        assert repaired.component_text == ("brain", "aging", "\u8b77\u8166")
        assert "lane: Ali Warm Explainer" in repaired_text
        assert "recipe: ali_warm_evidence_list" in repaired_text
        assert "emotion: serious" not in repaired_text

    def test_host_token_treats_left_or_right_as_unspecified_card_slot(self):
        from shared.thumbnail_idea import ParsedIdea
        import thousand_sunny.routers.bridge_project_thumbnails as bpt

        idea = ParsedIdea(
            hook="x",
            emotion_key="explaining",
            emotion_input="explaining",
            visual="template=shosho_benefit_list_card; component=benefit_list_card",
            decoration="",
            bg="warm",
            reference_template_id="shosho_benefit_list_card",
            component_type="benefit_list_card",
            host_directive="left or right third",
        )

        assert bpt._host_token(idea) == "card"


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
    def test_display_accent_token_keeps_badge_and_drops_design_note(self):
        from thousand_sunny.routers.bridge_project_thumbnails import _display_accent_token

        assert _display_accent_token("6(list badge top-left)") == "6"
        assert _display_accent_token("65 歲") == "65 歲"
        assert _display_accent_token("⚡") == "⚡"
        assert _display_accent_token("list card should be orange and placed top left") == ""

    @pytest.fixture
    def with_ideas(self, client, tmp_path, monkeypatch):
        """Seed the project with 3 brainstormed ideas (no LLM needed for render tests)."""

        async def fake_generate_bg(*, out_png, **_kw):
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(b"\x89PNG\r\n\x1a\nfake-bg")
            return {"provider": "test", "mode": "unit"}

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            lambda *a, **kw: SAMPLE_BRAINSTORM_LLM_RESPONSE,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.generate_thumbnail_bg",
            fake_generate_bg,
        )
        client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        return client

    def test_render_calls_staged_renderer_and_serves_partial(
        self, with_ideas, tmp_path, monkeypatch
    ):
        """Mock the staged renderer; verify the render endpoint returns the partial."""

        def fake_render(*, out_png, **_kw):
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(b"\x89PNG\r\n\x1a\nrendered")
            return out_png

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
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
        def fake_render(*, out_png, **_kw):
            out_png.parent.mkdir(parents=True, exist_ok=True)
            out_png.write_bytes(b"\x89PNG\r\n\x1a\nx")
            return out_png

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
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
        assert manifest["renders"][0]["component_plan"] is not None
        assert manifest["renders"][0]["ai_image_gen"] is None
        assert manifest["renders"][0]["visual_qa"]["status"] == "fail"

    def test_generate_openai_uses_stored_host_reference_and_records_candidate(
        self, with_ideas, tmp_path, monkeypatch
    ):
        from PIL import Image, ImageDraw

        host_ref = tmp_path / "host-reference.png"
        Image.new("RGBA", (480, 720), (30, 60, 90, 255)).save(host_ref)

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails._pick_youtube_host_with_reference_gate",
            lambda *_args, **_kw: (host_ref, {"selected": {"cutout_id": "HOST_REF"}}),
        )

        captured: dict = {}

        async def fake_generate(*, prompt, out_png, reference_images, quality, model, **_kw):
            captured["prompt"] = prompt
            captured["reference_images"] = list(reference_images)
            captured["quality"] = quality
            captured["model"] = model
            out_png.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (1536, 1024), (20, 30, 40))
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 768, 1024), fill=(245, 210, 90))
            draw.rectangle((768, 0, 1536, 1024), fill=(35, 120, 230))
            draw.ellipse((520, 180, 1040, 700), fill=(250, 250, 250))
            image.save(out_png, format="PNG")
            return {
                "model": model,
                "quality": quality,
                "size": "1536x1024",
                "estimated_output_cost_usd": 0.047,
                "out_png": str(out_png),
                "reference_images": [str(path) for path in reference_images],
            }

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.generate_thumbnail_from_references",
            fake_generate,
        )

        r = with_ideas.post(
            f"{PROJECT_ROUTE}/thumbnail/generate-openai",
            data={"idea_index": "0", "quality": "medium", "feedback": "face larger"},
        )

        assert r.status_code == 200, r.text
        assert "Commit final thumbnail" in r.text
        assert captured["reference_images"] == [host_ref]
        assert captured["quality"] == "medium"
        assert "Exact thumbnail headline text" in captured["prompt"]
        assert "face larger" in captured["prompt"]

        runs_dir = tmp_path / "data_thumbs" / PROJECT_SLUG / "runs"
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1
        candidate = run_dirs[0] / "v0_openai.png"
        raw = run_dirs[0] / "v0_openai_raw.png"
        assert candidate.is_file()
        assert raw.is_file()
        with Image.open(candidate) as saved:
            assert saved.size == (1280, 720)

        manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        record = manifest["renders"][0]
        assert record["source"] == "openai_direct_generation"
        assert record["provider_model"] == "gpt-image-2"
        assert record["quality"] == "medium"
        assert record["reference_mode"] == "host_only"
        assert record["references"] == [str(host_ref)]
        assert record["ai_image_gen"]["estimated_output_cost_usd"] == 0.047
        assert record["visual_qa"]["width"] == 1280
        assert record["visual_qa"]["height"] == 720

    def test_render_uses_pose_manifest_when_available(self, with_ideas, tmp_path, monkeypatch):
        cutout = tmp_path / "Attachments" / "cutouts" / "shosho" / "thoughtful" / "1.png"
        manifest_path = tmp_path / "pose_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "shosho_cutout_pose_manifest.v1",
                    "entries": [
                        {
                            "cutout_id": "C_SAFE",
                            "source_path": str(cutout),
                            "vault_relative_path": "Attachments/cutouts/shosho/thoughtful/1.png",
                            "original_emotion_folder": "thoughtful",
                            "tags": {
                                "body_angle": "front",
                                "gaze": "camera",
                                "expression_family": "thoughtful",
                                "intensity": "subtle",
                                "mouth": "slight_smile",
                                "brow": "relaxed",
                                "hands": "chin",
                                "crop": "waist",
                                "credibility": "high",
                            },
                            "use_context": ["ali_warm_explainer", "evidence_review"],
                            "avoid_context": ["warning"],
                            "confidence": 0.9,
                            "picker_policy": "eligible",
                        },
                        {
                            "cutout_id": "C_LOUD",
                            "source_path": str(
                                tmp_path
                                / "Attachments"
                                / "cutouts"
                                / "shosho"
                                / "surprised"
                                / "1.png"
                            ),
                            "vault_relative_path": "Attachments/cutouts/shosho/surprised/1.png",
                            "original_emotion_folder": "surprised",
                            "tags": {
                                "body_angle": "front",
                                "gaze": "camera",
                                "expression_family": "mild_surprise",
                                "intensity": "extreme",
                                "mouth": "open_wide",
                                "brow": "raised",
                                "hands": "hands_on_head",
                                "crop": "waist",
                                "credibility": "low",
                            },
                            "use_context": ["comedy_only"],
                            "avoid_context": ["ali_warm_explainer", "evidence_review"],
                            "confidence": 0.95,
                            "picker_policy": "manual_only",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("NAKAMA_CUTOUT_POSE_MANIFEST", str(manifest_path))
        r = with_ideas.post(
            f"{PROJECT_ROUTE}/thumbnail/render",
            data={"idea_index": "1", "director_notes": "", "stage": "person"},
        )
        assert r.status_code == 200, r.text
        assert "person placement" in r.text

        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        slug_dir = _thumbnails_dir() / PROJECT_SLUG / "runs"
        ts_dirs = list(slug_dir.iterdir())
        assert len(ts_dirs) == 1
        assert (ts_dirs[0] / "v1_person.png").is_file()
        manifest = json.loads((ts_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["renders"][0]["cutout_casting"]["selected"]["cutout_id"] == "C_SAFE"
        assert Path(manifest["renders"][0]["person_placement"]["cutout_path"]) == cutout
        assert manifest["renders"][0]["person_placement"]["filename"] == "v1_person.png"

    def test_render_person_stage_skips_expensive_full_render(
        self, with_ideas, tmp_path, monkeypatch
    ):
        async def fail_generate_bg(**_kw):
            pytest.fail("person stage should not generate an AI background")

        def fail_render_youtube(**_kw):
            pytest.fail("person stage should not render the full thumbnail")

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.generate_thumbnail_bg",
            fail_generate_bg,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_youtube_still",
            fail_render_youtube,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
            fail_render_youtube,
        )

        r = with_ideas.post(
            f"{PROJECT_ROUTE}/thumbnail/render",
            data={"idea_index": "0", "director_notes": "", "stage": "person"},
        )
        assert r.status_code == 200, r.text
        assert "Step 1 - person placement" in r.text
        assert "full render not run" in r.text
        assert "experimental full render" not in r.text

        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        slug_dir = _thumbnails_dir() / PROJECT_SLUG / "runs"
        ts_dirs = list(slug_dir.iterdir())
        assert len(ts_dirs) == 1
        assert (ts_dirs[0] / "v0_person.png").is_file()
        assert not (ts_dirs[0] / "v0.png").exists()
        manifest = json.loads((ts_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        first = manifest["renders"][0]
        assert first["stage"] == "person"
        assert first["filename"] == "v0_person.png"
        assert first["full_render_filename"] is None

    def test_render_layout_stage_writes_blocking_preview(self, with_ideas, tmp_path, monkeypatch):
        async def fail_generate_bg(**_kw):
            pytest.fail("layout stage should not generate an AI background")

        def fail_render_youtube(**_kw):
            pytest.fail("layout stage should not render the full thumbnail")

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.generate_thumbnail_bg",
            fail_generate_bg,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_youtube_still",
            fail_render_youtube,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
            fail_render_youtube,
        )

        r = with_ideas.post(
            f"{PROJECT_ROUTE}/thumbnail/render",
            data={"idea_index": "0", "director_notes": "card lower", "stage": "layout"},
        )
        assert r.status_code == 200, r.text
        assert "Step 2 - layout blocking" in r.text
        assert "right content" in r.text
        assert "full render not run" in r.text

        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        slug_dir = _thumbnails_dir() / PROJECT_SLUG / "runs"
        ts_dirs = list(slug_dir.iterdir())
        assert len(ts_dirs) == 1
        assert (ts_dirs[0] / "v0_layout.png").is_file()
        assert not (ts_dirs[0] / "v0.png").exists()
        manifest = json.loads((ts_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        first = manifest["renders"][0]
        assert first["stage"] == "layout"
        assert first["filename"] == "v0_layout.png"
        assert first["layout_blocking"]["content_side"] == "right"
        assert first["layout_blocking"]["layout_spec"]["director_notes"] == "card lower"

    def test_render_type_stage_writes_typography_preview(self, with_ideas, tmp_path, monkeypatch):
        async def fail_generate_bg(**_kw):
            pytest.fail("type stage should not generate an AI background")

        def fail_render_youtube(**_kw):
            pytest.fail("type stage should not render the full thumbnail")

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.generate_thumbnail_bg",
            fail_generate_bg,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_youtube_still",
            fail_render_youtube,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
            fail_render_youtube,
        )

        r = with_ideas.post(
            f"{PROJECT_ROUTE}/thumbnail/render",
            data={
                "idea_index": "0",
                "director_notes": "warmer, bigger bottom headline",
                "stage": "type",
            },
        )
        assert r.status_code == 200, r.text
        assert "Step 3 - typography" in r.text
        assert "bottom_headline" in r.text
        assert "full render not run" in r.text

        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        slug_dir = _thumbnails_dir() / PROJECT_SLUG / "runs"
        ts_dirs = list(slug_dir.iterdir())
        assert len(ts_dirs) == 1
        assert (ts_dirs[0] / "v0_layout.png").is_file()
        assert (ts_dirs[0] / "v0_type.png").is_file()
        assert not (ts_dirs[0] / "v0.png").exists()
        manifest = json.loads((ts_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        first = manifest["renders"][0]
        assert first["stage"] == "type"
        assert first["filename"] == "v0_type.png"
        assert first["layout_blocking"]["content_side"] == "right"
        assert first["typography"]["typography_spec"]["mode"] == "bottom_headline"
        assert first["typography"]["typography_spec"]["director_notes"] == (
            "warmer, bigger bottom headline"
        )

    def test_render_background_stage_writes_plate_preview(self, with_ideas, tmp_path, monkeypatch):
        async def fail_generate_bg(**_kw):
            pytest.fail("background stage should not generate an AI background")

        def fail_render_youtube(**_kw):
            pytest.fail("background stage should not render the full thumbnail")

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.generate_thumbnail_bg",
            fail_generate_bg,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_youtube_still",
            fail_render_youtube,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
            fail_render_youtube,
        )

        r = with_ideas.post(
            f"{PROJECT_ROUTE}/thumbnail/render",
            data={
                "idea_index": "0",
                "director_notes": "less busy, darker bottom",
                "stage": "background",
            },
        )
        assert r.status_code == 200, r.text
        assert "Step 4 - background plate" in r.text
        assert "clean_lab_plate" in r.text
        assert "full render not run" in r.text

        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        slug_dir = _thumbnails_dir() / PROJECT_SLUG / "runs"
        ts_dirs = list(slug_dir.iterdir())
        assert len(ts_dirs) == 1
        assert (ts_dirs[0] / "v0_layout.png").is_file()
        assert (ts_dirs[0] / "v0_background.png").is_file()
        assert not (ts_dirs[0] / "v0.png").exists()
        manifest = json.loads((ts_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        first = manifest["renders"][0]
        assert first["stage"] == "background"
        assert first["filename"] == "v0_background.png"
        assert first["layout_blocking"]["content_side"] == "right"
        assert first["background_plate"]["background_spec"]["style"] == "clean_lab_plate"
        assert first["background_plate"]["background_spec"]["director_notes"] == (
            "less busy, darker bottom"
        )
        assert first["background_plate"]["typography_spec"]["mode"] == "bottom_headline"

    def test_render_components_stage_writes_template_plan(self, with_ideas, tmp_path, monkeypatch):
        async def fail_generate_bg(**_kw):
            pytest.fail("components stage should not generate an AI background")

        def fail_render_youtube(**_kw):
            pytest.fail("components stage should not render the full thumbnail")

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.generate_thumbnail_bg",
            fail_generate_bg,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_youtube_still",
            fail_render_youtube,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
            fail_render_youtube,
        )

        r = with_ideas.post(
            f"{PROJECT_ROUTE}/thumbnail/render",
            data={
                "idea_index": "1",
                "director_notes": "benefit card bigger",
                "stage": "components",
            },
        )
        assert r.status_code == 200, r.text
        assert "Step 5 - component plan" in r.text
        assert "shosho_benefit_list_card" in r.text
        assert "Component Plan" in r.text
        assert "component map ready" in r.text
        assert "Step 6: render candidate" in r.text
        assert "full render not run" not in r.text

        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        slug_dir = _thumbnails_dir() / PROJECT_SLUG / "runs"
        ts_dirs = list(slug_dir.iterdir())
        assert len(ts_dirs) == 1
        assert (ts_dirs[0] / "v1_components.png").is_file()
        assert not (ts_dirs[0] / "v1.png").exists()
        manifest = json.loads((ts_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        first = manifest["renders"][0]
        assert first["stage"] == "components"
        assert first["filename"] == "v1_components.png"
        assert (
            first["component_plan"]["selected_variant"]["reference_template_id"]
            == "shosho_benefit_list_card"
        )

    def test_render_records_passing_visual_qa_for_valid_png(
        self, with_ideas, tmp_path, monkeypatch
    ):
        def fake_render(*, out_png, **_kw):
            from PIL import Image, ImageDraw

            out_png.parent.mkdir(parents=True, exist_ok=True)
            img = Image.new("RGB", (1280, 720), "white")
            draw = ImageDraw.Draw(img)
            for x in range(0, 1280, 16):
                color = (x % 255, (x * 2) % 255, (x * 3) % 255)
                draw.rectangle((x, 0, x + 8, 720), fill=color)
            draw.rectangle((60, 80, 560, 250), fill="black")
            draw.rectangle((720, 380, 1180, 640), fill=(245, 245, 245))
            img.save(out_png)
            return out_png

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
            fake_render,
        )

        r = with_ideas.post(
            "/bridge/projects/肌酸的妙用/thumbnail/render",
            data={"idea_index": "0", "director_notes": ""},
        )
        assert r.status_code == 200, r.text
        assert "Visual QA" in r.text
        assert "v0.png" in r.text

        from thousand_sunny.routers.bridge_project_thumbnails import _thumbnails_dir

        slug_dir = _thumbnails_dir() / "肌酸的妙用" / "runs"
        ts_dirs = list(slug_dir.iterdir())
        assert len(ts_dirs) == 1
        assert (ts_dirs[0] / "v0_person.png").is_file()
        manifest = json.loads((ts_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["renders"][0]["stage"] == "full"
        assert manifest["renders"][0]["filename"] == "v0.png"
        assert manifest["renders"][0]["visual_qa"]["status"] == "pass"

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

    def test_commit_rejects_candidate_with_failed_visual_qa(self, client, tmp_path):
        cand = self._seed_candidate(tmp_path)
        manifest = {
            "renders": [
                {
                    "filename": cand.name,
                    "visual_qa": {
                        "status": "fail",
                        "checks": [
                            {
                                "check_id": "image_readable",
                                "status": "fail",
                                "message": "cannot decode image",
                            }
                        ],
                    },
                }
            ]
        }
        (cand.parent / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )

        r = client.post(
            "/bridge/projects/肌酸的妙用/thumbnail/commit",
            data={"run_ts": "20260526T140000", "filename": "v0.png"},
        )
        assert r.status_code == 412
        assert "Visual QA failed" in r.text

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
lane: Jeff Clean Tutorial
recipe: jeff_80_percent_protocol
reference_template: jeff_tool_header_panel
title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用
component: ui_panel
component_text: 8 個習慣 / Start Here
host: one third, friendly, gaze toward panel
viewer_promise: 用 8 個習慣讓觀眾覺得可以快速掃描重點
evidence_fit: 影片內容有足夠習慣清單可以支撐
trust_risk: 避免把習慣清單說成醫療保證
大字：8 個習慣
我的表情：驚訝
視覺：template=jeff_tool_header_panel; component=ui_panel; text=8個習慣/Start Here; host=one third
數字/圖示：8
背景：白色
素材需求：checklist icons; clean white panel; habit card UI

Idea 2
archetype: [T-A8, T-V3]
lane: Ali Warm Explainer
recipe: ali_warm_evidence_list
reference_template: shosho_benefit_list_card
title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用
component: benefit_list_card
component_text: 實證 / 認知 / 增力
host: left third, thoughtful, gaze toward card
viewer_promise: 用 12 週實證讓觀眾相信這不是空泛建議
evidence_fit: 影片會引用研究週期與觀察結果
trust_risk: 不把研究結果外推成個人療效
大字：12 週實證
我的表情：思考
視覺：template=shosho_benefit_list_card; component=benefit_list_card; host=left
數字/圖示：12
背景：醫院走廊
素材需求：split screen lifestyle; research paper texture; subtle chart icon

Idea 3
archetype: [T-A2, T-V1]
lane: Jeff Clean Tutorial
recipe: jeff_clean_tutorial_dual_zone
reference_template: jeff_tool_header_panel
title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用
component: ui_panel
component_text: 真的嗎 / Start Here
host: one third, explaining, gaze toward panel
viewer_promise: 用提問感引導觀眾想知道真正做法
evidence_fit: 影片會給出可執行步驟與限制
trust_risk: 不使用奇蹟或突破口吻
大字：真的嗎
我的表情：解釋
視覺：template=jeff_tool_header_panel; component=ui_panel; text=真的嗎/Start Here; host=one third
數字/圖示：?
背景：深藍科學感
素材需求：clean dashboard panel; question icon; blue science background
"""


class TestPlaybookIntegration:
    """v2: focused Ali/Jeff workflow pack injected, vision images removed."""

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
        # Focused workflow pack present
        combined = "\n".join(p["text"] for p in parts)
        assert "Ali/Jeff thumbnail workflow pack" in combined
        assert "jeff_clean_tutorial_dual_zone" in combined
        assert "Do not use T-V6" in combined
        assert "Title-template match plan" in combined
        assert "template_options" in combined
        assert "brief_contract" in combined
        assert "component=" in combined
        assert "component: <component from the same template_option>" in combined
        # Brief preserved
        assert "Test title" in combined and "test sentence" in combined

    def test_router_supported_visual_tags_match_workflow_and_templates(self):
        from agents.foundry.thumbnail_templates import TEMPLATES
        from shared.thumbnail_workflow import SUPPORTED_RENDERABLE_VISUAL_TAGS
        from thousand_sunny.routers.bridge_project_thumbnails import (
            _SUPPORTED_YOUTUBE_VISUAL_TAGS,
        )

        assert _SUPPORTED_YOUTUBE_VISUAL_TAGS == set(SUPPORTED_RENDERABLE_VISUAL_TAGS)
        assert _SUPPORTED_YOUTUBE_VISUAL_TAGS == set(TEMPLATES)

    def test_brainstorm_prefers_checked_title_pool_inputs(self, client, tmp_path, monkeypatch):
        pool_dir = tmp_path / "title_pool"
        pool_dir.mkdir()
        pool = {
            "iteration": 1,
            "pool": [
                {"id": f"t{i:02d}", "archetype": "T-A2", "title": f"候選標題 {i}"}
                for i in range(1, 11)
            ],
            "checked_ids": [f"t{i:02d}" for i in range(1, 11)],
        }
        (pool_dir / "肌酸的妙用.json").write_text(
            json.dumps(pool, ensure_ascii=False),
            encoding="utf-8",
        )

        captured_messages: list[list[dict]] = []

        def fake_llm(messages, *, system=None, model=None, max_tokens=2048):
            captured_messages.append(messages[0]["content"])
            return SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            fake_llm,
        )

        r = client.post("/bridge/projects/肌酸的妙用/thumbnail/brainstorm")
        assert r.status_code == 200, r.text
        combined = "\n".join(part["text"] for part in captured_messages[0])
        assert "title_input_source: checked_title_pool" in combined
        assert "T10: 候選標題 10" in combined
        assert "肌酸不只練肌肉" not in combined

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
        assert meta["schema_version"] == "v2"
        runs = meta["runs"]
        assert len(runs) == 1
        idea_tags = [i["archetype_tags"] for i in runs[0]["ideas"]]
        assert ["T-A1", "T-V10", "JP-4"] in idea_tags
        assert ["T-A8", "T-V3"] in idea_tags
        assert ["T-A2", "T-V1"] in idea_tags
        template_ids = [i["reference_template_id"] for i in runs[0]["ideas"]]
        assert "shosho_benefit_list_card" in template_ids

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
        assert "reference_templates" in scope
        assert ["T-A1", "T-V10", "JP-4"] in scope["archetype_tags"]


class TestTitleDrivenThumbnailAcceptance:
    """End-to-end v1 workflow contract from checked titles to committed thumbnail."""

    def test_checked_titles_to_three_variants_asset_panel_render_qa_and_commit(
        self, client, tmp_path, monkeypatch
    ):
        publish_title = (
            "\u808c\u9178\u4e0d\u53ea\u7df4\u808c\u8089\uff1a"
            "3 \u500b\u4f60\u6c92\u807d\u904e\u7684\u5999\u7528"
        )
        pool_dir = tmp_path / "title_pool"
        pool_dir.mkdir()
        title_pool = {
            "iteration": 2,
            "pool": [
                {
                    "id": f"t{i:02d}",
                    "archetype": "T-A2",
                    "title": publish_title if i == 1 else f"candidate title {i}",
                }
                for i in range(1, 11)
            ],
            "checked_ids": [f"t{i:02d}" for i in range(1, 11)],
        }
        (pool_dir / f"{PROJECT_SLUG}.json").write_text(
            json.dumps(title_pool, ensure_ascii=False),
            encoding="utf-8",
        )

        captured_messages: list[list[dict]] = []

        def fake_llm(messages, *, system=None, model=None, max_tokens=2048):
            captured_messages.append(messages[0]["content"])
            return SAMPLE_BRAINSTORM_WITH_ARCHETYPE_TAGS

        async def fake_generate_bg(*, out_png, **_kw):
            _write_acceptance_thumbnail_png(out_png)
            return {"provider": "test", "mode": "acceptance"}

        def fake_render(*, out_png, **_kw):
            _write_acceptance_thumbnail_png(out_png)
            return out_png

        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.ask_claude_multi",
            fake_llm,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.generate_thumbnail_bg",
            fake_generate_bg,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_youtube_still",
            fake_render,
        )
        monkeypatch.setattr(
            "thousand_sunny.routers.bridge_project_thumbnails.render_staged_youtube_thumbnail",
            fake_render,
        )

        brainstorm = client.post(f"{PROJECT_ROUTE}/thumbnail/brainstorm")
        assert brainstorm.status_code == 200, brainstorm.text
        assert brainstorm.headers.get("HX-Trigger") == "thumbnail-assets-changed"
        combined_prompt = "\n".join(part["text"] for part in captured_messages[0])
        assert "title_input_source: checked_title_pool" in combined_prompt
        assert "T10: candidate title 10" in combined_prompt

        fm = yaml.safe_load(
            (tmp_path / "Projects" / f"{PROJECT_SLUG}.md")
            .read_text(encoding="utf-8")
            .split("---")[1]
        )
        ideas = fm["thumbnail_ideas"]
        assert len(ideas) == 3

        from shared.thumbnail_idea import parse_idea

        parsed = [parse_idea(raw) for raw in ideas]
        assert len({idea.title_pairing for idea in parsed}) == 1
        visual_tags = {
            tag for idea in parsed for tag in idea.archetype_tags if tag.startswith("T-V")
        }
        assert visual_tags == {"T-V1", "T-V3", "T-V10"}

        manifest_path = tmp_path / "data_thumbs" / PROJECT_SLUG / "asset_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["policy"]["download_allowed"] is False
        assert len(manifest["items"]) == 9
        assert {item["status"] for item in manifest["items"]} == {"needed"}

        assets_panel = client.get(f"{PROJECT_ROUTE}/thumbnail/assets")
        assert assets_panel.status_code == 200, assets_panel.text
        assert "Envato Elements" in assets_panel.text
        assert "download_allowed" in assets_panel.text

        asset_update = client.post(
            f"{PROJECT_ROUTE}/thumbnail/assets/idea01-asset01",
            data={
                "status": "candidate_found",
                "provider": "envato_elements",
                "asset_url": "https://elements.envato.com/checklist-icons",
                "provider_asset_id": "EV-ACCEPT-1",
                "notes": "acceptance-test candidate only",
            },
        )
        assert asset_update.status_code == 200, asset_update.text
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["items"][0]["status"] == "candidate_found"
        assert manifest["items"][0]["provenance"]["provider_asset_id"] == "EV-ACCEPT-1"

        render = client.post(
            f"{PROJECT_ROUTE}/thumbnail/render",
            data={"idea_index": "0", "director_notes": "acceptance pass"},
        )
        assert render.status_code == 200, render.text
        assert "Visual QA" in render.text
        assert "v0.png" in render.text

        runs_dir = tmp_path / "data_thumbs" / PROJECT_SLUG / "runs"
        run_dirs = sorted(path for path in runs_dir.iterdir() if path.is_dir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        render_manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        first_render = render_manifest["renders"][0]
        assert first_render["idea_index"] == 0
        assert first_render["director_notes"] == "acceptance pass"
        assert first_render["stage"] == "full"
        assert first_render["filename"] == "v0.png"
        assert first_render["visual_qa"]["status"] == "pass"
        assert first_render["person_placement"]["filename"] == "v0_person.png"
        assert first_render["template_tv_id"] == "T-V10"
        assert first_render["ai_image_gen"] is None
        assert first_render["background_plate"] is not None
        assert first_render["component_plan"] is not None

        commit = client.post(
            f"{PROJECT_ROUTE}/thumbnail/commit",
            data={"run_ts": run_dir.name, "filename": "v0.png"},
        )
        assert commit.status_code == 200, commit.text

        chosen = tmp_path / "Attachments" / "projects" / PROJECT_SLUG / "thumbnail.png"
        assert chosen.exists()
        updated_fm = yaml.safe_load(
            (tmp_path / "Projects" / f"{PROJECT_SLUG}.md")
            .read_text(encoding="utf-8")
            .split("---")[1]
        )
        assert updated_fm["thumbnail"] == f"Attachments/projects/{PROJECT_SLUG}/thumbnail.png"
        assert updated_fm["thumbnail_run"] == f"{run_dir.name}/v0.png"


def _write_acceptance_thumbnail_png(path: Path) -> None:
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 620, 720), fill=(8, 12, 18))
    draw.rectangle((620, 0, 1280, 720), fill=(245, 246, 240))
    draw.rectangle((70, 80, 540, 250), fill=(255, 255, 255))
    draw.rectangle((760, 360, 1190, 640), fill=(22, 120, 70))
    for x in range(0, 1280, 32):
        draw.line((x, 0, 1280 - x // 2, 720), fill=(40, 80, 160), width=3)
    img.save(path)


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

        # Stub a fresh LLM batch that honours the v2 contract; only idx=0 is used.
        fresh = (
            "Idea 1\n"
            "archetype: [T-A3, T-V8]\n"
            "lane: Jeff Clean Tutorial\n"
            "recipe: jeff_clean_tutorial_dual_zone\n"
            "reference_template: jeff_command_panel\n"
            "title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用\n"
            "component: command_panel\n"
            "component_text: 不只增肌 / 改看這裡\n"
            "host: one third, serious, gaze toward command panel\n"
            "viewer_promise: 新版視覺用強烈色塊測試更直接的好奇心\n"
            "evidence_fit: 仍然對應影片中的研究整理與實用重點\n"
            "trust_risk: 不暗示肌酸有治療效果\n"
            "大字：新版 hook\n"
            "我的表情：認真\n"
            "視覺：template=jeff_command_panel; component=command_panel; host=one third\n"
            "數字/圖示：新\n"
            "背景：新背景\n"
            "素材需求：color pop background; supplement cutout\n"
            "\n"
            "Idea 2\n"
            "archetype: [T-A8, T-V3]\n"
            "lane: Ali Warm Explainer\n"
            "recipe: ali_warm_evidence_list\n"
            "reference_template: shosho_benefit_list_card\n"
            "title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用\n"
            "component: benefit_list_card\n"
            "component_text: 護腦 / 抗老 / 認知\n"
            "host: left third, thoughtful, gaze toward card\n"
            "viewer_promise: 保留研究整理感\n"
            "evidence_fit: 對應影片中的研究脈絡\n"
            "trust_risk: 不外推個人療效\n"
            "大字：65 歲來得及\n"
            "我的表情：思考\n"
            "視覺：template=shosho_benefit_list_card; component=benefit_list_card; host=left\n"
            "數字/圖示：65\n"
            "背景：醫院走廊\n"
            "素材需求：older adult lifestyle photo; brain scan abstract\n"
            "\n"
            "Idea 3\n"
            "archetype: [T-A1, T-V10, JP-4]\n"
            "lane: Jeff Clean Tutorial\n"
            "recipe: jeff_80_percent_protocol\n"
            "reference_template: shosho_benefit_list_card\n"
            "title_pairing: 肌酸不只練肌肉：3 個你沒聽過的妙用\n"
            "component: benefit_list_card\n"
            "component_text: 5g / 護腦 / 增力\n"
            "host: left third, explaining, gaze toward card\n"
            "viewer_promise: 保留 5g 框架感\n"
            "evidence_fit: 對應常見研究劑量\n"
            "trust_risk: 不包裝成個人處方\n"
            "大字：每天 5g\n"
            "我的表情：解釋\n"
            "視覺：template=shosho_benefit_list_card; component=benefit_list_card; host=left\n"
            "數字/圖示：5g\n"
            "背景：實驗室桌面\n"
            "素材需求：supplement scoop photo; molecule icon\n"
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
