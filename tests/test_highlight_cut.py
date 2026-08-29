"""run_highlight_cut 純函數測試（variant 分組——去重移到評分後的機制）。"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_highlight_cut import (
    _open_highlight_source,
    _variant_groups,
    build_materialization_receipt,
    main,
    materialize,
    merge_miners,
    mining_input,
    validate,
    verify_materialization_receipt,
    write_materialization_receipt,
)

from agents.brook.script_video.editorial_master import (
    EditorialMasterContractError,
    EditorialMasterRequest,
)
from agents.brook.script_video.subtitle_handoff import (
    Stage5SubtitleContractError,
    Stage5SubtitleRequest,
)
from tests.brook.script_video.test_editorial_master import _seal


@dataclass(frozen=True)
class _FakeMasterSelection:
    srt_path: Path
    media_path: Path
    lineage: dict[str, object]

    def identity(self) -> dict[str, object]:
        return dict(self.lineage)


@dataclass(frozen=True)
class _FakeMasterRequest:
    selection: _FakeMasterSelection

    def open(self) -> _FakeMasterSelection:
        return self.selection


def _master_fixture(
    episode: Path,
    *,
    cue_count: int = 600,
    removed_text: str | None = None,
) -> _FakeMasterRequest:
    master = episode / "editorial-master" / "v1"
    master.mkdir(parents=True, exist_ok=True)
    blocks = []
    for cue in range(1, cue_count + 1):
        start = cue * 2
        end = start + 1
        text = f"official cue {cue}"
        if removed_text and cue == cue_count:
            text = removed_text
        blocks.append(
            f"{cue}\n00:{start // 60:02d}:{start % 60:02d},000 --> "
            f"00:{end // 60:02d}:{end % 60:02d},000\n{text}"
        )
    srt = master / "master.srt"
    srt.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    media = master / "master.mp4"
    media.write_bytes(b"mastered-program-feed")
    lineage = {
        "contract": "podcast-editorial-master-v1",
        "episode_id": episode.name,
        "content_hash": "1" * 64,
        "master_media_sha256": "2" * 64,
        "master_srt_sha256": __import__("hashlib").sha256(srt.read_bytes()).hexdigest(),
        "editorial_master_receipt": "editorial-master/v1/EDITORIAL-MASTER.json",
    }
    return _FakeMasterRequest(_FakeMasterSelection(srt, media, lineage))


def _c(cid, fmt, s, e):
    return {"id": cid, "format": fmt, "t_start": s, "t_end": e}


def test_overlapping_same_format_grouped():
    # story-L3(1015-1695) 包住 util-L2(1203-1695) → 同群組（2026-07-26 教訓案例）
    cands = [_c("L3", "long", 1015, 1695), _c("L2", "long", 1203, 1695), _c("LX", "long", 0, 500)]
    g = _variant_groups(cands)
    assert g["L3"] == g["L2"]
    assert g["LX"] != g["L3"]


def test_highlight_reexports_the_single_shared_materialization_authority() -> None:
    import shared.highlight_materialization as authority

    assert build_materialization_receipt is authority.build_materialization_receipt
    assert write_materialization_receipt is authority.write_materialization_receipt
    assert verify_materialization_receipt is authority.verify_materialization_receipt


def test_cross_format_never_grouped():
    # 長短片同範圍是不同產品，不歸同群
    cands = [_c("L1", "long", 100, 700), _c("S1", "short", 100, 180)]
    g = _variant_groups(cands)
    assert g["L1"] != g["S1"]


def test_chain_grouping_transitive():
    # A-B 重疊、B-C 重疊 → 三者同群（連通分量）
    cands = [_c("A", "short", 0, 100), _c("B", "short", 40, 140), _c("C", "short", 80, 180)]
    g = _variant_groups(cands)
    assert g["A"] == g["B"] == g["C"]


def test_small_overlap_not_grouped():
    # 重疊 40%（相對較短者）→ 不同群
    cands = [_c("A", "short", 0, 100), _c("B", "short", 65, 200)]
    g = _variant_groups(cands)
    assert g["A"] != g["B"]


def _miner_candidate(
    candidate_id: str,
    *,
    miner: str,
    fmt: str,
    cue_start: int,
    cue_end: int,
) -> dict:
    sections: list[dict] = []
    if fmt == "long":
        boundaries = [cue_start]
        span = cue_end - cue_start + 1
        boundaries.extend(cue_start + span * index // 3 for index in (1, 2))
        boundaries.append(cue_end + 1)
        sections = [
            {
                "section_id": f"section-{index + 1:02d}",
                "cue_start": start,
                "cue_end": boundaries[index + 1] - 1,
                "start_quote": f"official cue {start}",
                "end_quote": f"official cue {boundaries[index + 1] - 1}",
                "summary": f"semantic section {index + 1}",
                "transition_before": index > 0,
                "transition_title": f"Section {index + 1}" if index > 0 else None,
            }
            for index, start in enumerate(boundaries[:-1])
        ]
    return {
        "id": candidate_id,
        "format": fmt,
        "t_start": float(cue_start * 2),
        "t_end": float(cue_end * 2 + 1),
        "title": f"{candidate_id} title",
        "hook": f"official cue {cue_start}",
        "rationale": f"{candidate_id} rationale",
        "miner": miner,
        "head_trim": None,
        "cue_start": cue_start,
        "cue_end": cue_end,
        "sections": sections,
    }


def _miner_outputs(episode: Path) -> tuple[_FakeMasterRequest, dict[str, Path]]:
    request = _master_fixture(episode)
    lineage = request.open().identity()
    highlights = episode / "highlights"
    highlights.mkdir()
    paths: dict[str, Path] = {}
    for role in ("story", "punch", "value"):
        candidates = [
            _miner_candidate(
                f"{role}-L{number:02d}",
                miner=role,
                fmt="long",
                cue_start=cue_start,
                cue_end=cue_end,
            )
            for number, (cue_start, cue_end) in enumerate(
                ((1, 181), (61, 241), (121, 301)),
                start=1,
            )
        ]
        candidates.extend(
            _miner_candidate(
                f"{role}-S{number:02d}",
                miner=role,
                fmt="short",
                cue_start=cue_start,
                cue_end=cue_end,
            )
            for number, (cue_start, cue_end) in enumerate(
                ((401, 421), (431, 451), (461, 481)),
                start=1,
            )
        )
        path = highlights / f"miner-{role}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "contract": "podcast-highlight-miner-output-v2",
                    "miner_role": role,
                    "source_srt_sha256": lineage["master_srt_sha256"],
                    "editorial_master_lineage": lineage,
                    "candidates": candidates,
                }
            ),
            encoding="utf-8",
        )
        paths[role] = path
    return request, paths


def test_merge_miners_requires_all_three_outputs(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    paths["value"].rename(paths["value"].with_suffix(".missing"))

    with pytest.raises(Stage5SubtitleContractError, match="missing or invalid"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_one_candidate_per_role(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    for path in paths.values():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["candidates"] = payload["candidates"][:1]
        path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="at least 3 long and 3 short"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_ten_second_long_candidate(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["story"].read_text(encoding="utf-8"))
    payload["candidates"][0].update({"cue_start": 1, "cue_end": 6, "t_start": 2.0, "t_end": 13.0})
    paths["story"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="outside long duration tolerance"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_hallucinated_hook(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["value"].read_text(encoding="utf-8"))
    payload["candidates"][0]["hook"] = "this sentence is not in the official SRT"
    paths["value"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="hook is not a raw transcript substring"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_long_section_gap(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["value"].read_text(encoding="utf-8"))
    payload["candidates"][0]["sections"][1]["cue_start"] += 1
    paths["value"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="cover cues contiguously"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_hallucinated_section_quote(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["story"].read_text(encoding="utf-8"))
    payload["candidates"][0]["sections"][0]["start_quote"] = "fabricated"
    paths["story"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="section quote differs"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_role_or_format_id_drift(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["story"].read_text(encoding="utf-8"))
    payload["candidates"][0]["id"] = "punch-L01"
    paths["story"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="role and format"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_candidate_miner_role_drift(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["value"].read_text(encoding="utf-8"))
    payload["candidates"][0]["miner"] = "story"
    paths["value"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="miner field differs"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_timing_that_is_not_exact_cue_boundary(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["punch"].read_text(encoding="utf-8"))
    payload["candidates"][0]["t_start"] += 0.25
    paths["punch"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="differs from cue boundaries"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_short_below_tolerance(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["story"].read_text(encoding="utf-8"))
    payload["candidates"][3].update(
        {"cue_start": 401, "cue_end": 405, "t_start": 802.0, "t_end": 811.0}
    )
    paths["story"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="outside short duration tolerance"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_failure_preserves_existing_candidates_transaction(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    candidates_path = episode / "highlights" / "candidates.json"
    original = b'{"known":"good"}\n'
    candidates_path.write_bytes(original)
    payload = json.loads(paths["value"].read_text(encoding="utf-8"))
    payload["candidates"][0]["hook"] = "fabricated"
    paths["value"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="raw transcript substring"):
        merge_miners(episode, editorial_master_request=request)

    assert candidates_path.read_bytes() == original


def test_merge_miners_rejects_wrong_source_srt(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["punch"].read_text(encoding="utf-8"))
    payload["source_srt_sha256"] = "0" * 64
    paths["punch"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="source/lineage drift"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_duplicate_ids_within_worker(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["story"].read_text(encoding="utf-8"))
    payload["candidates"].append(payload["candidates"][0])
    paths["story"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="duplicated"):
        merge_miners(episode, editorial_master_request=request)


def test_merge_miners_rejects_partial_candidate_schema(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["value"].read_text(encoding="utf-8"))
    payload["candidates"][0].pop("cue_end")
    paths["value"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="schema drift"):
        merge_miners(episode, editorial_master_request=request)


def test_valid_three_miners_write_candidates_then_validate(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, _paths = _miner_outputs(episode)

    merged = merge_miners(episode, editorial_master_request=request)
    validated = validate(episode, editorial_master_request=request)
    candidates = json.loads(
        (episode / "highlights" / "candidates.json").read_text(encoding="utf-8")
    )

    assert merged["candidate_count"] == 18
    assert merged["long_candidate_count"] == 9
    assert validated["kept"] == {"long": 9, "short": 9}
    assert candidates["contract"] == "podcast-highlight-candidates-v2"
    assert candidates["editorial_master_lineage"] == request.open().identity()
    assert [item["id"] for item in candidates["candidates"]] == [
        "punch-L01",
        "story-L01",
        "value-L01",
        "punch-L02",
        "story-L02",
        "value-L02",
        "punch-L03",
        "story-L03",
        "value-L03",
        "punch-S01",
        "story-S01",
        "value-S01",
        "punch-S02",
        "story-S02",
        "value-S02",
        "punch-S03",
        "story-S03",
        "value-S03",
    ]


def test_merge_miners_cli_runs_strict_merge_then_validate(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, _paths = _miner_outputs(episode)
    # Projection tests deliberately evict/re-import ``run_highlight_cut``.  Patch
    # the globals actually used by this already-imported ``main`` function so
    # collection order cannot redirect the fake to a different module object.
    monkeypatch.setitem(
        main.__globals__,
        "EditorialMasterRequest",
        lambda **_kwargs: request,
    )

    assert main([str(episode), "--merge-miners"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["candidate_count"] == 18
    assert result["validation"]["kept"] == {"long": 9, "short": 9}


def test_default_source_fails_closed_even_when_raw_files_exist(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    (episode / "Default_2026-01-01_1.mp4").write_bytes(b"raw")
    (episode / "normalized.wav").write_bytes(b"raw-audio")
    (episode / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nraw cough\n",
        encoding="utf-8",
    )

    with pytest.raises(EditorialMasterContractError, match="Editorial Master is missing"):
        mining_input(episode)


def test_default_source_rejects_tampered_master_srt(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    selected = _seal(episode)
    selected.srt_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(EditorialMasterContractError, match="(size|hash) changed"):
        mining_input(episode)


def test_default_source_rejects_cross_episode_receipt(tmp_path: Path) -> None:
    first = tmp_path / "episode-a"
    second = tmp_path / "episode-b"
    first.mkdir()
    second.mkdir()
    _seal(first)
    shutil.copytree(first / "editorial-master", second / "editorial-master")

    with pytest.raises(EditorialMasterContractError, match="another episode"):
        mining_input(second)


def test_default_source_rejects_stale_expected_content_hash(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    _seal(episode)

    with pytest.raises(EditorialMasterContractError, match="content identity mismatch"):
        mining_input(
            episode,
            editorial_master_request=EditorialMasterRequest(
                episode_root=episode,
                expected_content_hash="0" * 64,
            ),
        )


def test_mining_input_exposes_master_srt_and_media_not_raw(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    selected = _seal(episode)
    (episode / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n咳嗽 抱歉\n",
        encoding="utf-8",
    )
    (episode / "Default_2026-01-01_1.mp4").write_bytes(b"raw")
    (episode / "normalized.wav").write_bytes(b"raw-audio")

    result = mining_input(episode)

    assert Path(result["srt_path"]) == selected.srt_path
    assert Path(result["media_path"]) == selected.media_path
    assert "咳嗽" not in selected.srt_path.read_text(encoding="utf-8")
    assert result["content_hash"] == selected.content_hash


def test_stage5_request_is_not_an_implicit_production_fallback(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()

    with pytest.raises(EditorialMasterContractError, match="Stage5-only"):
        mining_input(episode, subtitle_request=Stage5SubtitleRequest())


def test_explicit_legacy_v1_remains_forensic_only(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    legacy_srt = episode / "transcript.srt"
    legacy_srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nlegacy forensic\n",
        encoding="utf-8",
    )

    result = mining_input(
        episode,
        subtitle_request=Stage5SubtitleRequest(legacy_v1=True),
    )

    assert Path(result["srt_path"]) == legacy_srt
    assert result["media_path"] is None
    assert result["subtitle_mode"] == "legacy-v1"


def test_validate_hard_fails_outside_duration_tolerance(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, _paths = _miner_outputs(episode)
    merge_miners(episode, editorial_master_request=request)
    candidates_path = episode / "highlights" / "candidates.json"
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    payload["candidates"][0].update({"t_start": 2.0, "t_end": 13.0})
    candidates_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="outside long duration tolerance"):
        validate(episode, editorial_master_request=request)


def test_validate_cannot_promote_raw_candidates_to_master_lineage(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request = _master_fixture(episode)
    highlights = episode / "highlights"
    highlights.mkdir()
    candidates = highlights / "candidates.json"
    original = json.dumps(
        {
            "subtitle_lineage": {"subtitle_mode": "legacy-v1"},
            "candidates": [_c("L1", "long", 2.0, 363.0)],
        }
    ).encode()
    candidates.write_bytes(original)

    with pytest.raises(EditorialMasterContractError, match="editorial_master_lineage"):
        validate(episode, editorial_master_request=request)

    assert candidates.read_bytes() == original


def test_materialize_appends_exact_master_as_linked_audio_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request = _master_fixture(episode)
    selection = request.open()
    highlights = episode / "highlights"
    highlights.mkdir()
    candidate = {
        **_c("value-L01", "long", 2.0, 363.0),
        "title": "master only",
        "hook": "official cue 1",
    }
    lineage = selection.identity()
    (highlights / "candidates.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": lineage,
                "candidates": [candidate],
            }
        ),
        encoding="utf-8",
    )
    (highlights / "winners.json").write_text(
        json.dumps(
            {
                "editorial_master_lineage": lineage,
                "winners": [{"id": "value-L01", "rank": 1}],
            }
        ),
        encoding="utf-8",
    )

    class Clip:
        def __init__(self, name: str, path: Path | None = None) -> None:
            self.name = name
            self.path = path

        def GetName(self):
            return self.name

        def GetClipProperty(self, key):
            return str(self.path) if key == "File Path" and self.path else ""

    class Folder:
        def __init__(self) -> None:
            self.clips = [
                Clip("Default_2026-01-01_1.mp4", episode / "Default_2026-01-01_1.mp4"),
                Clip("normalized.wav", episode / "normalized.wav"),
            ]
            self.subfolders = []

        def GetClipList(self):
            return self.clips

        def GetSubFolderList(self):
            return self.subfolders

        def GetName(self):
            return "root"

    class Timeline:
        def __init__(self, name: str) -> None:
            self.name = name
            self.uid = f"uid-{name}"
            self.video_items = []
            self.audio_items = []
            self.subtitle_items = []

        def GetName(self):
            return self.name

        def GetUniqueId(self):
            return self.uid

        def SetName(self, name):
            self.name = name

        def GetTrackCount(self, kind):
            return 1 if kind in {"video", "audio", "subtitle"} else 0

        def AddTrack(self, _kind):
            return True

        def GetItemListInTrack(self, kind, _index):
            if kind == "video":
                return self.video_items
            if kind == "audio":
                return self.audio_items
            return self.subtitle_items

        def GetStartFrame(self):
            return 0

        def SetSetting(self, *_args):
            return True

    class MediaPool:
        def __init__(self, project) -> None:
            self.project = project
            self.root = Folder()
            self.append_specs = []

        def GetRootFolder(self):
            return self.root

        def ImportMedia(self, paths):
            imported = [Clip(Path(path).name, Path(path)) for path in paths]
            self.root.clips.extend(imported)
            return imported

        def AppendToTimeline(self, specs):
            self.append_specs.append(specs)
            if specs and isinstance(specs[0], dict):

                class TrackItem:
                    def __init__(self, media_pool_item) -> None:
                        self.media_pool_item = media_pool_item

                    def GetMediaPoolItem(self):
                        return self.media_pool_item

                item = TrackItem(specs[0]["mediaPoolItem"])
                media_type = specs[0].get("mediaType")
                if media_type in (None, 1):
                    self.project.current.video_items.append(item)
                if media_type in (None, 2):
                    self.project.current.audio_items.append(item)
            else:
                self.project.current.subtitle_items.append(object())
            return [object()]

        def AddSubFolder(self, _root, name):
            folder = Folder()
            folder.GetName = lambda: name
            self.root.subfolders.append(folder)
            return folder

        def SetCurrentFolder(self, _folder):
            return True

        def DeleteTimelines(self, _timelines):
            return True

        def CreateEmptyTimeline(self, name):
            timeline = Timeline(name)
            self.project.timelines.append(timeline)
            return timeline

    class Project:
        def __init__(self) -> None:
            self.timelines = [Timeline(episode.name)]
            self.current = self.timelines[0]
            self.media_pool = MediaPool(self)

        def GetName(self):
            return episode.name

        def GetSetting(self, _key):
            return "30"

        def GetMediaPool(self):
            return self.media_pool

        def GetTimelineCount(self):
            return len(self.timelines)

        def GetTimelineByIndex(self, index):
            return self.timelines[index - 1]

        def GetCurrentTimeline(self):
            return self.current

        def SetCurrentTimeline(self, timeline):
            self.current = timeline
            return True

    project = Project()

    class ProjectManager:
        def GetCurrentProject(self):
            return project

        def LoadProject(self, _name):
            return project

        def SaveProject(self):
            return True

    class Resolve:
        def GetProjectManager(self):
            return ProjectManager()

    import build_resolve_project

    monkeypatch.setattr(build_resolve_project, "connect_resolve", lambda: Resolve())
    monkeypatch.setattr(build_resolve_project, "_template_path", lambda: tmp_path / "missing.drt")
    monkeypatch.setattr(
        build_resolve_project,
        "find_main_video",
        lambda *_args: (_ for _ in ()).throw(AssertionError("raw fallback selected")),
    )

    result = materialize(episode, editorial_master_request=request)

    av_specs = [
        specs[0]
        for specs in project.media_pool.append_specs
        if specs and isinstance(specs[0], dict)
    ]
    assert len(av_specs) == 1
    assert Path(av_specs[0]["mediaPoolItem"].GetClipProperty("File Path")) == selection.media_path
    assert "mediaType" not in av_specs[0], "omitting mediaType preserves linked master A+V"
    assert all(
        spec["mediaPoolItem"].GetName() not in {"normalized.wav", "Default_2026-01-01_1.mp4"}
        for spec in av_specs
    )
    assert result["content_hash"] == lineage["content_hash"]
    assert result["markers"] == 0
    receipt_path = episode / "highlights" / "materialization" / "value-L01.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["timeline"] == {
        "name": "長1 - master only",
        "uid": "uid-長1 - master only",
    }
    assert receipt["editorial_master_lineage"] == lineage


def test_materialization_receipt_is_idempotent_and_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    source = _open_highlight_source(
        episode,
        editorial_master_request=_master_fixture(episode),
    )

    class Timeline:
        def GetName(self):
            return "長1 - test"

        def GetUniqueId(self):
            return "timeline-1"

        def GetItemListInTrack(self, _kind, _index):
            class MediaPoolItem:
                def GetClipProperty(_self, key):
                    assert key == "File Path"
                    return str(source.media_path)

            class TrackItem:
                def GetMediaPoolItem(_self):
                    return MediaPoolItem()

            return [TrackItem()]

    receipt = build_materialization_receipt(
        episode,
        cut_id="value-L01",
        cut_format="long",
        timeline=Timeline(),
        source_range={
            "start_sec": 2.0,
            "end_sec": 363.0,
            "start_frame": 60,
            "end_frame": 10890,
        },
        source=source,
    )
    first = write_materialization_receipt(episode, receipt)
    second = write_materialization_receipt(episode, receipt)
    assert first == second
    assert (
        verify_materialization_receipt(
            episode,
            "value-L01",
            source=source,
            timeline=Timeline(),
        )
        == receipt
    )

    class OtherTimeline(Timeline):
        def GetUniqueId(self):
            return "timeline-other"

    changed = build_materialization_receipt(
        episode,
        cut_id="value-L01",
        cut_format="long",
        timeline=OtherTimeline(),
        source_range=receipt["source_range"],
        source=source,
    )
    with pytest.raises(EditorialMasterContractError, match="conflicts"):
        write_materialization_receipt(episode, changed)
    write_materialization_receipt(episode, changed, replace=True)
    assert (
        verify_materialization_receipt(
            episode,
            "value-L01",
            source=source,
            timeline=OtherTimeline(),
        )["timeline"]["uid"]
        == "timeline-other"
    )

    changed_range = build_materialization_receipt(
        episode,
        cut_id="value-L01",
        cut_format="long",
        timeline=OtherTimeline(),
        source_range={
            "start_sec": 3.0,
            "end_sec": 363.0,
            "start_frame": 90,
            "end_frame": 10890,
        },
        source=source,
    )
    with pytest.raises(EditorialMasterContractError, match="source range"):
        write_materialization_receipt(episode, changed_range, replace=True)


def test_materialization_receipt_tamper_is_rejected(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    source = _open_highlight_source(
        episode,
        editorial_master_request=_master_fixture(episode),
    )

    class Timeline:
        def GetName(self):
            return "長1 - test"

        def GetUniqueId(self):
            return "timeline-1"

    receipt = build_materialization_receipt(
        episode,
        cut_id="value-L01",
        cut_format="long",
        timeline=Timeline(),
        source_range={
            "start_sec": 2.0,
            "end_sec": 363.0,
            "start_frame": 60,
            "end_frame": 10890,
        },
        source=source,
    )
    path = write_materialization_receipt(episode, receipt)
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["timeline"]["uid"] = "raw-timeline"
    path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(EditorialMasterContractError, match="content hash mismatch"):
        verify_materialization_receipt(
            episode,
            "value-L01",
            source=source,
        )
