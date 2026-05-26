"""ADR-033 D3 + D5 — emotions.yml schema + resolve_emotion alias map.

Verifies the single-source manifest at prompts/thumbnail/emotions.yml is
parseable and that resolve_emotion accepts every documented spelling. Codex
panel section 4 + Gemini panel section 3 both flagged the failure mode where
修修 types `驚訝` and the regex doesn't match an English enum — this test
locks the contract that all aliases route to the canonical key.
"""

from __future__ import annotations

import pytest

from shared.cutout_library import (
    EmotionLookupError,
    emotion_keys,
    load_emotions,
    resolve_emotion,
)


def test_emotions_yml_schema():
    emotions = load_emotions()
    assert len(emotions) == 7, "ADR-033 D5 default closed set is 7 emotions"

    required_keys = {"key", "zh_tw", "description"}
    for emo in emotions:
        missing = required_keys - emo.keys()
        assert not missing, f"emotion {emo.get('key')!r} missing fields {missing}"
        assert emo["key"].isascii() and emo["key"].islower(), (
            f"emotion key must be lowercase ASCII: {emo['key']!r}"
        )
        assert emo["zh_tw"], f"emotion {emo['key']} missing zh_tw label"


def test_default_seven_emotions_present():
    keys = set(emotion_keys())
    expected = {
        "excited",
        "thoughtful",
        "surprised",
        "explaining",
        "serious",
        "laughing",
        "pointing",
    }
    assert keys == expected, "ADR-033 D5 default emotion set drifted"


def test_resolve_english_key_passthrough():
    assert resolve_emotion("surprised") == "surprised"
    assert resolve_emotion("Surprised") == "surprised"
    assert resolve_emotion("  EXCITED  ") == "excited"


def test_resolve_zh_tw_label():
    assert resolve_emotion("驚訝") == "surprised"
    assert resolve_emotion("思考") == "thoughtful"
    assert resolve_emotion("大笑") == "laughing"


def test_resolve_aliases():
    """Codex panel §4 D3 + Gemini §3 — 修修 likely types `驚喜` not `surprised`."""
    assert resolve_emotion("驚喜") == "surprised"
    assert resolve_emotion("沈思") == "thoughtful"
    assert resolve_emotion("激動") == "excited"


def test_resolve_unknown_raises_with_canonical_options():
    with pytest.raises(EmotionLookupError) as exc:
        resolve_emotion("迷茫")  # not in the default set
    msg = str(exc.value)
    assert "驚訝" in msg, "error message should list canonical zh_tw options to guide 修修"
    assert "迷茫" in msg


def test_resolve_empty_string_raises():
    with pytest.raises(EmotionLookupError):
        resolve_emotion("")
    with pytest.raises(EmotionLookupError):
        resolve_emotion("   ")
