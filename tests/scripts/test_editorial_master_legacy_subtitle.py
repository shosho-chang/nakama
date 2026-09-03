"""ADR-063 換軌前的集數要能封存 Editorial Master。

修修 2026-09-03：抹布這一集的字幕停在舊契約（`degraded-dual-asr-v1`）。ADR-063
要求「不要改名或重寫抹布的產物」，升級只能靠一份綁定同一批 bytes 的新 handoff
——但那等於把整條 memo dual-audit 線重跑（抹布現在停在 `awaiting_text_audits`，
10 個輸入全缺），而那條線不是為了修正內容，是為了換契約名稱。
"""

from __future__ import annotations

import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "scripts/podcast_editorial_master.py").read_text(encoding="utf-8")


def test_seal_exposes_the_legacy_handoff() -> None:
    assert '"--degraded-release-handoff"' in SCRIPT
    # 必須真的傳下去，不是只長在 argparse 上
    assert "degraded_release_handoff=args.degraded_release_handoff," in SCRIPT


def test_selector_rejects_combining_both_handoffs() -> None:
    """兩個 handoff 同時給是矛盾的意圖，必須擋掉——否則來源出處會說不清。"""
    from agents.brook.script_video.subtitle_handoff import select_stage5_subtitle

    source = inspect.getsource(select_stage5_subtitle)
    branch = source[source.index("if subtitle_release_handoff is not None:") :]
    assert "degraded_release_handoff is not None" in branch[:400]
    assert "cannot be combined" in branch[:600]


def test_degraded_mode_is_recorded_in_the_identity() -> None:
    """來源模式要寫進 Editorial Master——衍生產物永遠查得到自己的字幕出處。"""
    from agents.brook.script_video.subtitle_handoff import select_stage5_subtitle

    source = inspect.getsource(select_stage5_subtitle)
    assert 'mode="degraded-dual-asr-v1"' in source

    handoff = (ROOT / "agents/brook/script_video/subtitle_handoff.py").read_text(encoding="utf-8")
    identity = handoff[handoff.index("def identity(") :][:1200]
    assert "subtitle_mode" in identity
