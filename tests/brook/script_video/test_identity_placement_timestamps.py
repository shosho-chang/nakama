"""SRT 時間戳不可以用精確浮點相等比對。

2026-09-11 20260721 呂冠緯 `story-L02`：SRT cue 1 是 `00:00:00,000 --> 00:00:01,816`，
兩個 worker 都判對了同一個 cue，卻被判成 identity drift——因為
`h*3600 + m*60 + s + ms/1000` 算出 1.8159999999999998，而 worker 端的
`總毫秒/1000` 是 1.816，兩個不同的 double。同一輪的 `,548` 與 `,239` 過了，
**純粹因為那兩個值在兩種算法下剛好落在同一個 bit**。

這裡鎖兩件事：解析器用整數毫秒（兩種算法收斂），比對留半毫秒容差
（既有收據存著當初繞過用的 1.8159999999999998，仍要驗得過）。
"""

from __future__ import annotations

import hashlib

import pytest

from agents.brook.script_video.identity_placement import (
    IdentityPlacementError,
    SrtCue,
    _cue_from_claim,
    _seconds,
)

#: 挑過的毫秒值：`,816` 是實際中獎的那一個，`,548` / `,239` 是同一輪僥倖通過的，
#: `,001` / `,999` 是邊界。
_STAMPS = (
    "00:00:01,816",
    "00:00:02,548",
    "00:00:34,239",
    "00:01:00,001",
    "01:02:03,999",
    "00:00:00,000",
)


def _total_ms(stamp: str) -> int:
    hours, minutes, rest = stamp.split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3_600_000 + int(minutes) * 60_000 + int(seconds) * 1_000 + int(millis)


def _naive_seconds(stamp: str) -> float:
    """退役的算法，保留在測試裡當對照組。"""
    hours, minutes, rest = stamp.split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


@pytest.mark.parametrize("stamp", _STAMPS)
def test_parser_agrees_with_the_integer_millisecond_algorithm(stamp: str) -> None:
    """worker 端「總毫秒 ÷ 1000」與解析器必須是同一個 double，不是相近。"""
    assert _seconds(stamp) == _total_ms(stamp) / 1000


def test_the_retired_algorithm_really_did_disagree() -> None:
    """對照組——證明這條 regression 不是想像出來的。"""
    assert _naive_seconds("00:00:01,816") != _total_ms("00:00:01,816") / 1000


def _cue(start: float, end: float) -> SrtCue:
    return SrtCue(number=1, start_sec=start, end_sec=end, text="來賓第一段實質回答")


def _claim(cue: SrtCue, *, start: float | None = None, end: float | None = None) -> dict:
    return {
        "number": cue.number,
        "start_sec": cue.start_sec if start is None else start,
        "end_sec": cue.end_sec if end is None else end,
        "text": cue.text,
        "text_sha256": hashlib.sha256(cue.text.encode("utf-8")).hexdigest(),
    }


@pytest.mark.parametrize("stamp", _STAMPS[:-1])
def test_either_algorithm_produces_an_acceptable_claim(stamp: str) -> None:
    """兩種算法寫出來的 audit 都要被接受——通過與否不該看運氣。"""
    cue = _cue(0.0, _seconds(stamp))
    for claimed_end in (_total_ms(stamp) / 1000, _naive_seconds(stamp)):
        assert _cue_from_claim(_claim(cue, end=claimed_end), [cue], "audit cue") is cue


def test_a_receipt_written_before_the_fix_still_verifies() -> None:
    """既有收據存的是 1.8159999999999998（當初為了繞過精確比對而改寫的序列化）。"""
    cue = _cue(0.0, _seconds("00:00:01,816"))
    assert _cue_from_claim(_claim(cue, end=1.8159999999999998), [cue], "audit cue") is cue


def test_a_real_millisecond_of_drift_is_still_rejected() -> None:
    """容差是半毫秒，不是放行。差一個毫秒仍然是不同的時間。"""
    cue = _cue(0.0, 1.816)
    with pytest.raises(IdentityPlacementError, match="end_sec"):
        _cue_from_claim(_claim(cue, end=1.817), [cue], "audit cue")


def test_the_drift_message_names_the_field_and_both_values() -> None:
    """舊訊息只說 identity drift，得自己去兩份 JSON 逐欄位比對才知道是哪一個。"""
    cue = _cue(0.0, 1.816)
    with pytest.raises(IdentityPlacementError) as error:
        _cue_from_claim(_claim(cue, start=0.5), [cue], "audit cue")
    message = str(error.value)
    assert "start_sec" in message and "0.5" in message and "end_sec" not in message
