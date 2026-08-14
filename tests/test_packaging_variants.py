"""VariantV1 / PackageV1.variants 的 schema 約束（ADR-054 附錄 C 延伸，修修 2026-08-14）。

變體板走「桌機 render 完 → gate 純勾選」，所以 schema 要在**寫檔那一刻**擋掉
gate 端無法補救的錯：橘框詞不在大字裡（render 出來不會有框）、variant_id 帶
CJK/空白（進 form value 與檔名會壞）、同一支重複 id（勾選對不回去）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.schemas.packaging import PackageV1, VariantV1

_HOST = "Attachments/cutouts/podcast/ep/host_v1_serious.png"
_GUEST = "Attachments/cutouts/podcast/ep/guest_v1_serious.png"


def _variant(**kw) -> VariantV1:
    base = {
        "variant_id": "r1-a",
        "thumbnail_png": "Attachments/packaging/ep/var-r1-a.png",
        "host_cutout": _HOST,
        "guest_cutout": _GUEST,
        "big_text": ["沒有資源", "怎麼活下來"],
        "highlight_text": "活下來",
    }
    return VariantV1(**{**base, **kw})


def test_valid_variant_round_trips():
    v = _variant()
    assert v.variant_id == "r1-a"
    assert v.big_text == ["沒有資源", "怎麼活下來"]


def test_highlight_must_be_substring_of_big_text():
    with pytest.raises(ValidationError, match="不在 big_text 內"):
        _variant(highlight_text="不存在的詞")


def test_highlight_may_span_the_line_break():
    # 大字兩行串起來比對 — 「資源怎麼」跨行仍算命中（render 時是同一段 inline 文字）
    assert _variant(highlight_text="資源").highlight_text == "資源"


def test_empty_highlight_is_allowed():
    assert _variant(highlight_text="").highlight_text == ""


def test_variant_id_must_be_ascii_slug():
    with pytest.raises(ValidationError, match="variant_id"):
        _variant(variant_id="變體 一")


def test_thumbnail_must_be_vault_relative():
    with pytest.raises(ValidationError, match="vault-relative"):
        _variant(thumbnail_png="G:/Footages/ep/packaging/var.png")


def test_big_text_cannot_be_empty():
    with pytest.raises(ValidationError):
        _variant(big_text=[])


def test_package_rejects_duplicate_variant_ids():
    with pytest.raises(ValidationError, match="duplicate variant_id"):
        PackageV1(
            title_rank=1,
            thumbnail_png="Attachments/packaging/ep/pkg-1.png",
            thumb_archetype_id="T-V1",
            joint_pairing_id="N1-fixed",
            host_cutout=_HOST,
            guest_cutout=_GUEST,
            variants=[_variant(), _variant()],
        )


def test_package_without_variants_still_valid():
    """舊集數的 packages.json 沒有 variants 欄位 — 讀得進來，gate 退化成單張。"""
    pkg = PackageV1(
        title_rank=1,
        thumbnail_png="Attachments/packaging/ep/pkg-1.png",
        thumb_archetype_id="T-V1",
        joint_pairing_id="N1-fixed",
        host_cutout=_HOST,
        guest_cutout=_GUEST,
    )
    assert pkg.variants == []
