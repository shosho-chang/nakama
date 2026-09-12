"""video_description 組裝邏輯測試（vault/DB 邊界靠 UAT）。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.usopp.video_description as vd
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


_EPISODE = "20260901 蘇予昕"


def _write_registration(tmp_path, cut_id, sections, *, approved=True, episode=_EPISODE, flat=False):
    """寫一份登錄檔。`flat=True` 寫成舊的扁平路徑（撞名那一版）。"""
    d = tmp_path / "registrations" / ("" if flat else episode)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cut_id}.json").write_text(
        json.dumps(
            {
                "episode_id": episode,
                "cut_id": cut_id,
                "human_approved": approved,
                "sections": sections,
            },
            ensure_ascii=False,
        ),
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

    assert chapters_from_registration(_EPISODE, "punch-L09") == [
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

    assert chapters_from_registration(_EPISODE, "punch-L09") == []


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


def test_same_cut_id_in_two_episodes_does_not_cross_over(tmp_path, monkeypatch):
    """`punch-L03` 這一集有，蘇予昕那集也有——扁平檔名讓章節表安靜地接到別集去。"""
    _write_registration(
        tmp_path,
        "punch-L03",
        [
            {"t0": 52.6, "transition_before": True, "transition_title": "蘇予昕的第一章"},
            {"t0": 107.8, "transition_before": True, "transition_title": "蘇予昕的第二章"},
        ],
    )
    _write_registration(
        tmp_path,
        "punch-L03",
        [
            {"t0": 31.0, "transition_before": True, "transition_title": "呂冠緯的第一章"},
            {"t0": 88.0, "transition_before": True, "transition_title": "呂冠緯的第二章"},
        ],
        episode="20260721 呂冠緯",
    )
    monkeypatch.setenv("NAKAMA_FINISHED_CUT_RUNTIME", str(tmp_path))

    assert chapters_from_registration("20260901 蘇予昕", "punch-L03") == [
        (0.0, "開場"),
        (52.6, "蘇予昕的第一章"),
        (107.8, "蘇予昕的第二章"),
    ]
    assert chapters_from_registration("20260721 呂冠緯", "punch-L03") == [
        (0.0, "開場"),
        (31.0, "呂冠緯的第一章"),
        (88.0, "呂冠緯的第二章"),
    ]


def test_legacy_flat_registration_is_read_when_it_is_this_episode(tmp_path, monkeypatch):
    """既有的三個扁平檔（都是蘇予昕的）不需要搬家就還讀得到。"""
    _write_registration(
        tmp_path,
        "punch-L04",
        [
            {"t0": 12.0, "transition_before": True, "transition_title": "A"},
            {"t0": 44.0, "transition_before": True, "transition_title": "B"},
        ],
        flat=True,
    )
    monkeypatch.setenv("NAKAMA_FINISHED_CUT_RUNTIME", str(tmp_path))

    assert chapters_from_registration(_EPISODE, "punch-L04") == [
        (0.0, "開場"),
        (12.0, "A"),
        (44.0, "B"),
    ]


def test_legacy_flat_registration_of_another_episode_is_not_borrowed(tmp_path, monkeypatch):
    """扁平檔沒有 episode 這一層，所以只能靠 payload 自報——對不上就不採。"""
    _write_registration(
        tmp_path,
        "punch-L04",
        [
            {"t0": 12.0, "transition_before": True, "transition_title": "A"},
            {"t0": 44.0, "transition_before": True, "transition_title": "B"},
        ],
        flat=True,
    )
    monkeypatch.setenv("NAKAMA_FINISHED_CUT_RUNTIME", str(tmp_path))

    assert chapters_from_registration("20260721 呂冠緯", "punch-L04") == []


# --- agent 切的章節表 --------------------------------------------------------
#
# 完整版沒有轉場卡，所以推不出章節；長片也不保險——轉場卡少於兩張就回空
# （20260721 的 story-L02 與 value-L02 各只有一張）。2026-09-11 那支 87 分鐘的
# 完整版上架時描述裡一個時間戳都沒有，就是這個缺口。

_CHAPTERS_DOC = {
    "schema": "nakama.publish_chapters.v1",
    "episode": "20260721 呂冠緯",
    "cut_id": "full",
    "generated_at": "2026-09-12T00:00:00Z",
    "source": "editorial-master/v1/master.srt",
    "chapters": [
        {"t0": 0.0, "title": "開場：這集在聊什麼"},
        {"t0": 318.0, "title": "AI 用到極致長什麼樣"},
        {"t0": 1123.0, "title": "回到教育現場"},
    ],
}


def _write_authored(episode_dir, cut_id, doc=None):
    path = episode_dir / "publish" / "chapters" / f"{cut_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(doc or _CHAPTERS_DOC)
    payload["cut_id"] = cut_id
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_authored_chapters_are_used_when_nothing_else_has_any(tmp_path):
    """完整版的三個既有來源全都空——這條就是為了那個洞。"""
    _write_authored(tmp_path, "full")

    assert vd.resolve_chapters(tmp_path, "full") == [
        (0.0, "開場：這集在聊什麼"),
        (318.0, "AI 用到極致長什麼樣"),
        (1123.0, "回到教育現場"),
    ]


def test_transition_cards_still_win_over_the_authored_table(tmp_path, monkeypatch):
    """轉場卡是畫面上真的有的東西，湊得到兩張就用它。"""
    # `resolve_chapters` 用 episode 資料夾名去查登錄檔，所以 payload 的 episode_id
    # 必須就是那個名字——production 本來就是這樣（資料夾 `20260721 呂冠緯`）。
    _write_registration(
        tmp_path,
        "punch-L09",
        [
            {"t0": 52.6, "transition_before": True, "transition_title": "第一個線索"},
            {"t0": 107.8, "transition_before": True, "transition_title": "情緒像粽子"},
        ],
        episode=tmp_path.name,
    )
    monkeypatch.setenv("NAKAMA_FINISHED_CUT_RUNTIME", str(tmp_path))
    _write_authored(tmp_path, "punch-L09")

    assert vd.resolve_chapters(tmp_path, "punch-L09") == [
        (0.0, "開場"),
        (52.6, "第一個線索"),
        (107.8, "情緒像粽子"),
    ]


def test_a_single_transition_card_falls_through_to_the_authored_table(tmp_path, monkeypatch):
    """一張轉場卡湊不出章節表（gate 要 ≥2），那就別讓描述空著。"""
    _write_registration(
        tmp_path,
        "story-L02",
        [{"t0": 52.6, "transition_before": True, "transition_title": "唯一一張"}],
        episode=tmp_path.name,
    )
    monkeypatch.setenv("NAKAMA_FINISHED_CUT_RUNTIME", str(tmp_path))
    _write_authored(tmp_path, "story-L02")

    assert [title for _t, title in vd.resolve_chapters(tmp_path, "story-L02")] == [
        "開場：這集在聊什麼",
        "AI 用到極致長什麼樣",
        "回到教育現場",
    ]


def test_no_authored_table_is_not_an_error(tmp_path):
    """舊集數本來就沒有這個檔。"""
    assert vd.chapters_from_authored(tmp_path, "full") == []


def test_a_broken_authored_table_fails_loud(tmp_path):
    """靜靜地當成沒有章節，等於讓一份切好的表無聲消失。"""
    path = tmp_path / "publish" / "chapters" / "full.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 這不是合法 JSON", encoding="utf-8")

    with pytest.raises(ValueError, match="不是合法的章節表"):
        vd.chapters_from_authored(tmp_path, "full")


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"chapters": _CHAPTERS_DOC["chapters"][:2]}, "至少要 3 章"),
        (
            {"chapters": [{"t0": 12.0, "title": "沒有從零開始"}, *_CHAPTERS_DOC["chapters"][1:]]},
            "首章必須是 0:00",
        ),
        (
            {
                "chapters": [
                    {"t0": 0.0, "title": "開場"},
                    {"t0": 5.0, "title": "太近了"},
                    {"t0": 900.0, "title": "第三章"},
                ]
            },
            "不足 10s",
        ),
    ],
)
def test_youtube_hard_rules_are_enforced_at_the_schema(tmp_path, mutation, match):
    """違反任何一條，YouTube 會整份忽略而且不報錯——所以在這裡擋。"""
    doc = {**_CHAPTERS_DOC, **mutation}
    _write_authored(tmp_path, "full", doc)

    with pytest.raises(ValueError, match=match):
        vd.chapters_from_authored(tmp_path, "full")
