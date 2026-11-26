"""第 5 類「解釋動畫」：把規劃表上核准的解釋動畫列，照 DP 寫的圖表規格渲成素材。

圖表是一組不分題材的品牌 infographic（修修 2026-11-25「照圖表種類重做」）：
橫條、直條、圓環、點陣、圓餅、大數字、折線、刻度、算式、對比——
`video/compositions/explainer/compositions/<chart>.html`，欄位與選圖規則見 skill youtube-explainer。

輸入有兩份：
- 規劃表那一列：版面（`layout`：滿版 full／右側欄 overlay）、蓋住的字幕範圍（＝整張停留的秒數）、
  「出處」（`source`，印在右上；規格檔可以自己寫 `source` 蓋掉）。
- DP 寫的圖表規格 `<episode>/visual_sources/<row_id>.json`：

      {"chart": "bars", "title": "誰比較多",
       "items": [{"label": "第一組", "value": 30, "at": {"cue": 412}},
                 {"label": "第二組", "value": 55, "at": {"cue": 414, "offset": 0.6}}]}

  時間欄位（`at`、`*_at`、`until`）可以寫秒數（從這張開始算），
  或寫 `{"cue": 字幕句號, "offset": 秒}`＝那句字幕開頭再往後幾秒
  （同一句裡講到的字，用 words.json 的字級時間算 offset）。

做之前先用無頭 Chrome 開圖表、跳到最後量版面：字超出安全框、兩段字疊在一起就擋下來，不渲。

用法：

    python -m agents.brook.youtube_video.visuals.explainer "G:/Footages/<ep>" --row <id> --check
    python -m agents.brook.youtube_video.visuals.explainer "G:/Footages/<ep>" [--row <id>] [--force]
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import html
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "explainer"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "explainer"
KIT_FILES = (
    COMPOSITION_DIR / "assets" / "kit" / "explainer-kit.js",
    COMPOSITION_DIR / "assets" / "kit" / "explainer-kit.css",
)
HYPERFRAMES = "hyperframes@0.7.72"
SOURCES_DIR = "visual_sources"
CHROME = os.environ.get("CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe")

MIN_SEC = 3.0
MAX_SEC = 60.0  # 圖表本身渲 60 秒，交件截到停留秒數
LAST_HOLD = 1.0  # 最後一個元素至少要在畫面上停這麼久才退場
MAX_TITLE_UNITS = {"full": 16.0, "overlay": 10.0}  # 標題 60px 一行：側欄寬 640 ≈ 10 個全形字

# 圖表 → （清單欄位, 最少, 滿版最多, 側欄最多, 每一項必填）；None＝沒有清單
CHARTS: dict[str, tuple[str | None, int, int, int, tuple[str, ...]]] = {
    "bars": ("items", 1, 6, 6, ("label", "value", "at")),
    "columns": ("items", 1, 8, 8, ("label", "value", "at")),
    "donut": ("items", 1, 3, 2, ("label", "value", "at")),
    "pie": ("parts", 1, 4, 4, ("label", "value", "at")),
    "dots": (None, 0, 0, 0, ()),
    "stat": (None, 0, 0, 0, ()),
    "line": ("series", 1, 2, 2, ("points", "at")),
    "axis": ("points", 1, 8, 8, ("label", "at")),
    "equation": ("lines", 1, 8, 8, ("at",)),
    "contrast": ("columns", 2, 2, 2, ("label", "at")),
}
# 沒有清單的圖表，頂層必填
TOP_REQUIRED = {"dots": ("lit", "dots_at", "lit_at"), "stat": ("value", "at"), "line": ("x",)}
TIME_KEY = re.compile(r"^(?:at|until|\w+_at)$")

Runner = Callable[[list[str], Path], None]
Checker = Callable[[str, Mapping[str, Any]], dict[str, Any]]


class ExplainerError(delivery.DeliveryError):
    """圖表規格或版面有問題；訊息直接給 DP／修修看。"""


# ── 規格檔 ──────────────────────────────────────────────────────────────


def sources_dir(episode_dir: Path) -> Path:
    return Path(episode_dir) / SOURCES_DIR


def spec_path(episode_dir: Path, row_id: str) -> Path:
    return sources_dir(episode_dir) / f"{row_id}.json"


def load_spec(episode_dir: Path, row_id: str) -> dict[str, Any]:
    path = spec_path(episode_dir, row_id)
    if not path.is_file():
        raise ExplainerError(
            f"{row_id}: 還沒有圖表規格 {path}（DP 要先寫，格式見 skill youtube-explainer）"
        )
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ExplainerError(f"{row_id}: 圖表規格要是一個 JSON 物件")
    return spec


def text_units(text: str) -> float:
    return sum(1.0 if unicodedata.east_asian_width(c) in "WF" else 0.55 for c in text)


def chart_html(chart: str) -> Path:
    return COMPOSITION_DIR / "compositions" / f"{chart}.html"


def component_sha(chart: str) -> str:
    """圖表 HTML＋共用積木一起雜湊：積木改了，所有圖表都要重做。"""
    digest = hashlib.sha256()
    for path in (chart_html(chart), *KIT_FILES):
        digest.update(delivery.file_sha(path).encode())
    return digest.hexdigest()


def resolve_times(
    node: Any, cues: Mapping[Any, Mapping[str, Any]], row_start: float, where: str = ""
) -> Any:
    """把時間欄位的 {"cue", "offset"} 換成秒數（從這張開始算）；其他原樣。"""
    if isinstance(node, list):
        return [resolve_times(v, cues, row_start, f"{where}[{i}]") for i, v in enumerate(node)]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        here = f"{where}.{key}" if where else key
        if TIME_KEY.match(key) and isinstance(value, dict):
            cue = cues.get(value.get("cue"))
            if cue is None:
                raise ExplainerError(f"{here} 的字幕 {value.get('cue')!r} 不在這一列蓋住的範圍裡")
            value = round(float(cue["start"]) - row_start + float(value.get("offset", 0)), 3)
        if TIME_KEY.match(key) and not isinstance(value, (int, float)):
            raise ExplainerError(f'{here} 要是秒數或 {{"cue": 句號, "offset": 秒}}')
        out[key] = resolve_times(value, cues, row_start, here)
    return out


def _times(node: Any, where: str = "") -> list[tuple[str, float]]:
    if isinstance(node, list):
        return [t for i, v in enumerate(node) for t in _times(v, f"{where}[{i}]")]
    if not isinstance(node, dict):
        return []
    found = []
    for key, value in node.items():
        here = f"{where}.{key}" if where else key
        if TIME_KEY.match(key) and isinstance(value, (int, float)):
            found.append((here, float(value)))
        else:
            found.extend(_times(value, here))
    return found


def check_shape(chart: str, spec: Mapping[str, Any], layout: str) -> None:
    """每一種圖表的必填欄位與數量上限（版面細節交給瀏覽器量）。"""
    key, lo, hi_full, hi_side, need = CHARTS[chart]
    for field in TOP_REQUIRED.get(chart, ()):
        if spec.get(field) in (None, ""):
            raise ExplainerError(f"{chart} 要有 {field}")
    if key is None:
        return
    entries = spec.get(key)
    if not isinstance(entries, list):
        raise ExplainerError(f"{chart} 要有 {key}（清單）")
    hi = hi_full if layout == "full" else hi_side
    if not lo <= len(entries) <= hi:
        side = "滿版" if layout == "full" else "側欄"
        raise ExplainerError(
            f"{chart}（{side}）的 {key} 要 {lo}–{hi} 項，現在 {len(entries)} 項——放不下就拆成兩張"
        )
    for i, entry in enumerate(entries):
        if chart == "equation" and entry.get("rule"):
            continue  # 算式的分隔線只有 at
        missing = [f for f in need if entry.get(f) in (None, "")]
        if missing:
            raise ExplainerError(f"{chart} 的 {key}[{i}] 缺 {'、'.join(missing)}")


def chart_spec(
    episode_dir: Path, plan: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[str, dict[str, Any], float, float]:
    """規劃表那一列＋規格檔 → （圖表, 給圖表的 spec, 起點秒數, 停留秒數）。"""
    if row.get("category") != CATEGORY:
        raise ExplainerError(f"{row.get('id')}: 不是解釋動畫")
    rid = row["id"]
    layout = row.get("layout")
    if layout not in ("full", "overlay"):
        raise ExplainerError(f"{rid}: 還沒選版面（滿版或疊在畫面上）")
    start, span = delivery.overlay_window(plan, row)
    seconds = round(span, 3)
    if not MIN_SEC <= seconds <= MAX_SEC:
        raise ExplainerError(f"{rid}: 蓋住 {seconds:.1f} 秒，解釋動畫要 {MIN_SEC:g}–{MAX_SEC:g} 秒")
    raw = load_spec(episode_dir, rid)
    chart = raw.get("chart")
    if chart not in CHARTS:
        raise ExplainerError(f"{rid}: chart 要是 {'／'.join(CHARTS)} 其中一種，現在是 {chart!r}")
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ExplainerError(f"{rid}: 要有標題（title）")
    if text_units(title) > MAX_TITLE_UNITS[layout]:
        raise ExplainerError(
            f"{rid}: 標題「{title}」太長"
            f"（{'滿版約 16' if layout == 'full' else '側欄約 10'} 個全形字以內）"
        )
    cues = {c["id"]: c for c in vp.cue_range(plan, row["start_cue"], row["end_cue"])}
    body = {k: v for k, v in raw.items() if k not in ("chart", "layout", "show_sec")}
    try:
        body = resolve_times(body, cues, start)
        check_shape(chart, body, layout)
    except ExplainerError as exc:
        raise ExplainerError(f"{rid}: {exc}") from exc
    for where, t in _times(body):
        if t < 0:
            raise ExplainerError(f"{rid}: {where} 是 {t:.2f} 秒，比這張開始還早")
        if t > seconds - LAST_HOLD:
            raise ExplainerError(
                f"{rid}: {where} 在 {t:.1f} 秒，整張只停 {seconds:.1f} 秒——"
                f"最後一個元素至少要停 {LAST_HOLD:g} 秒，請把字幕範圍往後拉"
            )
    source = str(body.pop("source", None) or row.get("source") or "").strip()
    body.pop("title", None)
    spec = {"layout": layout, "title": title, "show_sec": seconds, **body}
    if source:
        spec["source"] = source if source.startswith("資料：") else f"資料：{source}"
    return chart, spec, start, seconds


# ── 版面檢查（無頭 Chrome） ─────────────────────────────────────────────


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def translate_path(self, path):
        p = path.split("?")[0]
        if p.startswith("/compositions/assets/"):  # 圖表在 compositions/ 底下，assets/… 指到上一層
            p = p[len("/compositions") :]
        return str(COMPOSITION_DIR.joinpath(*[x for x in p.split("/") if x]))


CHECK_RE = re.compile(r'<pre id="exk-check">(.*?)</pre>', re.S)


def chrome_check(chart: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """開圖表（?check）跳到最後，回傳 {at, outside, overlap}。

    量法在 explainer-kit.js 的 check()。
    """
    if not Path(CHROME).is_file():
        raise ExplainerError(f"版面檢查要用 Chrome，找不到 {CHROME}（設環境變數 CHROME）")
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(COMPOSITION_DIR))
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    query = urllib.parse.urlencode({"spec": json.dumps(spec, ensure_ascii=False), "check": ""})
    url = f"http://127.0.0.1:{srv.server_address[1]}/compositions/{chart}.html?{query}"
    try:
        proc = subprocess.run(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--window-size=1920,1080",
                "--virtual-time-budget=8000",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            **quiet_kwargs(),
        )
    finally:
        srv.shutdown()
    hit = CHECK_RE.search(proc.stdout or "")
    if not hit:
        raise ExplainerError(f"版面檢查沒有結果（圖表可能壞了）：{(proc.stderr or '')[-400:]}")
    return json.loads(html.unescape(hit.group(1)))


def layout_problems(result: Mapping[str, Any]) -> list[str]:
    problems = [f"「{o['text']}」超出安全框 {o['box']}" for o in result.get("outside", [])]
    problems += [f"「{a}」跟「{b}」疊在一起" for a, b in result.get("overlap", [])]
    return problems


# ── 渲染與交件 ──────────────────────────────────────────────────────────


def _hyperframes_render(argv: list[str], cwd: Path) -> None:
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=sys.platform == "win32",  # npx 在 Windows 是 .cmd
        **quiet_kwargs(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"render 失敗：{(proc.stderr or proc.stdout or '')[-600:]}")


def _render(
    chart: str,
    variables: Mapping[str, Any],
    seconds: float,
    paths: delivery.AssetPaths,
    runner: Runner,
) -> None:
    paths.media.parent.mkdir(parents=True, exist_ok=True)
    raw = paths.media.with_name(paths.media.stem + ".full.mov")
    vars_file = paths.media.with_name(paths.media.stem + ".vars.json")
    vars_file.write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
    try:
        runner(
            [
                "npx",
                "--yes",
                HYPERFRAMES,
                "render",
                ".",
                "-c",
                f"compositions/{chart}.html",
                "-o",
                str(raw),
                "--format",
                "mov",
                "--fps",
                delivery.FPS,
                "-q",
                "standard",
                "--quiet",
                "--no-browser-gpu",
                "--variables-file",
                str(vars_file),
            ],
            COMPOSITION_DIR,
        )
        # 圖表固定渲 60 秒；交件長度＝停留秒數。ProRes 每格都是關鍵格，copy 截斷是逐格精準的。
        runner(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(raw),
                "-t",
                f"{seconds:.3f}",
                "-c",
                "copy",
                str(paths.media),
            ],
            COMPOSITION_DIR,
        )
    finally:
        raw.unlink(missing_ok=True)
        vars_file.unlink(missing_ok=True)


def check_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    checker: Checker = chrome_check,
) -> tuple[str, dict[str, Any], list[str]]:
    chart, spec, _, _ = chart_spec(episode_dir, plan, row)
    return chart, spec, layout_problems(checker(chart, spec))


def deliver_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    force: bool = False,
    runner: Runner = _hyperframes_render,
    checker: Checker = chrome_check,
) -> tuple[dict[str, Any], bool]:
    """檢查版面、渲一列並寫收據。回傳（收據, 這次有沒有真的渲）。"""
    chart, spec, start, seconds = chart_spec(episode_dir, plan, row)
    variables = {"spec": json.dumps(spec, ensure_ascii=False)}
    comp_sha = component_sha(chart)
    component = f"explainer/{chart}"
    sha = delivery.inputs_sha(
        category=CATEGORY, component=component, component_sha=comp_sha, variables=variables
    )
    paths = delivery.asset_paths(episode_dir, row["id"])
    if not force and delivery.is_current(paths, sha):
        return delivery.load_receipt(paths.receipt) or {}, False
    problems = layout_problems(checker(chart, spec))
    if problems:
        raise ExplainerError(f"{row['id']}: 版面放不下——" + "；".join(problems))
    _render(chart, variables, seconds, paths, runner)
    receipt = delivery.write_receipt(
        paths,
        row=row,
        component=component,
        component_sha=comp_sha,
        variables=variables,
        start_sec=start,
        duration_sec=seconds,
        sha=sha,
        extra={"chart": chart},
    )
    return receipt, True


def approved_rows(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in plan.get("rows", [])
        if row.get("category") == CATEGORY and row.get("status") == "approved"
    ]


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 主控台預設 cp1252，印中文會炸
    parser = argparse.ArgumentParser(description="渲解釋動畫（第 5 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只做這一列")
    parser.add_argument("--check", action="store_true", help="只檢查規格與版面，不渲")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in plan.get("rows", []) if row["id"] == args.row]
        if not rows:
            print(f"找不到這一列：{args.row}", file=sys.stderr)
            return 2
    failures = 0
    for row in rows:
        try:
            if args.check:
                chart, spec, problems = check_row(args.episode, plan, row)
                if problems:
                    raise ExplainerError(f"{row['id']}: " + "；".join(problems))
                print(f"版面 OK {row['id']}：{chart}（{spec['layout']}）{spec['show_sec']:.2f}s")
                continue
            receipt, rendered = deliver_row(args.episode, plan, row, force=args.force)
        except delivery.DeliveryError as exc:
            failures += 1
            print(f"✗ {exc}", file=sys.stderr)
            continue
        label = "渲好" if rendered else "沿用"
        print(
            f"{label} {row['id']}：{receipt.get('chart')}"
            f" {receipt['start_sec']:.2f}s 起 {receipt['duration_sec']:.2f}s"
        )
    print(f"解釋動畫：{len(rows) - failures}/{len(rows)} 張{'檢查' if args.check else '完成'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
