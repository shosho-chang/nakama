"""Quick QC for Qwen3-ASR SRT — extract plain text + check key terms vs WhisperX baseline."""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def srt_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    chars = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) >= 3:
            chars.append(lines[2])
    return "".join(chars)


def find_ctx(plain: str, kw: str, before: int = 15, after: int = 15) -> list[str]:
    out, idx = [], 0
    while True:
        i = plain.find(kw, idx)
        if i == -1:
            break
        out.append(plain[max(0, i - before) : i + len(kw) + after])
        idx = i + 1
    return out


def main() -> None:
    qwen = srt_text(Path("tests/files/out/qwen3-asr/20260415.srt"))
    wxv2 = srt_text(Path("tests/files/out/whisperx-v2/20260415.srt"))

    print(f"Qwen total chars (concat): {len(qwen)}")
    print(f"WhisperX-v2 total chars: {len(wxv2)}")
    print()
    print(f"Qwen first 200: {qwen[:200]}")
    print()
    print(f"WX-v2 first 200: {wxv2[:200]}")
    print()

    keywords = [
        "數位", "数位", "诸位", "諸位",
        "心酸", "辛酸",
        "Paul", "保羅", "保罗",
        "Traveling", "Travel", "Village", "village",
        "Hell", "hell",
        "花蓮", "花莲", "花连",
        "本尊", "本本尊",
        "可以喔", "可以齁", "Hello",
    ]
    print(f"{'keyword':<15}{'Qwen':>6}{'WX-v2':>8}  ctx")
    print("-" * 80)
    for kw in keywords:
        nq, nw = qwen.count(kw), wxv2.count(kw)
        ctxq = find_ctx(qwen, kw)[:1]
        ctxw = find_ctx(wxv2, kw)[:1]
        ctx = (ctxq[0] if ctxq else "") + " | " + (ctxw[0] if ctxw else "")
        print(f"{kw:<15}{nq:>6}{nw:>8}  {ctx}")


if __name__ == "__main__":
    main()
