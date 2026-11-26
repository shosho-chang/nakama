"""建畫面規劃表：稿與字幕解析、標題對到字幕、自動列、Director 列檢查。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.brook.youtube_video import plan_build as pb
from agents.brook.youtube_video import visual_plan as vp

SCRIPT = """---
tags: [x]
---
標題:測試

我猜在看這集節目的你，很可能會把財富自由當成夢想。

嗨大家，歡迎回到我的頻道，我是張修修。

## 六個財富階層
書中定義的財富是淨資產，指的是資產減掉負債。

### 策略四：建立屬於自己的事業
前面三個策略能幫你從第二階爬到第四階。

## Reference
https://example.com/should-not-count
"""

SPOKEN = [
    "我猜在看這集節目的你啊",
    "很可能會把財富自由當成夢想",
    "嘿大家",
    "歡迎回到我的頻道",
    "我是張修修",
    "書中定義的財富",
    "是所謂的淨資產",
    "指的是資產減掉負債",
    "策略四 建立屬於自己的事業",
    "前面三個策略能夠幫你從",
    "第二階慢慢的爬到第四階",
]


def _srt(lines=SPOKEN) -> str:
    out = []
    for i, text in enumerate(lines, start=1):
        a, b = (i - 1) * 2, i * 2
        out.append(f"{i}\n00:00:{a:02d},000 --> 00:00:{b:02d},000\n{text}")
    return "\n\n".join(out) + "\n"


def _episode(tmp_path: Path) -> tuple[Path, Path]:
    ep = tmp_path / "ep"
    ep.mkdir()
    (ep / "transcript.srt").write_text(_srt(), encoding="utf-8")
    script = tmp_path / "script.md"
    script.write_text(SCRIPT, encoding="utf-8")
    return ep, script


def test_parse_script_stops_at_reference_and_skips_front_matter():
    blocks = pb.parse_script(SCRIPT)
    kinds = [b.kind for b in blocks]
    assert kinds == ["p", "p", "h2", "p", "h3", "p"]
    assert blocks[2].text == "六個財富階層" and "example.com" not in "".join(b.text for b in blocks)


def test_parse_srt_strips_tags():
    cues = pb.parse_srt("1\n00:00:01,500 --> 00:00:02,000\n<b>哈囉</b>\n")
    assert cues == [{"id": 1, "start": 1.5, "end": 2.0, "text": "哈囉"}]


def test_init_builds_auto_rows_at_the_right_cues(tmp_path):
    ep, script = _episode(tmp_path)
    plan, found = pb.init(ep, script)
    assert (ep / pb.SCRIPT_SNAPSHOT).is_file()
    by_kind = {a.kind: plan["cues"][a.cue_index]["id"] for a in found}
    assert by_kind == {"channel_intro": 3, "chapter": 6, "section": 9}  # 策略四：唸出標題那句
    cats = {r["category"]: r for r in plan["rows"]}
    assert cats["chapter_card"]["kind"] == "insert" and cats["chapter_card"]["before_cue"] == 6
    assert cats["chapter_card"]["onscreen_text"] == "六個財富階層"
    assert cats["section_title"]["start_cue"] == cats["section_title"]["end_cue"] == 9
    assert cats["channel_intro"]["before_cue"] == 3
    assert all(r["origin"] == "auto" and r["original"] for r in plan["rows"])
    with pytest.raises(pb.BuildError, match="不覆蓋"):
        pb.init(ep, script)


def _rows_file(tmp_path: Path, rows) -> Path:
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def test_add_director_rows(tmp_path):
    ep, script = _episode(tmp_path)
    pb.init(ep, script)
    rows = [
        {"category": "concept_title", "start_cue": 7, "end_cue": 7, "onscreen_text": "淨資產",
         "description": "「淨資產」三個字"},
        {"category": "quote_card", "start_cue": 1, "end_cue": 2, "onscreen_text": "財富自由"},
    ]  # fmt: skip
    new = pb.add(ep, _rows_file(tmp_path, rows))
    assert [r["layout"] for r in new] == ["overlay", "full"]  # 固定版面自動帶
    plan = vp.load_plan(vp.plan_path(ep))
    assert len(plan["rows"]) == 5 and new[0]["origin"] == "director"
    assert "每分鐘" in pb.describe_stats(plan)


@pytest.mark.parametrize(
    "row, message",
    [
        ({"category": "nope", "start_cue": 1}, "不認得"),
        ({"category": "section_title", "start_cue": 1, "onscreen_text": "x"}, "自動產生"),
        ({"category": "concept_title", "start_cue": 99, "onscreen_text": "x"}, "不存在"),
        ({"category": "concept_title", "start_cue": 3, "end_cue": 2, "onscreen_text": "x"}, "之前"),
        ({"category": "concept_title", "start_cue": 1}, "必填"),
        ({"category": "others_clip", "start_cue": 1, "dp_instruction": "城市夜景"}, "版面"),
    ],
)
def test_bad_director_rows_are_refused(tmp_path, row, message):
    ep, script = _episode(tmp_path)
    pb.init(ep, script)
    with pytest.raises(pb.BuildError, match=message):
        pb.add(ep, _rows_file(tmp_path, [row]))
    assert len(vp.load_plan(vp.plan_path(ep))["rows"]) == 3  # 一列不合格就整批不寫
