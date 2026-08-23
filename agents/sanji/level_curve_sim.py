"""自由艦隊 gamification — 等級曲線（圓整版）＋ 登入權重對照"""

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PTS = {"login": 1, "like": 3, "bookmark": 15, "lesson": 5, "course": 30, "quiz": 5}
LESSONS = 5

# 由 D 曲線 3×(n-1)^3.2 圓整而來
ROUNDED = {
    1: 0,
    2: 10,
    3: 30,
    4: 100,
    5: 250,
    6: 500,
    7: 900,
    8: 1500,
    9: 2400,
    10: 3500,
    11: 5000,
    12: 7000,
    13: 10000,
    14: 14000,
    15: 20000,
}


class Arc:
    def __init__(self, name, logins, seasons, score, likes, bms, years=None):
        self.name, self.logins, self.seasons = name, logins, seasons
        self.score, self.likes, self.bms, self.years = score, likes, bms, years

    def pts(self, year):
        if self.years is not None and year not in self.years:
            return 0
        return (
            self.logins * PTS["login"]
            + self.seasons * self.score
            + self.seasons * (LESSONS * PTS["lesson"] + PTS["course"] + PTS["quiz"])
            + self.likes * PTS["like"]
            + self.bms * PTS["bookmark"]
        )


ARCS = [
    Arc("潛水型", 300, 1, 45, 5, 0),
    Arc("實踐型", 330, 4, 95, 15, 1),
    Arc("貢獻型", 220, 2, 65, 80, 10),
    Arc("全能型", 350, 4, 100, 150, 15),
    Arc("間歇型", 180, 2, 55, 20, 2, years={1, 3, 6}),
]


def level_of(pts, table):
    lv = 1
    for n, need in table.items():
        if pts >= need:
            lv = n
    return lv


def run(table, label, years=10):
    print("\n" + "=" * 74)
    print(label)
    print("=" * 74)
    print("\n【門檻表】")
    print("  " + "   ".join(f"L{n}:{table[n]:>6,}" for n in range(2, 9)))
    print("  " + "   ".join(f"L{n}:{table[n]:>6,}" for n in range(9, max(table) + 1)))

    print(f"\n【{years} 年等級軌跡】")
    print(f"  {'原型':<8}{'年收':>7}   " + "".join(f"Y{y:<3}" for y in range(1, years + 1)))
    print("  " + "-" * 78)
    for a in ARCS:
        cum, cells = 0, []
        for y in range(1, years + 1):
            cum += a.pts(y)
            cells.append(f"{level_of(cum, table):<4}")
        print(f"  {a.name:<8}{a.pts(1):>7,}   " + "".join(cells))

    print("\n【新人首季體感】")
    ob = LESSONS * PTS["lesson"] + PTS["course"] + PTS["quiz"]
    lg = PTS["login"]
    rows = [
        ("報名當天", ob + lg),
        ("第一週結束", ob + 7 * lg),
        ("首季結束（隨意）", ob + 85 * lg + 40 + 15),
        ("首季結束（認真）", ob + 90 * lg + 95 + 45),
    ]
    for lbl, v in rows:
        print(f"  {lbl:<20}{v:>6,} →  Lv.{level_of(v, table)}")


run(ROUNDED, "【方案 1】指數版門檻 × 登入 1 分/天", years=14)

PTS["login"] = 0
run(ROUNDED, "【方案 3】指數版門檻 × 登入不計入等級", years=14)
