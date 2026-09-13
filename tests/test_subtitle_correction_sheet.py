"""勘誤單要能被程式讀回來——不然它只是一份漂亮的待辦清單。

修修 2026-09-11 要格子的理由有兩個，第二個才是重點：「這樣**你也比較好知道我的回饋
在哪裡**」。所以解析器跟產生器是同一件事的兩半，格式改了兩邊要一起改。
"""

from __future__ import annotations

from pathlib import Path

from scripts.build_subtitle_correction_sheet import ANSWER_PREFIX, KEEP_LABEL, read_sheet

SHEET = f"""---
episode: "20260721 呂冠緯"
status: 已填
---

## A. 一個答案解決多句

### 那位「同學」是誰？

| 時間 | cue | 目前字幕 |
|---|---|---|
| `00:03:20` | 136 | 我的夜間部同學講的這樣子 |

- {ANSWER_PREFIX} 夜間部同學

## B. 我查到答案了

### cue 975 · `00:29:02`

> 新大附中之前退休的陳永元校長

- [x] **採用** — 興大附中之前退休的陳勇延校長
- [ ] {KEEP_LABEL}
- {ANSWER_PREFIX}

### cue 977 · `00:29:08`

> 後面這個是像台中的鍾昌榮老師

- [ ] **採用** — 後面這個是像台中的鍾昌宏老師
- [x] {KEEP_LABEL}
- {ANSWER_PREFIX}

### cue 304 · `00:08:23`

> 就用那個龍蝦

- [ ] {KEEP_LABEL}
- {ANSWER_PREFIX} 就用那個 LobeChat

### cue 143 · `00:03:38`

> 譬如說錯字或者是語句的順度

- [ ] {KEEP_LABEL}
- {ANSWER_PREFIX}
"""


def _read(tmp_path: Path, text: str = SHEET) -> dict:
    path = tmp_path / "sheet.md"
    path.write_text(text, encoding="utf-8")
    return read_sheet(path)


def test_ticked_option_becomes_a_correction(tmp_path: Path) -> None:
    assert _read(tmp_path)["cues"][975] == "興大附中之前退休的陳勇延校長"


def test_keep_original_is_recorded_separately(tmp_path: Path) -> None:
    """勾「維持原文」是**他看過而且決定不改**，跟沒填不一樣，要分得開。"""
    answers = _read(tmp_path)
    assert 977 in answers["keep"]
    assert 977 not in answers["cues"]


def test_free_text_is_read(tmp_path: Path) -> None:
    assert _read(tmp_path)["cues"][304] == "就用那個 LobeChat"


def test_blank_answer_is_not_a_correction(tmp_path: Path) -> None:
    answers = _read(tmp_path)
    assert 143 not in answers["cues"]
    assert 143 not in answers["keep"]


def test_free_text_beats_a_tick(tmp_path: Path) -> None:
    """兩個都動了，代表勾選不夠精確——以他自己寫的為準。"""
    text = SHEET.replace(
        f"- [x] **採用** — 興大附中之前退休的陳勇延校長\n- [ ] {KEEP_LABEL}\n- {ANSWER_PREFIX}",
        f"- [x] **採用** — 興大附中之前退休的陳勇延校長\n- [ ] {KEEP_LABEL}\n"
        f"- {ANSWER_PREFIX} 興大附中之前退休的陳勇延校長（他當時說的是興附）",
    )
    assert _read(tmp_path, text)["cues"][975].endswith("（他當時說的是興附）")


def test_cluster_answer_is_a_term_not_a_sentence(tmp_path: Path) -> None:
    """A 組的答案是一個**詞**，不是整句——不可以直接當 cue 文字寫回去。"""
    answers = _read(tmp_path)
    assert answers["clusters"]["那位「同學」是誰？"] == "夜間部同學"
    # 而且它不能混進逐句答案裡。
    assert all(cue in (975, 304) for cue in answers["cues"])


def test_untouched_sheet_yields_nothing(tmp_path: Path) -> None:
    blank = SHEET.replace("[x]", "[ ]").replace(f"{ANSWER_PREFIX} 夜間部同學", ANSWER_PREFIX)
    blank = blank.replace(f"{ANSWER_PREFIX} 就用那個 LobeChat", ANSWER_PREFIX)
    answers = _read(tmp_path, blank)
    assert answers["cues"] == {}
    assert answers["keep"] == []
    assert answers["clusters"] == {}
