from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

STAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$")


def stamp_seconds(value: str | int | float) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean timestamp")
    if isinstance(value, (int, float)):
        return float(value)
    match = STAMP_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid timestamp: {value!r}")
    hours, minutes, seconds, milliseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def srt_stamp(value: float) -> str:
    milliseconds = round(value * 1000)
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, milliseconds = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def parse_srt(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    cues: list[dict[str, object]] = []
    for expected, block in enumerate(re.split(r"\n{2,}", text), start=1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(expected):
            raise ValueError(f"invalid cue identity at {expected}")
        start_text, end_text = lines[1].split(" --> ", 1)
        cues.append(
            {
                "number": expected,
                "start": stamp_seconds(start_text),
                "end": stamp_seconds(end_text),
                "text": "\n".join(lines[2:]),
            }
        )
    return cues


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def load_candidates(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("candidates")
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"invalid miner payload: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("inputs", type=Path, nargs="+")
    args = parser.parse_args()

    srt_raw = args.srt.read_bytes()
    cues = parse_srt(args.srt)
    raw_candidates = [item for path in args.inputs for item in load_candidates(path)]
    raw_ids = [str(item.get("id", "")) for item in raw_candidates]
    if len(raw_ids) != len(set(raw_ids)) or not all(raw_ids):
        raise ValueError("missing or duplicate miner candidate id")

    prepared: list[dict[str, object]] = []
    for item in raw_candidates:
        fmt = item.get("format")
        if fmt not in {"long", "short"}:
            raise ValueError(f"invalid format for {item['id']}")
        start = stamp_seconds(item["t_start"])
        end = stamp_seconds(item["t_end"])
        cue_start = item.get("cue_start")
        cue_end = item.get("cue_end")
        if type(cue_start) is not int or type(cue_end) is not int:
            raise ValueError(f"invalid cue range for {item['id']}")
        if not (1 <= cue_start <= cue_end <= len(cues)):
            raise ValueError(f"out-of-range cue for {item['id']}")
        selected = cues[cue_start - 1 : cue_end]
        if abs(float(selected[0]["start"]) - start) > 0.0005:
            raise ValueError(f"start mismatch for {item['id']}")
        if abs(float(selected[-1]["end"]) - end) > 0.0005:
            raise ValueError(f"end mismatch for {item['id']}")
        duration = end - start
        low, high = (360, 1080) if fmt == "long" else (40, 180)
        if not low <= duration <= high:
            raise ValueError(f"duration outside tolerance for {item['id']}: {duration}")
        selected_text = "\n".join(str(cue["text"]) for cue in selected)
        hook = str(item.get("hook", "")).strip()
        if not hook or normalized(hook) not in normalized(selected_text):
            raise ValueError(f"hook is not exact selected transcript for {item['id']}")
        transcript = "\n\n".join(
            f"[{cue['number']} | {srt_stamp(float(cue['start']))} --> "
            f"{srt_stamp(float(cue['end']))}]\n{cue['text']}"
            for cue in selected
        )
        head_trim = item.get("head_trim")
        if head_trim in {0, 0.0, "", None}:
            head_trim = None
        elif not isinstance(head_trim, str):
            raise ValueError(f"invalid head_trim for {item['id']}")
        prepared.append(
            {
                "source_candidate_id": item["id"],
                "format": fmt,
                "t_start": round(start, 3),
                "t_end": round(end, 3),
                "title": str(item.get("title", "")).strip(),
                "hook": hook,
                "rationale": str(item.get("rationale", "")).strip(),
                "miner": str(item.get("miner", "")).strip(),
                "head_trim": head_trim,
                "cue_start": cue_start,
                "cue_end": cue_end,
                "transcript": transcript,
            }
        )

    prepared.sort(
        key=lambda item: (str(item["format"]), float(item["t_start"]), str(item["miner"]))
    )
    counters = {"long": 0, "short": 0}
    for item in prepared:
        fmt = str(item["format"])
        counters[fmt] += 1
        item["id"] = ("L" if fmt == "long" else "S") + f"{counters[fmt]:02d}"

    output = {
        "schema_version": 1,
        "source_srt": str(args.srt.resolve()),
        "source_srt_sha256": hashlib.sha256(srt_raw).hexdigest(),
        "miner_inputs": [
            {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in args.inputs
        ],
        "candidates": prepared,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"counts": counters, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
