"""純結構化修正單的自動執行。

修修 2026-09-03：「以後不能改成送出就自動驅動 Agent 去 render 嗎？多一個動作
覺得不好。」
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from agents.brook.podcast_carousel_autorun import (
    build_result_spec,
    is_autorunnable,
    next_revision,
)
from shared.schemas.podcast_carousel import CarouselCorrectionJobV1


def _job(**overrides) -> CarouselCorrectionJobV1:
    now = datetime.now(UTC)
    payload = {
        "job_id": "cj-" + "a" * 32,
        "episode_id": "20260805 林之晨",
        "source_revision": "r004",
        "source_manifest_sha256": "b" * 64,
        "created_at": now,
        "updated_at": now,
        "copy_edits": [
            {
                "page_id": "hook",
                "role": "hook",
                "artifact_sha256": "c" * 64,
                "fields": {"bridge": "改過的承接文字"},
            }
        ],
    }
    payload.update(overrides)
    return CarouselCorrectionJobV1.model_validate(payload)


def test_free_text_feedback_is_never_autorun():
    """「這句太繞了幫我改順」沒辦法機械套用——那需要 agent 讀懂它。"""
    job = _job(
        feedback_items=[{"page_id": "hook", "artifact_sha256": "c" * 64, "feedback": "這句太繞了"}],
        copy_edits=[],
    )
    assert is_autorunnable(job) is False


def test_structured_only_job_is_autorunnable():
    assert is_autorunnable(_job()) is True


def test_empty_job_is_not_autorunnable():
    with pytest.raises(Exception):
        _job(copy_edits=[])  # schema 本身就擋掉空單


def test_next_revision_pads_to_three_digits():
    assert next_revision("r004") == "r005"
    assert next_revision("r009") == "r010"
    assert next_revision("r099") == "r100"


def test_result_spec_applies_only_the_requested_fields(tmp_path):
    """把關在 `complete_job` 的 exact diff，但這裡就不該多動任何欄位。"""
    from tests.agents.brook.test_podcast_carousel_panel import _index_and_spec

    _index, spec = _index_and_spec(tmp_path)
    source = spec.model_copy(update={"revision": "r004"})
    job = _job(
        copy_edits=[
            {
                "page_id": source.pages[1].page_id,
                "role": source.pages[1].role,
                "artifact_sha256": "c" * 64,
                "fields": {"emphasis": source.pages[1].emphasis},
            }
        ]
    )

    payload, changed = build_result_spec(source_spec=source, job=job)
    before = json.loads(source.model_dump_json())

    assert payload["revision"] == "r005"
    assert payload["panel_inherited_from"] == "r004"
    # 只有 revision 與 panel_inherited_from 變動（欄位值本來就相同）
    assert changed == 2
    assert payload["pages"] == before["pages"]


def test_free_text_job_is_refused_before_it_is_claimed(tmp_path, monkeypatch):
    """拒絕要發生在認領之前——先認領再拒絕會把工作留在 claimed 擋住整個 revision。"""
    from agents.brook import podcast_carousel_autorun as mod

    claimed = []
    monkeypatch.setattr(
        "scripts.podcast_carousel_correction_job.claim_job",
        lambda *a, **k: claimed.append(a) or None,
    )
    job_path = tmp_path / "cj.json"
    job_path.write_text(
        _job(
            feedback_items=[
                {"page_id": "hook", "artifact_sha256": "c" * 64, "feedback": "這句太繞了"}
            ],
            copy_edits=[],
        ).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(mod.StructuredAutorunError, match="自由文字"):
        mod.execute_structured_job(episode_dir=tmp_path, job_path=job_path, executor_id="test")
    assert claimed == [], "含自由文字意見的工作不可以被認領"


def test_autorun_claims_with_a_lease_long_enough_for_the_render():
    """租約一過期，連 `fail_job` 都會被自己的 `_assert_claim` 擋下。

    出圖 + 完成驗收（每張受影響的卡都要再啟動一次 Chrome）可能遠超過預設的
    30 分鐘（2026-09-03 review 抓到）。
    """
    import inspect as _inspect

    from agents.brook import podcast_carousel_autorun as mod

    source = _inspect.getsource(mod.execute_structured_job)
    claim = source[source.index("claim_job(") :][:400]
    assert "lease_seconds=" in claim


def test_failure_to_mark_failed_is_not_swallowed():
    """吞掉它會讓工作停在 claimed，那個 revision 就再也送不出新修改。"""
    import inspect as _inspect

    from agents.brook import podcast_carousel_autorun as mod

    source = _inspect.getsource(mod.execute_structured_job)
    handler = source[source.index("except Exception as error") :]
    assert "except CorrectionJobTransitionError as fail_error:" in handler
    assert "pass" not in handler.split("fail_error")[1][:200]
