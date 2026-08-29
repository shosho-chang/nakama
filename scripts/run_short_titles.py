"""short-titles：短片橘底大字 punch 卡 — hyperframes 透明 overlay 層。

修修 2026-07-26 八輪裁決：字卡從 Fusion Text+ 全面改走 hyperframes
（Brook 影片線的 render 引擎，`video/compositions/punch_card/`）。
視覺語彙照鐘穎範本：逐行橘塊（#E87000）緊貼字寬、LINE Seed TW 特黑、
逐行 swipe-in + back-out pop、快收退場。

為什麼換掉 Fusion Text+（v1，PR #1043）：
- 生成器固定 5s 無 API 可調 → 曾被迫用 Opacity 關鍵影格 + 卡距 ≥5s 硬限
- InsertFusionTitle 是插入模式 → 曾被迫走巢狀 timeline 疊軌
- 動畫/排版天花板低（單 style、無 per-line 背景動畫）
hyperframes 渲出 **ProRes 4444 帶 alpha** 的普通 media clip（2026-07-26
實測 DaVinci 合成 OK——順帶補掉 Brook DP 降級表「alpha 未過 DaVinci
驗證」缺口），AppendToTimeline 想放哪放哪、想多長多長。

輸入：highlights/tighten/<id>_titles.json。既有 static card schema 保留；短片另可用
``states`` 建立同一 overlay 內持續加行／換詞／升級尺寸的 kinetic sequence：
    {"titles": [{"text": "它會改變\\n你的耐心", "t0": 25.9, "t1": 27.8,
                  "pos_y": 0.63}]}
    t0/t1 = （緊·導播）timeline 秒（與 <id>_tight SRT 同軸）

流程：逐卡 `npx hyperframes render`（cache：參數 hash 命中就跳過）→
episode `highlights/tighten/cards/` → 匯入 media pool「Cards」bin →
（緊·導播）timeline video track 3 依 t0 落點、t1 截長。冪等：舊卡
items/media 先清；v1 的「titles - <id>」巢狀層與子 timeline 一併清除。

版本釘死 hyperframes@0.7.72（重現性——它兩天一版，未釘版每次 render 漂
到 latest，且 cache hash 不含引擎版本；升版是有意識的決定：改版號→重渲
樣張驗過→cache 自然失效重建）。

用法：
    python scripts/run_short_titles.py <episode> --id punch-S1 [--stills <dir>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_highlight_cut import FORMAT_LABEL  # noqa: E402
from run_short_tighten import TIGHTEN_DIR, _load_winner, _open_editorial_master  # noqa: E402

from agents.brook.script_video.highlight_broll import (  # noqa: E402
    BrollContractError,
    verify_visual_recipe_lineage,
)

logger = logging.getLogger("short_titles")

REPO_ROOT = Path(__file__).resolve().parent.parent
COMP_DIR = REPO_ROOT / "video" / "compositions" / "punch_card"
CARDS_DIR = "highlights/tighten/cards"
COMP_SEC = 4.0  # punch_card.html data-duration——短片 show_sec 上限（留 0.2s 裕度）
COMP_WIDE_SEC = 8.0  # punch_card_wide.html data-duration——長片卡比短片長一倍
KINETIC_COMP = "kinetic_sequence.html"
KINETIC_COMP_SEC = 12.0


def _comp_sec(fmt: str, *, kinetic: bool = False) -> float:
    """這張卡能顯示多久——上限由 composition 自己的 data-duration 決定。

    三個 composition 的長度不一樣（短 4s / 長 8s / kinetic 12s）。共用單一常數
    的話，長片卡會被短片的上限誤殺，或短片被放行到它 render 不出來的長度。
    """
    if kinetic:
        return KINETIC_COMP_SEC
    return COMP_WIDE_SEC if fmt == "long" else COMP_SEC


# 三層字卡架構（修修 2026-07-26 九輪）：tier1=hero（每支 1–3 張，只能是
# insight/closing）、tier2=標準 punch 卡、tier3=逐字字幕（走 subtitle track）
# 格式參數（修修 2026-08-03 長片線）。短片欄 = 既有已驗收值，一個字沒動。
#
# 行寬上限的算法是「字級 × 字數 + padding ≤ 畫布寬」：
#   短片 tier2 150px×6 + padding ≈ 964 ≤ 1080；tier1 190px×5 ≈ 1006 ≤ 1080
#   長片 tier2 140px×8 + padding ≈ 1176 ≤ 1920；tier1 200px×6 ≈ 1248 ≤ 1920
# 長片走 punch_card_wide.html：16:9 有寬度沒高度，卡片改走橫幅——每行字數
# 放寬、pos_y 下修避開 ~0.88 起跳的字幕帶。
FORMAT_TITLES = {
    "short": {
        "comp": "punch_card.html",
        "max_line": 6,
        "max_line_hero": 5,
        # Full-transcript choreography uses a quiet caption tier for ordinary
        # speech.  Only emphasis/hero states use the old punch-card scale.
        "max_line_caption": 10,
        "max_line_emphasis": 7,
        "pos_y": {1: 0.58, 2: 0.66},
    },
    "long": {
        "comp": "punch_card_wide.html",
        "max_line": 8,
        "max_line_hero": 6,
        "pos_y": {1: 0.60, 2: 0.66},
        # 修修 2026-08-04 四輪定案：長片 hero = paper（白底黑字 + 橘手繪畫線），
        # 逐卡可用 titles.json 的 "style" 覆蓋（orange|paper|ink）
        "style": "paper",
    },
}
# 字卡企劃＝短片的論證骨架（二十五輪修修裁決：「它其實是在支持這整個短影片
# 內容的鋪陳，是不是也要有完整的規劃」）。每張卡必須標明在論證裡承擔哪一拍；
# 寫不出 beat 的卡就是不該存在的卡。
BEATS = ("hook", "mechanism", "evidence", "insight", "closing")
SHORT_ANIMATIONS = ("swipe", "slam", "wipe", "word")
SEQUENCE_TRANSITIONS = ("cut", "enter", "add", "replace", "promote", "type", "slam", "whip")
SEQUENCE_STAGES = ("free", "rails")  # rails 僅用來辨識並拒絕舊版臨時圖樣
SEQUENCE_EXITS = ("hard_cut", "whip")
TEXT_ROLES = ("caption", "emphasis", "hero", "hybrid")
OFFICIAL_PATTERN = "shards-gray-on-orange"
SEC_PER_CARD = 4.5  # 密度上限：卡片總數 ≤ 片長 ÷ 4.5（範本 67s 22 張 ≈ 每 3s 一張）
_DISPLAY_PUNCTUATION = re.compile(r"[，。？！；：、,.?!;:「」『』（）()《》〈〉【】〔〕—…·‧]")
# 上下分割 opener 的兩張臉都佔中央區；置中的 transcript card 只能落在
# 下方安全帶。0.84 是依 1080x1920 實際 render bbox（兩行約高 11%）加上
# 臉框下緣餘裕所得，避免只檢查畫布邊界卻把字蓋在人臉上。
SPLIT_OPENER_MIN_POS_Y = 0.84


def _validate_split_opener_face_clearance(titles: list[dict], opener_sec: float) -> None:
    """Fail closed when a centred title overlaps either split-opener face.

    The split layout reserves the middle of each half for a face.  Title cards
    are horizontally centred and can span most of the width, so the only legal
    placement during the opener is the bottom safe band.
    """
    if opener_sec <= 0:
        return
    for index, title in enumerate(titles):
        t0 = float(title.get("t0", 0.0))
        t1 = float(title.get("t1", t0))
        if t0 >= opener_sec or t1 <= 0:
            continue
        pos_y = float(title.get("pos_y", FORMAT_TITLES["short"]["pos_y"][2]))
        if pos_y < SPLIT_OPENER_MIN_POS_Y:
            raise SystemExit(
                f"上下分割開場的字卡 {index} pos_y={pos_y:.2f} 會遮住下半格人臉；"
                f"必須放到下方安全帶（pos_y >= {SPLIT_OPENER_MIN_POS_Y:.2f}）"
            )


def _validate_short_motion_grammar(titles: list[dict]) -> None:
    """Fail closed when a short falls back to monotonous template motion.

    The Zhong Ying Ep02 reference alternates entry grammar and line-size
    hierarchy.  A plan with four or more cards must therefore use at least two
    animations and two size signatures; three identical animations in a row
    are never allowed.  This is planning validation only and does not touch
    Resolve.
    """
    if not titles:
        return
    # kinetic sequence 自己在 `_validate_kinetic_sequence` 檢查 state 變化；這裡只
    # 攔既有 static card 又退回單一模板的情況。
    static_titles = [t for t in titles if not t.get("states")]
    if not static_titles:
        return
    animations = [
        str(t.get("animation", "slam" if int(t.get("tier", 2)) == 1 else "swipe"))
        for t in static_titles
    ]
    bad = [a for a in animations if a not in SHORT_ANIMATIONS]
    if bad:
        raise SystemExit(
            f"短片字卡 animation 不合法：{bad[0]!r}（要 {'/'.join(SHORT_ANIMATIONS)}）"
        )
    for index in range(2, len(animations)):
        if len(set(animations[index - 2 : index + 1])) == 1:
            raise SystemExit(
                f"字卡 {index - 1}–{index + 1} 連續三張都用 {animations[index]!r}——"
                "改用 swipe/slam/wipe/word 交替建立節奏"
            )
    if len(static_titles) >= 4 and len(set(animations)) < 2:
        raise SystemExit("四張以上短片字卡至少要有兩種進場動畫")

    sizes = {
        (
            float(t.get("card_scale", 1.0)),
            float(t.get("line1_scale", 1.0)),
            float(t.get("line2_scale", 1.0)),
        )
        for t in static_titles
    }
    if len(static_titles) >= 4 and len(sizes) < 2:
        raise SystemExit("四張以上短片字卡至少要有兩組整卡／行級尺寸，避免模板感")


def _title_text(title: dict) -> str:
    """Human-readable label for either a static card or a kinetic sequence."""
    if title.get("text"):
        return str(title["text"])
    states = title.get("states") or []
    if not states:
        return ""
    return "\n".join(str(x) for x in states[-1].get("lines", []))


def _validate_exact_transcript_span(index: int, displayed: str, source: str) -> None:
    """Require every kinetic title to be a verbatim contiguous transcript span.

    Line breaks and punctuation are presentation metadata, so they are ignored.
    Word deletion, reordering and paraphrase are not.  This is intentionally
    stricter than the retired character-overlap heuristic: the transcript is
    now the single source of truth and title planning may only style it.
    """
    displayed_n, source_n = _norm(displayed), _norm(source)
    if not displayed_n or displayed_n not in source_n:
        raise SystemExit(
            "sequence {}「{}」不是逐字稿的原文連續片段。\n"
            "  來源逐字稿：{}\n"
            "  字卡只可改斷行、大小與動態，不可刪字重組或改寫".format(
                index, displayed.replace(chr(10), "／"), source
            )
        )


def _validate_brand_pattern_usage(titles: list[dict]) -> None:
    """One official branded pattern moment per short, reserved for a gold quote."""
    if any(str(title.get("stage", "free")) == "rails" for title in titles):
        raise SystemExit(
            "stage=rails 是臨時繪製的三角形 pattern，已停用；"
            "改用 brand_pattern 與正式品牌 shards 素材"
        )
    patterned = [title for title in titles if title.get("brand_pattern")]
    if len(patterned) > 1:
        raise SystemExit(f"品牌 pattern 全片只能出現一次，目前有 {len(patterned)} 次")
    if not patterned:
        return
    spec = patterned[0]["brand_pattern"]
    if not isinstance(spec, dict):
        raise SystemExit("brand_pattern 必須是物件")
    if spec.get("asset") != OFFICIAL_PATTERN:
        raise SystemExit(f"brand_pattern 只能使用正式素材 {OFFICIAL_PATTERN!r}")
    if spec.get("role") != "gold_quote":
        raise SystemExit("brand_pattern 只保留給 role=gold_quote 的全片最重要金句")
    at = float(spec.get("at", -1))
    duration = float(spec.get("duration", 0))
    show_sec = float(patterned[0].get("t1", 0)) - float(patterned[0].get("t0", 0))
    if at < 0 or not 0.35 <= duration <= 1.5 or at + duration > show_sec:
        raise SystemExit("brand_pattern 的 at/duration 超出字卡區間或時長不在 0.35–1.5s")


def _validate_full_transcript_display(titles: list[dict]) -> None:
    """Short-form transcript choreography is punctuation-free on screen."""
    for sequence_index, title in enumerate(titles):
        for state_index, state in enumerate(title.get("states") or []):
            for line in state.get("lines") or []:
                punctuation = _DISPLAY_PUNCTUATION.search(str(line))
                if punctuation:
                    raise SystemExit(
                        "短片顯示文字不可含標點："
                        f"sequence {sequence_index} state {state_index} "
                        f"包含 {punctuation.group()!r}（{line}）"
                    )


def _validate_kinetic_sequence(index: int, title: dict, show_sec: float, fcfg: dict) -> None:
    states = title.get("states") or []
    if not 2 <= len(states) <= 8:
        raise SystemExit(f"sequence {index} states={len(states)} 不合法（要 2–8 個）")
    stage = str(title.get("stage", "free"))
    if stage not in SEQUENCE_STAGES:
        raise SystemExit(
            f"sequence {index} stage={stage!r} 不合法（要 {'/'.join(SEQUENCE_STAGES)}）"
        )
    exit_style = str(title.get("exit", "hard_cut"))
    if exit_style not in SEQUENCE_EXITS:
        raise SystemExit(
            f"sequence {index} exit={exit_style!r} 不合法（要 {'/'.join(SEQUENCE_EXITS)}）"
        )

    previous_at = -1.0
    transitions = []
    for state_index, state in enumerate(states):
        at = float(state.get("at", -1))
        if state_index == 0 and abs(at) > 0.05:
            raise SystemExit(f"sequence {index} 第一個 state 必須從 at=0 開始")
        if at <= previous_at or at >= show_sec - 0.12:
            raise SystemExit(
                f"sequence {index} state {state_index} at={at} 不合法；必須遞增且早於片尾"
            )
        previous_at = at
        transition = str(state.get("transition", "enter" if state_index == 0 else "replace"))
        if transition not in SEQUENCE_TRANSITIONS:
            raise SystemExit(
                f"sequence {index} state {state_index} transition={transition!r} 不合法"
            )
        transitions.append(transition)
        lines = state.get("lines")
        if (
            not isinstance(lines, list)
            or not 1 <= len(lines) <= 3
            or not all(isinstance(line, str) and line.strip() for line in lines)
        ):
            raise SystemExit(f"sequence {index} state {state_index} lines 必須是 1–3 行非空文字")
        state_tier = int(state.get("tier", title.get("tier", 2)))
        if state_tier not in (1, 2):
            raise SystemExit(f"sequence {index} state {state_index} tier={state_tier} 不合法")
        role = str(state.get("role", "hero" if state_tier == 1 else "emphasis"))
        if role not in TEXT_ROLES:
            raise SystemExit(
                f"sequence {index} state {state_index} role={role!r} 不合法"
                f"（要 {'/'.join(TEXT_ROLES)}）"
            )
        if role == "caption" and len(lines) > 2:
            raise SystemExit(
                f"sequence {index} state {state_index} caption 最多 2 行；"
                "一般逐字字卡不可把三個語意節拍同時堆在畫面上"
            )
        line_roles = state.get("line_roles")
        if line_roles is not None:
            if (
                not isinstance(line_roles, list)
                or len(line_roles) != len(lines)
                or any(str(line_role) not in TEXT_ROLES for line_role in line_roles)
            ):
                raise SystemExit(
                    f"sequence {index} state {state_index} line_roles 必須逐行提供"
                    f"且只能是 {'/'.join(TEXT_ROLES)}"
                )
        if role == "caption":
            limit = fcfg.get("max_line_caption", fcfg["max_line"])
        elif role in ("emphasis", "hybrid"):
            limit = fcfg.get("max_line_emphasis", fcfg["max_line"])
        else:
            limit = fcfg["max_line_hero"]
        too_long = [line for line in lines if len(line) > limit]
        if too_long:
            raise SystemExit(f"sequence {index} state {state_index} 行超過 {limit} 字：{too_long}")
        scales = state.get("scales", [1.0] * len(lines))
        if len(scales) != len(lines) or any(not 0.72 <= float(v) <= 1.35 for v in scales):
            raise SystemExit(
                f"sequence {index} state {state_index} scales 必須逐行提供且介於 0.72–1.35"
            )
        if str(state.get("style", "orange")) not in ("orange", "hybrid"):
            raise SystemExit(f"sequence {index} state {state_index} style 只允許 orange/hybrid")
    if len(states) >= 4 and len(set(transitions)) < 2 and set(transitions) != {"cut"}:
        raise SystemExit(f"sequence {index} 四個以上 state 不可全部使用同一 transition")


def _card_hash(variables: dict, comp: str = "punch_card.html") -> str:
    comp_digest = hashlib.md5((COMP_DIR / "compositions" / comp).read_bytes()).hexdigest()[:8]
    payload = json.dumps(variables, ensure_ascii=False, sort_keys=True) + comp + comp_digest
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]


def _render_card(variables: dict, out_path: Path, comp: str = "punch_card.html") -> None:
    """npx hyperframes render → ProRes 4444 alpha mov（約 ~20s/卡）。

    Windows shell=True 走 cmd.exe——單引號不是引號、中文 JSON 會炸，
    variables 一律走 --variables-file（檔案與 mov 同名 sidecar，兼作紀錄）。
    """
    vars_file = out_path.with_suffix(".vars.json")
    vars_file.write_text(json.dumps(variables, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = (
        f"npx --yes hyperframes@0.7.72 render . -c compositions/{comp} "
        f'-o "{out_path}" --format mov -q standard --quiet --no-browser-gpu '
        f'--variables-file "{vars_file}"'
    )
    logger.info("render card: %s", variables.get("line1"))
    proc = subprocess.run(
        cmd, shell=True, cwd=str(COMP_DIR), capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0 or not out_path.exists():
        raise SystemExit(f"hyperframes render 失敗: {(proc.stderr or '')[-400:]}")


def _validate_rendered_frame_safety(paths: list[Path]) -> None:
    """Run the fail-closed safe-area gate on every frame before touching Resolve."""
    candidates = [
        REPO_ROOT / ".venv-v2" / "Scripts" / "python.exe",
        REPO_ROOT / ".venv-v2" / "bin" / "python",
        Path(sys.executable),
    ]
    python = next((candidate for candidate in candidates if candidate.exists()), None)
    if python is None:
        raise SystemExit("找不到可執行逐幀安全區檢查的 Python")
    checker = REPO_ROOT / "scripts" / "check_title_frame_safety.py"
    proc = subprocess.run(
        [str(python), str(checker), *[str(path) for path in paths]],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise SystemExit("字卡逐幀安全區驗收失敗，未寫入 Resolve：\n" + proc.stdout[-5000:])
    logger.info("逐幀安全區驗收通過：%d 個 overlay", len(paths))


_PUNCT = r"[\s，。、？！「」『』（）()《》〈〉·,.?!:;\-—…]"


def _norm(s: str) -> str:
    """比對用正規化：去換行、空白、標點、引號，只留可讀字元。"""
    return re.sub(_PUNCT, "", s)


def _tight_srt_cues(episode_dir: Path, cid: str) -> dict[int, dict]:
    """Load the latest reviewed SRT as the only lexical source for short titles."""
    srts = sorted((episode_dir / "highlights/srt").glob(f"{cid}_tight_r*.srt"))
    if not srts:
        return {}
    cues: dict[int, dict] = {}
    for block in re.split(r"\n\s*\n", srts[-1].read_text(encoding="utf-8-sig").strip()):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            continue
        match = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not match:
            continue
        start = (
            int(match.group(1)) * 3600
            + int(match.group(2)) * 60
            + int(match.group(3))
            + int(match.group(4)) / 1000
        )
        end = (
            int(match.group(5)) * 3600
            + int(match.group(6)) * 60
            + int(match.group(7))
            + int(match.group(8)) / 1000
        )
        cue_id = int(lines[0].strip())
        cues[cue_id] = {"t0": start, "t1": end, "text": "".join(lines[2:]).strip()}
    return cues


def _validate_transcript_driven_title(
    index: int,
    title: dict,
    cues: dict[int, dict],
    *,
    require_full_state_coverage: bool = False,
) -> None:
    """Validate source custody and timing for a kinetic transcript title."""
    source_ids = title.get("source_cues")
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or not all(isinstance(cue_id, int) for cue_id in source_ids)
    ):
        raise SystemExit(f"sequence {index} 缺 source_cues；短片字卡必須指向最新 SRT，不可另寫文案")
    missing = [cue_id for cue_id in source_ids if cue_id not in cues]
    if missing:
        raise SystemExit(f"sequence {index} source_cues 在最新 SRT 不存在：{missing}")
    source = "".join(cues[cue_id]["text"] for cue_id in source_ids)
    t0, t1 = float(title["t0"]), float(title["t1"])
    if cues[source_ids[0]]["t0"] < t0 - 0.55 or cues[source_ids[-1]]["t1"] > t1 + 0.55:
        raise SystemExit(
            f"sequence {index} 的 t0/t1 沒有包住 source_cues {source_ids[0]}–{source_ids[-1]}"
        )
    for state_index, state in enumerate(title.get("states") or []):
        state_source_ids = state.get("source_cues")
        state_has_source = (
            isinstance(state_source_ids, list)
            and bool(state_source_ids)
            and all(isinstance(cue_id, int) for cue_id in state_source_ids)
        )
        if require_full_state_coverage and not state_has_source:
            raise SystemExit(
                f"sequence {index} state {state_index} 缺 source_cues；"
                "每個畫面狀態必須精確承接一段逐字稿"
            )
        if state_has_source and state_source_ids != list(
            range(state_source_ids[0], state_source_ids[-1] + 1)
        ):
            raise SystemExit(
                f"sequence {index} state {state_index} source_cues 必須連續：{state_source_ids}"
            )
        if state_has_source and any(cue_id not in source_ids for cue_id in state_source_ids):
            raise SystemExit(
                f"sequence {index} state {state_index} source_cues 不在 sequence 範圍內："
                f"{state_source_ids}"
            )
        trigger = state.get("trigger_cue")
        expected_trigger = state_source_ids[0] if state_has_source else None
        trigger_valid = (
            trigger == expected_trigger if require_full_state_coverage else trigger in source_ids
        )
        if not trigger_valid:
            raise SystemExit(
                f"sequence {index} state {state_index} 缺合法 trigger_cue；"
                "必須等於該 state 第一個 source_cue"
            )
        absolute_at = t0 + float(state.get("at", 0))
        cue = cues[trigger]
        if absolute_at < cue["t0"] - 0.35 or absolute_at > cue["t1"] + 0.35:
            raise SystemExit(
                f"sequence {index} state {state_index} @{absolute_at:.3f}s 與"
                f" trigger_cue={trigger}（{cue['t0']:.3f}–{cue['t1']:.3f}s）不同步"
            )
        displayed = "\n".join(state["lines"])
        if require_full_state_coverage:
            expected = "".join(cues[cue_id]["text"] for cue_id in state_source_ids)
        else:
            expected = source
        if require_full_state_coverage and _norm(displayed) != _norm(expected):
            raise SystemExit(
                f"sequence {index} state {state_index} 不是 source_cues 的完整原文。\n"
                f"  預期：{expected}\n  實際：{displayed.replace(chr(10), '／')}"
            )
        if not require_full_state_coverage:
            _validate_exact_transcript_span(index, displayed, source)

    state_ids = (
        [cue_id for state in (title.get("states") or []) for cue_id in state.get("source_cues", [])]
        if require_full_state_coverage
        else source_ids
    )
    if require_full_state_coverage and state_ids != source_ids:
        raise SystemExit(
            f"sequence {index} 的 states 沒有依序完整承接 source_cues："
            f"expected={source_ids} actual={state_ids}"
        )


def _validate_full_transcript_coverage(titles: list[dict], cues: dict[int, dict]) -> None:
    """Require every latest-SRT cue to appear exactly once, in source order."""
    expected = sorted(cues)
    title_ids = [cue_id for title in titles for cue_id in title.get("source_cues", [])]
    state_ids = [
        cue_id
        for title in titles
        for state in title.get("states", [])
        for cue_id in state.get("source_cues", [])
    ]
    if title_ids != expected or state_ids != expected:
        raise SystemExit(
            "逐字稿覆蓋不完整或 cue 重複／順序錯誤："
            f"expected={expected} titles={title_ids} states={state_ids}"
        )


def _load_titles(episode_dir: Path, cid: str) -> tuple[Path, list[dict]]:
    path = episode_dir / TIGHTEN_DIR / f"{cid}_titles.json"
    try:
        titles = json.loads(path.read_text(encoding="utf-8"))["titles"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(f"{path} 不存在或不是合法 title plan") from exc
    if not isinstance(titles, list):
        raise SystemExit(f"{path} titles 必須是 array")
    return path, titles


def validate_plan(episode_dir: Path, cid: str) -> dict:
    """Read-only preflight for the complete audited content-visual recipe pair."""

    master = _open_editorial_master(episode_dir)
    candidate, _winner = _load_winner(episode_dir, cid, master.identity())
    _path, titles = _load_titles(episode_dir, cid)
    try:
        lineage, _broll = verify_visual_recipe_lineage(
            episode_dir,
            cid,
            str(candidate["format"]),
            master.identity(),
            title_items=titles,
            editorial_master=master,
        )
    except BrollContractError as exc:
        raise SystemExit(f"Title visual production gate 失敗：{exc}") from exc
    return {
        "status": "plan-valid",
        "cut_id": cid,
        "format": candidate["format"],
        "title_count": len(titles),
        "visual_pipeline_content_hash": lineage["content_hash"],
    }


def emit_audited_recipe(
    cid: str,
    materializations: list[dict] | tuple[dict, ...],
    *,
    output_dir: Path,
) -> Path:
    """Deterministically project accepted title materializations into one recipe."""

    from agents.brook.script_video.highlight_visual_pipeline import (
        HighlightVisualContractError,
        validate_materialization_projection,
    )

    titles: list[dict] = []
    for index, raw in enumerate(materializations):
        try:
            projection = validate_materialization_projection(
                raw, label=f"materializations[{index}]"
            )
        except HighlightVisualContractError as exc:
            raise BrollContractError(f"DP title materialization schema 不合法：{exc}") from exc
        if projection["target_lane"] != "title_track3":
            continue
        implementation = projection["implementation_kind"]
        if implementation not in {"hero_title", "supporting_title"}:
            raise BrollContractError(f"DP title implementation 不合法：{implementation}")
        spec = projection["render_spec"]
        if not isinstance(spec, dict) or not isinstance(spec.get("render_params"), dict):
            raise BrollContractError("DP title 缺少 exact render_spec")
        params = spec["render_params"]
        titles.append(
            {
                "text": projection["on_screen_text"],
                "t0": projection["t0"],
                "t1": projection["t1"],
                "tier": 1 if implementation == "hero_title" else 2,
                "style": params["style"],
                "pos_y": params["pos_y"],
                "source_range": projection["source_range"],
                "media_path": projection["media"]["path"],
                "provenance": projection["provenance"],
                "render_spec": projection["render_spec"],
                "visual_materialization": projection,
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{cid}_titles.json"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"titles": titles}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def apply(
    episode_dir: Path,
    cid: str,
    stills_dir: Path | None = None,
    *,
    orchestrator_timeline_name: str | None = None,
    orchestrator_timeline_uid: str | None = None,
    recipe_path: Path | None = None,
    broll_recipe_path: Path | None = None,
) -> dict:
    from build_resolve_project import connect_resolve

    orchestrated = orchestrator_timeline_name is not None or orchestrator_timeline_uid is not None
    if orchestrated:
        if not orchestrator_timeline_name or not orchestrator_timeline_uid:
            raise SystemExit("new orchestrator apply requires exact Timeline name and UID")
        master = c = w = visual_lineage = None
        fmt = "long"
    else:
        master = _open_editorial_master(episode_dir)
        c, w = _load_winner(episode_dir, cid, master.identity())
        fmt = c.get("format", "short")
    fcfg = FORMAT_TITLES[fmt]
    if recipe_path is None:
        _titles_path, titles = _load_titles(episode_dir, cid)
    else:
        _titles_path = Path(recipe_path)
        try:
            titles = json.loads(_titles_path.read_text(encoding="utf-8"))["titles"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SystemExit(f"title recipe 不可讀：{_titles_path}") from exc
    # 短片 V2 的 plan 級旗標（逐字稿全覆蓋、轉場語彙）跟 titles 同一份檔。
    # titles 本身已經由上面讀進來（呼叫端可以換掉那支 loader），所以這裡讀不到
    # 檔案就當作沒有旗標，不再另外把整個流程擋掉。
    plan: dict = {}
    try:
        loaded = json.loads(_titles_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        loaded = None
    if isinstance(loaded, dict):
        plan = loaded
    covers_full_transcript = bool(plan.get("covers_full_transcript", False))
    transition_mode = str(plan.get("transition_mode", "kinetic"))
    if transition_mode not in ("kinetic", "cut"):
        raise SystemExit("transition_mode 只允許 kinetic/cut")
    if not orchestrated:
        try:
            visual_lineage, _broll = verify_visual_recipe_lineage(
                episode_dir,
                cid,
                str(fmt),
                master.identity(),
                title_items=titles,
                editorial_master=master,
            )
        except BrollContractError as exc:
            raise SystemExit(f"Title visual production gate 失敗：{exc}") from exc
    titles.sort(key=lambda x: x["t0"])
    # 短片 V2 的版面／動態／品牌規則管的是「這支 script 自己 render 的卡」。
    # 帶 visual_materialization 的卡是 DP 出圖、Director 審過的成品，它的版面已經
    # 在視覺產線上判過，這裡不再用另一套規則重判一次。
    authored = [row for row in titles if row.get("visual_materialization") is None]
    if fmt == "short" and authored:
        _validate_split_opener_face_clearance(
            authored,
            opener_sec=float(plan.get("split_opener_sec", 4.0)),
        )
        _validate_short_motion_grammar(authored)
        _validate_brand_pattern_usage(authored)
    tight_cues = _tight_srt_cues(episode_dir, cid)
    if fmt == "short" and covers_full_transcript and authored:
        if not tight_cues:
            raise SystemExit(f"找不到 {cid} 最新 tight SRT，不能驗證完整逐字稿覆蓋")
        _validate_full_transcript_coverage(authored, tight_cues)
        _validate_full_transcript_display(authored)
    heroes = [x for x in titles if int(x.get("tier", 2)) == 1]
    if fmt == "short" and not 1 <= len(heroes) <= 3:
        raise SystemExit(
            f"hero（tier 1）有 {len(heroes)} 張——修修二十七輪：1–3 張（中段一張論點、片尾一張收束）"
        )
    # beat 只約束「這支 script 自己 render」的卡；DP 已出圖的卡由 visual
    # pipeline 審過，這裡不再要求它帶 beat。
    bad_beat = [
        x
        for x in heroes
        if x.get("visual_materialization") is None and x.get("beat") not in ("insight", "closing")
    ]
    if bad_beat:
        raise SystemExit(
            "hero 的 beat 只能是 insight（論點）或 closing（收束）："
            + "、".join(_title_text(x).replace(chr(10), "／") for x in bad_beat)
        )

    # 1) 逐卡取得素材：帶 visual_materialization 的直接沿用 DP 出的成品位元組
    #    （ADR-066，materializer 不重出也不改 render spec）；其餘走本 script 的
    #    render（參數 hash cache）。
    cards_dir = episode_dir / CARDS_DIR
    cards_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i, t in enumerate(titles):
        show_sec = round(float(t["t1"]) - float(t["t0"]), 2)
        projection = t.get("visual_materialization")
        if projection is not None:
            max_show_sec = _comp_sec(fmt) if orchestrated else _comp_sec(fmt) - 0.2
            if not 0.5 <= show_sec <= max_show_sec:
                raise SystemExit(
                    f"卡片 {i} 顯示 {show_sec}s 超出範圍（0.5–{max_show_sec}s）——"
                    f"composition data-duration 固定 {_comp_sec(fmt):.0f}s，"
                    "更長的卡拆兩張或改 t1"
                )
            lines = t["text"].split(chr(10))
            tier = int(t.get("tier", 2))
            if tier not in (1, 2):
                raise SystemExit(f"卡片 {i} tier={tier} 不合法（1=hero 2=標準）")
            limit = fcfg["max_line_hero"] if tier == 1 else fcfg["max_line"]
            too_long = [x for x in lines if len(x) > limit]
            if not orchestrated and too_long:
                raise SystemExit(
                    f"卡片 {i}（tier {tier}）行超過 {limit} 字：{too_long}——改寫或拆行"
                )
            mov = (episode_dir / projection["media"]["path"]).resolve()
            params = projection["render_spec"]["render_params"]
            jobs.append(
                {
                    "mov": mov,
                    "t0": float(t["t0"]),
                    "show_sec": show_sec,
                    "text": t["text"],
                    "pos_y": float(params["pos_y"]),
                    "source_start": float(projection["source_range"]["start_sec"]),
                    "source_end": float(projection["source_range"]["end_sec"]),
                    "timeline_name": f"{cid}_title_{projection['materialization_id']}",
                }
            )
            continue

        states = t.get("states") or []
        comp = KINETIC_COMP if states else fcfg["comp"]
        comp_sec = _comp_sec(fmt, kinetic=bool(states))
        if states and fmt != "short":
            raise SystemExit("kinetic sequence 目前只支援直式短片")
        if not 0.5 <= show_sec <= comp_sec - 0.2:
            raise SystemExit(
                f"卡片 {i} 顯示 {show_sec}s 超出範圍（0.5–{comp_sec - 0.2}s）——"
                f"{comp} data-duration 固定 {comp_sec:.0f}s，更長的 sequence 要按語意拆段"
            )
        label = _title_text(t)
        beat = t.get("beat")
        if beat not in BEATS:
            raise SystemExit(
                f"卡片 {i}「{label.replace(chr(10), '／')}」缺 beat 或不合法"
                f"（要 {'/'.join(BEATS)}）——寫不出它在論證裡的角色，就不該放這張卡"
            )
        tier = int(t.get("tier", 2))
        if tier not in (1, 2):
            raise SystemExit(f"卡片 {i} tier={tier} 不合法（1=hero 2=標準）")
        if states:
            _validate_kinetic_sequence(i, t, show_sec, fcfg)
        # 短片 V2：逐字稿是唯一文字來源。企劃只能指 cue、斷行、指定層級與
        # 動態；每一個畫面狀態都必須是來源 cue 的原文連續片段。
        if fmt == "short":
            if not states:
                raise SystemExit(
                    f"短片卡片 {i} 必須使用 transcript-driven states，不能另寫 static 文案"
                )
            if not tight_cues:
                raise SystemExit(f"找不到 {cid} 最新 tight SRT，不能產生短片文字編舞")
            _validate_transcript_driven_title(
                i,
                t,
                tight_cues,
                require_full_state_coverage=covers_full_transcript,
            )

        pos_y = float(t.get("pos_y", fcfg["pos_y"][tier]))
        if states:
            variables = {
                "sequence_json": json.dumps(
                    {
                        "states": states,
                        "stage": "free",
                        "exit": str(t.get("exit", "hard_cut")),
                        "brand_pattern": t.get("brand_pattern"),
                        "transition_mode": transition_mode,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "show_sec": show_sec,
                "pos_y": pos_y,
            }
        else:
            lines = label.split("\n")
            limit = fcfg["max_line_hero"] if tier == 1 else fcfg["max_line"]
            too_long = [x for x in lines if len(x) > limit]
            if too_long:
                raise SystemExit(
                    f"卡片 {i}（tier {tier}）行超過 {limit} 字：{too_long}——改寫或拆行"
                )
            variables = {
                "line1": lines[0],
                "line2": lines[1] if len(lines) > 1 else "",
                "show_sec": show_sec,
                "pos_y": pos_y,
                "tier": tier,
            }
            card_scale = float(t.get("card_scale", 1.0))
            if not 0.75 <= card_scale <= 1.15:
                raise SystemExit(f"卡片 {i} card_scale={card_scale} 不合法（允許 0.75–1.15）")
            variables["card_scale"] = card_scale
            if fmt == "short":
                animation = str(t.get("animation", "slam" if tier == 1 else "swipe"))
                if animation not in SHORT_ANIMATIONS:
                    raise SystemExit(
                        f"卡片 {i} animation={animation!r} 不合法"
                        f"（要 {'/'.join(SHORT_ANIMATIONS)}）"
                    )
                variables["animation"] = animation
                for key in ("line1_scale", "line2_scale"):
                    value = float(t.get(key, 1.0))
                    if not 0.78 <= value <= 1.20:
                        raise SystemExit(f"卡片 {i} {key}={value} 不合法（允許 0.78–1.20）")
                    variables[key] = value
            style = t.get("style", fcfg.get("style"))
            if style:  # 短片無 style 概念——不進 variables，hash 不變
                variables["style"] = style
        h = _card_hash(variables, comp)
        mov = cards_dir / f"{cid}_{i}_{h}.mov"
        if not mov.exists():
            _render_card(variables, mov, comp)
        else:
            logger.info("cache hit: %s", mov.name)
        jobs.append(
            {
                "mov": mov,
                "t0": float(t["t0"]),
                "show_sec": show_sec,
                "text": label,
                "pos_y": pos_y,
            }
        )

    if fmt == "short":
        _validate_rendered_frame_safety([job["mov"] for job in jobs])

    # Re-open both roots immediately before any Resolve access. CURRENT may
    # switch while jobs are prepared; a different generation must never apply.
    if not orchestrated:
        master = _open_editorial_master(episode_dir)
        c, w = _load_winner(episode_dir, cid, master.identity())
        try:
            fresh_lineage, _broll = verify_visual_recipe_lineage(
                episode_dir,
                cid,
                str(c["format"]),
                master.identity(),
                title_items=titles,
                editorial_master=master,
            )
        except BrollContractError as exc:
            raise SystemExit(f"Title visual production gate 失敗：{exc}") from exc
        if fresh_lineage != visual_lineage:
            raise SystemExit("Title visual pipeline CURRENT 在準備期間切換，未連線 Resolve")

    # 2) Resolve：匯入 + 疊軌
    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != episode_dir.name:
        project = pm.LoadProject(episode_dir.name)
    if project is None:
        raise SystemExit(f"project「{episode_dir.name}」不存在")
    fps = float(project.GetSetting("timelineFrameRate"))
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    director_label = (
        orchestrator_timeline_name
        if orchestrated
        else f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}（緊·導播）"
    )
    timelines = {}
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t:
            timelines[t.GetName()] = t
    director = timelines.get(director_label)
    if director is None:
        raise SystemExit(f"「{director_label}」不存在——先跑 run_short_director")
    if orchestrated:
        timeline_uid = None
        for method_name in ("GetUniqueId", "GetUniqueID"):
            method = getattr(director, method_name, None)
            value = method() if callable(method) else None
            if isinstance(value, str) and value.strip():
                timeline_uid = value.strip()
                break
        if timeline_uid != orchestrator_timeline_uid:
            raise SystemExit("new orchestrator target Timeline UID changed before title apply")
    selected = project.SetCurrentTimeline(director)
    if orchestrated and selected is False:
        raise SystemExit("Resolve refused to select orchestrator target Timeline")
    if orchestrated:
        get_current = getattr(project, "GetCurrentTimeline", None)
        current = get_current() if callable(get_current) else director
        current_uid = None
        for method_name in ("GetUniqueId", "GetUniqueID"):
            method = getattr(current, method_name, None)
            value = method() if callable(method) else None
            if isinstance(value, str) and value.strip():
                current_uid = value.strip()
                break
        if current_uid != orchestrator_timeline_uid:
            raise SystemExit("Resolve current Timeline differs from orchestrator target UID")

    removed_subtitles = 0
    if fmt == "short" and covers_full_transcript:
        # This plan is the subtitle renderer.  Keeping the Resolve subtitle
        # track would duplicate every sentence at the bottom of frame.
        for ti in range(1, director.GetTrackCount("subtitle") + 1):
            subtitle_items = director.GetItemListInTrack("subtitle", ti) or []
            if subtitle_items:
                if not director.DeleteClips(subtitle_items):
                    raise SystemExit(f"無法清除 subtitle track {ti}；中止以避免雙重字幕")
                removed_subtitles += len(subtitle_items)

    dur = (director.GetEndFrame() - director.GetStartFrame()) / fps
    cap = max(3, int(dur / SEC_PER_CARD))
    if not orchestrated and len(titles) > cap:
        raise SystemExit(
            f"字卡 {len(titles)} 張超過密度上限 {cap} 張（片長 {dur:.0f}s ÷ "
            f"{SEC_PER_CARD:.0f}s）——每句都想 highlight 反而稀釋畫龍點睛，"
            "砍掉鋪陳句、只留論證骨架的每一拍"
        )

    # 冪等清場：track1+ 的舊卡/巢狀 item、v1 子 timeline、media pool 舊卡
    # ⚠️ 前綴 <cid>_ 會誤殺 broll 的貼紙/概念卡（<cid>_broll_*，track 4）——
    # 十七輪血案：titles 重跑把整條 track 4 滅掉。broll 卡明確排除。
    def _mine(name: str) -> bool:
        return name.startswith((f"{cid}_", f"titles - {cid}")) and not name.startswith(
            f"{cid}_broll_"
        )

    for ti in range(1, director.GetTrackCount("video") + 1):
        stale = [
            it
            for it in (director.GetItemListInTrack("video", ti) or [])
            if _mine(it.GetName() or "")
        ]
        if stale:
            director.DeleteClips(stale)
    if f"titles - {cid}" in timelines:  # v1 Fusion 巢狀子 timeline 退役清除
        mp.DeleteTimelines([timelines[f"titles - {cid}"]])
    cards_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "Cards"), None
    ) or mp.AddSubFolder(root, "Cards")
    stale_clips = [cl for cl in (cards_bin.GetClipList() or []) if _mine(cl.GetName() or "")]
    if stale_clips:
        mp.DeleteClips(stale_clips)

    # 元素互相遮擋防呆（二十五輪血案：字卡「被社群媒體綁架」壓在貼紙上）
    # 字卡（track 3）與貼紙/概念卡（track 4）都在畫面中下段——時間重疊
    # 就一定互相打架。broll.json 是唯一真相，這裡直接擋。
    broll_path = (
        Path(broll_recipe_path)
        if broll_recipe_path is not None
        else episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    )
    # The new long orchestrator has already had these exact rendered frames
    # reviewed by a visual agent.  Keep this approximate layout heuristic for
    # the legacy authoring route only; it must not overrule an approved image.
    if not orchestrated and broll_path.exists():
        overlays = [
            it
            for it in json.loads(broll_path.read_text(encoding="utf-8"))["items"]
            if it["kind"] in ("sticker", "icon_motion", "concept")
        ]
        for job in jobs:
            a0, a1 = job["t0"], job["t0"] + job["show_sec"]
            for ov in overlays:
                b0, b1 = float(ov["t0"]), float(ov["t1"])
                if not (a0 < b1 and a1 > b0):
                    continue
                # 時間重疊不必然打架——**垂直分層**就能共存（二十八輪：
                # 上一輪為了避讓把貼紙從 2.2s 搬到 8.8s，語意時機整個跑掉。
                # 版面問題要用版面解，不可用時間解）。
                # 貼紙帶：y_pct ± size_pct/2（畫面高比例，粗估用寬度比例）
                y = float(ov.get("y_pct", 46)) / 100
                half = float(ov.get("size_pct", 26)) / 100 * 0.5
                sticker_bottom = y + half
                card_top = float(job.get("pos_y", 0.63)) - 0.09  # 卡片高約 18% 畫面
                if sticker_bottom > card_top:
                    raise SystemExit(
                        f"字卡「{job['text'].replace(chr(10), '／')}」({a0}–{a1:.1f}s) 與"
                        f" {ov['kind']}「{ov.get('slug')}」({b0}–{b1}s) 同時出現且垂直重疊"
                        f"（貼紙下緣 {sticker_bottom:.2f} > 卡片上緣 {card_top:.2f}）"
                        "——把貼紙 y_pct 往上移或縮小 size_pct，**不要改時間**"
                    )

    mp.SetCurrentFolder(cards_bin)
    while director.GetTrackCount("video") < 3:
        director.AddTrack("video")
    made = []
    tl_start = director.GetStartFrame()
    tl_end = director.GetEndFrame()  # 清完舊卡後 = 主畫面實際結束幀
    for job in jobs:
        items = mp.ImportMedia([str(job["mov"])]) or []
        if not items:
            raise SystemExit(f"匯入失敗: {job['mov']}")
        items[0].SetClipProperty("Clip Name", job["timeline_name"])
        record = tl_start + int(job["t0"] * fps)
        # 卡片退場動畫收在 show_sec 內，截到 show_sec + 2 frames；
        # 並鉗位在主畫面結束前——卡片伸出片尾會變「黑底浮卡」（盲審 S2 抓到）
        requested_frames = (
            int(round((job["source_end"] - job["source_start"]) * fps))
            if orchestrated
            else int(job["show_sec"] * fps) + 2
        )
        dur = min(requested_frames, max(1, tl_end - record))
        ok = mp.AppendToTimeline(
            [
                {
                    "mediaPoolItem": items[0],
                    "mediaType": 1,
                    "trackIndex": 3,
                    "recordFrame": record,
                    "startFrame": int(job["source_start"] * fps),
                    "endFrame": int(job["source_start"] * fps) + dur,
                }
            ]
        )
        if not ok or not ok[0]:
            raise SystemExit(f"疊軌失敗 @{job['t0']}")
        placed = ok[0]
        placed_source = placed.GetMediaPoolItem()
        if placed_source is None:
            raise SystemExit(f"字卡落軌後失去 Media Pool 來源 @{job['t0']}: {job['mov']}")
        placed_path = placed_source.GetClipProperty("File Path") or ""
        if Path(placed_path).resolve() != Path(job["mov"]).resolve():
            raise SystemExit(
                f"字卡來源錯誤 @{job['t0']}: expected={job['mov']} actual={placed_path}"
            )
        made.append(
            {"text": job["text"].replace("\n", "/"), "at": job["t0"], "show_sec": job["show_sec"]}
        )
    mp.SetCurrentFolder(root)
    pm.SaveProject()

    stills = []
    if stills_dir is not None:
        stills_dir.mkdir(parents=True, exist_ok=True)
        rjobs = []
        for i, job in enumerate(jobs):
            fr = tl_start + int((job["t0"] + job["show_sec"] / 2) * fps)
            project.SetRenderSettings(
                {
                    "MarkIn": fr,
                    "MarkOut": fr,
                    "TargetDir": str(stills_dir),
                    "CustomName": f"card_{cid}_{i}",
                }
            )
            jid = project.AddRenderJob()
            if jid:
                rjobs.append((jid, f"card_{cid}_{i}"))
        project.StartRendering([j for j, _ in rjobs], isInteractiveMode=False)
        for _ in range(120):
            if not project.IsRenderingInProgress():
                break
            time.sleep(1)
        for jid, name in rjobs:
            project.DeleteRenderJob(jid)
            stills.append(name)

    return {
        "status": "titled",
        "timeline": director_label,
        "cards": made,
        "stills": stills,
        "subtitle_mode": "title_choreography" if covers_full_transcript else "srt_track",
        "removed_subtitle_items": removed_subtitles,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片橘底大字 punch 卡（hyperframes overlay）")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    parser.add_argument("--stills", help="物化後渲樣張到此資料夾")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="驗 current Director/DP/Audit、exact recipes/media，不連 Resolve",
    )
    args = parser.parse_args(argv)
    result = (
        validate_plan(Path(args.episode), args.id)
        if args.validate_only
        else apply(Path(args.episode), args.id, Path(args.stills) if args.stills else None)
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
