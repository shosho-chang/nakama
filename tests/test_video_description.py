"""video_description 組裝邏輯測試（vault/DB 邊界靠 UAT）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.usopp.video_description import (  # noqa: E402
    build_description,
    chapters_from_broll,
    chosen_package,
    fmt_ts,
    load_citations,
)

PACKAGES = {
    "episode": "ep",
    "cuts": [
        {
            "cut_id": "punch-L5",
            "citations": ["Science 2010 心思漫遊"],
            "titles": [
                {"text": "標題一", "rank": 1},
                {"text": "標題三", "rank": 3},
            ],
            "packages": [
                {"title_rank": 3, "thumbnail_png": "Attachments/x/f.png"},
            ],
        }
    ],
}
APPROVAL = {"approvals": [{"cut_id": "punch-L5", "approved": True, "primary_package": 3}]}


def test_fmt_ts():
    assert fmt_ts(0) == "00:00"
    assert fmt_ts(127.0) == "02:07"
    assert fmt_ts(3725) == "1:02:05"


def test_chapters_from_broll_prepends_opening():
    items = [
        {"comp": "transition_title", "t0": 38.0, "vars": {"title": "睡眠"}},
        {"comp": "transition_title", "t0": 127.0, "vars": {"title": "情緒是建構的"}},
        {"kind": "video", "t0": 50.0},  # 非轉場卡不進章節
    ]
    ch = chapters_from_broll(items)
    assert ch[0] == (0.0, "開場")
    assert ch[1] == (38.0, "睡眠")
    assert len(ch) == 3


def test_chapters_too_few_returns_empty():
    """YT 分章至少 3 章——轉場卡 <2 個寧可不分章，不出殘缺表。"""
    items = [{"comp": "transition_title", "t0": 38.0, "vars": {"title": "睡眠"}}]
    assert chapters_from_broll(items) == []


def test_chosen_package_follows_approval():
    """標題是「已決定」（primary_package 指向）而非候選 top-1。"""
    pkg = chosen_package(PACKAGES, APPROVAL, "punch-L5")
    assert pkg["title"] == "標題三"
    assert pkg["thumbnail"] == "Attachments/x/f.png"


def test_chosen_package_unapproved_fails_loud():
    with pytest.raises(ValueError):
        chosen_package(PACKAGES, {"approvals": []}, "punch-L5")


def test_load_citations_missing_cut_fails_loud():
    with pytest.raises(ValueError):
        load_citations(PACKAGES, "ghost-1")


def test_build_description_four_blocks():
    out = build_description(
        "hook 第一句。",
        [(0.0, "開場"), (38.0, "睡眠")],
        ["Science 2010"],
        "——\n訂閱頻道",
    )
    assert out.index("hook") < out.index("⏱ 00:00 開場") < out.index("本集引用")
    assert "・Science 2010" in out
    assert out.rstrip().endswith("訂閱頻道")


def test_build_description_short_form_omits_empty_blocks():
    out = build_description("hook。", [], [], "footer")
    assert "⏱" not in out
    assert "本集引用" not in out
    assert out == "hook。\n\nfooter"


def test_load_footer_strips_html_comments():
    """YT 描述不解析 HTML——模板註解絕不能跟著發上去。"""
    from agents.usopp.video_description import load_footer

    out = load_footer()
    assert "<!--" not in out
    assert "-->" not in out
