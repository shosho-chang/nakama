"""Generic 「URL + 要 highlight 的文字段落 → B-roll mp4」生成器。

把 spike v8 的 Reader-specific 邏輯抽出來：
- 不再去吃 foliate-view 內部 API
- 改成：通用 DOM 全文搜尋目標字串 → createRange → getClientRects → 動畫疊上

用法：
    python web_highlight_record.py \
        --url https://example.com/article \
        --text "你要 highlight 的那個段落原文（程式會在 DOM 全文搜)" \
        --caption "可選：出現在底部的標題（預設用 page title）" \
        --out out.mp4

選用：
    --cookie nakama_auth=<token>     # 多個 --cookie 可疊
    --duration 7000                  # 動畫長度 ms（預設 6900）
    --zoom 1.35                      # zoom 終點 scale（預設 1.35）
    --viewport 1920x1080             # 預設 1920x1080

實作摘要：
- 用 TreeWalker 串連所有 text node → 拼成 fullText
- indexOf 找目標 → 算出對應 (startNode/Offset, endNode/Offset)
- Range.getClientRects() 拿每一視覺行 bbox
- 若 range 不在 viewport：先 scrollIntoView 再量
- 套 v8 animation pipeline → CDP screencast → ffmpeg mp4

不支援（先擱）：
- iframe 內文（Reader 是 same-origin iframe，要另外 recurse）
- Shadow DOM
- 文字跨多個 detached subtree
"""

from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

FFMPEG = r"C:\ffmpeg\bin\ffmpeg.exe"

# JS：搜整個 document.body 拼出 fullText + node 邊界表 → indexOf 找目標 → createRange
FIND_TEXT_JS = r"""
({needle, scrollIntoView}) => {
  const norm = s => s.replace(/\s+/g, ' ').trim();
  const target = norm(needle);
  if (!target) return { error: 'empty needle' };

  // 收所有 text node + 它們的字元 offset 在 fullText 中的位置
  const walker = document.createTreeWalker(
    document.body, NodeFilter.SHOW_TEXT,
    { acceptNode: n => {
        // 跳過 script / style / 隱藏 element 的 text node
        const p = n.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        const tag = p.tagName;
        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') return NodeFilter.FILTER_REJECT;
        const cs = getComputedStyle(p);
        if (cs.display === 'none' || cs.visibility === 'hidden') return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    }
  );

  const nodes = [];
  let fullText = '';
  // 我們用「normalised text」做 indexOf，所以也要 normalize 每個 node 的 contribution
  // 用 raw text + 對應 normalized offset map，會太複雜。簡化：直接累積 raw，搜尋 raw。
  // 但要對 needle 也 raw 化（不 normalize）— 比較粗暴但通常 OK。
  let node;
  while ((node = walker.nextNode())) {
    const raw = node.nodeValue || '';
    nodes.push({ node, start: fullText.length, end: fullText.length + raw.length });
    fullText += raw;
  }

  // 先試 raw 直接 indexOf
  let pos = fullText.indexOf(needle);
  let matchLen = needle.length;
  // 找不到再試 normalize 後比對（壓縮多空白）
  if (pos < 0) {
    const normFull = norm(fullText);
    const normPos = normFull.indexOf(target);
    if (normPos < 0) {
      return { error: 'text not found',
               sample: fullText.slice(0, 200),
               searched: target.slice(0, 200) };
    }
    // 回推 raw 位置：normalize 過後位置會 shift，這裡用簡單對應（找 needle 的前 30 字 raw substring 第一個出現位置）
    const prefix = target.slice(0, Math.min(40, target.length));
    pos = fullText.toLowerCase().indexOf(prefix.toLowerCase());
    if (pos < 0) {
      return { error: 'normalized match but raw fallback failed', sample: fullText.slice(0, 200) };
    }
    // 結尾：用 target 後綴去找
    const suffix = target.slice(-Math.min(40, target.length)).toLowerCase();
    const sufPos = fullText.toLowerCase().indexOf(suffix, pos);
    if (sufPos >= 0) matchLen = (sufPos + suffix.length) - pos;
    else matchLen = needle.length;  // fallback
  }

  // 從 nodes table 找 start / end
  const findAt = idx => {
    for (const n of nodes) {
      if (idx >= n.start && idx <= n.end) {
        return { node: n.node, offset: idx - n.start };
      }
    }
    return null;
  };
  const startLoc = findAt(pos);
  const endLoc = findAt(pos + matchLen);
  if (!startLoc || !endLoc) return { error: 'node mapping failed' };

  const range = document.createRange();
  range.setStart(startLoc.node, startLoc.offset);
  range.setEnd(endLoc.node, endLoc.offset);

  if (scrollIntoView) {
    // scroll range 中心到 viewport 中央
    const r = range.getBoundingClientRect();
    const targetY = r.top + r.height / 2;
    const dy = targetY - window.innerHeight / 2;
    if (Math.abs(dy) > 50) {
      window.scrollBy({ top: dy, behavior: 'instant' });
    }
  }

  const rawRects = Array.from(range.getClientRects());
  const VW = window.innerWidth;
  const VH = window.innerHeight;
  const rects = [];
  for (const r of rawRects) {
    if (r.width < 30 || r.height < 12) continue;
    // 只留可見的 rect
    if (r.left < 0 || r.top < 40 || r.left + r.width > VW || r.top + r.height > VH - 40) continue;
    rects.push({ x: r.left, y: r.top, w: r.width, h: r.height });
  }
  if (rects.length === 0) return { error: 'rects empty (text off-viewport even after scroll?)' };
  rects.sort((a, b) => (a.y - b.y) || (a.x - b.x));
  const minX = Math.min(...rects.map(r => r.x));
  const minY = Math.min(...rects.map(r => r.y));
  const maxX = Math.max(...rects.map(r => r.x + r.w));
  const maxY = Math.max(...rects.map(r => r.y + r.h));
  return {
    text: range.toString().slice(0, 200),
    rects,
    bbox: { x: minX, y: minY, w: maxX - minX, h: maxY - minY },
    pageTitle: document.title,
  };
}
"""

