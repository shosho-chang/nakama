"""run_highlight_cut 純函數測試（variant 分組——去重移到評分後的機制）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_highlight_cut import _variant_groups, main, merge_miners, validate

from agents.brook.script_video.subtitle_handoff import (
    Stage5SubtitleContractError,
    Stage5SubtitleRequest,
)
from tests.brook.script_video.test_verified_projection_handoff import (
    _memo_dual_audit_release_fixture,
)


def _c(cid, fmt, s, e):
    return {"id": cid, "format": fmt, "t_start": s, "t_end": e}


def test_overlapping_same_format_grouped():
    # story-L3(1015-1695) 包住 util-L2(1203-1695) → 同群組（2026-07-26 教訓案例）
    cands = [_c("L3", "long", 1015, 1695), _c("L2", "long", 1203, 1695), _c("LX", "long", 0, 500)]
    g = _variant_groups(cands)
    assert g["L3"] == g["L2"]
    assert g["LX"] != g["L3"]


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
    }


def _miner_outputs(episode: Path) -> tuple[Stage5SubtitleRequest, dict[str, Path]]:
    _memo_dual_audit_release_fixture(episode, cue_count=600, actual_cue_count=600)
    request = Stage5SubtitleRequest()
    lineage = request.open(episode).identity()
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
                    "schema_version": 1,
                    "contract": "podcast-highlight-miner-output-v1",
                    "miner_role": role,
                    "source_srt_sha256": lineage["subtitle_srt_sha256"],
                    "subtitle_lineage": lineage,
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
        merge_miners(episode, subtitle_request=request)


def test_merge_miners_rejects_one_candidate_per_role(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    for path in paths.values():
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["candidates"] = payload["candidates"][:1]
        path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="at least 3 long and 3 short"):
        merge_miners(episode, subtitle_request=request)


def test_merge_miners_rejects_ten_second_long_candidate(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["story"].read_text(encoding="utf-8"))
    payload["candidates"][0].update(
        {"cue_start": 1, "cue_end": 6, "t_start": 2.0, "t_end": 13.0}
    )
    paths["story"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="outside long duration tolerance"):
        merge_miners(episode, subtitle_request=request)


def test_merge_miners_rejects_hallucinated_hook(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["value"].read_text(encoding="utf-8"))
    payload["candidates"][0]["hook"] = "this sentence is not in the official SRT"
    paths["value"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="hook is not a raw transcript substring"):
        merge_miners(episode, subtitle_request=request)


def test_merge_miners_rejects_role_or_format_id_drift(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["story"].read_text(encoding="utf-8"))
    payload["candidates"][0]["id"] = "punch-L01"
    paths["story"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="role and format"):
        merge_miners(episode, subtitle_request=request)


def test_merge_miners_rejects_candidate_miner_role_drift(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["value"].read_text(encoding="utf-8"))
    payload["candidates"][0]["miner"] = "story"
    paths["value"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="miner field differs"):
        merge_miners(episode, subtitle_request=request)


def test_merge_miners_rejects_timing_that_is_not_exact_cue_boundary(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["punch"].read_text(encoding="utf-8"))
    payload["candidates"][0]["t_start"] += 0.25
    paths["punch"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="differs from cue boundaries"):
        merge_miners(episode, subtitle_request=request)


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
        merge_miners(episode, subtitle_request=request)


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
        merge_miners(episode, subtitle_request=request)

    assert candidates_path.read_bytes() == original


def test_merge_miners_rejects_wrong_source_srt(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["punch"].read_text(encoding="utf-8"))
    payload["source_srt_sha256"] = "0" * 64
    paths["punch"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="source/lineage drift"):
        merge_miners(episode, subtitle_request=request)


def test_merge_miners_rejects_duplicate_ids_within_worker(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["story"].read_text(encoding="utf-8"))
    payload["candidates"].append(payload["candidates"][0])
    paths["story"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="duplicated"):
        merge_miners(episode, subtitle_request=request)


def test_merge_miners_rejects_partial_candidate_schema(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, paths = _miner_outputs(episode)
    payload = json.loads(paths["value"].read_text(encoding="utf-8"))
    payload["candidates"][0].pop("cue_end")
    paths["value"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="schema drift"):
        merge_miners(episode, subtitle_request=request)


def test_valid_three_miners_write_candidates_then_validate(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, _paths = _miner_outputs(episode)

    merged = merge_miners(episode, subtitle_request=request)
    validated = validate(episode, subtitle_request=request)
    candidates = json.loads(
        (episode / "highlights" / "candidates.json").read_text(encoding="utf-8")
    )

    assert merged["candidate_count"] == 18
    assert merged["long_candidate_count"] == 9
    assert validated["kept"] == {"long": 9, "short": 9}
    assert candidates["contract"] == "podcast-highlight-candidates-v1"
    assert candidates["subtitle_lineage"] == request.open(episode).identity()
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


def test_merge_miners_cli_runs_strict_merge_then_validate(tmp_path: Path, capsys) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    _request, _paths = _miner_outputs(episode)

    assert main([str(episode), "--merge-miners"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["candidate_count"] == 18
    assert result["validation"]["kept"] == {"long": 9, "short": 9}


def test_validate_hard_fails_outside_duration_tolerance(tmp_path: Path) -> None:
    episode = tmp_path / "episode"
    episode.mkdir()
    request, _paths = _miner_outputs(episode)
    merge_miners(episode, subtitle_request=request)
    candidates_path = episode / "highlights" / "candidates.json"
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    payload["candidates"][0].update({"t_start": 2.0, "t_end": 13.0})
    candidates_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Stage5SubtitleContractError, match="outside long duration tolerance"):
        validate(episode, subtitle_request=request)
