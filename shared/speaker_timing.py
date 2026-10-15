"""Episode speaker-timing tokens — the shared loader for speaker analysis.

Two callers need word/segment-level timing to feed
``shared.speaker_assign.assign_word_speakers``: the short-director
(``scripts/run_short_director.py``) and the thumbnail cutout sampler
(``.claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py sample``).

Sources, in order:

1. Legacy ``<episode>/subs/words.json`` (WhisperX pipeline).
2. ADR-063 Memo Dual-Audit release: ``memo_recognition_evidence`` referenced
   by the release ledger, accepted only when its bytes match the ledger's
   ``sha256`` / ``size_bytes`` and its ``normalized_audio_sha256`` matches the
   ledger's. Episodes produced by the Memo pipeline have no ``subs/words.json``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agents.brook.script_video.subtitle_handoff import Stage5SubtitleRequest


def load_speaker_timing_tokens(episode_dir: Path) -> list[dict]:
    """Load fresh, episode-bound timing tokens for speaker switching."""
    legacy = episode_dir / "subs" / "words.json"
    if legacy.is_file():
        raw = json.loads(legacy.read_text(encoding="utf-8"))
        words = raw.get("words") if isinstance(raw, dict) else raw
        if not isinstance(words, list):
            raise SystemExit(f"{legacy} 的 words 必須是 array")
        return words

    selection = Stage5SubtitleRequest().open(episode_dir)
    handoff = selection.handoff
    ledger_path = getattr(handoff, "release_ledger_path", None)
    if selection.mode != "memo-dual-audit-v1" or not isinstance(ledger_path, Path):
        raise SystemExit(
            "導播缺少正式 speaker timing evidence（無 words.json／Memo release ledger）"
        )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ref = ledger.get("inputs", {}).get("memo_recognition_evidence")
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "size_bytes"}:
        raise SystemExit("release ledger 的 memo_recognition_evidence schema 不完整")
    evidence_path = (episode_dir / ref["path"]).resolve()
    root = episode_dir.resolve()
    if evidence_path != root and root not in evidence_path.parents:
        raise SystemExit("memo_recognition_evidence path escape")
    evidence_raw = evidence_path.read_bytes()
    if (
        len(evidence_raw) != ref["size_bytes"]
        or hashlib.sha256(evidence_raw).hexdigest() != ref["sha256"]
    ):
        raise SystemExit("memo_recognition_evidence bytes 與 release ledger 不符")
    evidence = json.loads(evidence_raw)
    if evidence.get("contract") != "memo-recognition-evidence-v1" or evidence.get(
        "normalized_audio_sha256"
    ) != ledger.get("normalized_audio_sha256"):
        raise SystemExit("memo_recognition_evidence identity 與 release ledger 不符")
    tokens = evidence.get("tokens")
    if not isinstance(tokens, list) or not tokens:
        raise SystemExit("memo_recognition_evidence tokens 必須是 non-empty array")
    words: list[dict] = []
    previous_end = -1
    for index, token in enumerate(tokens):
        if not isinstance(token, dict):
            raise SystemExit(f"Memo token {index} 必須是 object")
        text, start_ms, end_ms = token.get("text"), token.get("start_ms"), token.get("end_ms")
        if (
            not isinstance(text, str)
            or not text.strip()
            or type(start_ms) is not int
            or type(end_ms) is not int
            or start_ms < previous_end
            or end_ms <= start_ms
        ):
            raise SystemExit(f"Memo token {index} timing/text 無效")
        words.append({"word": text, "start": start_ms / 1000, "end": end_ms / 1000})
        previous_end = end_ms
    return words
