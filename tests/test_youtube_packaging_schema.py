"""YouTube 封面配方與上架標題紀錄的 schema。"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from shared.schemas.youtube_packaging import (
    AB_TITLE_MAX,
    TitleTrackingFileV1,
    YouTubeCoverFileV1,
)

SLUG = "20261027-wealth-ladder"


def _recipe(**over):
    r = {
        "bg": {"src": f"Attachments/packaging/{SLUG}/assets/bg.jpg"},
        "feature": {
            "src": f"Attachments/packaging/{SLUG}/assets/cover.jpg",
            "cx": 975,
            "cy": 372,
            "h": 520,
        },
        "person": {
            "src": "Attachments/cutouts/shosho/explaining/2.png",
            "cx": 474,
            "top": 36,
            "h": 940,
        },
        "text": {
            "x": 48,
            "y": 486,
            "size": 124,
            "lead": "財富階梯分六階",
            "main": "你在第幾階？",
            "hl": "第幾階",
        },
    }
    r.update(over)
    return r


def test_cover_roundtrip_with_defaults():
    f = YouTubeCoverFileV1.model_validate(
        {"episode": "20261027 財富階梯 AI Agent 版", "recipe": _recipe()}
    )
    assert f.recipe.feature.frame_style == "skew"
    assert f.recipe.text.rot == -6
    assert f.recipe.logo.on is False
    dumped = f.model_dump(mode="json", by_alias=True)
    assert dumped["schema"] == "nakama.youtube_cover.v1"
    assert YouTubeCoverFileV1.model_validate(dumped) == f


@pytest.mark.parametrize(
    "bad",
    [
        "E:/Shosho LifeOS/Attachments/cutouts/shosho/a.png",  # 絕對路徑
        "/Attachments/cutouts/shosho/a.png",
        "Attachments/cutouts/shosho/../../secret.png",  # 跳出
        "Attachments/books/cover.jpg",  # 不在允許目錄
    ],
)
def test_asset_paths_must_be_vault_relative_and_whitelisted(bad):
    r = _recipe()
    r["person"]["src"] = bad
    with pytest.raises(ValidationError):
        YouTubeCoverFileV1.model_validate({"episode": "x", "recipe": r})


def test_backslash_paths_are_normalised():
    r = _recipe()
    r["person"]["src"] = "Attachments\\cutouts\\shosho\\explaining\\2.png"
    f = YouTubeCoverFileV1.model_validate({"episode": "x", "recipe": r})
    assert f.recipe.person.src == "Attachments/cutouts/shosho/explaining/2.png"


def test_unknown_frame_style_rejected():
    r = _recipe()
    r["feature"]["frame_style"] = "rounded-card"
    with pytest.raises(ValidationError):
        YouTubeCoverFileV1.model_validate({"episode": "x", "recipe": r})


def test_needs_render():
    now = datetime.now(timezone.utc)
    f = YouTubeCoverFileV1.model_validate({"episode": "x", "recipe": _recipe()})
    assert not f.needs_render()  # 從沒存過配方
    f.requested_at = now
    assert f.needs_render()  # 存了還沒出圖
    f.rendered_at = now + timedelta(seconds=5)
    assert not f.needs_render()  # 出過了
    f.requested_at = now + timedelta(seconds=10)
    assert f.needs_render()  # 又存了一次


def _titles(n_pub):
    return [{"id": f"t{i}", "text": f"標題{i}", "published": i < n_pub} for i in range(5)]


def test_published_is_ab_and_capped():
    f = TitleTrackingFileV1.model_validate({"titles": _titles(AB_TITLE_MAX)})
    assert [t.ab for t in f.titles] == [True] * AB_TITLE_MAX + [False] * (5 - AB_TITLE_MAX)
    with pytest.raises(ValidationError, match="最多"):
        TitleTrackingFileV1.model_validate({"titles": _titles(AB_TITLE_MAX + 1)})


def test_duplicate_title_ids_rejected():
    with pytest.raises(ValidationError, match="重複"):
        TitleTrackingFileV1.model_validate({"titles": [{"id": "a"}, {"id": "a"}]})


def test_tracking_keeps_extra_prototype_fields():
    f = TitleTrackingFileV1.model_validate(
        {"video_url": "https://youtu.be/x", "primary_title": "t1", "titles": []}
    )
    assert f.model_dump()["primary_title"] == "t1"
