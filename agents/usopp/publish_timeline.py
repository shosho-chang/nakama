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

這裡把對應關係變成一份可稽核的紀錄，並且**每次 render 前都拿實際 timeline 長度
跟那份紀錄的 preview 對一次**。

ADR-069 之後，長片那條線的紀錄就是 finished cut 的 **plan record**：它記著這個
plan 鋪到了哪一條 timeline、preview 的實際長度、以及成品的 event 與 component。
在那之前這裡只認得封存過的 Release，而封存鏈從來沒有跑過一次——於是每一支長片
都落在「沒有 Release」那條路上，分章與字幕來源全部回退。

**沒有 plan record 的 cut**：短片線（ADR-067）不走 finished cut production，所以
它仍然由人記一次 `highlights/publish-timelines.v1.json`。那種 entry 要填
`expected_duration_sec`，來源是**修修看過的那份 review preview** 的長度——護欄擋
的是「preview 之後有人動過 timeline」，跟長片擋錯片是同一件事。
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


class PlanRecordUnreadable(PublishTimelineError):
    """紀錄**在**，但讀不回來——跟「這支本來就沒有紀錄」是兩件事。

    後者是短片線的正常狀況，呼叫端可以往下找別的來源；前者代表分章、字幕與長度
    護欄的來源同時壞了，往下找就是拿 ADR-065 的舊時間軸冒充成品。兩者共用一個
    回傳值（`None`）的話，發布線分不出自己在哪一種狀況。
    """


@dataclass(frozen=True)
class PublishTimelineTarget:
    """一支 cut 的 render 目標，附上它該有的長度供 render 前複驗。"""

    cut_id: str
    timeline: str
    #: 這支 cut 的 plan record 身分。沒有 record 的成品（短片線）為 None。
    plan_id: str | None
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
    missing = [k for k in ("timeline", "expected_duration_sec") if not entry.get(k)]
    if missing:
        raise PublishTimelineError(f"{cut_id} 的對應表少了欄位 {missing}")
    if "plan_id" not in entry and "release_id" not in entry:
        raise PublishTimelineError(
            f"{cut_id} 的對應表沒有 plan_id 欄位。沒有 plan record 的成品要明寫 "
            '"plan_id": null——欄位不見與「刻意沒有」必須分得出來'
        )
    # ADR-069 之前這一格叫 `release_id`。既有的對應表照樣讀得回來。
    plan_id = entry.get("plan_id", entry.get("release_id"))
    return PublishTimelineTarget(
        cut_id=cut_id,
        timeline=str(entry["timeline"]),
        plan_id=str(plan_id) if plan_id else None,
        release_cut_id=str(entry.get("release_cut_id") or cut_id),
        expected_duration_sec=float(entry["expected_duration_sec"]),
    )


def verify_duration(target: PublishTimelineTarget, actual_duration_sec: float) -> None:
    """render 前最後一道：實際 timeline 長度必須等於對照物的長度。

    對照物是 plan record 的 preview；沒有 record 的成品（短片線）則是修修看過的
    那份 review preview。訊息要講清楚是拿什麼在對，不然看到「plan None」的人會
    以為是程式壞了而不是 timeline 被動過。

    **修法也要講對。** `target_for` 現在先問 plan record，有紀錄就不讀對應表了，
    所以對有紀錄的 cut 叫人去改 `publish-timelines.v1.json` 是白改一場——那個檔
    在這條路上根本沒有被打開過。
    """
    delta = abs(actual_duration_sec - target.expected_duration_sec)
    if delta > DURATION_TOLERANCE_SEC:
        if target.plan_id:
            against = f"plan record {target.plan_id} 的成品長度"
            remedy = (
                "  → 這條 timeline 不是這份紀錄的內容。專案裡通常還留著同名的舊剪輯；\n"
                f"     先在 Resolve 裡確認哪一條才是「{target.timeline}」，或重跑一次\n"
                "     materialization 讓紀錄與 timeline 重新對上，不要就這樣 render 出去。"
            )
        else:
            against = "修修看過的 review preview 長度"
            remedy = (
                "  → 這條 timeline 不是這支成品的內容。專案裡通常還留著同名的舊剪輯；\n"
                f"     先確認 {MAP_RELPATH} 指到正確的那條，不要就這樣 render 出去。"
            )
        raise PublishTimelineError(
            f"{target.cut_id}: timeline「{target.timeline}」長度 {actual_duration_sec:.3f}s，"
            f"但{against}是 "
            f"{target.expected_duration_sec:.3f}s（差 {delta:.3f}s）。\n" + remedy
        )


