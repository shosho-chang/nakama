"""第 7 類「論文／文件 highlighter」：把規劃表上核准的文件列渲成滿版 overlay 素材。

設計（修修 2026-11-16 定版）見 `video/compositions/doc_highlight/compositions/doc_highlight.html`：
品牌冷灰滿版、白紙文件疊在左上右下兩角的品牌碎片上，黃色螢光筆隨旁白一步一步劃，
鏡頭推近到劃的地方；左下橘色「資料來源」字牌。

輸入有兩份：
- 規劃表那一列：「出處」（`source`）＝畫面上的資料來源文字；「給 DP 的指示」寫文件在哪。
- DP 寫的文件說明檔 `<episode>/visual_sources/<row_id>.json`（格式見 skill youtube-doc-highlight）：

      {"document": "111年新聞稿.pdf", "page": 15, "crop": null,
       "steps": [{"cue": 133, "marks": [{"text": "金融性資產(B)"},
                                         {"text": "222", "row": "金融性資產(B)"}]}]}

  每一步在哪一句字幕開頭劃、劃哪裡。劃的位置用文件裡的字自動找（PDF 文字層），
  找不到字（掃描檔、圖片）就用 `rect` 手動標。`--check` 先產一張框線檢查圖給 DP 看過再渲。

時間：整張停留＝這一列蓋住的字幕總長（4–60 秒）。

用法：

    python -m agents.brook.youtube_video.visuals.doc_highlight "G:/Footages/<ep>" --row <id> --check
    python -m agents.brook.youtube_video.visuals.doc_highlight "G:/Footages/<ep>"
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "doc_highlight"
COMPONENT = "doc_highlight"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "doc_highlight"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "doc_highlight.html"
HYPERFRAMES = "hyperframes@0.7.72"
SOURCES_DIR = "visual_sources"

MIN_SEC = 4.0
MAX_SEC = 60.0  # 元件本身渲 60 秒，交件截到停留秒數
FIRST_AT = 0.8  # 灰底刷完、紙浮上來之後才開始劃
LAST_HOLD = 1.0  # 最後一步至少要在畫面上停這麼久
TARGET_PX = 2400  # 裁出來的文件渲成約這麼寬：鏡頭推近到 1:1 也不糊
MAX_SOURCE_UNITS = 60.0
PDF_SUFFIXES = (".pdf",)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

Box = tuple[float, float, float, float]  # x0, y0, x1, y1（左上原點、y 往下；PDF 用點，圖片用 px）
Runner = Callable[[list[str], Path], None]


class DocError(delivery.DeliveryError):
    """文件或說明檔有問題；訊息直接給 DP／修修看。"""


# ── 文件 ────────────────────────────────────────────────────────────────


class PdfDocument:
    """PDF 的一頁：用 PDFium 找字的位置、渲成圖。"""

    def __init__(self, path: Path, page: int):
        import pypdfium2 as pdfium

        self.pdf = pdfium.PdfDocument(str(path))
        if not 1 <= page <= len(self.pdf):
            raise DocError(f"{path.name} 只有 {len(self.pdf)} 頁，沒有第 {page} 頁")
        self.page = self.pdf[page - 1]
        self.width, self.height = self.page.get_size()
        self.text = self.page.get_textpage()
        self.has_text = bool(self.text.get_text_bounded().strip())

    def find(self, query: str) -> list[Box]:
        if not self.has_text:
            raise DocError("這頁 PDF 沒有文字層（可能是掃描檔），請改用 rect 手動標位置")
        boxes = []
        search = self.text.search(query)
        while hit := search.get_next():
            first, count = hit
            parts = [self.text.get_charbox(i) for i in range(first, first + count)]
            left = min(p[0] for p in parts)
            right = max(p[2] for p in parts)
            bottom = min(p[1] for p in parts)
            top = max(p[3] for p in parts)
            boxes.append((left, self.height - top, right, self.height - bottom))
        return boxes

    def line_segments(self, box: Box) -> list[Box]:
        """跟 box 同一行的文字片段（PDF 裡一段連續的字，表格裡通常是一格）。"""
        middle, height = (box[1] + box[3]) / 2, box[3] - box[1]
        segments = []
        for i in range(self.text.count_rects()):
            left, bottom, right, top = self.text.get_rect(i)
            y0, y1 = self.height - top, self.height - bottom
            if abs((y0 + y1) / 2 - middle) <= height * 0.6:
                segments.append((left, y0, right, y1))
        return segments

    def render(self, crop: Box, scale: float) -> Image.Image:
        x0, y0, x1, y1 = crop
        image = self.page.render(
            scale=scale, crop=(x0, self.height - y1, self.width - x1, y0)
        ).to_pil()
        return image.convert("RGB")


class ImageDocument:
    """圖片（截圖）：沒有文字層，只能用 rect 手動標。"""

    has_text = False

    def __init__(self, path: Path):
        self.image = Image.open(path).convert("RGB")
        self.width, self.height = self.image.size

    def find(self, query: str) -> list[Box]:
        raise DocError("圖片沒有文字層，請改用 rect 手動標位置（單位：圖片 px）")

    def line_segments(self, box: Box) -> list[Box]:
        return []

    def render(self, crop: Box, scale: float) -> Image.Image:
        x0, y0, x1, y1 = (round(v) for v in crop)
        image = self.image.crop((x0, y0, x1, y1))
        if scale != 1:
            image = image.resize((round(image.width * scale), round(image.height * scale)))
        return image


def open_document(path: Path, page: int | None) -> PdfDocument | ImageDocument:
    if not path.is_file():
        raise DocError(f"找不到文件：{path}")
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return PdfDocument(path, page or 1)
    if suffix in IMAGE_SUFFIXES:
        return ImageDocument(path)
    raise DocError(f"不支援的文件格式 {suffix}（只吃 PDF 與 png／jpg／webp；網頁請先列印成 PDF）")


# ── 螢光筆範圍 ──────────────────────────────────────────────────────────


def _pick(hits: Sequence[Box], nth: int, what: str) -> Box:
    if not hits:
        raise DocError(f"文件裡找不到「{what}」")
    if not 1 <= nth <= len(hits):
        raise DocError(f"「{what}」只找到 {len(hits)} 處，沒有第 {nth} 處")
    return hits[nth - 1]


def _reading_order(boxes: Sequence[Box]) -> list[Box]:
    return sorted(boxes, key=lambda b: (round(b[1] / 4), b[0]))


def find_text(doc, mark: Mapping[str, Any]) -> Box:
    """`{"text": 字, "row": 同一列開頭的字（可省）, "nth": 第幾處（從 1 起，可省）}`。"""
    query = str(mark["text"])
    nth = int(mark.get("nth", 1))
    hits = doc.find(query)
    row = mark.get("row")
    if row:
        anchor = _pick(_reading_order(doc.find(str(row))), int(mark.get("row_nth", 1)), str(row))
        middle = (anchor[1] + anchor[3]) / 2
        tolerance = (anchor[3] - anchor[1]) * 0.6
        hits = [
            b for b in hits if abs((b[1] + b[3]) / 2 - middle) <= tolerance and b[0] >= anchor[0]
        ]
        hits.sort(key=lambda b: b[0])
        return _pick(hits, nth, f"{query}（{row} 那一列）")
    return _pick(_reading_order(hits), nth, query)


def resolve_mark(doc, mark: Mapping[str, Any]) -> tuple[Box, str]:
    """一個螢光筆範圍 → （框, 種類 text／column）。字的框外面留一點邊。"""
    if "rect" in mark:
        x0, y0, x1, y1 = (float(v) for v in mark["rect"])
        if not (x1 > x0 and y1 > y0):
            raise DocError(f"rect {mark['rect']} 要寫成 [左, 上, 右, 下]")
        return (x0, y0, x1, y1), str(mark.get("kind", "text"))
    if "column" in mark:
        # 整欄：從 top 那段字的下緣，劃到 column 那一格的下緣。表格數字靠右對齊，
        # 欄寬＝同一行相鄰兩格右緣的距離（找不到鄰格就用字高估）。
        cell = find_text(doc, mark["column"])
        top = find_text(doc, mark["top"]) if mark.get("top") else cell
        height = cell[3] - cell[1]
        gaps = [abs(s[2] - cell[2]) for s in doc.line_segments(cell)]
        gaps = [g for g in gaps if g > height * 0.5]
        pitch = min(gaps) if gaps else height * 4
        x0 = min(cell[0] - height * 0.3, cell[2] - pitch * 0.93)
        band = (x0, top[3] + height * 0.3, cell[2] + pitch * 0.03, cell[3] + height * 0.35)
        return band, "column"
    if "text" in mark:
        x0, y0, x1, y1 = find_text(doc, mark)
        h = y1 - y0
        return (x0 - h * 0.3, y0 - h * 0.25, x1 + h * 0.3, y1 + h * 0.25), "text"
    raise DocError(f"看不懂的螢光筆範圍：{dict(mark)}（要有 text、column 或 rect）")


# ── 說明檔 → 元件變數 ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Layout:
    image: Image.Image
    marks: dict[str, tuple[float, float, float, float, str]]  # 名稱 → 圖上 x, y, w, h, 種類
    steps: list[tuple[float, list[str]]]  # （秒數, 這一步劃的名稱）


def sources_dir(episode_dir: Path) -> Path:
    return Path(episode_dir) / SOURCES_DIR


def spec_path(episode_dir: Path, row_id: str) -> Path:
    return sources_dir(episode_dir) / f"{row_id}.json"


def load_spec(episode_dir: Path, row_id: str) -> dict[str, Any]:
    path = spec_path(episode_dir, row_id)
    if not path.is_file():
        raise DocError(
            f"{row_id}: 還沒有文件說明檔 {path}（DP 要先寫，格式見 skill youtube-doc-highlight）"
        )
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not spec.get("document") or not spec.get("steps"):
        raise DocError(f"{row_id}: 說明檔要有 document 與 steps")
    return spec


def source_text(row: Mapping[str, Any]) -> str:
    text = (row.get("source") or "").strip()
    if not text:
        raise DocError(f"{row['id']}: 「出處」是空的——畫面左下要印資料來源")
    if "\n" in text:
        raise DocError(f"{row['id']}: 出處只能一行")
    if text.lower().startswith(("http://", "https://")):
        raise DocError(
            f"{row['id']}: 出處會印在畫面上，請寫機構／報告名稱；網址寫進「給 DP 的指示」"
        )
    units = sum(1.0 if unicodedata.east_asian_width(c) in "WF" else 0.55 for c in text)
    if units > MAX_SOURCE_UNITS:
        raise DocError(f"{row['id']}: 出處太長，請縮短（約 60 個全形字以內）")
    return text.removeprefix("資料來源：").removeprefix("資料來源:")


def build_layout(
    episode_dir: Path, plan: Mapping[str, Any], row: Mapping[str, Any], seconds: float
) -> Layout:
    spec = load_spec(episode_dir, row["id"])
    doc = open_document(sources_dir(episode_dir) / spec["document"], spec.get("page"))
    cues = {cue["id"]: cue for cue in vp.cue_range(plan, row["start_cue"], row["end_cue"])}
    row_start = float(cues[row["start_cue"]]["start"])

    names: dict[str, str] = {}
    boxes: dict[str, tuple[Box, str]] = {}
    steps: list[tuple[float, list[str]]] = []
    for i, step in enumerate(spec["steps"], start=1):
        cue = cues.get(step.get("cue"))
        if cue is None:
            raise DocError(
                f"{row['id']}: 第 {i} 步的字幕 {step.get('cue')!r} 不在這一列蓋住的範圍裡"
            )
        at = round(max(FIRST_AT, float(cue["start"]) - row_start), 3)
        if steps and at <= steps[-1][0]:
            raise DocError(f"{row['id']}: 第 {i} 步要排在第 {i - 1} 步之後（字幕要往後）")
        if at > seconds - LAST_HOLD:
            raise DocError(f"{row['id']}: 第 {i} 步在 {at:.1f} 秒才劃，整張只停 {seconds:.1f} 秒")
        step_names = []
        for mark in step.get("marks") or []:
            key = json.dumps(mark, ensure_ascii=False, sort_keys=True)
            if key not in names:
                names[key] = f"m{len(names) + 1}"
                try:
                    boxes[names[key]] = resolve_mark(doc, mark)
                except DocError as exc:
                    raise DocError(f"{row['id']}: 第 {i} 步：{exc}") from exc
            step_names.append(names[key])
        steps.append((at, step_names))

    if spec.get("crop"):
        crop = tuple(float(v) for v in spec["crop"])
    elif boxes:
        xs0, ys0, xs1, ys1 = zip(*(b for b, _ in boxes.values()), strict=True)
        union = (min(xs0), min(ys0), max(xs1), max(ys1))
        margin = max(0.04 * (union[2] - union[0]), 0.02 * doc.width)
        crop = (union[0] - margin, union[1] - margin, union[2] + margin, union[3] + margin)
    else:
        crop = (0.0, 0.0, doc.width, doc.height)
    crop = (max(0.0, crop[0]), max(0.0, crop[1]), min(doc.width, crop[2]), min(doc.height, crop[3]))
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        raise DocError(f"{row['id']}: crop {crop} 不在文件範圍內")
    scale = TARGET_PX / (crop[2] - crop[0])
    scale = min(6.0, max(1.5, scale)) if isinstance(doc, PdfDocument) else min(1.0, scale)
    image = doc.render(crop, scale)
    sx = image.width / (crop[2] - crop[0])
    sy = image.height / (crop[3] - crop[1])
    marks = {
        name: (
            round((b[0] - crop[0]) * sx, 1),
            round((b[1] - crop[1]) * sy, 1),
            round((b[2] - b[0]) * sx, 1),
            round((b[3] - b[1]) * sy, 1),
            kind,
        )
        for name, (b, kind) in boxes.items()
    }
    return Layout(image=image, marks=marks, steps=steps)


def check_image(layout: Layout) -> Image.Image:
    """每一步框在哪：紅框＋步驟編號，給 DP 確認沒框錯。"""
    image = layout.image.copy()
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(18, image.width // 60))
    first_step = {}
    for i, (_, names) in enumerate(layout.steps, start=1):
        for name in names:
            first_step.setdefault(name, i)
    for name, (x, y, w, h, kind) in layout.marks.items():
        color = (0, 120, 255) if kind == "column" else (230, 0, 0)
        draw.rectangle((x, y, x + w, y + h), outline=color, width=4)
        draw.text((x + 6, y + 2), str(first_step.get(name, "?")), fill=color, font=font)
    return image


def _data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def card_variables(
    episode_dir: Path, plan: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[dict, float, float, Layout]:
    if row.get("category") != CATEGORY:
        raise DocError(f"{row.get('id')}: 不是論文／文件 highlighter")
    start, span = delivery.overlay_window(plan, row)
    seconds = round(span, 3)
    if seconds < MIN_SEC:
        raise DocError(f"{row['id']}: 只蓋住 {seconds:.1f} 秒，文件至少要 {MIN_SEC:g} 秒")
    if seconds > MAX_SEC:
        raise DocError(f"{row['id']}: 蓋住 {seconds:.1f} 秒，上限 {MAX_SEC:g} 秒，請拆成兩張")
    source = source_text(row)
    layout = build_layout(episode_dir, plan, row, seconds)
    variables = {
        "img": _data_uri(layout.image),
        "doc_w": layout.image.width,
        "doc_h": layout.image.height,
        "marks": json.dumps(
            {k: list(v) for k, v in layout.marks.items()}, ensure_ascii=False, sort_keys=True
        ),
        "steps": json.dumps(layout.steps, ensure_ascii=False),
        "source": source,
        "show_sec": seconds,
    }
    return variables, start, seconds, layout


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
    variables: Mapping[str, Any], seconds: float, paths: delivery.AssetPaths, runner: Runner
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
                "compositions/doc_highlight.html",
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
        # 元件固定渲 60 秒；交件長度＝停留秒數。ProRes 每格都是關鍵格，copy 截斷是逐格精準的。
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


def receipt_variables(variables: Mapping[str, Any]) -> dict[str, Any]:
    """收據不存整張圖（太大）；圖的內容已經算進 inputs_sha。"""
    slim = dict(variables)
    slim["img"] = f"<{len(variables['img'])} chars data URI>"
    return slim


def write_check(episode_dir: Path, row_id: str, layout: Layout) -> Path:
    path = sources_dir(episode_dir) / f"{row_id}.check.png"
    check_image(layout).save(path)
    return path


def deliver_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    force: bool = False,
    runner: Runner = _hyperframes_render,
) -> tuple[dict[str, Any], bool]:
    """渲一列並寫收據。回傳（收據, 這次有沒有真的渲）。"""
    variables, start, seconds, _ = card_variables(episode_dir, plan, row)
    component_sha = delivery.file_sha(COMPOSITION_HTML)
    sha = delivery.inputs_sha(
        category=CATEGORY, component=COMPONENT, component_sha=component_sha, variables=variables
    )
    paths = delivery.asset_paths(episode_dir, row["id"])
    if not force and delivery.is_current(paths, sha):
        return delivery.load_receipt(paths.receipt) or {}, False
    _render(variables, seconds, paths, runner)
    receipt = delivery.write_receipt(
        paths,
        row=row,
        component=COMPONENT,
        component_sha=component_sha,
        variables=receipt_variables(variables),
        start_sec=start,
        duration_sec=seconds,
        sha=sha,
    )
    return receipt, True


def approved_rows(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in plan.get("rows", [])
        if row.get("category") == CATEGORY and row.get("status") == "approved"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲論文／文件 highlighter（第 7 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只做這一列")
    parser.add_argument("--check", action="store_true", help="只產框線檢查圖，不渲")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到核准的文件 highlighter：{args.row}", file=sys.stderr)
            return 2
    failures = 0
    for row in rows:
        try:
            if args.check:
                _, _, seconds, layout = card_variables(args.episode, plan, row)
                path = write_check(args.episode, row["id"], layout)
                times = "、".join(f"{t:.1f}s" for t, _ in layout.steps)
                print(
                    f"檢查圖 {row['id']}：{path}"
                    f"（{len(layout.steps)} 步：{times}；停 {seconds:.1f}s）"
                )
                continue
            receipt, rendered = deliver_row(args.episode, plan, row, force=args.force)
        except delivery.DeliveryError as exc:
            failures += 1
            print(f"✗ {exc}", file=sys.stderr)
            continue
        label = "渲好" if rendered else "沿用"
        steps = len(json.loads(receipt["variables"]["steps"]))
        print(
            f"{label} {row['id']}：{steps} 步"
            f" {receipt['start_sec']:.2f}s 起 {receipt['duration_sec']:.2f}s"
        )
    done = len(rows) - failures
    print(f"文件 highlighter：{done}/{len(rows)} 張{'檢查圖' if args.check else ''}完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
