"""人工勘誤：修修看完勘誤表之後，對特定 cue 給出的正確文字。

**這是顯示層，不是證據層。** `release.srt` 是 ADR-063 的 hash-bound 證據——它記的是
「機器當初聽到什麼、雙審與仲裁能確立什麼」，那份東西封存之後一個 byte 都不動。
但機器有查不到的東西：私人稱謂、只有在場的人才知道的產品名、同音的公眾人物姓名。
那些會留在 `unresolved-components.json` 裡、字幕維持 Memo 原文。

修修看過勘誤表、給出答案之後，答案要進到觀眾看得到的字幕裡。走的是跟語助詞清理
同一條路（`strip_fillers_srt_file` 的註解）：顯示副本可以跟 release 不同，只要
**改了什麼、誰改的、依據是什麼**留得下來。差別是語助詞清理有規則可循，人工勘誤沒有，
所以它需要一份具名收據。

順序（修修 2026-09-11 定的流程）：

    release.srt → 套人工勘誤 → 語助詞清理 → 顯示副本 → timeline

勘誤先套、清理後跑，因為勘誤單上的 cue 編號與原文都是對著 `release.srt` 寫的；
語助詞清理會刪 cue、也會改文字，跑完編號就對不上了。

fail-closed 的地方有三個，每一個都擋過真實的錯法：

- `release_srt_sha256` 對不上 → 這份勘誤是針對另一版 release 寫的
- `from` 與該 cue 的實際文字不符 → 勘誤單過期，或 cue 編號抄錯
- `to` 等於 `from` → 沒有實際改動，多半是填表時漏改
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

CONTRACT = "nakama.subtitle_human_corrections.v1"

_BLOCK = re.compile(r"\n\s*\n")


class SubtitleCorrectionError(ValueError):
    """這份勘誤套不上去——寧可停下來，不要默默改錯字幕。"""


@dataclass(frozen=True, slots=True)
class Correction:
    cue: int
    before: str
    after: str
    basis: str


@dataclass(frozen=True, slots=True)
class CorrectionSet:
    episode_id: str
    release_srt_sha256: str
    attested_by: str
    attested_at: str
    corrections: tuple[Correction, ...]


def corrections_path(episode_dir: Path) -> Path:
    return Path(episode_dir) / "subtitle-human-corrections.v1.json"


def _text(payload: dict, key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SubtitleCorrectionError(f"{label} 缺少或空白：{key}")
    return value


def load_corrections(path: Path) -> CorrectionSet:
    """讀一份勘誤檔。結構壞掉一律 fail closed。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SubtitleCorrectionError(f"勘誤檔讀不到或不是 JSON：{path}") from error
    if not isinstance(payload, dict):
        raise SubtitleCorrectionError("勘誤檔不是物件")
    if payload.get("contract") != CONTRACT:
        raise SubtitleCorrectionError(f"勘誤檔 contract 不支援：{payload.get('contract')!r}")

    rows = payload.get("corrections")
    if not isinstance(rows, list) or not rows:
        raise SubtitleCorrectionError("勘誤檔沒有任何一筆更正")

    seen: set[int] = set()
    items: list[Correction] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SubtitleCorrectionError(f"corrections[{index}] 不是物件")
        cue = row.get("cue")
        if not isinstance(cue, int) or isinstance(cue, bool) or cue < 1:
            raise SubtitleCorrectionError(f"corrections[{index}] cue 無效：{cue!r}")
        if cue in seen:
            raise SubtitleCorrectionError(f"同一個 cue 被更正兩次：{cue}")
        seen.add(cue)
        before = _text(row, "from", label=f"corrections[{index}]")
        after = _text(row, "to", label=f"corrections[{index}]")
        if before == after:
            raise SubtitleCorrectionError(f"corrections[{index}] cue {cue} 的 from 與 to 相同")
        items.append(
            Correction(
                cue=cue,
                before=before,
                after=after,
                basis=_text(row, "basis", label=f"corrections[{index}]"),
            )
        )

    return CorrectionSet(
        episode_id=_text(payload, "episode_id", label="勘誤檔"),
        release_srt_sha256=_text(payload, "release_srt_sha256", label="勘誤檔"),
        attested_by=_text(payload, "attested_by", label="勘誤檔"),
        attested_at=_text(payload, "attested_at", label="勘誤檔"),
        corrections=tuple(sorted(items, key=lambda item: item.cue)),
    )


def apply_corrections_text(srt_text: str, document: CorrectionSet) -> tuple[str, dict]:
    """把勘誤套進 SRT 文字，回傳 (新文字, stats)。

    逐筆核對 `from`：對不上就停。**不做模糊比對**——勘誤單上的原文是從
    `release.srt` 逐字抄下來的，對不上就代表這份單子不是針對這一版寫的，
    這時候硬套會把正確的句子改壞。
    """
    blocks = [block for block in _BLOCK.split(srt_text.strip()) if block.strip()]
    by_cue: dict[int, int] = {}
    for position, block in enumerate(blocks):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        try:
            by_cue[int(lines[0].strip())] = position
        except ValueError:
            continue

    applied = 0
    for item in document.corrections:
        position = by_cue.get(item.cue)
        if position is None:
            raise SubtitleCorrectionError(f"cue {item.cue} 不在這份 SRT 裡")
        lines = blocks[position].splitlines()
        current = " ".join(line.strip() for line in lines[2:]).strip()
        if current != item.before:
            raise SubtitleCorrectionError(
                f"cue {item.cue} 的原文與勘誤單不符：\n"
                f"  SRT ：{current!r}\n"
                f"  勘誤：{item.before!r}"
            )
        blocks[position] = "\n".join([lines[0], lines[1], item.after])
        applied += 1

    return "\n\n".join(blocks) + "\n", {
        "corrections_applied": applied,
        "cues_in": len(blocks),
        "attested_by": document.attested_by,
    }


def open_for_release(episode_dir: Path, *, release_srt: Path) -> CorrectionSet | None:
    """這一集有勘誤檔就讀出來並驗綁定；沒有就回 None（不是錯誤）。"""
    path = corrections_path(episode_dir)
    if not path.is_file():
        return None
    document = load_corrections(path)
    episode_id = Path(episode_dir).name
    if document.episode_id != episode_id:
        raise SubtitleCorrectionError(
            f"勘誤檔的 episode_id 是 {document.episode_id!r}，但這一集是 {episode_id!r}"
        )
    digest = hashlib.sha256(Path(release_srt).read_bytes()).hexdigest()
    if document.release_srt_sha256 != digest:
        raise SubtitleCorrectionError(
            "勘誤檔綁的不是這一版 release.srt——它是針對另一版寫的，套上去會改錯句子\n"
            f"  勘誤檔：{document.release_srt_sha256}\n"
            f"  release：{digest}"
        )
    return document