def _plan_cut(episode_dir: Path, cut_id: str):
    """這支 cut 的 plan record 投影，沒有就回 None；紀錄壞掉則 raise。

    「沒有紀錄」與「紀錄讀不動」必須分得出來。前者是正常的（短片線本來就不走
    finished cut production），後者是**分章與字幕的來源壞了**——安靜地回 None 會
    讓描述欄少掉全部時間戳、字幕退回 ADR-065 的舊 tight SRT，而沒有人知道發生
    過什麼。ADR-069 之前這條路是會 raise 的（「分章來源不可信，先確認 pointer」），
    改寫成讀 plan record 時掉了。
    """

    from agents.brook.script_video.finished_cut_production import build_plan_record_reader

    episode_dir = Path(episode_dir)
    inspection = build_plan_record_reader(episode_dir).inspect_current(episode_dir.name)
    if inspection.state == "invalid":
        code = inspection.error_code or "plan_record_invalid"
        raise PlanRecordUnreadable(
            f"{cut_id}: {episode_dir.name} 的 plan record 讀不回來（{code}）——"
            "分章、字幕與長度護欄的來源同時不可信。先修紀錄，不要就這樣發出去。"
        )
    if inspection.state != "ready":
        return None
    return next((row for row in inspection.cuts if row.cut_id == cut_id), None)


def plan_record_target(episode_dir: Path, cut_id: str) -> PublishTimelineTarget | None:
    """從 plan record 直接推出 render 目標——這支 cut 不必登記在對應表裡。

    ADR-069 之前 timeline 名只能由人記在 `publish-timelines.v1.json`：機器那條路
    （`canonical_timeline_from_transactions`）要求交易 `status == "committed"`，
    而全機器沒有一筆交易 commit 過，所以它永遠回 None。

    plan record 現在直接記著「這個 plan 鋪到了哪一條 timeline」與 preview 的實際
    長度，兩個護欄要的東西都在裡面。人只需要為**沒有 record 的成品**（短片線）
    維護對應表。

    回 None 代表這支沒有 record（或 record 是 ADR-069 之前產的、沒記 timeline），
    呼叫端回頭讀對應表；紀錄壞掉則 raise `PlanRecordUnreadable`。
    """

    cut = _plan_cut(episode_dir, cut_id)
    if cut is None or not cut.timeline or cut.preview.duration_sec is None:
        return None
    return PublishTimelineTarget(
        cut_id=cut_id,
        timeline=cut.timeline,
        plan_id=cut.plan_id,
        release_cut_id=cut_id,
        expected_duration_sec=float(cut.preview.duration_sec),
    )


def target_for(episode_dir: Path, cut_id: str) -> PublishTimelineTarget:
    """這支 cut 的 render 目標：先問 plan record，沒有才讀對應表。"""

    recorded = plan_record_target(episode_dir, cut_id)
    if recorded is not None:
        return recorded
    timeline_map = load_timeline_map(episode_dir)
    if timeline_map is None:
        raise PublishTimelineError(
            f"{cut_id} 既沒有 plan record，也沒有 {MAP_RELPATH}——"
            "沒有任何可稽核的來源能說出要 render 哪一條 timeline。"
        )
    return resolve_target(timeline_map, cut_id)


