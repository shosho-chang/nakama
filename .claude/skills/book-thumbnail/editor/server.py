"""書籍介紹影片封面編輯器（本機原型）。

用法：
    python .claude/skills/book-thumbnail/editor/server.py --work-dir "<輸出資料夾>" [--port 8765]

瀏覽器開 http://127.0.0.1:<port>/ 調整；按「存檔並出圖」會：
  1. 把配方寫進 <work-dir>/spec.json
  2. 用 headless Chromium 對 /?render=1（同一份 HTML、拿掉操作介面）截圖成
     <work-dir>/cover.png 與 cover-320.png
編輯畫面與出圖是同一份 DOM，所以拖到哪裡、出圖就在哪裡。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
CHROME = Path(
    os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1223\chrome-win64\chrome.exe")
)
FONT_DIR = Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"))
CUTOUT_DIR = Path("E:/Shosho LifeOS/Attachments/cutouts/shosho")
LOGO = Path("E:/data/podcast thumbnail props/channel_logo_white.png")

DEFAULT_SPEC = {
    "bg": {"src": "", "brightness": 0.40, "grayscale": 0.7, "blur": 1.5, "warm": 0.35},
    "book": {
        "src": "",
        "cx": 905,
        "cy": 352,
        "h": 560,
        "tilt": -12,
        "frame": True,
        "frame_style": "skew",
    },
    "person": {"src": "", "cx": 330, "top": 46, "h": 880, "glow": True, "flip": False},
    "text": {"x": 44, "y": 446, "size": 128, "rot": -6, "lead": "", "main": "", "hl": "", "en": ""},
    "logo": {"on": False, "x": 30, "y": 30, "h": 64},
    "bars": True,
    "title": "",
    "title_rank": None,
    "bigtext_id": None,
}


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = (
            deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
        )
    return out


class Ctx:
    work: Path
    roots: list[Path]
    port: int
    lock = threading.Lock()

    @classmethod
    def spec_path(cls) -> Path:
        return cls.work / "spec.json"

    @classmethod
    def load_spec(cls) -> dict:
        p = cls.spec_path()
        spec = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return deep_merge(DEFAULT_SPEC, spec)


def allowed(path: Path) -> bool:
    rp = path.resolve()
    return any(rp == r or r in rp.parents for r in Ctx.roots)


def lists() -> dict:
    cut = sorted(str(p).replace("\\", "/") for p in CUTOUT_DIR.glob("*/*.png"))
    bg_dir = Ctx.work.parent / "stock"
    bgs = sorted(str(p).replace("\\", "/") for p in bg_dir.glob("*.jpg")) if bg_dir.exists() else []
    return {"cutouts": cut, "backgrounds": bgs}


def packaging(cut_id: str = "full") -> dict:
    """title-brainstorm 的 packages.json ＋ emit_bigtext 的 bigtext.json → 編輯器挑選清單。"""
    pdir = Ctx.work / "packaging"
    titles, options = [], []
    pk = pdir / "packages.json"
    if pk.is_file():
        cut = next(
            (
                c
                for c in json.loads(pk.read_text(encoding="utf-8")).get("cuts", [])
                if c.get("cut_id") == cut_id
            ),
            None,
        )
        if cut:
            titles = [
                {"rank": t["rank"], "text": t["text"], "payoff": t.get("payoff", "")}
                for t in sorted(cut.get("titles", []), key=lambda t: t["rank"])
            ]
    bt = pdir / cut_id / "bigtext.json"
    if bt.is_file():
        options = json.loads(bt.read_text(encoding="utf-8")).get("options", [])
    return {"titles": titles, "options": options}


AB_MAX = 3  # YouTube「測試和比較」一次最多 3 個標題


def tracking_path() -> Path:
    return Ctx.work / "packaging" / "title_tracking.json"


def load_tracking() -> dict:
    """上架標題紀錄：修修微調後的文字、哪幾條進 A/B、哪幾條已放上頻道。

    第一次開啟時由 packages.json 的標題初始化；之後以這份檔案為準（修修的編輯不被
    重新發想覆蓋——新一輪發想的標題只會補進來，不會改掉已存在的列）。"""
    p = tracking_path()
    data = (
        json.loads(p.read_text(encoding="utf-8"))
        if p.is_file()
        else {"video_url": "", "check_after_days": 7, "titles": []}
    )
    have = {t.get("source") for t in data["titles"]}
    for t in packaging()["titles"]:
        src = f"packages#{t['rank']}:{t['text']}"
        if src not in have:
            data["titles"].append(
                {
                    "id": f"t{len(data['titles']) + 1}",
                    "text": t["text"],
                    "original_text": t["text"],
                    "source": src,
                    "ab": False,
                    "published": False,
                    "published_at": None,
                    "edited_at": None,
                }
            )
    return data


def save_tracking(data: dict) -> dict:
    # 「已上頻道」＝有參加 A/B 測試（修修 2026-11-12：不需要兩個勾選），ab 跟著 published 走
    for t in data.get("titles", []):
        t["ab"] = bool(t.get("published"))
    ab = [t for t in data.get("titles", []) if t["ab"]]
    if len(ab) > AB_MAX:
        raise ValueError(f"已上頻道（A/B）最多 {AB_MAX} 個標題，目前勾了 {len(ab)} 個")
    now = datetime.now(timezone.utc).isoformat()
    prev_data = load_tracking()
    # 開著的舊頁面送來空網址時，不蓋掉已記錄的影片網址（2026-11-12：網址由 API 補登後，
    # 修修那頁的自動存檔差點把它清空）
    if not (data.get("video_url") or "").strip() and prev_data.get("video_url"):
        data["video_url"] = prev_data["video_url"]
        data["video_id"] = prev_data.get("video_id")
    old = {t["id"]: t for t in prev_data["titles"]}
    for t in data["titles"]:
        t["text"] = (t.get("text") or "").strip()
        prev = old.get(t["id"], {})
        if t["text"] != prev.get("text"):
            t["edited_at"] = now
        if t.get("published") and not t.get("published_at"):
            t["published_at"] = now
        if not t.get("published"):
            t["published_at"] = None
    data["updated_at"] = now
    tracking_path().parent.mkdir(parents=True, exist_ok=True)
    tracking_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 上架用：A/B 的標題一行一條（只有一條時就是唯一標題）
    (Ctx.work / "title.txt").write_text("".join(t["text"] + "\n" for t in ab), encoding="utf-8")
    return {"ok": True, "saved_at": now, "titles": data["titles"]}


def render() -> dict:
    out = Ctx.work / "cover.png"
    url = f"http://127.0.0.1:{Ctx.port}/?render=1"
    with Ctx.lock:
        subprocess.run(
            [
                str(CHROME),
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                "--window-size=1280,720",
                "--virtual-time-budget=4000",
                f"--screenshot={out}",
                url,
            ],
            check=True,
            capture_output=True,
            timeout=90,
        )
        from PIL import Image

        im = Image.open(out).convert("RGB")
        im.save(out)
        im.resize((320, 180), Image.LANCZOS).save(Ctx.work / "cover-320.png")
    return {"png": str(out), "small": str(Ctx.work / "cover-320.png")}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            html = (HERE / "editor.html").read_text(encoding="utf-8")
            boot = {
                "spec": Ctx.load_spec(),
                "lists": lists(),
                "packaging": packaging(),
                "tracking": load_tracking(),
                "ab_max": AB_MAX,
                "render": q.get("render") == ["1"],
            }
            html = html.replace("/*__BOOT__*/null", json.dumps(boot, ensure_ascii=False))
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if u.path == "/asset":
            p = Path(q.get("p", [""])[0])
            if not p.is_file() or not allowed(p):
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return self._send(200, p.read_bytes(), ctype)
        if u.path == "/thumb":
            # 去背原檔 2–3k px，挑選格只需要小圖；縮圖快取在 work-dir/_thumbs
            p = Path(q.get("p", [""])[0])
            if not p.is_file() or not allowed(p):
                return self._send(404, b"not found", "text/plain")
            import hashlib

            from PIL import Image

            cache = Ctx.work / "_thumbs"
            cache.mkdir(exist_ok=True)
            key = hashlib.sha1(f"{p}|{p.stat().st_mtime}".encode()).hexdigest() + ".png"
            out = cache / key
            if not out.exists():
                im = Image.open(p)
                im.thumbnail((320, 320))
                im.save(out)
            return self._send(200, out.read_bytes(), "image/png")
        if u.path in ("/cover.png", "/cover-320.png"):
            p = Ctx.work / u.path.lstrip("/")
            if p.exists():
                return self._send(200, p.read_bytes(), "image/png")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        if self.path == "/tracking":
            try:
                res = save_tracking(body)
            except ValueError as e:
                return self._send(
                    400,
                    json.dumps({"error": str(e)}, ensure_ascii=False).encode(),
                    "application/json",
                )
            return self._send(200, json.dumps(res, ensure_ascii=False).encode(), "application/json")
        if self.path != "/save":
            return self._send(404, b"", "text/plain")
        # 標題與封面分開（修修 2026-11-12）：/save 只管封面，title.txt 由 /tracking 維護
        spec = deep_merge(DEFAULT_SPEC, body)
        Ctx.spec_path().write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            res = render()
        except Exception as e:  # 出圖失敗要讓前端看得到，不吞掉
            return self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        return self._send(200, json.dumps(res, ensure_ascii=False).encode(), "application/json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True, type=Path)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    Ctx.work = a.work_dir.resolve()
    Ctx.work.mkdir(parents=True, exist_ok=True)
    Ctx.port = a.port
    Ctx.roots = [
        p.resolve()
        for p in (CUTOUT_DIR, LOGO.parent, FONT_DIR, Ctx.work.parent, Path("E:/nakama/data/books"))
    ]
    print(f"book cover editor → http://127.0.0.1:{a.port}/  (work: {Ctx.work})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
