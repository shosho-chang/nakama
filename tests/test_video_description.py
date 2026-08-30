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


def test_description_hook_requires_many_short_paragraphs():
    """2026-08-30 修修裁決：段數不設上限，改卡單段長度與自我指涉開場。

    正例就是他實際上架的 punch-L04 hook（4 段 / 57-78-76-45 字）；
    反例的長段落取自同批被他點名「每一段都太長」的 value-L01。
    """
    shipped = "\n\n".join(
        [
            "當 AI 讓許多服務跟產品的價格一路往下，世界會面臨同時通膨和通縮的怪現象，"
            "稀缺的東西將會貴到大多數人負擔不起。",
            "而每個人手上的資產也會呈現截然不同的 K 型發展。錢放在銀行只會被通膨慢慢吃掉，"
            "懂得把錢部署到好的投資工具、讓自己站到資本家那一側的人，才可能跟著往上走。",
            "Jamie 在書中提到，更讓人要提高警覺的是：當 AI 把所有需求都顧好，"
            "人可能就像 Elon Musk 形容的拉布拉多，住在天堂卻失去做決定的能力。",
            "能否找到自己為什麼活著，將自主權從 AI 手上拿回來，"
            "是個所有人現在就可以開始思考的事情。",
        ]
    )
    assert validate_description_hook(shipped) == shipped

    two_paragraphs = "\n\n".join(shipped.split("\n\n")[:2])
    with pytest.raises(ValueError, match="至少 3 段"):
        validate_description_hook(two_paragraphs)

    with pytest.raises(ValueError, match="總長需"):
        validate_description_hook("太短。\n\n也短。\n\n還是短。")

    dense = (
        "林之晨自己學 AI 就是這個路徑：先拿 AI agent 做東西、做到發現自己不夠用，"
        "才回頭去搞清楚 Context Window 到底是什麼。他說那種學法一點都不痛苦，"
        "因為知道搞懂之後能做出什麼。他認為真正落後的是大人——業界招聘已經在考"
        "「你能不能現場用 AI 解題」，美國常春藤畢業生的失業率正在創歷史新高，"
        "但很多父母還在要求孩子走那條舊路。"
    )
    with pytest.raises(ValueError, match="段太長"):
        validate_description_hook("\n\n".join([dense, dense, dense]))

    with pytest.raises(ValueError, match="自我指涉開場"):
        validate_description_hook(
            "這次和林之晨聊到一件我覺得很根本的事。\n\n" + "\n\n".join(shipped.split("\n\n")[1:])
        )


def test_load_footer_strips_html_comments():
    """YT 描述不解析 HTML——模板註解絕不能跟著發上去。"""
    from agents.usopp.video_description import load_footer

    out = load_footer()
    assert "<!--" not in out
    assert "-->" not in out