# CSS template — v8 結論：橘色 32%、ease-out-expo zoom 到 1.35、will-change 預熱
HIGHLIGHT_ALPHA_EDGE = 0.24
HIGHLIGHT_ALPHA_MID = 0.34

INJECTED_CSS_TMPL = f"""
/* === highlight overlay layer === */
__SHELL_SELECTOR__ {{
  transform-origin: __CX__px __CY__px;
  will-change: transform;
  animation: highlight-kenburns __DURms__ cubic-bezier(0.16, 1, 0.3, 1) 100ms both;
}}
#__hl_vignette {{
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9997;
  background: radial-gradient(
    ellipse 720px 340px at __CX__px __CY__px,
    transparent 0%,
    transparent 42%,
    rgba(15, 12, 8, 0.62) 100%
  );
  animation: highlight-vignette __DURms__ ease-out both;
}}
.__hl_line {{
  position: fixed;
  width: 0;
  background: linear-gradient(
    90deg,
    rgba(233, 137, 101, 0) 0%,
    rgba(233, 137, 101, {HIGHLIGHT_ALPHA_EDGE}) 5%,
    rgba(233, 137, 101, {HIGHLIGHT_ALPHA_MID}) 50%,
    rgba(233, 137, 101, {HIGHLIGHT_ALPHA_EDGE}) 95%,
    rgba(233, 137, 101, 0) 100%
  );
  border-radius: 2px;
  z-index: 9998;
  pointer-events: none;
  animation: highlight-line 850ms cubic-bezier(0.22, 0.61, 0.36, 1) both;
}}
#__hl_caption {{
  position: fixed;
  bottom: 7%;
  left: 50%;
  transform: translateX(-50%);
  color: #faf7f2;
  font-family: "LINE Seed TW", "Noto Sans TC", system-ui, sans-serif;
  font-size: 28px;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-align: center;
  text-shadow: 0 2px 18px rgba(0, 0, 0, 0.9), 0 0 6px rgba(0, 0, 0, 0.6);
  z-index: 10000;
  opacity: 0;
  white-space: nowrap;
  max-width: 80vw;
  overflow: hidden;
  text-overflow: ellipsis;
  animation: highlight-caption calc(__DURms__ * 0.8) ease-out 1400ms both;
}}
@keyframes highlight-kenburns {{
  0%   {{ transform: scale(1.00); }}
  100% {{ transform: scale(__ZOOM__); }}
}}
@keyframes highlight-vignette {{
  0%   {{ opacity: 0; }}
  25%  {{ opacity: 1; }}
  85%  {{ opacity: 1; }}
  100% {{ opacity: 0.78; }}
}}
@keyframes highlight-line {{
  0%   {{ width: 0; opacity: 0; }}
  20%  {{ opacity: 1; }}
  100% {{ width: var(--__lw); opacity: 1; }}
}}
@keyframes highlight-caption {{
  0%   {{ opacity: 0; transform: translate(-50%, 14px); }}
  10%  {{ opacity: 1; transform: translate(-50%, 0); }}
  90%  {{ opacity: 1; transform: translate(-50%, 0); }}
  100% {{ opacity: 0; transform: translate(-50%, -6px); }}
}}
"""


