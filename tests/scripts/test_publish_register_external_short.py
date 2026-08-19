from __future__ import annotations

import hashlib

import pytest

from agents.usopp.youtube_short_preflight import ShortPreflightResult
from scripts.publish_register_external_short import register_external_short
from shared.release_store import get_release, update_target


def _preflight(path):
    return ShortPreflightResult(
        file_path=str(path),
        file_bytes=path.stat().st_size,
        container_format="mov,mp4,m4a,3gp,3g2,mj2",
        duration_sec=61.25,
        width=1080,
        height=1920,
        sample_aspect_ratio="1:1",
        display_aspect_ratio=9 / 16,
        video_codec="h264",
        audio_codec="aac",
        fps=30,
    )


def _register(tmp_path, *, cut_id="partner-S01", source_bytes=b"partner-short"):
    episode = tmp_path / "20260819 partner-shorts-e2e"
    episode.mkdir(exist_ok=True)
    source = tmp_path / "delivery.mp4"
    source.write_bytes(source_bytes)
    output = register_external_short(
        episode_dir=episode,
        file_path=source,
        cut_id=cut_id,
        work_title="合作夥伴 Short E2E 01",
        captions_burned=True,
        rights_cleared=True,
        preflight_fn=_preflight,
    )
    return output, episode, source


def test_register_copies_hashes_and_creates_draft_release(tmp_path):
    output, episode, source = _register(tmp_path)
    canonical = episode / "highlights" / "exports" / "partner-S01.mp4"
    assert canonical.read_bytes() == source.read_bytes()
    assert output["copied"] is True
    assert output["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert output["acknowledgements"] == {
        "captions_burned": True,
        "rights_cleared": True,
    }

    release = get_release(episode.name, "partner-S01")
    assert release["format"] == "short"
    assert release["file_path"] == str(canonical.resolve())
    assert release["duration_sec"] == 61.25
    assert release["file_bytes"] == len(source.read_bytes())
    assert release["targets"][0]["platform"] == "youtube"
    assert release["targets"][0]["status"] == "draft"


def test_same_source_and_cut_are_idempotent_and_preserve_target_state(tmp_path):
    first, episode, _ = _register(tmp_path)
    release = get_release(episode.name, "partner-S01")
    update_target(release["targets"][0]["id"], status="approved", title="已審標題")

    second, _, _ = _register(tmp_path)
    after = get_release(episode.name, "partner-S01")
    assert second["copied"] is False
    assert first["release_id"] == second["release_id"]
    assert first["youtube_target_id"] == second["youtube_target_id"]
    assert after["targets"][0]["status"] == "approved"
    assert after["targets"][0]["title"] == "已審標題"


def test_existing_destination_with_different_hash_fails_closed(tmp_path):
    episode = tmp_path / "ep"
    destination = episode / "highlights" / "exports" / "partner-S01.mp4"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"canonical-original")
    source = tmp_path / "delivery.mp4"
    source.write_bytes(b"different-delivery")

    with pytest.raises(ValueError, match="SHA-256 不同"):
        register_external_short(
            episode_dir=episode,
            file_path=source,
            cut_id="partner-S01",
            work_title="test",
            captions_burned=True,
            rights_cleared=True,
            preflight_fn=_preflight,
        )
    assert destination.read_bytes() == b"canonical-original"
    assert get_release("ep", "partner-S01") is None


def test_existing_video_id_blocks_reimport_without_touching_file(tmp_path):
    _, episode, _ = _register(tmp_path)
    release = get_release(episode.name, "partner-S01")
    target = release["targets"][0]
    update_target(target["id"], status="uploaded", video_id="yt-existing")
    canonical = episode / "highlights" / "exports" / "partner-S01.mp4"
    before = canonical.read_bytes()

    with pytest.raises(ValueError, match="已有 video_id=yt-existing"):
        _register(tmp_path)
    assert canonical.read_bytes() == before
    after = get_release(episode.name, "partner-S01")["targets"][0]
    assert after["status"] == "uploaded"
    assert after["video_id"] == "yt-existing"


@pytest.mark.parametrize(
    ("captions_burned", "rights_cleared", "needle"),
    [(False, True, "captions-burned"), (True, False, "rights-cleared")],
)
def test_acknowledgements_are_required(
    tmp_path, captions_burned, rights_cleared, needle
):
    episode = tmp_path / "ep"
    episode.mkdir()
    source = tmp_path / "delivery.mp4"
    source.write_bytes(b"video")
    with pytest.raises(ValueError, match=needle):
        register_external_short(
            episode_dir=episode,
            file_path=source,
            cut_id="partner-S01",
            work_title="test",
            captions_burned=captions_burned,
            rights_cleared=rights_cleared,
            preflight_fn=_preflight,
        )


def test_preflight_errors_do_not_create_canonical_or_db_row(tmp_path):
    episode = tmp_path / "ep"
    episode.mkdir()
    source = tmp_path / "delivery.mp4"
    source.write_bytes(b"video")

    def rejected(path):
        return ShortPreflightResult(file_path=str(path), errors=("landscape",))

    with pytest.raises(ValueError, match="landscape"):
        register_external_short(
            episode_dir=episode,
            file_path=source,
            cut_id="partner-S01",
            work_title="test",
            captions_burned=True,
            rights_cleared=True,
            preflight_fn=rejected,
        )
    assert not (episode / "highlights" / "exports" / "partner-S01.mp4").exists()
    assert get_release("ep", "partner-S01") is None
