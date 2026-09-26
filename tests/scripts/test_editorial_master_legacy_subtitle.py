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


def test_legacy_episode_alias_must_be_declared_verbatim() -> None:
    """這道檢查是擋「把 A 集的字幕封進 B 集」，不可以為了 legacy 就放寬。

    抹布的 handoff 寫 `20260814-moboo`、資料夾是 `20260814 抹布`，而 ADR-063
    禁止改寫那些產物。放行的方式是**要求操作者明講**：別名逐字給對才過，
    而且會寫進不可變收據，這個例外永遠留在證據鏈上。
    """
    import pytest

    from agents.brook.script_video.editorial_master import (
        EditorialMasterContractError,
        _validate_stage5_identity,
    )

    identity = {"episode_id": "20260814-moboo", "subtitle_mode": "degraded-dual-asr-v1"}

    # 沒宣告 → 擋
    with pytest.raises(EditorialMasterContractError, match="another episode"):
        _validate_stage5_identity(identity, "20260814 抹布")

    # 宣告錯 → 擋
    with pytest.raises(EditorialMasterContractError, match="another episode"):
        _validate_stage5_identity(identity, "20260814 抹布", legacy_episode_alias="20260814-wrong")

    # 逐字對上 → 放行，而且記下來
    stage5 = _validate_stage5_identity(
        identity, "20260814 抹布", legacy_episode_alias="20260814-moboo"
    )
    assert stage5["legacy_episode_alias"] == "20260814-moboo"
    assert stage5["episode_id"] == "20260814-moboo", "原始 id 不可被覆寫"


def test_matching_episode_never_gets_an_alias_recorded() -> None:
    """正常路徑不該多出這個欄位——它出現就代表這一集用了 legacy 例外。"""
    from agents.brook.script_video.editorial_master import _validate_stage5_identity

    stage5 = _validate_stage5_identity(
        {"episode_id": "20260805 林之晨"},
        "20260805 林之晨",
        legacy_episode_alias="20260814-moboo",
    )
    assert "legacy_episode_alias" not in stage5


def test_read_back_honours_the_recorded_alias_only() -> None:
    """讀回端沒有操作者可以宣告——只能認收據自己帶的那份宣告。

    封存寫入通過、讀回被擋，會讓整個封存回滾（2026-09-03 抹布實際發生，
    白跑 8 分鐘算圖）。兩端必須用同一份證據。
    """
    import inspect

    from agents.brook.script_video import editorial_master as mod

    source = inspect.getsource(mod)
    gate = source[source.index("Stage 5 lineage belongs to another episode") - 700 :]
    assert 'stage5.get("legacy_episode_alias")' in gate
    # 別名必須正好等於 handoff 宣告的 id，不能是任意字串
    assert 'alias != stage5.get("episode_id")' in gate
