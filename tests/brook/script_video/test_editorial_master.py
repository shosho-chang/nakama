from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.brook.script_video import editorial_master as editorial_master_module
from agents.brook.script_video.editorial_master import (
    EditorialMasterArtifactConflictError,
    EditorialMasterContractError,
    EditorialMasterRequest,
    EditorialMasterTimelineDriftError,
    inspect_timeline,
    seal_editorial_master,
    verify_editorial_master,
)
from scripts import podcast_editorial_master as editorial_master_cli


class FakeItem:
    def __init__(self, name: str, start: int, end: int, uid: str) -> None:
        self._name = name
        self._start = start
        self._end = end
        self._uid = uid

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetUniqueId(self):
        return self._uid

    def GetMediaPoolItem(self):
        return None


class FakeTimeline:
    def __init__(self, *, uid: str = "timeline-1") -> None:
        self.uid = uid
        self.tracks = {
            ("video", 1): [FakeItem("A-roll", 0, 300, "video-1")],
            ("audio", 1): [FakeItem("Mix", 0, 300, "audio-1")],
            ("subtitle", 1): [
                FakeItem("第一句", 30, 60, "subtitle-1"),
                FakeItem("第二句\n換行", 75, 105, "subtitle-2"),
            ],
        }

    def GetName(self):
        return "Episode"

    def GetUniqueId(self):
        return self.uid

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return 300

    def GetSetting(self, name):
        return {"timelineFrameRate": "30", "timelinePlaybackFrameRate": "30"}.get(name)

    def GetTrackCount(self, kind):
        return max((index for track_kind, index in self.tracks if track_kind == kind), default=0)

    def GetItemListInTrack(self, kind, index):
        return self.tracks.get((kind, index), [])

    def GetIsTrackEnabled(self, kind, index):
        return True


class FakeProject:
    def __init__(self, timeline: FakeTimeline) -> None:
        self.timeline = timeline

    def GetName(self):
        return "Episode"

    def GetTimelineCount(self):
        return 1

    def GetTimelineByIndex(self, index):
        return self.timeline if index == 1 else None


class FakeProjectManager:
    def __init__(self, project: FakeProject) -> None:
        self.project = project

    def GetCurrentProject(self):
        return self.project

    def LoadProject(self, name):
        return self.project if name == self.project.GetName() else None


class FakeResolve:
    def __init__(self, timeline: FakeTimeline | None = None) -> None:
        self.timeline = timeline or FakeTimeline()
        self.project = FakeProject(self.timeline)

    def GetProjectManager(self):
        return FakeProjectManager(self.project)


def _request(episode: Path, **overrides) -> EditorialMasterRequest:
    values = {
        "episode_root": episode,
        "project_name": "Episode",
        "timeline_name": "Episode",
        "expected_timeline_uid": "timeline-1",
    }
    values.update(overrides)
    return EditorialMasterRequest(**values)


def _renderer(payload: bytes = b"fake-mp4"):
    def render(_project, _timeline, target: Path) -> Path:
        target.write_bytes(payload)
        return target

    return render


def _probe(_path: Path) -> float:
    return 10.0


def _seal(episode: Path, **overrides):
    request = _request(episode, **overrides)
    return seal_editorial_master(
        request,
        FakeResolve(),
        renderer=_renderer(),
        media_probe=_probe,
        stage5_identity={
            "subtitle_mode": "memo-dual-audit-v1",
            "episode_id": episode.name,
            "subtitle_srt_sha256": "a" * 64,
        },
        human_approved=True,
        approved_by="human:test",
        approved_at="2026-08-22T00:00:00+08:00",
    )


def test_inspect_serializes_subtitles_deterministically(tmp_path: Path) -> None:
    inspected = inspect_timeline(_request(tmp_path), FakeResolve())

    assert inspected.srt_text == (
        "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
        "2\n00:00:02,500 --> 00:00:03,500\n第二句\n換行\n"
    )
    assert inspected.snapshot["timeline"]["uid"] == "timeline-1"
    assert inspected.snapshot["subtitle_cue_count"] == 2


