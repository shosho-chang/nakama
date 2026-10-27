"""scripts/build_rental_commute.py：解析臺北捷運官網路線查詢結果。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_rental_commute.py"
_spec = importlib.util.spec_from_file_location("build_rental_commute", _SCRIPT)
brc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(brc)

# 2026-10-26 web.metro.taipei/pages2026/WebRoutePlan/BR04/... 的結構（節錄）
_DIRECT = """<div>最小旅行時間</div><div><span>9分鐘｜於 16:49 從 萬芳醫院 發車經過 4 站</span>
<td>萬芳醫院</td><td>16:49</td><td>往南港展覽館</td><td>經過 4 站 ( 8 分鐘)</td><td>科技大樓</td>
</div><div>最少轉乘次數</div><div>12分鐘 往南港展覽館 往淡水</div>"""
_TRANSFER = """<div>最小旅行時間</div><div><span>22分鐘｜於 16:49 從 萬芳醫院 發車經過 9 站</span>
<td>萬芳醫院</td><td>往南港展覽館</td><td>經過 5 站 ( 10 分鐘)</td><td>大安</td>
<td>往淡水</td><td>經過 4 站 ( 8 分鐘)</td><td>台北車站</td></div><div>最少轉乘次數</div>"""


def test_parse_route_direct():
    assert brc.parse_route(_DIRECT) == (9, 0)


def test_parse_route_with_transfer():
    assert brc.parse_route(_TRANSFER) == (22, 1)


def test_station_options_regex():
    html = '<option value="BR04">BR04 萬芳醫院</option><option value="R22A">R22A 新北投</option>'
    assert brc._OPTION_RE.findall(html) == [("BR04", "萬芳醫院"), ("R22A", "新北投")]
