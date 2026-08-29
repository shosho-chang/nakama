"""publish_timeline — 成品 render 要用哪一條 Resolve timeline，由 Release 說了算。

發布線原本用 `winners.json` 的 rank + title 湊出 timeline 顯示名
（`長3 - 當天堂變成圈養，人還剩什麼（緊·導播）`），完全不認得 ADR-066 的
Finished Cut Release。20260805 林之晨的實測：

    cut                        Release preview     湊出來的 timeline
    value-L01                     592.90s            592.97s   ← 對得上
    value-L02                     563.71s            329.53s   ← 差 234 秒
    long3-fresh-20260828-r4       492.31s            260.00s   ← 差 232 秒

舊的短版 timeline 都還留在專案裡，所以 render **不會報錯**——它會把 260 秒的
舊剪輯冒充成 492 秒的成品，掛上已核准的標題與縮圖登錄進 DB。安靜地發錯內容
比失敗更糟，因為沒有人會知道。

這裡把對應關係變成 episode 內一份顯式紀錄（`highlights/publish-timelines.v1.json`），
並且**每次 render 前都拿實際 timeline 長度跟 Release preview 對一次**。ADR-066
的 run 可由 resolve transaction 機器推導；migrated Release 沒有 transaction，
只能由人記一次——記下來的東西可以被稽核，猜出來的不行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MAP_RELPATH = "highlights/publish-timelines.v1.json"
SCHEMA = "nakama.publish_timelines.v1"

# Release preview 是轉出來的 mp4，容器長度跟 timeline 的 frame 數本來就會差一兩
# 個 frame（實測 592.900 vs 592.967）。這道護欄要抓的是差**兩百多秒**的錯片，
# 所以留 2 秒寬容仍有兩位數的安全倍率，不會為了 rounding 誤停產線。
DURATION_TOLERANCE_SEC = 2.0


class PublishTimelineError(RuntimeError):
    """對應關係缺漏或對不上——一律停，不回退到會出錯片的舊猜法。"""


@dataclass(frozen=True)
class PublishTimelineTarget:
    """一支 cut 的 render 目標，附上它該有的長度供 render 前複驗。"""

    cut_id: str
    timeline: str
    release_id: str
    release_cut_id: str
    expected_duration_sec: float


def load_timeline_map(episode_dir: Path) -> dict | None:
    """讀 episode 的 timeline 對應表；沒有這個檔就回 None（舊集數沿用舊行為）。"""
    path = Path(episode_dir) / MAP_RELPATH
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise PublishTimelineError(f"{path} 的 schema 不是 {SCHEMA}——不採信來路不明的對應表")
    return payload


def resolve_target(timeline_map: dict, cut_id: str) -> PublishTimelineTarget:
    """對應表 → 這支 cut 的 render 目標。缺項是錯誤，不是「就用舊規則」。"""
    entry = (timeline_map.get("cuts") or {}).get(cut_id)
    if entry is None:
        known = ", ".join(sorted((timeline_map.get("cuts") or {}))) or "（空）"
        raise PublishTimelineError(
            f"{cut_id} 不在 {MAP_RELPATH} 裡——這支還沒登記要用哪條 timeline。\n"
            f"  已登記的有：{known}\n"
            f"  補上它，不要讓 publish_prep 回頭用 winners.json 的名字猜（會出錯片）。"
        )
    missing = [k for k in ("timeline", "release_id", "expected_duration_sec") if not entry.get(k)]
    if missing:
        raise PublishTimelineError(f"{cut_id} 的對應表少了欄位 {missing}")
    return PublishTimelineTarget(
        cut_id=cut_id,
        timeline=str(entry["timeline"]),
        release_id=str(entry["release_id"]),
        release_cut_id=str(entry.get("release_cut_id") or cut_id),
        expected_duration_sec=float(entry["expected_duration_sec"]),
    )


def verify_duration(target: PublishTimelineTarget, actual_duration_sec: float) -> None:
    """render 前最後一道：實際 timeline 長度必須等於 Release 的 preview 長度。"""
    delta = abs(actual_duration_sec - target.expected_duration_sec)
    if delta > DURATION_TOLERANCE_SEC:
        raise PublishTimelineError(
            f"{target.cut_id}: timeline「{target.timeline}」長度 {actual_duration_sec:.3f}s，"
            f"但 Release {target.release_id} 的成品長度是 "
            f"{target.expected_duration_sec:.3f}s（差 {delta:.3f}s）。\n"
            f"  → 這條 timeline 不是這個 Release 的內容。專案裡通常還留著同名的舊剪輯；\n"
            f"     先確認 {MAP_RELPATH} 指到正確的那條，不要就這樣 render 出去。"
        )


def canonical_timeline_from_transactions(
    transactions_dir: Path, transaction_receipt_id: str
) -> str | None:
    """ADR-066 run：由 Release 的 transaction receipt 反查 canonical timeline 名。

    migrated Release 沒有 transaction，回 None——那種只能靠對應表裡人記的值。
    """
    directory = Path(transactions_dir)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("resolve-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8")).get("payload") or {}
        if payload.get("transaction_receipt_id") != transaction_receipt_id:
            continue
        if payload.get("status") != "committed":
            continue
        canonical = payload.get("canonical") or {}
        name = canonical.get("name")
        return str(name) if name else None
    return None


def release_chapters(episode_dir: Path, cut_id: str) -> list[tuple[float, str]]:
    """YouTube 分章取自 Release 的滿版轉場卡——與成品同一個時間軸。

    章節本來讀 `highlights/tighten/<cut>_broll.json`，那是 ADR-065 製作線的殘留：
    20260805 的 value-L02 broll 最遠只到 326.7s，但成品是 563.7s，於是描述欄的
    分章全部落在錯的位置（實際產出過 02:09/02:56/04:09/04:24/04:28，正確答案是
    00:43/03:39/04:41/07:10）。Release 的 fullscreen_transition component 才是
    跟成品同源的那份。

    回空 list 代表「這集沒有可信的分章」——沒有分章好過錯的分章。
    """
    timeline_map = load_timeline_map(episode_dir)
    if timeline_map is None:
        return []
    target = resolve_target(timeline_map, cut_id)

    from agents.brook.script_video.finished_cut_production import build_current_release_reader

    inspection = build_current_release_reader(episode_dir).inspect_current(Path(episode_dir).name)
    cut = next(
        (c for c in inspection.cuts if c.release_id == target.release_id),
        None,
    )
    if cut is None:
        raise PublishTimelineError(
            f"{cut_id}: 對應表指的 Release {target.release_id} 不在 exact current——"
            "分章來源不可信，先確認 pointer"
        )
    marks = sorted(
        (float(c.t0), " ".join(str(c.display).split()))
        for c in cut.components
        if c.implementation_kind == "fullscreen_transition" and str(c.display).strip()
    )
    if len(marks) < 2:
        return []
    return [(0.0, "開場"), *marks]


def release_subtitle(episode_dir: Path, cut_id: str) -> Path | None:
    """Release 的字幕檔——描述欄逐字稿的來源，與成品同一份內容。

    描述欄的 hook 本來讀 `highlights/srt/<cut>_tight_r*.srt`，同樣是 ADR-065 的
    殘留：punch-L04 的 tight SRT 只有 260 秒的舊剪輯，成品卻是 492 秒，於是 LLM
    是照著一份不存在的影片在寫文案。Release 的 subtitle 才是成品那份。

    回 None 代表沒有對應表或檔案不在，由呼叫端回退。
    """
    timeline_map = load_timeline_map(episode_dir)
    if timeline_map is None:
        return None
    target = resolve_target(timeline_map, cut_id)

    from agents.brook.script_video.finished_cut_production import build_current_release_reader

    episode_dir = Path(episode_dir)
    inspection = build_current_release_reader(episode_dir).inspect_current(episode_dir.name)
    cut = next((c for c in inspection.cuts if c.release_id == target.release_id), None)
    if cut is None or not cut.subtitle:
        return None
    path = episode_dir / cut.subtitle.reference
    return path if path.is_file() else None


def packaging_cut_id(episode_dir: Path, release_cut_id: str) -> str:
    """Release 的 cut id → 發布線（winners／packages）的 cut id。

    兩邊是不同的識別空間：成品審核講 Release 的 `long3-fresh-20260828-r4`，
    packaging 與 `winners.json` 講 `punch-L04`。2026-08-29 修修在成品審核按下
    「核准這支」時兩邊都撞牆——publish_prep 收到 Release 的 id，log 只留下一行
    `--cut long3-fresh-20260828-r4 不在 winners.json`；redirect 帶著同一個 id 去
    packaging 板，回 `cut not found`。核准其實已經寫進 audit，只是後面兩步都在對
    一個它們不認識的名字說話。

    對應表的 `release_cut_id` 本來就是這個 join，這裡只是把它反過來查。沒有對應表
    （舊集數）或查不到（多數 cut 兩邊同名）就原樣回傳，維持既有行為。
    """
    timeline_map = load_timeline_map(Path(episode_dir))
    if timeline_map is None:
        return release_cut_id
    for cut_id, entry in (timeline_map.get("cuts") or {}).items():
        if str(entry.get("release_cut_id") or cut_id) == release_cut_id:
            return str(cut_id)
    return release_cut_id


def export_matches_current_release(episode_dir: Path, cut_id: str, receipt: dict | None) -> bool:
    """已 render 的成品是不是**現在這個** Release 的內容。

    amendment 會重封 Release 而不改變片長（把一支 b-roll 移位、拿掉另一支，長度
    分毫不差），所以長度護欄看不出差別，而 publish_prep 的 receipt 只記得「render
    過了」。2026-08-29 long3 就是這樣：成品 15:19 出的，Release 19:07 才重封，
    再按核准會直接跳過 render，把舊畫面當成新成品交出去。

    receipt 沒有 `release_id` 代表它是這個欄位之前產的——這種一律當**不是**現在
    這版，寧可多 render 一次，也不要安靜發錯內容。舊集數（沒有對應表）不受影響。
    """
    timeline_map = load_timeline_map(Path(episode_dir))
    if timeline_map is None:
        return True
    try:
        target = resolve_target(timeline_map, cut_id)
    except PublishTimelineError:
        return True
    rows = [row for row in (receipt or {}).get("cuts") or [] if row.get("cut_id") == cut_id]
    if len(rows) != 1:
        return False
    return str(rows[0].get("release_id") or "") == target.release_id
