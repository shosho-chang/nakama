"""ADR-033 D5 + D9 — cutout library lookup for YouTube + Podcast routes."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from shared.cutout_library import (
    EmotionLookupError,
    pick_podcast_guest,
    pick_podcast_host,
    pick_youtube_host,
)


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """Build a fake vault with the cutout structure ADR-033 §VAULT additions describes."""
    cutouts = tmp_path / "Attachments" / "cutouts"

    # YouTube host library — populate 'surprised' + 'thoughtful' folders
    for emo in ("surprised", "thoughtful"):
        folder = cutouts / "shosho" / emo
        folder.mkdir(parents=True)
        for n in (1, 2, 3):
            (folder / f"{n}.png").write_bytes(b"PNG-fake-content")

    # Empty emotion folder (excited) — for the empty-folder branch
    (cutouts / "shosho" / "excited").mkdir(parents=True)

    # Podcast per-episode cutouts (ep_slug = "ep42")
    podcast_dir = cutouts / "podcast" / "ep42"
    podcast_dir.mkdir(parents=True)
    for name in ("host_v1_surprised.png", "host_v2_thoughtful.png", "guest_v1.png", "guest_v2.png"):
        (podcast_dir / name).write_bytes(b"PNG-fake")

    return tmp_path


def test_pick_youtube_host_returns_existing_png(vault_root: Path):
    path = pick_youtube_host("surprised", vault_root, rng=random.Random(42))
    assert path.exists()
    assert path.parent.name == "surprised"
    assert path.suffix == ".png"


def test_pick_youtube_host_accepts_zh_tw(vault_root: Path):
    path = pick_youtube_host("驚訝", vault_root, rng=random.Random(42))
    assert "surprised" in str(path)


def test_pick_youtube_host_deterministic_with_seed(vault_root: Path):
    rng1 = random.Random(123)
    rng2 = random.Random(123)
    assert pick_youtube_host("thoughtful", vault_root, rng=rng1) == pick_youtube_host(
        "thoughtful", vault_root, rng=rng2
    )


def test_pick_youtube_host_missing_folder_raises(vault_root: Path):
    with pytest.raises(FileNotFoundError, match="missing"):
        pick_youtube_host("laughing", vault_root)


def test_pick_youtube_host_empty_folder_raises(vault_root: Path):
    with pytest.raises(FileNotFoundError, match="empty"):
        pick_youtube_host("excited", vault_root)


def test_pick_youtube_host_unknown_emotion_raises(vault_root: Path):
    with pytest.raises(EmotionLookupError):
        pick_youtube_host("迷茫", vault_root)


def test_pick_podcast_host_with_emotion_match_in_filename(vault_root: Path):
    fm = {
        "thumbnail_active_cutouts": {
            "host": [
                "Attachments/cutouts/podcast/ep42/host_v1_surprised.png",
                "Attachments/cutouts/podcast/ep42/host_v2_thoughtful.png",
            ],
            "guest": ["Attachments/cutouts/podcast/ep42/guest_v1.png"],
        }
    }
    path = pick_podcast_host("ep42", "surprised", vault_root, fm, rng=random.Random(0))
    assert "surprised" in path.stem


def test_pick_podcast_host_falls_back_when_no_emotion_match(vault_root: Path):
    """If no active cutout has the resolved emotion in its name, any active is fine."""
    fm = {
        "thumbnail_active_cutouts": {
            "host": ["Attachments/cutouts/podcast/ep42/host_v1_surprised.png"],
        }
    }
    # 'laughing' isn't in any host filename — should still pick the only available
    path = pick_podcast_host("ep42", "laughing", vault_root, fm, rng=random.Random(0))
    assert path.exists()


def test_pick_podcast_host_no_active_raises(vault_root: Path):
    fm = {"thumbnail_active_cutouts": {"host": [], "guest": []}}
    with pytest.raises(FileNotFoundError, match="No active host"):
        pick_podcast_host("ep42", "surprised", vault_root, fm)


def test_pick_podcast_host_active_paths_missing_on_disk_raises(vault_root: Path):
    fm = {
        "thumbnail_active_cutouts": {
            "host": ["Attachments/cutouts/podcast/ep42/missing_v9.png"],
        }
    }
    with pytest.raises(FileNotFoundError, match="none exist on disk"):
        pick_podcast_host("ep42", "surprised", vault_root, fm)


def test_pick_podcast_guest_independent_pool(vault_root: Path):
    fm = {
        "thumbnail_active_cutouts": {
            "host": ["Attachments/cutouts/podcast/ep42/host_v1_surprised.png"],
            "guest": [
                "Attachments/cutouts/podcast/ep42/guest_v1.png",
                "Attachments/cutouts/podcast/ep42/guest_v2.png",
            ],
        }
    }
    path = pick_podcast_guest("ep42", "thoughtful", vault_root, fm, rng=random.Random(0))
    assert "guest" in path.stem
