# 漫畫預覽：把渲好的漫畫頁接起來，蓋在成品影片那一段的原聲＋字幕上——跟上片後長得一樣，
# 才看得出字幕有沒有壓到重點、聲音和畫面對不對得上。
#   python -I tools/comic_preview.py --video <成品.mp4> --srt <成品.srt> --start <秒> --out <預覽.mp4> p1.mp4:25.793 p2.mp4:14.214
#   每一頁寫「檔案:長度」（長度＝頁面 data-duration 以前、下一頁開始那一刻；就是頁面檔 T.end）
#   --start＝第一頁 0 秒在成品影片的秒數（頁面檔開頭註解有寫）
#   --fps＝成品影片的影格率（預設 30000/1001；舊的 23.976 影片給 24000/1001），跟漫畫頁渲染的 --fps 一樣
# 混音：成品原聲＋漫畫頁裡的音效，normalize=0（不壓小原聲）再過 limiter。字幕用微軟正黑體，跟成品同位置。
import sys, re, argparse, subprocess, tempfile, shutil
sys.stdout.reconfigure(encoding="utf-8")   # Windows 主控台預設 cp1252，印中文會炸
from pathlib import Path

STYLE = ("Fontname=Microsoft JhengHei,Fontsize=22,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
         "BorderStyle=1,Outline=3,Shadow=0,MarginV=24")


def ts(s):
    h, m, r = s.strip().split(":")
    sec, ms = r.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def fm(x):
    x = max(0.0, x)
    h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def cut_srt(src, t0, dur, dst):
    blocks, out, n = re.split(r"\n\s*\n", Path(src).read_text(encoding="utf-8-sig").strip()), [], 1
    for b in blocks:
        L = b.split("\n")
        if len(L) < 3 or "-->" not in L[1]:
            continue
        a, e = (ts(x) for x in L[1].split("-->"))
        if e <= t0 or a >= t0 + dur:
            continue
        out.append(f"{n}\n{fm(a - t0)} --> {fm(min(e, t0 + dur) - t0)}\n{' '.join(L[2:])}\n")
        n += 1
    Path(dst).write_text("\n".join(out), encoding="utf-8")
    return n - 1


def ff(*a, cwd=None):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True, cwd=cwd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--srt", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", default="30000/1001", help="成品影片的影格率（舊的 23.976 影片給 24000/1001）")
    ap.add_argument("pages", nargs="+")
    o = ap.parse_args()
    pages = [(Path(p.rsplit(":", 1)[0]).resolve(), float(p.rsplit(":", 1)[1])) for p in o.pages]
    total = sum(d for _, d in pages)
    with tempfile.TemporaryDirectory() as tmp:
        ff("-ss", str(o.start), "-t", f"{total:.3f}", "-i", o.video, "-vf", "scale=1920:1080", "-r", o.fps,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "src.mp4", cwd=tmp)
        cues = cut_srt(o.srt, o.start, total, Path(tmp) / "seg.srt")
        args, fl = [], ""
        for i, (p, d) in enumerate(pages):
            args += ["-i", str(p)]
            fl += f"[{i}:v]trim=0:{d},setpts=PTS-STARTPTS[v{i}];[{i}:a]atrim=0:{d},asetpts=PTS-STARTPTS[a{i}];"
        ins = "".join(f"[v{i}][a{i}]" for i in range(len(pages)))
        ff(*args, "-filter_complex", f"{fl}{ins}concat=n={len(pages)}:v=1:a=1[cv][ca];[cv]fps={o.fps}[v]", "-map", "[v]", "-map", "[ca]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "pcm_s16le", "comic.mov", cwd=tmp)
        ff("-i", "comic.mov", "-i", "src.mp4", "-filter_complex",
           f"[0:v]subtitles=seg.srt:force_style='{STYLE}'[v];[1:a][0:a]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.9[a]",
           "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "out.mp4", cwd=tmp)
        shutil.move(str(Path(tmp) / "out.mp4"), o.out)
    print(f"{o.out}（{total:.3f} 秒、字幕 {cues} 句）")


if __name__ == "__main__":
    main()