def plan_chapters(episode_dir: Path, cut_id: str) -> list[tuple[float, str]] | None:
    """YouTube 分章取自 plan record 的滿版轉場卡——與成品同一個時間軸。

    章節本來讀 `highlights/tighten/<cut>_broll.json`，那是 ADR-065 製作線的殘留：
    20260805 的 value-L02 broll 最遠只到 326.7s，但成品是 563.7s，於是描述欄的
    分章全部落在錯的位置（實際產出過 02:09/02:56/04:09/04:24/04:28，正確答案是
    00:43/03:39/04:41/07:10）。record 的 fullscreen_transition component 才是
    跟成品同源的那份。

    ADR-069 之前這裡還要對應表先指出一個 `release_id`，而封存鏈從未跑過，所以
    它對每一支 cut 都直接回空 list。現在只問 record 有沒有這支。

    回傳是三態，因為呼叫端要分得出兩件不同的事：

    * `None` —— 這支沒有 plan record（短片線）。呼叫端可以往下找別的來源。
    * `[]` —— 有紀錄，而紀錄說這支沒有可信的分章。**這是權威答案**，不可以
      回頭撿 `_broll.json`，那份是 ADR-065 的舊時間軸。
    """

    cut = _plan_cut(episode_dir, cut_id)
    if cut is None:
        return None
    marks = sorted(
        (float(component.t0), " ".join(str(component.display).split()))
        for component in cut.components
        if component.implementation_kind == "fullscreen_transition"
        and str(component.display).strip()
    )
    if len(marks) < 2:
        return []
    return [(0.0, "開場"), *marks]


def plan_subtitle(episode_dir: Path, cut_id: str) -> Path | None:
    """plan record 的字幕檔——描述欄逐字稿的來源，與成品同一份內容。

    描述欄的 hook 本來讀 `highlights/srt/<cut>_tight_r*.srt`，同樣是 ADR-065 的
    殘留：punch-L04 的 tight SRT 只有 260 秒的舊剪輯，成品卻是 492 秒，於是 LLM
    是照著一份不存在的影片在寫文案。record 的 subtitle 才是成品那份。

    回 None 代表沒有 record 或檔案不在，由呼叫端回退。
    """

    cut = _plan_cut(episode_dir, cut_id)
    if cut is None or not cut.subtitle:
        return None
    path = Path(episode_dir) / cut.subtitle.reference
    return path if path.is_file() else None


def packaging_cut_id(episode_dir: Path, release_cut_id: str) -> str:
    """成品審核的 cut id → 發布線（winners／packages）的 cut id。

    兩邊是不同的識別空間：成品審核講 `long3-fresh-20260828-r4`，
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


def export_matches_plan_record(episode_dir: Path, cut_id: str, receipt: dict | None) -> bool:
    """已 render 的成品是不是**現在這份紀錄**的內容。

    重鑄一份 plan 可以不改變片長（把一支 b-roll 移位、拿掉另一支，長度分毫不差），
    所以長度護欄看不出差別，而 publish_prep 的 receipt 只記得「render 過了」。
    2026-08-29 long3 就是這樣：成品 15:19 出的，紀錄 19:07 才換，再按核准會直接
    跳過 render，把舊畫面當成新成品交出去。

    receipt 沒有 `plan_id`（舊欄位名 `release_id`）代表它是這個欄位之前產的——
    這種一律當**不是**現在這版，寧可多 render 一次，也不要安靜發錯內容。

    同一條理由適用於「紀錄壞掉」：回 True 等於說「已經 render 的那份就是現在這版」，
    而我們其實根本不知道現在這版是什麼。回 False 只是多 render 一次。
    """
    try:
        target = target_for(Path(episode_dir), cut_id)
    except PlanRecordUnreadable:
        return False
    except PublishTimelineError:
        # 既沒有紀錄也沒有對應表：還沒走 ADR-066 的舊集數，不擋。
        return True
    rows = [row for row in (receipt or {}).get("cuts") or [] if row.get("cut_id") == cut_id]
    if len(rows) != 1:
        return False
    if target.plan_id is None:
        # 沒有 plan record ⇒ 這一輪 render 才第一次留下紀錄，沒有舊 id 可比對。
        # 長度護欄（verify_duration）仍然在 render 前跑過了。
        return True
    recorded = rows[0].get("plan_id") or rows[0].get("release_id") or ""
    return str(recorded) == target.plan_id
