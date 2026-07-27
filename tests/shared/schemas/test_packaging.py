"""Tests for shared/schemas/packaging.py — packages.json + approval.json (ADR-054 §附錄C/D15).

Coverage:
- TitleV1 rank gate: rank 4/5 require panel_note
- PackageV1 path validation: vault-relative only, PNG ASCII slug
- CutV1 thumbnail asymmetry: long must NOT set it; short MUST explicitly set null
- CutV1 title/package count constraints (long=5/3, short=1/0)
- title_trace_ref is exempt from absolute-path rejection
- PackagesFileV1 round-trip
- ApprovalV1 happy path
- parse_packages() / parse_approval() file loaders
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.schemas.packaging import (
    ApprovalV1,
    CutV1,
    PackagesFileV1,
    PackageV1,
    TitleV1,
    parse_approval,
    parse_packages,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _title(rank: int = 1, panel_note: str | None = None) -> dict:
    return {
        "text": f"爆炸性揭露：標題 rank {rank}",
        "archetype_id": "T-A3",
        "angle_combo": ["反直覺", "恐懼"],
        "payoff": "三分鐘後你會重新考慮這件事",
        "cite": "srt/punch-L1_r003.srt#12",
        "rank": rank,
        "panel_note": panel_note,
    }


def _package(title_rank: int = 1) -> dict:
    return {
        "title_rank": title_rank,
        "thumbnail_png": "Attachments/packaging/20260723-xieboran/pkg-L1-1.png",
        "thumb_archetype_id": "T-V8",
        "joint_pairing_id": "JP-1",
        "host_cutout": "Attachments/cutouts/shosho/surprised/shosho_v1_surprised.png",
        "guest_cutout": "Attachments/cutouts/podcast/20260723-xieboran/guest_v2_thoughtful.png",
    }


def _long_cut_data() -> dict:
    titles = [
        _title(1),
        _title(2),
        _title(3),
        _title(4, panel_note="角度重複，缺乏差異化"),
        _title(5, panel_note="數字缺乏支撐，過度誇大"),
    ]
    packages = [_package(1), _package(2), _package(3)]
    return {
        "cut_id": "punch-L1",
        "format": "long",
        "information_origin": "full_text",
        "visual_recipe": "podcast",
        "aspect": "16:9",
        "titles": titles,
        "packages": packages,
        "citations": ["Science 2010 心思漫遊與快樂度"],
        "brand_flags": [],
        "title_trace_ref": "packaging/punch-L1/title_trace.json",
    }


def _short_cut_data() -> dict:
    return {
        "cut_id": "punch-S1",
        "format": "short",
        "information_origin": "full_text",
        "visual_recipe": "podcast",
        "aspect": "16:9",
        "titles": [_title(1)],
        "packages": [],
        "thumbnail": None,
    }


def _packages_file_data() -> dict:
    return {
        "episode": "20260723 謝伯讓",
        "generated_at": "2026-07-27T12:00:00+08:00",
        "cuts": [_long_cut_data(), _short_cut_data()],
    }


def _approval_data() -> dict:
    return {
        "cut_id": "punch-L1",
        "approved": True,
        "primary_package": 1,
        "reject_note": None,
        "decided_at": datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# TitleV1
# ---------------------------------------------------------------------------


def test_title_v1_rank_1_no_panel_note_ok():
    t = TitleV1(**_title(1))
    assert t.rank == 1
    assert t.panel_note is None


def test_title_v1_rank_3_no_panel_note_ok():
    t = TitleV1(**_title(3))
    assert t.rank == 3


def test_title_v1_rank_4_requires_panel_note():
    with pytest.raises(ValidationError, match="panel_note"):
        TitleV1(**_title(4, panel_note=None))


def test_title_v1_rank_5_requires_panel_note():
    with pytest.raises(ValidationError, match="panel_note"):
        TitleV1(**_title(5, panel_note=None))


def test_title_v1_rank_4_empty_panel_note_rejected():
    with pytest.raises(ValidationError, match="panel_note"):
        TitleV1(**_title(4, panel_note=""))


def test_title_v1_rank_4_with_panel_note_ok():
    t = TitleV1(**_title(4, panel_note="角度重複"))
    assert t.panel_note == "角度重複"


def test_title_v1_extra_field_rejected():
    with pytest.raises(ValidationError):
        TitleV1(**_title(1), extra_field="oops")


# ---------------------------------------------------------------------------
# PackageV1
# ---------------------------------------------------------------------------


def test_package_v1_happy():
    p = PackageV1(**_package())
    assert p.title_rank == 1
    assert p.thumbnail_png.endswith(".png")


def test_package_v1_windows_abs_path_thumbnail_rejected():
    data = _package()
    data["thumbnail_png"] = "E:\\Shosho LifeOS\\Attachments\\packaging\\pkg.png"
    with pytest.raises(ValidationError, match="vault-relative"):
        PackageV1(**data)


def test_package_v1_windows_abs_path_host_cutout_rejected():
    data = _package()
    data["host_cutout"] = "G:/footages/cutouts/shosho_v1.png"
    with pytest.raises(ValidationError, match="vault-relative"):
        PackageV1(**data)


def test_package_v1_linux_abs_path_guest_cutout_rejected():
    data = _package()
    data["guest_cutout"] = "/home/shosho/cutouts/guest_v2_thoughtful.png"
    with pytest.raises(ValidationError, match="vault-relative"):
        PackageV1(**data)


def test_package_v1_png_cjk_filename_rejected():
    data = _package()
    data["thumbnail_png"] = "Attachments/packaging/謝伯讓封面.png"
    with pytest.raises(ValidationError, match=r"PNG filename"):
        PackageV1(**data)


def test_package_v1_png_space_in_filename_rejected():
    data = _package()
    data["thumbnail_png"] = "Attachments/packaging/my file.png"
    with pytest.raises(ValidationError, match=r"PNG filename"):
        PackageV1(**data)


# ---------------------------------------------------------------------------
# CutV1 — thumbnail asymmetry
# ---------------------------------------------------------------------------


def test_cut_long_happy():
    cut = CutV1(**_long_cut_data())
    assert cut.format == "long"
    assert len(cut.titles) == 5
    assert len(cut.packages) == 3
    assert "thumbnail" not in cut.model_fields_set


def test_cut_short_happy():
    cut = CutV1(**_short_cut_data())
    assert cut.format == "short"
    assert len(cut.titles) == 1
    assert cut.packages == []
    assert "thumbnail" in cut.model_fields_set
    assert cut.thumbnail is None


def test_cut_long_with_thumbnail_rejected():
    data = _long_cut_data()
    data["thumbnail"] = None
    with pytest.raises(ValidationError, match="must NOT include thumbnail"):
        CutV1(**data)


def test_cut_short_without_thumbnail_rejected():
    data = _short_cut_data()
    del data["thumbnail"]
    with pytest.raises(ValidationError, match="must explicitly set thumbnail"):
        CutV1(**data)


# ---------------------------------------------------------------------------
# CutV1 — title/package count
# ---------------------------------------------------------------------------


def test_cut_long_wrong_title_count_rejected():
    data = _long_cut_data()
    data["titles"] = data["titles"][:4]  # 4 instead of 5
    with pytest.raises(ValidationError, match="5 titles"):
        CutV1(**data)


def test_cut_long_wrong_package_count_rejected():
    data = _long_cut_data()
    data["packages"] = data["packages"][:2]  # 2 instead of 3
    with pytest.raises(ValidationError, match="3 packages"):
        CutV1(**data)


def test_cut_short_with_packages_rejected():
    data = _short_cut_data()
    data["packages"] = [_package(1)]
    with pytest.raises(ValidationError, match="empty packages"):
        CutV1(**data)


def test_cut_short_wrong_title_count_rejected():
    data = _short_cut_data()
    data["titles"] = [_title(1), _title(2)]  # 2 instead of 1
    with pytest.raises(ValidationError, match="1 title"):
        CutV1(**data)


# ---------------------------------------------------------------------------
# CutV1 — title_trace_ref exempt from abs-path check
# ---------------------------------------------------------------------------


def test_cut_long_title_trace_ref_absolute_allowed():
    data = _long_cut_data()
    data["title_trace_ref"] = "G:/footages/20260723-xieboran/packaging/punch-L1/title_trace.json"
    cut = CutV1(**data)
    assert cut.title_trace_ref.startswith("G:/")


# ---------------------------------------------------------------------------
# PackagesFileV1
# ---------------------------------------------------------------------------


def test_packages_file_happy():
    pf = PackagesFileV1(**_packages_file_data())
    assert pf.episode == "20260723 謝伯讓"
    assert len(pf.cuts) == 2


def test_packages_file_roundtrip():
    original = PackagesFileV1(**_packages_file_data())
    reborn = PackagesFileV1.model_validate(json.loads(original.model_dump_json()))
    assert reborn == original


# ---------------------------------------------------------------------------
# ApprovalV1
# ---------------------------------------------------------------------------


def test_approval_happy():
    a = ApprovalV1(**_approval_data())
    assert a.cut_id == "punch-L1"
    assert a.approved is True
    assert a.primary_package == 1


def test_approval_primary_package_out_of_range():
    data = _approval_data()
    data["primary_package"] = 0
    with pytest.raises(ValidationError):
        ApprovalV1(**data)


def test_approval_primary_package_too_large():
    data = _approval_data()
    data["primary_package"] = 4
    with pytest.raises(ValidationError):
        ApprovalV1(**data)


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------


def test_parse_packages_loader():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(_packages_file_data(), f)
        path = f.name
    result = parse_packages(path)
    assert result.episode == "20260723 謝伯讓"


def test_parse_packages_loader_bad_shape_raises():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"episode": "test", "generated_at": "now"}, f)  # missing cuts
        path = f.name
    with pytest.raises(ValidationError):
        parse_packages(path)


def test_parse_approval_loader():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(_approval_data(), f)
        path = f.name
    result = parse_approval(path)
    assert result.cut_id == "punch-L1"


def test_parse_approval_loader_bad_shape_raises():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"cut_id": "punch-L1"}, f)  # missing required fields
        path = f.name
    with pytest.raises(ValidationError):
        parse_approval(path)
