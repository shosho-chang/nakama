"""自由艦隊 gamification — 等級曲線校準器（分享期 vs 全經濟）。

跑法：``python -m agents.sanji.level_curve_sim``

權重從 ``rules.XP_TABLE`` 讀，不複製常數——分數表一改，這裡的結論自動跟著變。

校準原則（順序即優先序）：
 1. 新人第一個讚就升級（onboarding 當下就要有回饋）
 2. 分享期（只計被讚／被收藏）一年內每種原型都要看得到位移
 3. 全經濟開啟後不得有人「掉級」→ 門檻只准往下調，所以現在就要調到位
 4. 全經濟十年，最投入者約 L14–15、潛水型約 L9–11——曲線在十年尺度上仍有空間
"""

from __future__ import annotations

import io
import sys

from agents.sanji.rules import LEVEL_THRESHOLDS, XP_TABLE

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

CURRENT = dict(LEVEL_THRESHOLDS)

# 候選曲線：底部壓縮（分享期也走得動），中上維持指數（全經濟十年才爬得完）
CANDIDATE = {
    1: 0,
    2: 10,
    3: 50,
    4: 150,
    5: 400,
    6: 1_000,
    7: 2_000,
    8: 4_000,
    9: 7_000,
    10: 12_000,
    11: 20_000,
    12: 32_000,
    13: 52_000,
    14: 85_000,
    15: 140_000,
}


class Arc:
    """一種玩家原型的「一年份」活動量。"""

    def __init__(
        self, name, logins, seasons, checkins, streaks, full_att, courses, likes, bms, years=None
    ):
        self.name = name
        self.logins = logins
        self.seasons = seasons
        self.checkins = checkins  # 每季打卡天數
        self.streaks = streaks  # 每季拿到的連七獎次數
        self.full_att = full_att  # 是否每季全勤
        self.courses = courses  # 一年完成幾門課（每門 5 課＋測驗）
        self.likes = likes
        self.bms = bms
        self.years = years  # None = 每年都在；否則只在這些年份活躍

    def active(self, year: int) -> bool:
        return self.years is None or year in self.years

    def xp(self, year: int, *, economy: str) -> int:
        if not self.active(year):
            return 0

        share = self.likes * XP_TABLE["like_received"] + self.bms * XP_TABLE["bookmark_received"]
        if economy == "share":
            return share

        rest = (
            self.seasons * self.checkins * XP_TABLE["checkin_day"]
            + self.seasons * self.streaks * XP_TABLE["streak_7"]
            + (self.seasons * XP_TABLE["full_attendance"] if self.full_att else 0)
            + self.courses
            * (
                5 * XP_TABLE["lesson_completed"]
                + XP_TABLE["course_completed"]
                + XP_TABLE["quiz_passed"]
            )
        )
        login = self.logins * XP_TABLE["presence_day"]
        return share + rest + (login if economy == "full_login" else 0)


ARCS = [
    #        名稱      登入 季 打卡 連七 全勤  課  讚  收藏
    Arc("潛水型", 300, 1, 40, 2, False, 1, 5, 0),
    Arc("實踐型", 330, 4, 78, 8, True, 4, 15, 1),
    Arc("貢獻型", 220, 2, 55, 4, False, 2, 80, 10),
    Arc("全能型", 350, 4, 82, 9, True, 4, 150, 15),
    Arc("間歇型", 180, 2, 45, 3, False, 2, 20, 2, years={1, 3, 6}),
]

ECONOMIES = [
    ("share", "分享期（現在：只計被讚 ＋ 被收藏）"),
    ("full_nologin", "全經濟 · 登入分只換貝里、不計等級（建議）"),
    ("full_login", "全經濟 · 登入分也計等級"),
]


def level_of(xp: int, table: dict[int, int]) -> int:
    lv = 1
    for n, need in table.items():
        if xp >= need:
            lv = n
    return lv


def print_table(table: dict[int, int], label: str) -> None:
    print(f"\n【{label}】")
    print(
        f"  {'Lv':>3}  {'門檻 XP':>9}  {'級距':>8}  {'比':>5}   分享期意義（10 XP/讚、100 XP/收藏）"
    )
    prev = 0
    for n in sorted(table):
        need = table[n]
        gap = need - prev
        ratio = f"{need / prev:.2f}x" if prev else "—"
        hint = ""
        if need:
            likes = need / XP_TABLE["like_received"]
            hint = f"≈ {likes:,.0f} 個讚 或 {need / XP_TABLE['bookmark_received']:,.1f} 個收藏"
        print(f"  {n:>3}  {need:>9,}  {gap:>8,}  {ratio:>5}   {hint}")
        prev = need


def print_trajectory(table: dict[int, int], economy: str, label: str, years: int = 14) -> None:
    print(f"\n【{label}】{years} 年等級軌跡")
    header = "".join(f"Y{y:<3}" for y in range(1, years + 1))
    print(f"  {'原型':<8}{'年 XP':>8}   {header}")
    print("  " + "-" * (20 + 4 * years))
    for a in ARCS:
        cum, cells = 0, []
        for y in range(1, years + 1):
            cum += a.xp(y, economy=economy)
            cells.append(f"{level_of(cum, table):<4}")
        print(f"  {a.name:<8}{a.xp(1, economy=economy):>8,}   " + "".join(cells))
        if years >= 10:
            total = sum(a.xp(y, economy=economy) for y in range(1, 11))
            print(f"  {'':<8}{'':>8}   （10 年累計 {total:,} XP → Lv.{level_of(total, table)}）")


def print_onboarding(table: dict[int, int]) -> None:
    like = XP_TABLE["like_received"]
    bm = XP_TABLE["bookmark_received"]
    print("\n【分享期新人體感】")
    rows = [
        ("第一篇貼文拿到 1 個讚", like),
        ("第一篇貼文拿到 3 個讚", 3 * like),
        ("第一篇被收藏", bm),
        ("第一週（5 讚）", 5 * like),
        ("第一個月（隨手分享：12 讚 1 收藏）", 12 * like + bm),
        ("第一個月（認真分享：30 讚 3 收藏）", 30 * like + 3 * bm),
        ("第一季（認真分享）", 90 * like + 9 * bm),
    ]
    for lbl, v in rows:
        print(f"  {lbl:<34}{v:>7,} XP →  Lv.{level_of(v, table)}")


def main() -> None:
    print("=" * 96)
    print("等級曲線校準 — 現行 vs 候選")
    print("=" * 96)
    print(f"\n分數表：{ {k: v for k, v in XP_TABLE.items()} }")

    print_table(CURRENT, "現行門檻（為舊權重校準：讚 30 / 收藏 150 等值）")
    print_onboarding(CURRENT)
    print_trajectory(CURRENT, "share", "現行門檻 × 分享期")

    print("\n" + "=" * 96)
    print_table(CANDIDATE, "候選門檻")
    print_onboarding(CANDIDATE)
    for eco, label in ECONOMIES:
        print_trajectory(CANDIDATE, eco, f"候選門檻 × {label}")


if __name__ == "__main__":
    main()
