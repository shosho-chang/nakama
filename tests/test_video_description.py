"""video_description 組裝邏輯測試（vault/DB 邊界靠 UAT）。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.usopp.video_description import (  # noqa: E402
    build_description,
    chapters_from_broll,
    chapters_from_registration,
    chosen_package,
    fmt_ts,
    load_citations,
    public_citations,
    validate_description_hook,
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


def _write_registration(tmp_path, cut_id, sections, *, approved=True):
    d = tmp_path / "registrations"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cut_id}.json").write_text(
        json.dumps({"cut_id": cut_id, "human_approved": approved, "sections": sections}),
        encoding="utf-8",
    )
    return tmp_path


def test_chapters_come_from_the_approved_registration(tmp_path, monkeypatch):
    """ADR-066 之後轉場卡只活在登錄檔裡；broll 檔已經撈不到，章節不能因此變空。"""
    _write_registration(
        tmp_path,
        "punch-L09",
        [
            {
                "t0": 0.0,
                "transition_before": False,
                "transition_title": None,
                "chapter_title": "整段摘要不該當章節名",
            },
            {"t0": 52.6, "transition_before": True, "transition_title": "第一個線索"},
            {"t0": 107.8, "transition_before": True, "transition_title": "情緒像粽子"},
        ],
    )
    monkeypatch.setenv("NAKAMA_FINISHED_CUT_RUNTIME", str(tmp_path))

    assert chapters_from_registration("punch-L09") == [
        (0.0, "開場"),
        (52.6, "第一個線索"),
        (107.8, "情緒像粽子"),
    ]


def test_registration_without_human_approval_is_not_a_chapter_source(tmp_path, monkeypatch):
    _write_registration(
        tmp_path,
        "punch-L09",
        [
            {"t0": 52.6, "transition_before": True, "transition_title": "第一個線索"},
            {"t0": 107.8, "transition_before": True, "transition_title": "情緒像粽子"},
        ],
        approved=False,
    )
    monkeypatch.setenv("NAKAMA_FINISHED_CUT_RUNTIME", str(tmp_path))

    assert chapters_from_registration("punch-L09") == []


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


def test_internal_transcript_provenance_never_becomes_public_citation():
    citations = [
        "highlights/srt/value-L01_tight_r012.srt#00:00:00-00:02:55",
        r"G:\\Footages\\episode\\highlights\\srt\\value-L01.srt",
        "transcript@00:03:33",
        "research/paper.pdf",
        "The Lancet 2024 dementia prevention report",
        "https://doi.org/10.1016/S0140-6736(24)01296-0",
        "https://example.org/public-paper.pdf",
    ]

    assert public_citations(citations) == [
        "The Lancet 2024 dementia prevention report",
        "https://doi.org/10.1016/S0140-6736(24)01296-0",
        "https://example.org/public-paper.pdf",
    ]


def test_load_citations_filters_internal_provenance_at_canonical_seam():
    packages = {
        "cuts": [
            {
                "cut_id": "value-L01",
                "citations": [
                    "highlights/srt/value-L01_tight_r012.srt#00:00:00-00:02:55",
                    "Science 2010 mind wandering study",
                ],
            }
        ]
    }

    assert load_citations(packages, "value-L01") == ["Science 2010 mind wandering study"]


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


def test_build_description_defensively_omits_internal_citation_paths():
    out = build_description(
        "hook。",
        [],
        [
            "highlights/srt/value-L01_tight_r012.srt#00:00:00-00:02:55",
            "The Lancet 2024",
        ],
        "footer",
    )

    assert "value-L01_tight_r012.srt" not in out
    assert "・The Lancet 2024" in out


def test_description_hook_requires_compact_paragraphs():
    paragraph = (
        "林之晨從自己在不同教育環境裡的經驗談起，拆解制度如何影響一個人理解學習、"
        "選擇道路與承擔風險的方式。這些看似個人的決定，其實都帶著家庭期待、社會條件與"
        "時代留下的痕跡，也會影響他後來面對失敗與成功的尺度。"
    )
    valid = f"{paragraph}\n\n{paragraph}"

    assert validate_description_hook(valid) == valid
    with pytest.raises(ValueError, match="約 200–300 字"):
        validate_description_hook("太短。")
    with pytest.raises(ValueError, match="1–4 個短段落"):
        validate_description_hook("\n\n".join(["短句。"] * 5))


def test_rejected_hook_is_quoted_back_in_the_error():
    """退稿要看得到稿子——只回一句規則，生成端寫了什麼就永遠查不到了。"""
    slop = "這一段會告訴你為什麼原生家庭很重要，" + "而且它真的很重要。" * 12

    with pytest.raises(ValueError) as excinfo:
        validate_description_hook(slop)
    assert "被退回的 hook" in str(excinfo.value)
    assert slop in str(excinfo.value)


def test_load_footer_strips_html_comments():
    """YT 描述不解析 HTML——模板註解絕不能跟著發上去。"""
    from agents.usopp.video_description import load_footer

    out = load_footer()
    assert "<!--" not in out
    assert "-->" not in out
