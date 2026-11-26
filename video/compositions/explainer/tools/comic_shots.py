# 逐格截圖：把漫畫頁在指定的秒數截圖，拼成一張對照表（每張 640×360，左上角標秒數）
#   python -I tools/comic_shots.py compositions/comic_coffee_p1.html <out.png> 0.2 1.5 3.0 …
#   單張全尺寸截圖也會留下來：<out 去掉 .png>_<秒數>.png（要細看某一格時直接開那張）
# 自己起一個本機靜態伺服器（OS 挑空的 port，不佔 8000 等別人的 port），
# 用 headless Chrome 開 ?t=<秒數> 截圖。
#   Chrome 路徑：環境變數 CHROME，預設 C:/Program Files/Google/Chrome/Application/chrome.exe
# 用途：每個動作的前、中、後都截（滑、點頭、跳、轉身、切下一格），看有沒有出格、被擋、看不懂。
import functools
import http.server
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows 主控台預設 cp1252，印中文會炸

ROOT = Path(__file__).resolve().parent.parent  # video/compositions/explainer
CHROME = os.environ.get("CHROME", "C:/Program Files/Google/Chrome/Application/chrome.exe")
FONT = "C\\:/Windows/Fonts/arial.ttf"


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path):
        p = path.split("?")[0]
        if p.startswith(
            "/compositions/assets/"
        ):  # 頁面在 compositions/ 底下，用相對路徑 assets/… 指到上一層
            p = p[len("/compositions") :]
        return str(ROOT.joinpath(*[x for x in p.split("/") if x]))


def main():
    comp, out, times = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
    if not times:
        sys.exit("用法：comic_shots.py <頁面.html> <out.png> 秒數 …")
    rel = Path(comp).resolve().relative_to(ROOT).as_posix()
    out.parent.mkdir(
        parents=True, exist_ok=True
    )  # 資料夾不存在時 Chrome 會靜默失敗、最後只看到 ffmpeg 報錯
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT))
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    shots = []
    try:
        for t in times:
            f = out.with_name(f"{out.stem}_{t}.png")
            subprocess.run(
                [
                    CHROME,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--window-size=1920,1080",
                    "--virtual-time-budget=4000",
                    f"--screenshot={f}",
                    f"http://127.0.0.1:{port}/{rel}?t={t}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            shots.append(f)
    finally:
        srv.shutdown()
    if len(shots) == 1:
        out.write_bytes(shots[0].read_bytes())
    else:
        args, fl, lay, cols = [], "", [], 4
        for i, (f, t) in enumerate(zip(shots, times)):
            args += ["-i", str(f)]
            fl += (
                f"[{i}]scale=640:360,drawtext=fontfile='{FONT}':text='{t}':x=8:y=8:"
                f"fontsize=28:fontcolor=red:box=1:boxcolor=white[v{i}];"
            )
            lay.append(f"{(i % cols) * 640}_{(i // cols) * 360}")
        ins = "".join(f"[v{i}]" for i in range(len(shots)))
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                *args,
                "-filter_complex",
                f"{fl}{ins}xstack=inputs={len(shots)}:layout={'|'.join(lay)}:fill=gray",
                str(out),
            ],
            check=True,
        )
    print(out)


if __name__ == "__main__":
    main()
