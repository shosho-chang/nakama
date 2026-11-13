"""產生 config/rental-commute.yaml：從家附近的捷運站到台北捷運每一站要搭多久。

資料來源：臺北捷運官網「路線及票價查詢」的「最小旅行時間」（已含等車時間），
例如 https://web.metro.taipei/pages2026/WebRoutePlan/BR04/BR08 → 9 分鐘。
租屋追蹤（agents/franky/jobs/rental_watch.py）用它替物件的「離家距離」加減分。

換家附近的站才需要重跑（每站一個請求、間隔 1 秒，約 2–3 分鐘）::

    python scripts/build_rental_commute.py --home BR04
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import date
from pathlib import Path

import httpx
import yaml

_BASE = "https://web.metro.taipei/pages2026/WebRoutePlan"
_OUT = Path(__file__).resolve().parent.parent / "config" / "rental-commute.yaml"
_UA = "Mozilla/5.0 (nakama rental-watch; one-off commute table)"

_OPTION_RE = re.compile(
    r'<option[^>]*value="([A-Z]+\d+A?)"[^>]*>\s*[A-Z]+\d+A?\s+([^<]+?)\s*</option>'
)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_route(html: str) -> tuple[int, int]:
    """「最小旅行時間」那段 → (分鐘, 轉乘次數)。"""
    text = _TAG_RE.sub(" ", html)
    start = text.index("最小旅行時間")
    end = text.find("最少轉乘次數", start)
    block = text[start : end if end > 0 else None]
    minutes = int(re.search(r"(\d+)\s*分鐘", block).group(1))
    transfers = max(0, len(re.findall(r"\s往\S", block)) - 1)
    return minutes, transfers


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default="BR04", help="家附近的捷運站代碼（預設 BR04 萬芳醫院）")
    ap.add_argument("--out", type=Path, default=_OUT)
    args = ap.parse_args()

    client = httpx.Client(headers={"User-Agent": _UA}, timeout=20.0, follow_redirects=True)
    first = client.get(f"{_BASE}/{args.home}/BR08").text
    stations = dict(_OPTION_RE.findall(first))  # 代碼 → 站名（兩個下拉選單重複，dict 去重）
    home_name = stations[args.home]

    minutes: dict[str, int] = {home_name: 0}
    transfers: dict[str, int] = {home_name: 0}
    for code, name in stations.items():
        if code == args.home:
            continue
        time.sleep(1.0)
        m, t = parse_route(client.get(f"{_BASE}/{args.home}/{code}").text)
        # 轉乘站（大安、中山…）有好幾個代碼：取最快的
        if name not in minutes or m < minutes[name]:
            minutes[name], transfers[name] = m, t
        print(f"{code} {name}: {m} 分鐘，轉乘 {t} 次")

    data = {
        "home": {"code": args.home, "name": home_name},
        "source": f"臺北捷運官網路線及票價查詢（{_BASE}/{args.home}/<站代碼>）最小旅行時間，含等車",
        "fetched": date.today().isoformat(),
        "minutes": dict(sorted(minutes.items(), key=lambda kv: (kv[1], kv[0]))),
        "transfers": {k: transfers[k] for k in sorted(transfers) if transfers[k]},
    }
    header = (
        "# 由 scripts/build_rental_commute.py 產生，別手改；換家附近的站就重跑。\n"
        "# 租屋追蹤用它算「離家多遠」（rental_watch.py）。\n"
    )
    args.out.write_text(
        header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"wrote {args.out} ({len(minutes)} 站)")


if __name__ == "__main__":
    main()
