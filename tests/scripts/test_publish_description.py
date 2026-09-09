from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.publish_description as publish_description
from agents.usopp.video_description import validate_description_hook

# 段數不設上限、單段不超過 _HOOK_MAX_PARAGRAPH_CHARS、不可自我指涉開場
# （2026-08-30 修修裁決，見 agents/usopp/video_description.py）。
_VALID_HOOK = (
    "睡得少不只是隔天精神差。學習之後的大腦還需要時間，把當天的新資訊重新整理、穩定保存。\n\n"
    "謝伯讓從記憶的固化過程談起，說明作息反覆被打斷時，注意力、判斷與情緒為什麼會一起塌下來。\n\n"
    "面對工作壓力、夜間滑手機與不規律的生活，可以先觀察哪幾個訊號，再決定要調整什麼。\n\n"
    "理解自己的限制之後，才找得到真正能長期維持、不必靠意志力硬撐的做法。"
)


def _description_fixture(tmp_path: Path, monkeypatch) -> tuple[Path, dict, list[dict]]:
    episode_dir = tmp_path / "20260723 謝伯讓"
    broll_dir = episode_dir / "highlights" / "tighten"
    broll_dir.mkdir(parents=True)
    srt_dir = episode_dir / "highlights" / "srt"
    srt_dir.mkdir(parents=True)
    (srt_dir / "punch-L5_tight_r001.srt").write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n睡眠會影響記憶鞏固。\n",
        encoding="utf-8",
    )
    (broll_dir / "punch-L5_broll.json").write_text(
        json.dumps(
            {
                "items": [
                    {"comp": "transition_title", "t0": 35, "vars": {"title": "第一段"}},
                    {"comp": "transition_title", "t0": 91, "vars": {"title": "第二段"}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    packaging = vault / "Attachments" / "packaging" / "20260723-xieboran"
    packaging.mkdir(parents=True)
    (packaging / "packages.json").write_text(
        json.dumps(
            {
                "episode": episode_dir.name,
                "cuts": [
                    {
                        "cut_id": "punch-L5",
                        "citations": ["Science 2010"],
                        "titles": [{"text": "睡眠如何改變記憶", "rank": 1}],
                        "packages": [
                            {
                                "title_rank": 1,
                                "thumbnail_png": "Attachments/packaging/20260723-xieboran/p.png",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (packaging / "approval.json").write_text(
        json.dumps({"approvals": [{"cut_id": "punch-L5", "approved": True, "primary_package": 1}]}),
        encoding="utf-8",
    )
    target = {
        "id": 42,
        "platform": "youtube",
        "status": "draft",
        "title": "",
        "description": "",
        "thumbnail_path": "",
        "error": None,
    }
    updates: list[dict] = []

    def update_target(target_id: int, **fields):
        assert target_id == 42
        target.update(fields)
        updates.append(fields)

    monkeypatch.setattr(publish_description, "get_vault_path", lambda: vault)
    monkeypatch.setattr(
        publish_description,
        "get_release",
        lambda episode, cut_id: {"targets": [target]},
    )
    monkeypatch.setattr(publish_description, "update_target", update_target)
    return episode_dir, target, updates


def test_description_draft_happy_path_is_written_to_release(tmp_path, monkeypatch):
    episode_dir, target, _ = _description_fixture(tmp_path, monkeypatch)
    prompts = []

    result = publish_description.ensure_description_draft(
        episode_dir,
        "punch-L5",
        hook_generator=lambda prompt: prompts.append(prompt) or _VALID_HOOK,
    )

    assert result["state"] == "ready"
    assert target["description"].startswith("睡得少不只是隔天精神差")
    assert "⏱ 00:00 開場" in target["description"]
    assert target["error"] is None
    assert "睡眠如何改變記憶" in prompts[0]


def test_description_draft_interruption_is_resumable(tmp_path, monkeypatch):
    episode_dir, target, _ = _description_fixture(tmp_path, monkeypatch)

    result = publish_description.ensure_description_draft(
        episode_dir,
        "punch-L5",
        hook_generator=lambda prompt: (_ for _ in ()).throw(
            RuntimeError("subscription unavailable")
        ),
    )

    assert result["state"] == "interrupted"
    assert target["description"] == ""
    assert target["error"].startswith("DESCRIPTION_DRAFT_INTERRUPTED:")

    resumed = publish_description.ensure_description_draft(
        episode_dir,
        "punch-L5",
        hook_generator=lambda prompt: _VALID_HOOK,
    )
    assert resumed["state"] == "ready"
    assert target["description"]
    assert target["error"] is None


def test_description_draft_preserves_manual_edit(tmp_path, monkeypatch):
    episode_dir, target, updates = _description_fixture(tmp_path, monkeypatch)
    target["description"] = "這是我手動改好的版本，不能被覆蓋。"

    result = publish_description.ensure_description_draft(
        episode_dir,
        "punch-L5",
        hook_generator=lambda prompt: pytest.fail("manual draft must not call provider"),
    )

    assert result["state"] == "preserved"
    assert target["description"] == "這是我手動改好的版本，不能被覆蓋。"
    assert updates == []


def test_description_draft_without_tight_srt_is_interrupted(tmp_path, monkeypatch):
    episode_dir, target, _ = _description_fixture(tmp_path, monkeypatch)
    (episode_dir / "highlights" / "srt" / "punch-L5_tight_r001.srt").unlink()

    result = publish_description.ensure_description_draft(
        episode_dir,
        "punch-L5",
        hook_generator=lambda prompt: pytest.fail("missing evidence must block provider"),
    )

    assert result["state"] == "interrupted"
    assert target["description"] == ""
    # 逐字稿來源可能是 Release 字幕或舊線的 tight SRT，訊息只保證指出「字幕缺了」。
    assert "字幕" in target["error"]


@pytest.mark.parametrize(
    "hook",
    [
        "這不是睡眠問題，而是記憶問題。",
        "這一段會帶你看睡眠研究。",
        "我們深入探討大腦。",
    ],
)
def test_description_hook_rejects_ai_slop(hook):
    with pytest.raises(ValueError, match="AI slop"):
        validate_description_hook(hook)