def test_seal_commits_verified_selection_and_identity(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()

    selection = _seal(episode)

    assert selection.video_path == episode / "editorial-master/v1/master.mp4"
    assert selection.srt_path.read_text(encoding="utf-8").startswith("1\n00:00:01,000")
    assert selection.identity() == {
        "contract": "podcast-editorial-master-v1",
        "episode_id": "Episode",
        "content_hash": selection.content_hash,
        "master_media_sha256": selection.receipt["artifacts"]["media"]["sha256"],
        "master_srt_sha256": selection.receipt["artifacts"]["subtitles"]["sha256"],
        "editorial_master_receipt": "editorial-master/v1/EDITORIAL-MASTER.json",
    }
    assert (
        json.loads(selection.snapshot_path.read_text(encoding="utf-8"))["snapshot_sha256"]
        == selection.receipt["timeline"]["snapshot_sha256"]
    )


def test_seal_requires_explicit_human_approval_and_matching_stage5_episode(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    request = _request(episode)

    with pytest.raises(EditorialMasterContractError, match="human approval"):
        seal_editorial_master(
            request,
            FakeResolve(),
            renderer=_renderer(),
            media_probe=_probe,
            stage5_identity={"episode_id": "Episode"},
            human_approved=False,
            approved_by="human:test",
        )
    with pytest.raises(EditorialMasterContractError, match="another episode"):
        seal_editorial_master(
            request,
            FakeResolve(),
            renderer=_renderer(),
            media_probe=_probe,
            stage5_identity={"episode_id": "Other"},
            human_approved=True,
            approved_by="human:test",
        )
    assert not (episode / "editorial-master/v1").exists()


def test_wrong_episode_and_timeline_uid_fail_closed(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    selection = _seal(episode)

    with pytest.raises(EditorialMasterContractError, match="episode ID"):
        verify_editorial_master(episode, expected_episode_id="Other")
    with pytest.raises(EditorialMasterTimelineDriftError, match="UID"):
        verify_editorial_master(episode, expected_timeline_uid="other-uid")
    with pytest.raises(EditorialMasterTimelineDriftError, match="UID"):
        inspect_timeline(_request(episode, expected_timeline_uid="other-uid"), FakeResolve())
    assert selection.video_path.is_file()


@pytest.mark.parametrize("artifact", ["master.mp4", "master.srt"])
def test_media_and_srt_tamper_fail_closed(tmp_path: Path, artifact: str) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    _seal(episode)
    path = episode / "editorial-master/v1" / artifact
    path.write_bytes(path.read_bytes() + b"tamper")

    with pytest.raises(EditorialMasterContractError, match="(size|hash) changed"):
        verify_editorial_master(episode)


def test_cross_episode_and_partial_destination_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "Episode"
    target = tmp_path / "Other"
    source.mkdir()
    target.mkdir()
    _seal(source)
    shutil.copytree(source / "editorial-master", target / "editorial-master")

    with pytest.raises(EditorialMasterContractError, match="another episode"):
        verify_editorial_master(target)

    partial = tmp_path / "Partial"
    (partial / "editorial-master/v1").mkdir(parents=True)
    (partial / "editorial-master/v1/master.mp4").write_bytes(b"partial")
    with pytest.raises(EditorialMasterArtifactConflictError, match="commit marker"):
        verify_editorial_master(partial)


def _rewrite_receipt(receipt_path: Path, mutate) -> None:
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    mutate(payload)
    payload.pop("content_hash")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["content_hash"] = hashlib.sha256(canonical).hexdigest()
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_path_escape_fails_even_with_self_consistent_receipt(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    selection = _seal(episode)
    _rewrite_receipt(
        selection.receipt_path,
        lambda payload: payload["artifacts"]["media"].update(
            {"path": "../Other/editorial-master/v1/master.mp4"}
        ),
    )

    with pytest.raises(EditorialMasterContractError, match="escapes episode root"):
        verify_editorial_master(episode)


def test_live_timeline_drift_fails_closed(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    _seal(episode)
    changed = FakeResolve()
    changed.timeline.tracks[("video", 1)][0]._end = 299
    live = inspect_timeline(_request(episode), changed).snapshot

    with pytest.raises(EditorialMasterTimelineDriftError, match="live Resolve"):
        verify_editorial_master(episode, live_snapshot=live)


def test_identical_rerun_is_idempotent_without_render(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    first = _seal(episode)
    before = {path.name: path.read_bytes() for path in first.receipt_path.parent.iterdir()}

    def forbidden_renderer(_project, _timeline, _target):
        raise AssertionError("idempotent seal must not render again")

    second = seal_editorial_master(
        _request(episode),
        FakeResolve(),
        renderer=forbidden_renderer,
        media_probe=_probe,
        stage5_identity={
            "subtitle_mode": "memo-dual-audit-v1",
            "episode_id": "Episode",
            "subtitle_srt_sha256": "a" * 64,
        },
        human_approved=True,
        approved_by="human:test",
    )

    assert second.content_hash == first.content_hash
    assert {path.name: path.read_bytes() for path in second.receipt_path.parent.iterdir()} == before


def test_existing_master_with_different_stage5_identity_fails_closed(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    _seal(episode)

    with pytest.raises(EditorialMasterArtifactConflictError, match="Stage 5"):
        seal_editorial_master(
            _request(episode),
            FakeResolve(),
            renderer=_renderer(b"different"),
            media_probe=_probe,
            stage5_identity={
                "subtitle_mode": "memo-dual-audit-v1",
                "episode_id": "Episode",
                "subtitle_srt_sha256": "c" * 64,
            },
            human_approved=True,
            approved_by="human:test",
        )


def test_existing_master_with_live_timeline_drift_fails_before_render(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    _seal(episode)
    changed = FakeResolve()
    changed.timeline.tracks[("video", 1)][0]._end = 299

    with pytest.raises(EditorialMasterTimelineDriftError, match="live Resolve"):
        seal_editorial_master(
            _request(episode),
            changed,
            renderer=lambda *_args: pytest.fail("drift must fail before render"),
            media_probe=_probe,
            stage5_identity={
                "subtitle_mode": "memo-dual-audit-v1",
                "episode_id": "Episode",
                "subtitle_srt_sha256": "a" * 64,
            },
            human_approved=True,
            approved_by="human:test",
        )


def test_failed_render_never_publishes_partial_v1(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()

    def failed_renderer(_project, _timeline, target):
        target.write_bytes(b"partial")
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        seal_editorial_master(
            _request(episode),
            FakeResolve(),
            renderer=failed_renderer,
            media_probe=_probe,
            stage5_identity={"episode_id": "Episode"},
            human_approved=True,
            approved_by="human:test",
        )
    assert not (episode / "editorial-master/v1").exists()
    assert not list((episode / "editorial-master").glob(".v1.staging-*"))


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (50, 80, "overlap"),
        (90, 90, "non-positive"),
        (290, 310, "outside timeline"),
    ],
)
def test_subtitle_timing_qc_rejects_invalid_cues(
    tmp_path: Path, start: int, end: int, message: str
) -> None:
    resolve = FakeResolve()
    resolve.timeline.tracks[("subtitle", 1)][1]._start = start
    resolve.timeline.tracks[("subtitle", 1)][1]._end = end

    with pytest.raises(EditorialMasterContractError, match=message):
        inspect_timeline(_request(tmp_path), resolve)


def test_receipt_records_zero_subtitle_timing_defects(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    selection = _seal(episode)

    assert selection.receipt["artifacts"]["subtitles"]["timing_qc"] == {
        "non_positive_duration_count": 0,
        "out_of_timeline_count": 0,
        "overlap_count": 0,
    }


def test_default_media_probe_requires_video_and_audio_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    monkeypatch.setattr(
        editorial_master_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"format": {"duration": "10.0"}, "streams": [{"codec_type": "video"}]}
            ),
        ),
    )

    with pytest.raises(EditorialMasterContractError, match="video and audio"):
        seal_editorial_master(
            _request(episode),
            FakeResolve(),
            renderer=_renderer(),
            stage5_identity={"episode_id": "Episode"},
            human_approved=True,
            approved_by="human:test",
        )
    assert not list((episode / "editorial-master").glob(".v1.staging-*"))


def test_orphan_staging_directory_is_never_ready(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    orphan = episode / "editorial-master/.v1.staging-orphan"
    orphan.mkdir(parents=True)
    (orphan / "EDITORIAL-MASTER.json").write_text("{}", encoding="utf-8")

    assert editorial_master_module.editorial_master_status(episode)["status"] == "missing"
    with pytest.raises(EditorialMasterContractError, match="missing"):
        verify_editorial_master(episode)


def test_cli_seal_fresh_opens_official_stage5_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    observed = {}

    class FakeStage5Selection:
        def identity(self):
            return {"episode_id": "Episode", "subtitle_srt_sha256": "b" * 64}

    class FakeStage5Request:
        def __init__(self, *, subtitle_release_handoff=None):
            observed["handoff"] = subtitle_release_handoff

        def open(self, root):
            observed["opened"] = Path(root)
            return FakeStage5Selection()

    class FakeMasterSelection:
        def identity(self):
            return {"contract": "podcast-editorial-master-v1"}

    def fake_seal(request, resolve, **kwargs):
        observed["request"] = request
        observed["resolve"] = resolve
        observed["stage5"] = kwargs["stage5_identity"]
        return FakeMasterSelection()

    resolve = object()
    monkeypatch.setattr(editorial_master_cli, "Stage5SubtitleRequest", FakeStage5Request)
    monkeypatch.setattr(editorial_master_cli, "_connect_resolve", lambda: resolve)
    monkeypatch.setattr(editorial_master_cli, "seal_editorial_master", fake_seal)

    result = editorial_master_cli.main(
        ["seal", str(episode), "--human-approved", "--approved-by", "human:test"]
    )

    assert result == 0
    assert observed["opened"] == episode
    assert observed["handoff"] is None
    assert observed["stage5"]["subtitle_srt_sha256"] == "b" * 64
    assert observed["resolve"] is resolve


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["project"].update({"name": "Forged Project"}),
        lambda payload: payload["timeline"].update({"name": "Forged Timeline"}),
        lambda payload: payload["timeline"].update({"uid": "forged-uid"}),
        lambda payload: payload["timeline"].update({"fps": "24"}),
        lambda payload: payload["timeline"].update({"start_frame": 1}),
        lambda payload: payload["timeline"].update({"end_frame": 299}),
        lambda payload: payload["timeline"].update({"duration_frames": 299}),
        lambda payload: payload["timeline"].update({"duration_sec": 9.966}),
    ],
)
def test_forged_signed_receipt_timeline_fields_fail_against_snapshot(
    tmp_path: Path, mutate
) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    selection = _seal(episode)
    _rewrite_receipt(selection.receipt_path, mutate)

    with pytest.raises(EditorialMasterTimelineDriftError, match="snapshot"):
        verify_editorial_master(episode)


def test_forged_signed_receipt_subtitle_count_fails_against_snapshot(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    selection = _seal(episode)
    _rewrite_receipt(
        selection.receipt_path,
        lambda payload: payload["artifacts"]["subtitles"].update({"cue_count": 999}),
    )

    with pytest.raises(EditorialMasterContractError, match="cue count"):
        verify_editorial_master(episode)


def test_rehashed_overlapping_srt_still_fails_timing_qc(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    selection = _seal(episode)
    overlapping = selection.srt_path.read_text(encoding="utf-8").replace(
        "00:00:02,500 --> 00:00:03,500",
        "00:00:01,500 --> 00:00:03,500",
    )
    selection.srt_path.write_text(overlapping, encoding="utf-8", newline="\n")

    def resign(payload):
        record = payload["artifacts"]["subtitles"]
        content = selection.srt_path.read_bytes()
        record["bytes"] = len(content)
        record["sha256"] = hashlib.sha256(content).hexdigest()

    _rewrite_receipt(selection.receipt_path, resign)

    with pytest.raises(EditorialMasterContractError, match="timing QC"):
        verify_editorial_master(episode)


def test_rehashed_resigned_srt_text_rewrite_fails_against_snapshot(tmp_path: Path) -> None:
    episode = tmp_path / "Episode"
    episode.mkdir()
    selection = _seal(episode)
    rewritten = selection.srt_path.read_text(encoding="utf-8").replace("第一句", "改寫句")
    selection.srt_path.write_text(rewritten, encoding="utf-8", newline="\n")

    def resign(payload):
        record = payload["artifacts"]["subtitles"]
        content = selection.srt_path.read_bytes()
        record["bytes"] = len(content)
        record["sha256"] = hashlib.sha256(content).hexdigest()

    _rewrite_receipt(selection.receipt_path, resign)

    with pytest.raises(EditorialMasterContractError, match="cue.*snapshot"):
        verify_editorial_master(episode)
