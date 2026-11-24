# 量音效實際響起的秒數（拿來對動作）：濾掉 40Hz 以下的雜訊後找「靜音結束」的時間點
#   python -I tools/sfx_onsets.py <渲好的頁面.mp4>
#   對照頁面檔裡動作的秒數，差距要在一格（30fps＝0.033 秒）上下。
#   會多出不是音效開頭的點：片尾（頁面結束），以及音效被 data-duration 切掉的地方——音效檔尾巴不是真的靜音時
#   （例：mario_kick.wav 撞擊後有約 -25dB 的尾巴），切點是一個台階，會被當成新的聲音。
#   對不上任何動作的點，先查是不是某個音效的「data-start＋data-duration」。音效檔開頭有空白的，data-media-start 要跳過（字典 note 有寫幾秒）。
import sys, re, subprocess
sys.stdout.reconfigure(encoding="utf-8")   # Windows 主控台預設 cp1252，印中文會炸

r = subprocess.run(["ffmpeg", "-v", "info", "-i", sys.argv[1], "-af", "highpass=f=40,silencedetect=n=-40dB:d=0.15",
                    "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
print(" ".join(f"{float(x):.3f}" for x in re.findall(r"silence_end: ([0-9.]+)", r.stderr)))
