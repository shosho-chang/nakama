"""Camera / speaker cross-validation helper for funnel Stage 3 (ADR-054 A8).

Verifies that the expected speaker dominates within a given time window,
using word-level speaker assignments from ``shared.speaker_assign``.

Motivation: ``run_short_director.py:50-54`` hard-codes CAM1=speaker0,
CAM2=speaker1 per episode. Swapping cameras between episodes silently samples
the wrong face without reporting an error. This helper catches that.
"""

from __future__ import annotations


def validate_cam_speaker(
    words: list[dict],
    word_speakers: list[int | None],
    window: tuple[float, float],
    expected_speaker: int,
    *,
    threshold: float = 0.6,
) -> None:
    """Raise if ``expected_speaker`` does not dominate ``window``.

    Args:
        words: word-level dicts with ``"start"`` and ``"end"`` float timestamps,
               as produced by WhisperX / ``shared.speaker_assign`` callers.
        word_speakers: parallel list of speaker indices (``int | None``),
               as returned by ``shared.speaker_assign.assign_word_speakers``.
        window: ``(t_start, t_end)`` in seconds — the clip window to validate.
        expected_speaker: the speaker index (0 or 1) expected to dominate.
        threshold: minimum fraction of assigned words that must belong to
               ``expected_speaker`` (default 0.6 = 60 %).

    Raises:
        ValueError: if the actual fraction is below ``threshold``.
            The message includes the fraction and instructs the operator
            to check ``director.json cam mapping``.
    """
    if len(words) != len(word_speakers):
        raise ValueError(
            f"words ({len(words)}) and word_speakers ({len(word_speakers)}) must be same length"
        )

    t_start, t_end = window
    in_window_speakers = [
        s
        for w, s in zip(words, word_speakers)
        if w.get("start") is not None
        and w.get("end") is not None
        and float(w["end"]) > t_start
        and float(w["start"]) < t_end
        and s is not None
    ]

    if not in_window_speakers:
        return

    fraction = in_window_speakers.count(expected_speaker) / len(in_window_speakers)
    if fraction < threshold:
        raise ValueError(
            f"Camera/speaker mismatch in window {t_start:.1f}s–{t_end:.1f}s: "
            f"expected speaker {expected_speaker} fraction ≥ {threshold:.0%}, "
            f"got {fraction:.1%} ({in_window_speakers.count(expected_speaker)}"
            f"/{len(in_window_speakers)} words). "
            "請確認 director.json cam mapping — 換集時若 speaker↔camera 對調，"
            "請在 highlights/tighten/director.json 更新 cams 欄位。"
        )
