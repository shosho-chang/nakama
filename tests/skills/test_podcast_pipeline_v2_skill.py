"""A fresh agent must deterministically route Podcast work through V2."""

from pathlib import Path

SKILL = Path(".claude/skills/podcast-pipeline/SKILL.md")


def test_skill_exposes_one_memo_first_v2_state_machine_without_v1_fallback() -> None:
    text = SKILL.read_text(encoding="utf-8")
    required = (
        "Audio/Live-Mix.wav",
        "Audio/1_COMBO-1.wav",
        "Audio/2_COMBO-2.wav",
        "normalized-handoff.v1.json",
        "podcast_subtitle_v2_references.py prepare",
        "podcast_subtitle_v2_references.py accept",
        "podcast_subtitle_v2_evidence.py prepare-recognition",
        "podcast_subtitle_v2_evidence.py accept-recognition",
        "podcast_subtitle_v2_evidence.py prepare-cues",
        "podcast_subtitle_v2_evidence.py accept-cues",
        "podcast_subtitle_v2_evidence.py status",
        "python -m agents.brook.podcast_subtitles",
        "--reference-manifest",
        "PODCAST_SUBTITLE_V2_NORMALIZED_HANDOFF_MANIFEST",
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_MANIFEST",
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_SOURCE_EXPORT",
        "PODCAST_SUBTITLE_V2_MEMO_RECOGNITION_ACCEPTANCE_RECEIPT",
        "PODCAST_SUBTITLE_V2_MEMO_CUE_SOURCE_EXPORT",
        "PODCAST_SUBTITLE_V2_MEMO_CUE_ACCEPTANCE_RECEIPT",
        ".subtitle-v2/subscription-work",
        "resolve-project",
        "highlight-cut",
        "longform-cut",
        "thumbnail-brainstorm",
        "publish_upload.py --cc-only",
        "youtube_publish_reconcile.py",
        "Verified Projection",
        "explicit legacy forensic",
    )
    for marker in required:
        assert marker in text, f"Podcast Skill missing V2 routing marker: {marker}"
    forbidden = (
        "prep 完成 → 下一步 subtitle-gen",
        "run_transcribe.py",
        "--no-auphonic",
        "--outtakes-from",
    )
    for marker in forbidden:
        assert marker not in text, f"Podcast Skill still exposes V1 production route: {marker}"
