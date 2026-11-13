"""字幕前後文審查（podcast-pipeline S7C）。

第二套 ASR ＋ 麥克風說話者 ＋ 前後文審查 → 有把握的修正寫回 live ST1。

為什麼要這一關（2026-11-12，20261111 李惠貞）：節目最後來賓說「好 謝謝學生」——字面通順，
所以 S4 兩份「只看文字」的審核都沒標；S5 只對 major-risk 的 25 項聽音檔。整條線沒有任何一關
拿音檔對全集文字，也沒有說話者資訊。這句是來賓說的、來賓整集都叫主持人「修修」，應為
「好 謝謝修修」。當天臨時補了一套流程，抓到 52 條內容錯誤、清掉 103 句殘留標點；本檔是事後
固定下來的版本。流程見 `.claude/skills/podcast-pipeline/SKILL.md` §S7C。

子命令：
    second-asr   Faster-Whisper large-v3（固定 revision）跑全集 normalized.wav，產字級 timestamps。
                 與 Memo（large-v2）是不同模型——這是第二意見，不是 Memo 的重跑。
    align        live ST1 快照 SRT × 主持／來賓麥克風 × ASR2
                 → review.jsonl ＋ 分段審查檔 review-NN.txt
    apply        審查員 proposals ＋ 主 agent 複核決定 ＋ 全集標點清理 → context-fixes.json →
                 `resolve_subtitle_rebuild.py rebuild --fix-file`（先 --scratch 再正式）
    listen-list  待聽清單（Vault `NN-字幕待聽清單.md`）

前提：live ST1 的 timeline 時間必須等於 normalized.wav／COMBO 的時間（S7 建好的 base
timeline，修修還沒插 Intro、沒剪掉段落）。`align` 會用 ASR2 吻合率擋下時鐘錯位。

純邏輯（說話者判定、標點清理、幀計算、對齊、決定合併）的測試在
tests/scripts/test_subtitle_context_review.py。

用法（PowerShell，repo 根目錄；<work> = <episode>\\subtitle-work\\context-review-v1）：
    E:\\nakama\\.venv-v2\\Scripts\\python.exe scripts\\resolve_subtitle_rebuild.py snapshot `
      --project "<project>" --timeline "<timeline>" --out-dir "<work>"
    E:\\nakama\\.venv-v2\\Scripts\\python.exe scripts\\subtitle_context_review.py second-asr `
      --episode "<episode>" --out-dir "<work>"
    E:\\nakama\\.venv-v2\\Scripts\\python.exe scripts\\subtitle_context_review.py align `
      --episode "<episode>" --srt "<work>\\st1-live.<timeline>.<stamp>.srt" --out-dir "<work>"
    E:\\nakama\\.venv-v2\\Scripts\\python.exe scripts\\subtitle_context_review.py apply `
      --work-dir "<work>" --project "<project>" --timeline "<timeline>" --scratch
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.resolve_subtitle_rebuild import (  # noqa: E402
    parse_fps,
    parse_srt_timestamp,
)
from shared.subtitle_finalize import space_han_latin  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PRECONDITION = 2

# ---- second ASR（2026-11-12 李惠貞實跑設定；revision 是 immutable commit SHA）
ASR2_MODEL = "Systran/faster-whisper-large-v3"
ASR2_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
ASR2_WORDS_NAME = "faster-large-v3.words.json"
ASR2_MANIFEST_NAME = "faster-large-v3.manifest.json"

# ---- align
SAMPLE_RATE = 16000
HOST_TRACK = Path("Audio") / "1_COMBO-1.wav"  # 固定是主持人（SKILL.md Standing authorization）
GUEST_TRACK = Path("Audio") / "2_COMBO-2.wav"  # 固定是來賓
SPEAKER_MARGIN_DB = 8.0
SPEAKER_HOST = "主持"
SPEAKER_GUEST = "來賓"
SPEAKER_BOTH = "兩人"  # 重疊或分不出來
WORD_WINDOW_PAD_SEC = 0.15  # 字的中點落在 cue 前後 0.15s 內就算同時段
SIMILAR_ENOUGH = 0.8  # 低於此值才在審查檔附 ⟂ASR2
DEFAULT_CHUNK_SIZE = 620  # 李惠貞 3659 句 → 6 段 → 6 位平行審查員
# 時鐘錯位保險：ASR2 吻合（sim ≥ 0.8）的句數比例低於此值就中止。李惠貞實測對齊時 0.665，
# 把同一份資料的時鐘偏 2 秒就掉到 0.003、偏 5 秒以上是 0（2026-11-12 用實跑產物驗證）。
MIN_MATCH_SHARE = 0.35
_SIM_STRIP = re.compile(r"[\s，。、！？,.!?~～]")

# ---- apply：字幕不留標點（skill 既有集合 `，。；：？！ ,.;:?!` 再加 `~～…`）。
# 「、」（列舉）與書名號《》〈〉不在集合內，保留。
STRIP_PUNCT = "，。；：？！,.;:?!~～…"
_PUNCT_RUN = re.compile(f"[{re.escape(STRIP_PUNCT)}]+")
# 數字內的 . , :（3.5、1,000、10:30）與英數之間的 .（Node.js、shosho.tw）不是句讀
_PROTECTED = (
    (re.compile(r"(?<=\d)([.,:])(?=\d)"), {".": "\x00", ",": "\x01", ":": "\x02"}),
    (re.compile(r"(?<=[A-Za-z0-9])(\.)(?=[A-Za-z])|(?<=[A-Za-z])(\.)(?=[0-9])"), {".": "\x00"}),
)
_RESTORE = {"\x00": ".", "\x01": ",", "\x02": ":"}

CHUNK_NAME = "review-{:02d}.txt"
PROPOSALS_GLOB = "proposals/proposals-*.json"
DECISIONS_NAME = "decisions.json"
FIXES_NAME = "context-fixes.json"


class ReviewError(Exception):
    """輸入不符預期；呼叫端以 exit 2 結束，Resolve 沒被動過。"""


# ================================================================ pure logic


def cue_frame(timestamp: str, fps: Fraction) -> int:
    """SRT 時間碼 → timeline 幀（round(sec × fps)，精確分數、四捨五入）。

    沿用 resolve_subtitle_rebuild 的同一個函式：rebuild 的 `--fix` 用 live ST1 的
    GetStart() 比對起始幀，兩邊算法不同就會對不上。不用 float 的 round()——
    Python 的 round 是銀行家捨入，0.5 幀會往偶數靠。
    """
    return parse_srt_timestamp(timestamp, fps)


def frame_timecode(frame: int, fps: Fraction) -> str:
    """幀 → 'HH:MM:SS'（無條件捨去到秒，審查檔與待聽清單用）。"""
    seconds = math.floor(Fraction(frame) / fps)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class SrtCue:
    n: int
    start_frame: int
    end_frame: int
    text: str


def parse_live_srt(text: str, fps: Fraction) -> list[SrtCue]:
    """live ST1 快照 SRT → 有編號的 cue。多行文字以單一空白接起來（同 rebuild 快照的單行文字）。"""
    cues: list[SrtCue] = []
    body = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()
    for block in re.split(r"\n[ \t]*\n", body) if body else []:
        lines = block.split("\n")
        if len(lines) < 3 or " --> " not in lines[1] or not lines[0].strip().isdigit():
            raise ReviewError(f"malformed SRT block: {block[:80]!r}")
        start, _, end = lines[1].partition(" --> ")
        cues.append(
            SrtCue(
                n=int(lines[0].strip()),
                start_frame=cue_frame(start, fps),
                end_frame=cue_frame(end, fps),
                text=" ".join(line.strip() for line in lines[2:]).strip(),
            )
        )
    numbers = [cue.n for cue in cues]
    if len(set(numbers)) != len(numbers):
        raise ReviewError("SRT cue numbers are not unique")
    return cues


def rms_db(samples: Sequence[float] | Any) -> float:
    """一段 PCM 的 RMS dB（int16 刻度）。空段回 -99。"""
    count = len(samples)
    if not count:
        return -99.0
    try:
        import numpy as np

        values = np.asarray(samples, dtype=np.float64)
        mean_square = float((values * values).mean())
    except ImportError:  # pragma: no cover - numpy 在正式環境一定有
        mean_square = sum(float(v) * float(v) for v in samples) / count
    return 20 * math.log10(math.sqrt(mean_square) + 1e-9)


def classify_speaker(host_db: float, guest_db: float, margin: float = SPEAKER_MARGIN_DB) -> str:
    """主持麥（COMBO-1）與來賓麥（COMBO-2）音量差 **大於** margin dB 才判給一方，否則「兩人」。

    兩支麥都會收到對方的聲音（串音），所以不能只看哪邊大；8 dB 是李惠貞那集的實用門檻。
    """
    if host_db - guest_db > margin:
        return SPEAKER_HOST
    if guest_db - host_db > margin:
        return SPEAKER_GUEST
    return SPEAKER_BOTH


def similarity_key(text: str) -> str:
    return _SIM_STRIP.sub("", text)


def similarity(live: str, alt: str) -> float:
    key = similarity_key(live)
    if not key:
        return 0.0
    return round(difflib.SequenceMatcher(None, key, similarity_key(alt)).ratio(), 2)


def words_in_span(
    words: Sequence[Mapping[str, Any]],
    start_sec: float,
    end_sec: float,
    pad: float = WORD_WINDOW_PAD_SEC,
) -> str:
    """字的中點落在 [start - pad, end + pad] 的 ASR2 字，依序接起來。"""
    lo, hi = start_sec - pad, end_sec + pad
    return "".join(
        str(word["word"])
        for word in words
        if lo <= (float(word["start"]) + float(word["end"])) / 2 <= hi
    ).strip()


def flatten_words(segments: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for segment in segments:
        out.extend(segment.get("words") or [])
    out.sort(key=lambda word: (float(word["start"]), float(word["end"])))
    return out


def review_line(row: Mapping[str, Any]) -> str:
    """審查檔一行：`編號|時間碼|說話者|字幕`，ASR2 差異大時加 `   ⟂ASR2: …`。"""
    line = f"{row['n']}|{row['tc']}|{row['speaker']}|{row['text']}"
    if row["sim"] < SIMILAR_ENOUGH:
        line += f"   ⟂ASR2: {row['alt'] or '（沒聽到）'}"
    return line


def chunk_rows(rows: Sequence[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        raise ReviewError(f"chunk size must be positive, got {size}")
    return [list(rows[i : i + size]) for i in range(0, len(rows), size)]


def match_share(rows: Sequence[Mapping[str, Any]]) -> float:
    """有文字的 cue 中，ASR2 吻合（sim ≥ 0.8）的比例。時鐘錯位時會掉到很低。"""
    scored = [row for row in rows if similarity_key(str(row["text"]))]
    if not scored:
        return 0.0
    return sum(row["sim"] >= SIMILAR_ENOUGH for row in scored) / len(scored)


def strip_punctuation(text: str) -> str:
    """字幕標點清理：去掉 STRIP_PUNCT（換成空白、合併空白），保留「、」與書名號；
    數字內的 `. , :` 與英數之間的 `.` 保留；最後過 space_han_latin（半形英數與漢字間一個空白）。
    """
    protected = text
    for pattern, marks in _PROTECTED:
        protected = pattern.sub(lambda m: marks[m.group(m.lastindex or 0)], protected)
    out = _PUNCT_RUN.sub(" ", protected)
    for mark, original in _RESTORE.items():
        out = out.replace(mark, original)
    out = re.sub(r"\s+", " ", out).strip()
    return space_han_latin(out)


# ---------------------------------------------------------------- apply logic


@dataclass(frozen=True)
class PlannedFix:
    n: int
    frame: int
    old: str
    new: str
    kind: str  # "content"（審查修正，可能同時清了標點）或 "format"（只清標點／英數空白）
    source: str
    why: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "start": self.frame,
            "old": self.old,
            "new": self.new,
            "kind": self.kind,
            "source": self.source,
            "why": self.why,
        }


@dataclass
class ApplyPlan:
    fixes: list[PlannedFix] = field(default_factory=list)
    needs_ear: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "content": sum(fix.kind == "content" for fix in self.fixes),
            "format": sum(fix.kind == "format" for fix in self.fixes),
            "needs_ear": len(self.needs_ear),
            "skipped": len(self.skipped),
        }


def _require(entry: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in entry:
        raise ReviewError(f"{label}: missing {key!r}")
    return entry[key]


def _as_n(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewError(f"{label}: cue number must be an integer, got {value!r}")
    return value


def collect_proposals(
    proposal_docs: Sequence[tuple[str, Mapping[str, Any]]],
    rows: Mapping[int, Mapping[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """各段 proposals → {n: fix}、needs_ear 清單。

    `old` 必須逐字等於 review.jsonl 那句（審查員照抄錯了就擋，不猜）。兩段對同一句給出
    不同 `new` 是衝突，必須由主 agent 在 decisions 的 `edited` 裁決，這裡先標記。
    """
    fixes: dict[int, dict[str, Any]] = {}
    ears: list[dict[str, Any]] = []
    for name, doc in proposal_docs:
        for raw in doc.get("fixes") or []:
            n = _as_n(_require(raw, "n", name), name)
            label = f"{name} fix {n}"
            if n not in rows:
                raise ReviewError(f"{label}: no such cue in review.jsonl")
            old = str(_require(raw, "old", label)).strip()
            new = str(_require(raw, "new", label)).strip()
            if old != rows[n]["text"]:
                raise ReviewError(f"{label}: old {old!r} != review text {rows[n]['text']!r}")
            entry = {
                "n": n,
                "new": new,
                "confidence": raw.get("confidence", ""),
                "evidence": raw.get("evidence", ""),
                "source": name,
            }
            if n in fixes and fixes[n]["new"] != new:
                fixes[n]["conflict"] = sorted({fixes[n]["new"], new})
                fixes[n]["source"] += f"+{name}"
            elif n not in fixes:
                fixes[n] = entry
        for raw in doc.get("needs_ear") or []:
            n = _as_n(_require(raw, "n", name), name)
            if n not in rows:
                raise ReviewError(f"{name} needs_ear {n}: no such cue in review.jsonl")
            ears.append(
                {
                    "n": n,
                    "why": str(raw.get("why", "")),
                    "maybe": str(raw.get("maybe", "")),
                    "source": name,
                }
            )
    return fixes, ears


def plan_fixes(
    rows: Mapping[int, Mapping[str, Any]],
    proposals: Mapping[int, Mapping[str, Any]],
    reviewer_ears: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, Any],
) -> ApplyPlan:
    """合併審查員 proposals 與主 agent 的複核決定，再對**全集**做標點清理。

    decisions（主 agent 逐條複核後寫的 decisions.json）：
        accepted:  [n, ...]                                 照審查員的 new 上線
        rejected:  [{"n", "why", "needs_ear": bool}, ...]   不改；needs_ear=true 進待聽清單
        edited:    [{"n", "new", "why"}, ...]                主 agent 改寫 new（也用來裁決衝突）
        added:     [{"n", "old", "new", "why"}, ...]         審查員沒提、主 agent 自己抓到的
        needs_ear: [{"n", "why", "maybe"}, ...]              主 agent 自己加的待聽
    每一條 proposal 都必須恰好出現在 accepted／rejected／edited 其中之一——沒複核完不准套。
    """
    accepted = {_as_n(n, "accepted") for n in decisions.get("accepted") or []}
    rejected = {
        _as_n(_require(entry, "n", "rejected"), "rejected"): entry
        for entry in decisions.get("rejected") or []
    }
    edited = {
        _as_n(_require(entry, "n", "edited"), "edited"): entry
        for entry in decisions.get("edited") or []
    }
    added = {
        _as_n(_require(entry, "n", "added"), "added"): entry
        for entry in decisions.get("added") or []
    }

    buckets = [("accepted", accepted), ("rejected", set(rejected)), ("edited", set(edited))]
    for n in proposals:
        hits = [name for name, members in buckets if n in members]
        if len(hits) != 1:
            raise ReviewError(
                f"proposal {n}: must be in exactly one of accepted/rejected/edited, "
                f"found {hits or 'none'}"
            )
        if "conflict" in proposals[n] and hits != ["edited"]:
            raise ReviewError(
                f"proposal {n}: reviewers disagree {proposals[n]['conflict']}; "
                "resolve it in 'edited'"
            )
    for name, members in buckets:
        stray = sorted(n for n in members if n not in proposals)
        if stray:
            raise ReviewError(
                f"decisions.{name} lists cues with no proposal: {stray} (use 'added')"
            )
    for n, entry in added.items():
        if n in proposals:
            raise ReviewError(f"added {n}: already proposed by a reviewer; use accepted/edited")
        if n not in rows:
            raise ReviewError(f"added {n}: no such cue in review.jsonl")
        if str(_require(entry, "old", f"added {n}")).strip() != rows[n]["text"]:
            raise ReviewError(f"added {n}: old does not match review text {rows[n]['text']!r}")

    content: dict[int, tuple[str, str, str]] = {}  # n -> (new, source, why)
    for n in accepted:
        content[n] = (proposals[n]["new"], proposals[n]["source"], proposals[n].get("evidence", ""))
    for n, entry in edited.items():
        content[n] = (
            str(_require(entry, "new", f"edited {n}")).strip(),
            f"edited({proposals[n]['source']})",
            str(entry.get("why", "")),
        )
    for n, entry in added.items():
        content[n] = (
            str(_require(entry, "new", f"added {n}")).strip(),
            "added",
            str(entry.get("why", "")),
        )

    plan = ApplyPlan()
    for n in sorted(rows):
        row = rows[n]
        old = str(row["text"])
        if n in content:
            raw_new, source, why = content[n]
            kind = "content"
        else:
            raw_new, source, why, kind = old, "punctuation", "", "format"
        new = strip_punctuation(raw_new)
        if not new:
            plan.skipped.append({"n": n, "old": old, "proposed": raw_new, "why": "清理後是空字串"})
            plan.needs_ear.append(
                {
                    "n": n,
                    "why": "整句只剩標點或空白，清掉會變空句；要不要刪這句由人決定",
                    "maybe": "",
                    "source": source,
                }
            )
            continue
        if new == old:
            if kind == "content":
                plan.skipped.append({"n": n, "old": old, "proposed": raw_new, "why": "與現況相同"})
            continue
        plan.fixes.append(
            PlannedFix(
                n=n, frame=int(row["frame"]), old=old, new=new, kind=kind, source=source, why=why
            )
        )

    fixed = {fix.n for fix in plan.fixes if fix.kind == "content"}
    ear_sources: list[Mapping[str, Any]] = [
        *reviewer_ears,
        *(
            {
                "n": n,
                "why": str(entry.get("why", "")),
                "maybe": proposals[n]["new"],
                "source": "rejected",
            }
            for n, entry in rejected.items()
            if entry.get("needs_ear")
        ),
        *({**entry, "source": "main-agent"} for entry in decisions.get("needs_ear") or []),
    ]
    seen: set[int] = {entry["n"] for entry in plan.needs_ear}
    for entry in ear_sources:
        n = _as_n(_require(entry, "n", "needs_ear"), "needs_ear")
        if n not in rows:
            raise ReviewError(f"needs_ear {n}: no such cue in review.jsonl")
        if n in fixed or n in seen:  # 已經改了（主 agent 有把握）或重複
            continue
        seen.add(n)
        plan.needs_ear.append(
            {
                "n": n,
                "why": str(entry.get("why", "")),
                "maybe": str(entry.get("maybe", "")),
                "source": str(entry.get("source", "")),
            }
        )
    plan.needs_ear.sort(key=lambda entry: entry["n"])
    return plan


def render_listen_list(
    rows: Mapping[int, Mapping[str, Any]],
    needs_ear: Sequence[Mapping[str, Any]],
    *,
    title: str,
    episode: str,
    timeline: str,
    created: str,
    applied: Mapping[str, int],
) -> str:
    """Vault `NN-字幕待聽清單.md`。時間碼是 Resolve timeline 位置。"""
    lines = [
        "---",
        f"title: {title}",
        f"episode: {episode}",
        f"created: {created}",
        f"resolve_timeline: {timeline}",
        "---",
        "",
        f"# {title}",
        "",
        "這些句子放在前後文裡可疑，但證據不足以直接改（兩套 ASR 聽到的不一致，或一樣錯），"
        "**只能聽原音決定**。時間碼是 Resolve timeline 位置。",
        "",
        f"已直接改進 Resolve 的（不用再看）：前後文修正 {applied.get('content', 0)} 句、"
        f"標點／英數空白清理 {applied.get('format', 0)} 句。",
        "",
        "| 時間 | 說話者 | 現在字幕 | 可能是 | 為什麼可疑 |",
        "|---|---|---|---|---|",
    ]

    def cell(value: Any) -> str:
        return str(value).replace("|", "／").replace("\n", " ").strip() or "？"

    for entry in sorted(needs_ear, key=lambda item: item["n"]):
        row = rows[entry["n"]]
        lines.append(
            f"| {row['tc']} | {cell(row['speaker'])} | {cell(row['text'])} | "
            f"{cell(entry.get('maybe', ''))} | {cell(entry.get('why', ''))} |"
        )
    if not needs_ear:
        lines.append("| — | — | （沒有待聽的句子） | — | — |")
    return "\n".join(lines) + "\n"


# ================================================================== I/O helpers


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ReviewError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewError(f"bad JSON in {path}: {exc}") from exc


def load_review_rows(path: Path) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ReviewError(f"missing file: {path} (run align first)") from exc
    for line in lines:
        if line.strip():
            row = json.loads(line)
            rows[int(row["n"])] = row
    if not rows:
        raise ReviewError(f"{path} is empty")
    return rows


def _load_pcm_int16(path: Path) -> Any:
    import numpy as np

    if not path.exists():
        raise ReviewError(f"missing mic track: {path}")
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, np.int16)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ===================================================================== commands


def cmd_second_asr(args: argparse.Namespace) -> int:
    episode: Path = args.episode
    out_dir: Path = args.out_dir
    source = episode / "normalized.wav"
    if not source.exists():
        print(f"ABORT: {source} not found (S2 not done?)", file=sys.stderr)
        return EXIT_PRECONDITION
    out_dir.mkdir(parents=True, exist_ok=True)
    wav16 = out_dir / "normalized.16k.wav"
    if not wav16.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(wav16),
            ],
            check=True,
        )

    from faster_whisper import WhisperModel

    model = WhisperModel(ASR2_MODEL, device="cuda", compute_type="float16", revision=ASR2_REVISION)
    segments, info = model.transcribe(
        str(wav16),
        language="zh",
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    rows = []
    for segment in segments:
        rows.append(
            {
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text,
                "words": [
                    {
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "word": w.word,
                        "p": round(w.probability, 3),
                    }
                    for w in (segment.words or [])
                ],
            }
        )
        if len(rows) % 200 == 0:
            print(f"{len(rows)} segments, t={segment.end:.0f}s", flush=True)
    words_path = out_dir / ASR2_WORDS_NAME
    words_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    _write_json(
        out_dir / ASR2_MANIFEST_NAME,
        {
            "model": ASR2_MODEL,
            "revision": ASR2_REVISION,
            "device": "cuda",
            "compute_type": "float16",
            "language": "zh",
            "word_timestamps": True,
            "vad_filter": False,
            "condition_on_previous_text": False,
            "source": str(source),
            "audio_duration": getattr(info, "duration", None),
            "segments": len(rows),
        },
    )
    print(f"done: {len(rows)} segments -> {words_path}")
    return EXIT_OK


def build_review_rows(
    cues: Sequence[SrtCue],
    fps: Fraction,
    host: Any,
    guest: Any,
    words: Sequence[Mapping[str, Any]],
    convert: Any,
) -> list[dict[str, Any]]:
    rows = []
    for cue in cues:
        start_sec = float(Fraction(cue.start_frame) / fps)
        end_sec = float(Fraction(cue.end_frame) / fps)
        a, b = int(start_sec * SAMPLE_RATE), int(end_sec * SAMPLE_RATE)
        host_db, guest_db = rms_db(host[a:b]), rms_db(guest[a:b])
        alt = convert(words_in_span(words, start_sec, end_sec)).strip()
        rows.append(
            {
                "n": cue.n,
                "frame": cue.start_frame,
                "end_frame": cue.end_frame,
                "start": round(start_sec, 3),
                "end": round(end_sec, 3),
                "tc": frame_timecode(cue.start_frame, fps),
                "text": cue.text,
                "speaker": classify_speaker(host_db, guest_db),
                "host_db": round(host_db, 1),
                "guest_db": round(guest_db, 1),
                "alt": alt,
                "sim": similarity(cue.text, alt),
            }
        )
    return rows


def cmd_align(args: argparse.Namespace) -> int:
    try:
        fps = parse_fps(args.fps)
        cues = parse_live_srt(Path(args.srt).read_text(encoding="utf-8-sig"), fps)
        words_path = args.words or (args.out_dir / ASR2_WORDS_NAME)
        words = flatten_words(_read_json(words_path))
        host = _load_pcm_int16(args.episode / HOST_TRACK)
        guest = _load_pcm_int16(args.episode / GUEST_TRACK)
    except (ReviewError, ValueError) as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION

    import opencc

    converter = opencc.OpenCC("s2twp")
    rows = build_review_rows(cues, fps, host, guest, words, converter.convert)
    share = match_share(rows)
    if share < MIN_MATCH_SHARE and not args.allow_low_match:
        print(
            f"ABORT: only {share:.0%} of cues match ASR2 (need ≥ {MIN_MATCH_SHARE:.0%}). "
            "The ST1 timeline clock probably differs from normalized.wav "
            "(Intro inserted / sections cut?) — run on the base timeline from S7.",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "review.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    chunks = chunk_rows(rows, args.chunk_size)
    for index, part in enumerate(chunks, 1):
        (out_dir / CHUNK_NAME.format(index)).write_text(
            "\n".join(review_line(row) for row in part) + "\n", encoding="utf-8"
        )
    (out_dir / "proposals").mkdir(exist_ok=True)
    speakers = {
        k: sum(row["speaker"] == k for row in rows)
        for k in (SPEAKER_HOST, SPEAKER_GUEST, SPEAKER_BOTH)
    }
    print(
        f"cues={len(rows)} chunks={len(chunks)} asr2_match={share:.0%} "
        f"low_similarity={sum(row['sim'] < SIMILAR_ENOUGH for row in rows)} speakers={speakers}"
    )
    return EXIT_OK


def _plan_from_work_dir(work_dir: Path) -> tuple[dict[int, dict[str, Any]], ApplyPlan]:
    rows = load_review_rows(work_dir / "review.jsonl")
    paths = sorted(work_dir.glob(PROPOSALS_GLOB))
    if not paths:
        raise ReviewError(f"no {PROPOSALS_GLOB} under {work_dir}")
    docs = [(path.stem, _read_json(path)) for path in paths]
    proposals, ears = collect_proposals(docs, rows)
    decisions = _read_json(work_dir / DECISIONS_NAME)
    return rows, plan_fixes(rows, proposals, ears, decisions)


def cmd_apply(args: argparse.Namespace) -> int:
    work_dir: Path = args.work_dir
    try:
        rows, plan = _plan_from_work_dir(work_dir)
    except ReviewError as exc:
        print(f"ABORT (nothing changed): {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    _write_json(
        work_dir / FIXES_NAME,
        {
            "counts": plan.counts(),
            "fixes": [fix.to_json() for fix in plan.fixes],
            "needs_ear": plan.needs_ear,
            "skipped": plan.skipped,
        },
    )
    print(f"plan: {plan.counts()} -> {work_dir / FIXES_NAME}")
    for fix in [f for f in plan.fixes if f.kind == "content"][:8]:
        print(f"  {fix.n} @{fix.frame}: {fix.old} => {fix.new}")
    if args.plan_only:
        return EXIT_OK
    if not plan.fixes:
        print("nothing to write to Resolve")
        return EXIT_OK

    argv = [
        sys.executable,
        str(_REPO / "scripts" / "resolve_subtitle_rebuild.py"),
        "rebuild",
        "--project",
        args.project,
        "--timeline",
        args.timeline,
        "--fix-file",
        str(work_dir / FIXES_NAME),
        "--out-dir",
        str(work_dir),
    ]
    if args.scratch:
        argv.append("--scratch")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(argv, cwd=_REPO, env=env)
    print(f"resolve_subtitle_rebuild exit {result.returncode}")
    return result.returncode


def cmd_listen_list(args: argparse.Namespace) -> int:
    try:
        rows = load_review_rows(args.work_dir / "review.jsonl")
        data = _read_json(args.work_dir / FIXES_NAME)
    except ReviewError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    # 待聽清單顯示「現在 Resolve 上」的文字：套過標點清理的句子用清理後的版本
    for fix in data.get("fixes") or []:
        if int(fix["n"]) in rows:
            rows[int(fix["n"])]["text"] = fix["new"]
    text = render_listen_list(
        rows,
        data.get("needs_ear") or [],
        title=args.title,
        episode=args.episode_name,
        timeline=args.timeline,
        created=args.created,
        applied=data.get("counts") or {},
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(data.get('needs_ear') or [])} cues to listen)")
    return EXIT_OK


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="subtitle_context_review",
        description="Podcast S7C: whole-episode subtitle context review against a second ASR.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    asr = commands.add_parser("second-asr", help="Faster-Whisper large-v3 on normalized.wav")
    asr.add_argument("--episode", required=True, type=Path)
    asr.add_argument("--out-dir", required=True, type=Path)
    asr.set_defaults(handler=cmd_second_asr)

    align = commands.add_parser("align", help="live ST1 x mics x ASR2 -> review.jsonl + chunks")
    align.add_argument("--episode", required=True, type=Path)
    align.add_argument("--srt", required=True, type=Path, help="live ST1 snapshot SRT")
    align.add_argument("--out-dir", required=True, type=Path)
    align.add_argument("--words", type=Path, help=f"default <out-dir>/{ASR2_WORDS_NAME}")
    align.add_argument("--fps", default="30", help="timeline frame rate (default 30)")
    align.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    align.add_argument(
        "--allow-low-match",
        action="store_true",
        help="skip the ASR2 match-share clock check (only if you know why it is low)",
    )
    align.set_defaults(handler=cmd_align)

    apply = commands.add_parser("apply", help="proposals + decisions -> rebuild --fix-file")
    apply.add_argument("--work-dir", required=True, type=Path)
    apply.add_argument("--project")
    apply.add_argument("--timeline")
    mode = apply.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan-only", action="store_true", help="write context-fixes.json only")
    mode.add_argument("--scratch", action="store_true", help="rebuild on a throwaway duplicate")
    mode.add_argument("--live", action="store_true", help="rebuild the real timeline ST1")
    apply.set_defaults(handler=cmd_apply)

    listen = commands.add_parser("listen-list", help="render the Vault 字幕待聽清單")
    listen.add_argument("--work-dir", required=True, type=Path)
    listen.add_argument("--out", required=True, type=Path)
    listen.add_argument("--title", required=True, help="e.g. 李惠貞 字幕待聽清單")
    listen.add_argument("--episode-name", required=True, help="e.g. 20261111 李惠貞")
    listen.add_argument("--timeline", required=True)
    listen.add_argument("--created", required=True, help="YYYY-MM-DD")
    listen.set_defaults(handler=cmd_listen_list)

    args = parser.parse_args(argv)
    if args.command == "apply" and not args.plan_only and not (args.project and args.timeline):
        parser.error("apply --scratch/--live needs --project and --timeline")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
