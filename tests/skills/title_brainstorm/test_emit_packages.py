"""Tests for .claude/skills/title-brainstorm/scripts/emit_packages.py (ADR-054 S4).

Loads the module via importlib (path contains a hyphen) following the same
pattern used by tests/skills/kb_search/test_search_pipeline.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EMIT_SCRIPT = (
    _REPO_ROOT / ".claude" / "skills" / "title-brainstorm" / "scripts" / "emit_packages.py"
)


def _load_emit_module():
    spec = importlib.util.spec_from_file_location("emit_packages_under_test", _EMIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


emit_mod = _load_emit_module()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_LONG_TITLES = [
    {
        "text": f"長片標題第{i}條：測試用 payoff 第{i}",
        "archetype_id": "T-A1",
        "angle_combo": ["好奇缺口"],
        "payoff": "點進去你就會知道",
        "cite": f"srt/punch-L1.srt#{i * 10}",
        "rank": i,
        "panel_note": f"rank {i} 落選理由" if i >= 4 else None,
    }
    for i in range(1, 6)
]

_SHORT_TITLE = [
    {
        "text": "短片直出標題：這就是 payoff",
        "archetype_id": "T-A6",
        "angle_combo": ["好奇缺口"],
        "payoff": "點進去你就知道了",
        "cite": "srt/short-S1.srt#5",
        "rank": 1,
    }
]


def _long_input(episode: str = "20260723-xieboran") -> dict:
    return {
        "episode": episode,
        "cut_id": "punch-L1",
        "format": "long",
        "information_origin": "full_text",
        "visual_recipe": "podcast",
        "aspect": "16:9",
        "citations": [],
        "brand_flags": [],
        "titles": _LONG_TITLES,
        "title_trace": {"keywords": {}, "ta_profile": "TA 畫像測試", "tier1": [], "tier2": []},
    }


def _short_input(episode: str = "20260723-xieboran") -> dict:
    return {
        "episode": episode,
        "cut_id": "short-S1",
        "format": "short",
        "information_origin": "one_liner",
        "visual_recipe": "podcast",
        "aspect": "16:9",
        "citations": [],
        "brand_flags": [],
        "titles": _SHORT_TITLE,
        "title_trace": {"keywords": {}, "ta_profile": "TA 短片測試"},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmitShortFilm:
    def test_short_film_produces_valid_packages_json(self, tmp_path):
        """Short-format output must pass the full PackagesFileV1 validator (S1 schema)."""
        from shared.schemas.packaging import parse_packages

        packaging_dir = tmp_path / "packaging"
        emit_mod.emit(_short_input(), packaging_dir)

        pkg_path = packaging_dir / "packages.json"
        assert pkg_path.exists(), "packages.json should be written for short format"
        parsed = parse_packages(pkg_path)
        assert len(parsed.cuts) == 1
        cut = parsed.cuts[0]
        assert cut.format == "short"
        assert len(cut.titles) == 1
        assert cut.packages == []
        assert "thumbnail" in cut.model_fields_set

    def test_short_film_title_count_one(self, tmp_path):
        packaging_dir = tmp_path / "packaging"
        emit_mod.emit(_short_input(), packaging_dir)
        data = json.loads((packaging_dir / "packages.json").read_text(encoding="utf-8"))
        assert len(data["cuts"][0]["titles"]) == 1


class TestEmitLongFilm:
    def test_long_film_writes_title_trace_json(self, tmp_path):
        """title_trace.json must always be written."""
        packaging_dir = tmp_path / "packaging"
        emit_mod.emit(_long_input(), packaging_dir)
        trace_path = packaging_dir / "title_trace.json"
        assert trace_path.exists()
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        assert trace["cut_id"] == "punch-L1"
        assert len(trace["titles"]) == 5

    def test_long_film_titles_five_with_panel_notes(self, tmp_path):
        """Long-format must have 5 titles; ranks 4-5 must have panel_note."""
        packaging_dir = tmp_path / "packaging"
        emit_mod.emit(_long_input(), packaging_dir)
        data = json.loads((packaging_dir / "packages.json").read_text(encoding="utf-8"))
        titles = data["cuts"][0]["titles"]
        assert len(titles) == 5
        for t in titles:
            if t["rank"] >= 4:
                assert t.get("panel_note"), f"rank {t['rank']} title missing panel_note"


class TestDFGradeGate:
    def test_df_grade_archetype_rejected_at_emit_layer(self, tmp_path, monkeypatch):
        """Titles with D/F-grade archetype_id must be stripped before writing (emit gate)."""
        # Monkeypatch _load_df_title_archetypes to return a known bad archetype
        monkeypatch.setattr(emit_mod, "_load_df_title_archetypes", lambda: {"T-A3"})

        data = _short_input()
        data["titles"] = [
            {
                "text": "D-grade 標題：這條應該被剔除",
                "archetype_id": "T-A3",
                "angle_combo": ["反直覺"],
                "payoff": "payoff",
                "cite": "srt/test.srt#1",
                "rank": 1,
            },
            {
                "text": "好的備用標題：這條應該保留",
                "archetype_id": "T-A6",
                "angle_combo": ["好奇缺口"],
                "payoff": "payoff",
                "cite": "srt/test.srt#2",
                "rank": 1,
            },
        ]

        packaging_dir = tmp_path / "packaging"
        result = emit_mod.emit(data, packaging_dir)
        assert result["df_rejected"] == 1
        assert result["titles_ok"] == 1

    def test_all_df_grade_raises_validation_error(self, tmp_path, monkeypatch):
        """If all titles are D/F-grade and none remain, TitleV1 validation fails for short."""
        monkeypatch.setattr(emit_mod, "_load_df_title_archetypes", lambda: {"T-A6"})

        data = _short_input()
        packaging_dir = tmp_path / "packaging"
        # short format with 0 titles after gate → TitleV1 count constraint should fire
        with pytest.raises((ValueError, Exception)):
            emit_mod.emit(data, packaging_dir)


class TestVaultCopy:
    def test_emit_copies_to_vault(self, tmp_path):
        """With vault_path set, packages.json and title_trace.json are copied to vault."""
        packaging_dir = tmp_path / "packaging"
        vault_path = tmp_path / "vault"

        emit_mod.emit(
            _short_input(episode="20260723-xieboran"), packaging_dir, vault_path=vault_path
        )

        vault_ep = vault_path / "Attachments" / "packaging" / "20260723-xieboran"
        assert (vault_ep / "packages.json").exists()
        assert (vault_ep / "title_trace.json").exists()

    def test_emit_skips_vault_when_not_set(self, tmp_path):
        """Without vault_path, only packaging_dir files are written."""
        packaging_dir = tmp_path / "packaging"
        result = emit_mod.emit(_short_input(), packaging_dir, vault_path=None)
        assert result["vault_copies"] == []
        assert (packaging_dir / "packages.json").exists()


class TestPlaybookMapping:
    def test_playbook_md_covers_all_six_angles(self):
        """PLAYBOOK.md mapping table must reference all 6 emotion angles."""
        playbook_path = _REPO_ROOT / ".claude" / "skills" / "title-brainstorm" / "PLAYBOOK.md"
        content = playbook_path.read_text(encoding="utf-8")
        required_angles = [
            "好奇缺口",
            "恐懼／損失",
            "渴望／嚮往",
            "反直覺／衝突",
            "共鳴／被看見",
            "內幕／窺探",
        ]
        for angle in required_angles:
            assert angle in content, f"PLAYBOOK.md missing angle: {angle}"


class TestEmitMergesInsteadOfOverwriting:
    """ADR-054 D14 逐支處理：一集多支各跑一次 emit，不可互相覆寫。

    2026-07-29 謝伯讓集踩到：舊版兩個分支都 write(cuts=[單一 cut])，跑第二支
    會把第一支的標題與**已 render 的 packages** 一起抹掉（含 vault SoT）。
    """

    def test_second_cut_preserves_first(self, tmp_path):
        mod = _load_emit_module()
        first = _long_input()
        first["cut_id"] = "punch-L5"
        mod.emit(first, tmp_path, vault_path=None)

        second = _long_input()
        second["cut_id"] = "story-L1"
        mod.emit(second, tmp_path, vault_path=None)

        data = json.loads((tmp_path / "packages.json").read_text(encoding="utf-8"))
        assert [c["cut_id"] for c in data["cuts"]] == ["punch-L5", "story-L1"]

    def test_rerun_same_cut_replaces_in_place(self, tmp_path):
        """冪等：重跑同一支只換那一支，不追加、不動其他支。"""
        mod = _load_emit_module()
        mod.emit({**_long_input(), "cut_id": "punch-L5"}, tmp_path, vault_path=None)
        mod.emit({**_long_input(), "cut_id": "story-L1"}, tmp_path, vault_path=None)
        mod.emit({**_long_input(), "cut_id": "punch-L5"}, tmp_path, vault_path=None)

        data = json.loads((tmp_path / "packages.json").read_text(encoding="utf-8"))
        assert [c["cut_id"] for c in data["cuts"]] == ["punch-L5", "story-L1"]

    def test_existing_packages_not_clobbered_by_later_cut(self, tmp_path):
        """已配好封面的 cut，其 packages 不可被別支的 emit 洗掉。"""
        mod = _load_emit_module()
        mod.emit({**_long_input(), "cut_id": "punch-L5"}, tmp_path, vault_path=None)

        pkg_path = tmp_path / "packages.json"
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
        data["cuts"][0]["packages"] = [
            {
                "title_rank": r,
                "thumbnail_png": f"Attachments/packaging/ep/p{r}.png",
                "thumb_archetype_id": "T-V2",
                "joint_pairing_id": "N2-fixed",
                "host_cutout": "Attachments/cutouts/h.png",
                "guest_cutout": "Attachments/cutouts/g.png",
            }
            for r in (1, 2, 3)
        ]
        pkg_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        mod.emit({**_long_input(), "cut_id": "story-L1"}, tmp_path, vault_path=None)

        after = json.loads(pkg_path.read_text(encoding="utf-8"))
        punch = next(c for c in after["cuts"] if c["cut_id"] == "punch-L5")
        assert len(punch["packages"]) == 3

    def test_corrupt_file_fails_loud_not_silent_rebuild(self, tmp_path):
        """壞損 JSON 不可靜默重建——重建等於把別支的成果丟掉。"""
        mod = _load_emit_module()
        (tmp_path / "packages.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="合法 JSON"):
            mod.emit(_long_input(), tmp_path, vault_path=None)

    def test_long_draft_has_no_extra_keys(self, tmp_path):
        """草稿 cut 不可留 `_draft`/`_note`：CutV1 是 extra_forbid，
        留著會讓 attach_packages 的整檔驗證炸掉。"""
        mod = _load_emit_module()
        mod.emit(_long_input(), tmp_path, vault_path=None)
        cut = json.loads((tmp_path / "packages.json").read_text(encoding="utf-8"))["cuts"][0]
        assert not [k for k in cut if k.startswith("_")]

    def test_vault_dir_uses_ascii_slug_not_cjk_episode(self, tmp_path):
        """vault 落點用 ASCII slug（ADR-054 D10），不是 CJK 的 episode 欄。"""
        mod = _load_emit_module()
        vault = tmp_path / "vault"
        payload = {
            **_long_input(),
            "episode": "20260723 謝伯讓",
            "episode_slug": "20260723-xieboran",
        }
        mod.emit(payload, tmp_path / "work", vault_path=vault)
        assert (vault / "Attachments" / "packaging" / "20260723-xieboran").is_dir()
        assert not (vault / "Attachments" / "packaging" / "20260723 謝伯讓").exists()
