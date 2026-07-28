"""transcript-punctuate：逐字稿的語意標點 pass（subagent 改標點，程式把關）。

``run_transcript_prose.py`` 的標點全部由**實測停頓**推導，那有天花板：有些
句子只有懂意思才斷得對。實例（修修 2026-07-28 兩輪 review 都抓到同一句）：

    講者實際說的：  你反芻了過去〔換氣〕好的事情我們要延續 不好的事情我們要作為警惕
    照停頓斷：      你反芻了過去好的事情，我們要延續不好的事情，我們要作為警惕
                                                    ^^^^^^^^^^^^^^ 意思整個相反
    照語意斷：      你反芻了過去，好的事情我們要延續，不好的事情我們要作為警惕

所以加這一層。**風險是它可能順手改字**——逐字稿一旦被改字就不是逐字稿了，
所以這裡的重點不是 prompt 寫得多好，而是 ``apply`` 的兩道機械驗證：

1. **字元同一性**：把標點與空白全部拿掉之後，改寫版必須與原版**逐字相同**。
   多一個字、少一個字、換一個同義詞，整塊退回。
2. **講者序列同一性**：允許把一個人的長段落再切成幾段（可讀性），但把文字
   搬給另一位講者、或動到問答順序，整塊退回。

驗證不過的 chunk **不會**被寫進去，也不會靜默跳過——``apply`` 會把它列出來
要求重做。零 API 費用（走 Cowork subagent，不打 API）。

流程：
    python scripts/run_transcript_punctuate.py emit  <episode>
        → <episode>/punct_work/NNN.md（每塊約 2500 字，切在段落邊界）
    〔skill 派 subagent 逐塊改標點，寫成 NNN.done.md〕
    python scripts/run_transcript_punctuate.py apply <episode>
        → 驗證 → 覆寫 transcript_prose.md（原版備份為 .pre_punct.md）
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("transcript_punctuate")

PROSE_NAME = "transcript_prose.md"
BACKUP_NAME = "transcript_prose.pre_punct.md"
WORK_DIR = "punct_work"
CHUNK_CHARS = 2500

_PARA_RE = re.compile(r"^\*\*(?P<speaker>.+?)\*\*：(?P<text>.*)$", re.S)
_STRIP_RE = re.compile(r"[\s，。？！、；：…「」『』（）()《》〈〉·,.?!:;\-—]")


def parse_prose(md: str) -> list[tuple[str, str]]:
    """Markdown → [(講者, 段落文字)]；非段落行（引言警語等）忽略。"""
    out: list[tuple[str, str]] = []
    for block in md.split("\n\n"):
        block = block.strip()
        m = _PARA_RE.match(block)
        if m:
            out.append((m.group("speaker").strip(), m.group("text").strip()))
    return out


def render_prose(paragraphs: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"**{spk}**：{text}" for spk, text in paragraphs)


def _bare(paragraphs: list[tuple[str, str]]) -> str:
    """全部段落串成一條沒有標點的字元流——字元同一性就比這個。"""
    return _STRIP_RE.sub("", "".join(text for _spk, text in paragraphs))


def _turns(paragraphs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """相鄰同講者的段落併成一個 turn → [(講者, 去標點的文字)]。

    比「整份串起來的字元流」嚴格：文字若在兩位講者的交界處被搬動，字元流
    與講者序列都不會變（自家測試抓到的漏洞），但 turn 的內容會變。
    允許同一人的長段落被切成幾段——那在 turn 這層看起來是一樣的。
    """
    turns: list[tuple[str, str]] = []
    for spk, text in paragraphs:
        bare = _STRIP_RE.sub("", text)
        if turns and turns[-1][0] == spk:
            turns[-1] = (spk, turns[-1][1] + bare)
        else:
            turns.append((spk, bare))
    return turns


def verify(original: list[tuple[str, str]], rewritten: list[tuple[str, str]]) -> str | None:
    """回傳 None 代表通過；否則回傳人看得懂的失敗原因。"""
    if not rewritten:
        return "改寫版沒有任何段落（格式是不是壞了？每段要是 `**講者**：內容`）"
    a, b = _bare(original), _bare(rewritten)
    if a != b:
        import difflib

        diffs = [
            f"原文[{i1}:{i2}]={a[i1:i2]!r} → 改寫={b[j1:j2]!r}"
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes()
            if tag != "equal"
        ]
        return "文字被改動了（只准搬標點）：\n    " + "\n    ".join(diffs[:5])
    ta, tb = _turns(original), _turns(rewritten)
    if [s for s, _ in ta] != [s for s, _ in tb]:
        return "講者序列被改動了（可以把同一人的長段落再切，不可以把話搬給另一個人）"
    for (spk, x), (_, y) in zip(ta, tb):
        if x != y:
            return (
                f"「{spk}」那一段的文字被動過了——文字在講者交界處被搬動，"
                "整體字數雖然一樣但話換人講了"
            )
    return None


def _chunks(paragraphs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    out: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    size = 0
    for para in paragraphs:
        if cur and size + len(para[1]) > CHUNK_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(para)
        size += len(para[1])
    if cur:
        out.append(cur)
    return out


def emit(episode_dir: Path) -> dict:
    prose_path = episode_dir / PROSE_NAME
    if not prose_path.exists():
        raise FileNotFoundError(f"找不到 {prose_path}（先跑 run_transcript_prose.py）")
    paragraphs = parse_prose(prose_path.read_text(encoding="utf-8"))
    work = episode_dir / WORK_DIR
    work.mkdir(exist_ok=True)
    chunks = _chunks(paragraphs)
    for i, chunk in enumerate(chunks, start=1):
        (work / f"{i:03d}.md").write_text(
            render_prose(chunk) + "\n", encoding="utf-8", newline="\n"
        )
    return {
        "status": "emitted",
        "paragraphs": len(paragraphs),
        "chunks": len(chunks),
        "work_dir": str(work),
        "next": f"派 subagent 逐塊改標點 → {work}/NNN.done.md，再跑 apply",
    }


def apply(
    episode_dir: Path,
    *,
    dry_run: bool = False,
    vault: tuple[str, str, str | None] | None = None,
) -> dict:
    prose_path = episode_dir / PROSE_NAME
    work = episode_dir / WORK_DIR
    if not work.is_dir():
        raise FileNotFoundError(f"找不到 {work}（先跑 emit）")

    merged: list[tuple[str, str]] = []
    failures: list[dict] = []
    unchanged: list[str] = []
    for src in sorted(work.glob("[0-9][0-9][0-9].md")):
        original = parse_prose(src.read_text(encoding="utf-8"))
        done = src.with_suffix("").with_suffix(".done.md")
        done = src.parent / f"{src.stem}.done.md"
        if not done.exists():
            unchanged.append(src.name)
            merged.extend(original)  # 沒改的照原樣留著，不是錯誤
            continue
        rewritten = parse_prose(done.read_text(encoding="utf-8"))
        reason = verify(original, rewritten)
        if reason:
            failures.append({"chunk": done.name, "reason": reason})
            merged.extend(original)  # 驗證不過就用原版，絕不寫進可疑內容
        else:
            merged.extend(rewritten)

    body = render_prose(merged)
    vault_rel: str | None = None
    if not dry_run and not failures:
        # replace 不是 rename：Windows 上 rename 遇到既有備份會炸，POSIX 則
        # 靜默覆蓋——兩邊行為不一致的 API 不要用。重跑 apply 時備份就是「這次
        # 套用之前」的版本，覆蓋是對的。
        prose_path.replace(episode_dir / BACKUP_NAME)
        prose_path.write_text(body + "\n", encoding="utf-8", newline="\n")
        if vault:
            # vault 那份是同一份稿子的副本——不同步就會停在標點修好前的版本
            from run_transcript_prose import _write_vault

            host, guest, slug = vault
            vault_rel = _write_vault(
                body, episode_dir=episode_dir, host=host, guest=guest, slug=slug
            )

    return {
        "status": "dry-run" if dry_run else ("rejected" if failures else "applied"),
        "chunks_applied": len(list(work.glob("*.done.md"))) - len(failures),
        "chunks_missing": unchanged,
        "failures": failures,
        "paragraphs": len(merged),
        "vault": vault_rel,
        "backup": None if (dry_run or failures) else str(episode_dir / BACKUP_NAME),
        "note": "有 chunk 沒過驗證 → 整批不寫檔，修好那幾塊再跑一次" if failures else None,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="逐字稿語意標點 pass")
    parser.add_argument("command", choices=("emit", "apply"))
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--dry-run", action="store_true", help="apply 只驗證不寫檔")
    parser.add_argument("--host", default="張修修", help="apply 同步 vault 用")
    parser.add_argument(
        "--guest", default=None, help="apply 要同步 vault 就給（不給則只寫 episode 內）"
    )
    parser.add_argument("--slug", default=None, help="vault 檔名（預設 = 資料夾名）")
    args = parser.parse_args(argv)

    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    result = (
        emit(episode_dir)
        if args.command == "emit"
        else apply(
            episode_dir,
            dry_run=args.dry_run,
            vault=(args.host, args.guest, args.slug) if args.guest else None,
        )
    )
    import json

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("failures") else 0


if __name__ == "__main__":
    sys.exit(main())