def parse_cookie(s: str) -> dict:
    """name=value → cookie dict for context.add_cookies."""
    if "=" not in s:
        raise ValueError(f"bad cookie format: {s!r}")
    name, _, value = s.partition("=")
    return {"name": name, "value": value}


def parse_viewport(s: str) -> tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


def record(
    url: str,
    text: str,
    caption: str | None,
    out_mp4: Path,
    cookies: list[dict] | None = None,
    duration_ms: int = 6900,
    zoom: float = 1.35,
    viewport: tuple[int, int] = (1920, 1080),
    shell_selector: str = "body",
) -> int:
    """主流程。回傳 exit code。"""
    vw, vh = viewport
    frames_dir = out_mp4.parent / (out_mp4.stem + "_frames")
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    frames_captured: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": vw, "height": vh},
            ignore_https_errors=True,
        )
        if cookies:
            # cookies 需要 domain/url 才能 add — 從 url 推
            from urllib.parse import urlparse

            host = urlparse(url).hostname
            for c in cookies:
                c.setdefault("domain", host)
                c.setdefault("path", "/")
            context.add_cookies(cookies)

        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)  # 給 lazy JS / 字型 / 圖片 settle

        pick = page.evaluate(FIND_TEXT_JS, {"needle": text, "scrollIntoView": True})
        if pick.get("error"):
            print(f"ERROR: {pick['error']}", file=sys.stderr)
            if "sample" in pick:
                print(f"  page sample: {pick['sample']!r}", file=sys.stderr)
                print(f"  searched   : {pick.get('searched', '')!r}", file=sys.stderr)
            return 2
        # scroll 後可能要再量一次 — but evaluate already returns post-scroll rects
        page.wait_for_timeout(300)

        print(f"matched text: {pick['text'][:120]!r}")
        print(f"  lines: {len(pick['rects'])}")
        for i, r in enumerate(pick["rects"]):
            print(f"    line {i + 1}: x={r['x']:.0f} y={r['y']:.0f} w={r['w']:.0f} h={r['h']:.0f}")
        print(f"  bbox: {pick['bbox']}")
        print(f"  page title: {pick['pageTitle']!r}")

        cap_text = caption or pick["pageTitle"] or url

        bbox = pick["bbox"]
        cx = bbox["x"] + bbox["w"] / 2
        cy = bbox["y"] + bbox["h"] / 2

        css = (
            INJECTED_CSS_TMPL.replace("__SHELL_SELECTOR__", shell_selector)
            .replace("__CX__", f"{cx:.1f}")
            .replace("__CY__", f"{cy:.1f}")
            .replace("__DURms__", str(duration_ms))
            .replace("__ZOOM__", f"{zoom:.3f}")
        )

        # 預熱 compositing layer
        page.evaluate(
            """({sel, cx, cy}) => {
                const el = document.querySelector(sel);
                if (el) {
                    el.style.transformOrigin = cx + 'px ' + cy + 'px';
                    el.style.willChange = 'transform';
                    el.style.transform = 'scale(1.0)';
                }
            }""",
            {"sel": shell_selector, "cx": cx, "cy": cy},
        )
        page.wait_for_timeout(250)

        client = context.new_cdp_session(page)

        def on_frame(params):
            frames_captured.append({"data": params["data"], "ts": time.time()})
            try:
                client.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:
                pass

        client.on("Page.screencastFrame", on_frame)

        page.evaluate(
            """({css, rects, caption}) => {
                const style = document.createElement('style');
                style.id = '__hl_style';
                style.textContent = css;
                document.head.appendChild(style);

                const v = document.createElement('div');
                v.id = '__hl_vignette';
                document.body.appendChild(v);

                const STAGGER = 220;
                const BASE_DELAY = 800;
                rects.forEach((r, i) => {
                  const s = document.createElement('div');
                  s.className = '__hl_line';
                  s.style.left = r.x + 'px';
                  s.style.top = r.y + 'px';
                  s.style.height = r.h + 'px';
                  s.style.setProperty('--__lw', r.w + 'px');
                  s.style.animationDelay = (BASE_DELAY + i * STAGGER) + 'ms';
                  document.body.appendChild(s);
                });

                const cap = document.createElement('div');
                cap.id = '__hl_caption';
                cap.textContent = caption;
                document.body.appendChild(cap);
            }""",
            {"css": css, "rects": pick["rects"], "caption": cap_text},
        )

        client.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 92,
                "maxWidth": vw,
                "maxHeight": vh,
                "everyNthFrame": 1,
            },
        )
        page.wait_for_timeout(duration_ms + 300)
        client.send("Page.stopScreencast")
        page.wait_for_timeout(200)

        context.close()
        browser.close()

    print(f"\n抓到 {len(frames_captured)} frames")
    if not frames_captured:
        print("ERROR: no frames", file=sys.stderr)
        return 3

    for i, f in enumerate(frames_captured):
        (frames_dir / f"f_{i:05d}.jpg").write_bytes(base64.b64decode(f["data"]))

    if len(frames_captured) >= 2:
        span = frames_captured[-1]["ts"] - frames_captured[0]["ts"]
        fps = (len(frames_captured) - 1) / span if span > 0 else 30
    else:
        fps = 30
    print(f"capture fps: {fps:.1f}")

    cmd = [
        FFMPEG,
        "-y",
        "-framerate",
        f"{fps:.2f}",
        "-i",
        str(frames_dir / "f_%05d.jpg"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-crf",
        "14",
        "-preset",
        "slow",
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    print("ffmpeg:", " ".join(cmd))
    subprocess.run(cmd, check=True, capture_output=True)

    size_mb = out_mp4.stat().st_size / 1024 / 1024
    print(f"\n最終 mp4: {out_mp4} ({size_mb:.2f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--text", required=True, help="要 highlight 的文字段落（程式會在 DOM 全文搜）")
    ap.add_argument("--caption", default=None, help="底部 caption；預設用 page title")
    ap.add_argument("--out", default="out/highlight.mp4")
    ap.add_argument("--cookie", action="append", default=[], help="name=value，可多次")
    ap.add_argument("--duration", type=int, default=6900)
    ap.add_argument("--zoom", type=float, default=1.35)
    ap.add_argument("--viewport", default="1920x1080")
    ap.add_argument(
        "--shell-selector",
        default="body",
        help="哪個 element 套 zoom（預設 body；Reader 用 .reader-shell）",
    )
    args = ap.parse_args()

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cookies = [parse_cookie(c) for c in args.cookie]
    vw, vh = parse_viewport(args.viewport)

    return record(
        url=args.url,
        text=args.text,
        caption=args.caption,
        out_mp4=out,
        cookies=cookies,
        duration_ms=args.duration,
        zoom=args.zoom,
        viewport=(vw, vh),
        shell_selector=args.shell_selector,
    )


if __name__ == "__main__":
    sys.exit(main())
