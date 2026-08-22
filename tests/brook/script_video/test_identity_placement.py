from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from agents.brook.script_video.identity_placement import (
    IdentityPlacementError,
    accept_identity_placement,
    emit_guest_namecard_recipe,
    verify_guest_namecard_recipe,
    verify_identity_placement,
)
from shared.highlight_materialization import (
    HighlightSource,
    build_materialization_receipt,
    write_materialization_receipt,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class _FakeMaster:
    value: dict[str, object]
    media_path: Path
    srt_path: Path

    def identity(self) -> dict[str, object]:
        return dict(self.value)


def _master(root: Path, *, suffix: str = "a") -> _FakeMaster:
    media = root / "editorial-master" / "v1" / "master.mp4"
    srt = media.with_name("master.srt")
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"master-media")
    srt.write_text("master subtitles", encoding="utf-8")
    selected = _FakeMaster(
        value={
            "contract": "podcast-editorial-master-v1",
            "episode_id": root.name,
            "content_hash": suffix * 64,
            "master_media_sha256": "b" * 64,
            "master_srt_sha256": "c" * 64,
            "editorial_master_receipt": "editorial-master/v1/EDITORIAL-MASTER.json",
        },
        media_path=media,
        srt_path=srt,
    )
    if suffix == "a":
        highlights = root / "highlights"
        highlights.mkdir(exist_ok=True)
        (highlights / "candidates.json").write_text(
            json.dumps(
                {
                    "editorial_master_lineage": selected.identity(),
                    "candidates": [
                        {
                            "id": "value-L01",
                            "format": "long",
                            "t_start": 10.0,
                            "t_end": 100.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (highlights / "winners.json").write_text(
            json.dumps(
                {
                    "editorial_master_lineage": selected.identity(),
                    "winners": [{"id": "value-L01", "rank": 1}],
                }
            ),
            encoding="utf-8",
        )

        class Timeline:
            def GetName(self) -> str:
                return "長1 - fixture"

            def GetUniqueId(self) -> str:
                return "fixture-timeline-uid"

        payload = build_materialization_receipt(
            root,
            cut_id="value-L01",
            cut_format="long",
            timeline=Timeline(),
            source_range={
                "start_sec": 10.0,
                "end_sec": 100.0,
                "start_frame": 300,
                "end_frame": 3000,
            },
            source=HighlightSource(
                srt_path=srt,
                media_path=media,
                lineage=selected.identity(),
            ),
        )
        write_materialization_receipt(root, payload)
    return selected


def _cut(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "20260805-linzhichen"
    cut_dir = root / "highlights" / "identity-placement" / "value-L01"
    cut_dir.mkdir(parents=True)
    srt_dir = root / "highlights" / "srt"
    srt_dir.mkdir(parents=True)
    srt = srt_dir / "value-L01_tight_r001.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\n主持人開場\n\n"
        "2\n00:00:43,000 --> 00:00:45,000\n來賓第一段實質回答\n\n"
        "3\n00:00:45,000 --> 00:00:48,500\n回答繼續\n",
        encoding="utf-8",
        newline="\n",
    )
    return root, cut_dir, srt


def _cue(number: int = 2) -> dict[str, object]:
    if number == 2:
        text = "來賓第一段實質回答"
        start, end = 43.0, 45.0
    else:
        text = "回答繼續"
        start, end = 45.0, 48.5
    return {
        "number": number,
        "start_sec": start,
        "end_sec": end,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _srt_identity(root: Path, srt: Path) -> dict[str, object]:
    return {
        "path": srt.relative_to(root).as_posix(),
        "bytes": srt.stat().st_size,
        "sha256": _sha256(srt),
        "cue_count": 3,
    }


def _write_audit(
    path: Path,
    *,
    root: Path,
    srt: Path,
    master: _FakeMaster,
    worker_id: str,
    cue: dict[str, object] | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "contract": "podcast-identity-placement-worker-audit-v1",
                "episode_id": root.name,
                "cut_id": "value-L01",
                "worker_id": worker_id,
                "editorial_master": master.identity(),
                "cut_srt": _srt_identity(root, srt),
                "accepted_guest_cue": cue or _cue(),
                "verdict": "accept_first_substantive_guest_cue",
                "rationale": "這是來賓開始完整回答，而非主持人開場。",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _accepted(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, _FakeMaster]:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(audit_a, root=root, srt=srt, master=master, worker_id="worker-a")
    _write_audit(audit_b, root=root, srt=srt, master=master, worker_id="worker-b")
    accept_identity_placement(
        root,
        cut_id="value-L01",
        cut_srt=srt,
        audit_a=audit_a,
        audit_b=audit_b,
        editorial_master=master,  # type: ignore[arg-type]
    )
    return root, srt, audit_a, audit_b, master


def test_two_distinct_audits_and_lin_43_second_card_pass(tmp_path: Path) -> None:
    root, _srt, _audit_a, _audit_b, master = _accepted(tmp_path)
    selected = verify_identity_placement(
        root,
        cut_id="value-L01",
        guest_namecard_start=43.0,
        guest_namecard_end=48.2,
        editorial_master=master,  # type: ignore[arg-type]
    )
    assert selected.receipt["contract"] == "podcast-identity-placement-v1"
    assert selected.accepted_guest_cue["number"] == 2
    assert selected.accepted_guest_cue["start_sec"] == 43.0


def test_emit_event_writes_renderer_recipe_at_accepted_cue(tmp_path: Path) -> None:
    root, _srt, _audit_a, _audit_b, master = _accepted(tmp_path)
    event = emit_guest_namecard_recipe(
        root,
        cut_id="value-L01",
        name="林之晨",
        title="《逆分工》共同作者",
        duration_sec=5.2,
        editorial_master=master,  # type: ignore[arg-type]
    )
    assert event["kind"] == "guest-namecard"
    assert event["t0"] == 43.0
    assert event["t1"] == 48.2
    recipe = json.loads(
        (root / "highlights" / "tighten" / "value-L01_broll.json").read_text(
            encoding="utf-8"
        )
    )
    assert recipe["items"] == [event]
    selected = verify_guest_namecard_recipe(
        root, cut_id="value-L01", editorial_master=master  # type: ignore[arg-type]
    )
    assert event["identity_placement"] == selected.identity()


def test_recipe_timestamp_or_lineage_tamper_fails_closed(tmp_path: Path) -> None:
    root, _srt, _audit_a, _audit_b, master = _accepted(tmp_path)
    emit_guest_namecard_recipe(
        root,
        cut_id="value-L01",
        name="林之晨",
        title="《逆分工》共同作者",
        editorial_master=master,  # type: ignore[arg-type]
    )
    path = root / "highlights" / "tighten" / "value-L01_broll.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["items"][0]["t0"] = 8.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IdentityPlacementError, match="before accepted guest speech"):
        verify_guest_namecard_recipe(
            root, cut_id="value-L01", editorial_master=master  # type: ignore[arg-type]
        )

    payload["items"][0]["t0"] = 43.0
    payload["items"][0]["identity_placement"]["content_hash"] = "e" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IdentityPlacementError, match="recipe lineage is stale"):
        verify_guest_namecard_recipe(
            root, cut_id="value-L01", editorial_master=master  # type: ignore[arg-type]
        )


def test_free_string_self_report_is_rejected(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    audit_a.write_text(
        json.dumps({"worker": "A", "report": "guest starts at 43"}), encoding="utf-8"
    )
    _write_audit(audit_b, root=root, srt=srt, master=master, worker_id="worker-b")
    with pytest.raises(IdentityPlacementError, match="fields mismatch"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=srt,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )


def test_same_worker_is_rejected(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(audit_a, root=root, srt=srt, master=master, worker_id="worker-a")
    _write_audit(audit_b, root=root, srt=srt, master=master, worker_id="worker-a")
    with pytest.raises(IdentityPlacementError, match="distinct worker_id"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=srt,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )


def test_different_cues_are_rejected(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(audit_a, root=root, srt=srt, master=master, worker_id="worker-a")
    _write_audit(
        audit_b, root=root, srt=srt, master=master, worker_id="worker-b", cue=_cue(3)
    )
    with pytest.raises(IdentityPlacementError, match="quorum conflict"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=srt,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )


def test_audit_timestamp_drift_is_rejected(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    drifted = _cue()
    drifted["start_sec"] = 43.001
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(
        audit_a, root=root, srt=srt, master=master, worker_id="worker-a", cue=drifted
    )
    _write_audit(audit_b, root=root, srt=srt, master=master, worker_id="worker-b")
    with pytest.raises(IdentityPlacementError, match="timestamp/text identity drift"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=srt,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )


def test_stale_srt_and_stale_master_fail_closed(tmp_path: Path) -> None:
    root, srt, _audit_a, _audit_b, master = _accepted(tmp_path)
    srt.write_text(srt.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(IdentityPlacementError, match="byte size drift|hash drift"):
        verify_identity_placement(
            root,
            cut_id="value-L01",
            editorial_master=master,  # type: ignore[arg-type]
        )

    root2, _srt2, _a2, _b2, _master2 = _accepted(tmp_path / "second")
    with pytest.raises(IdentityPlacementError, match="Master identity is stale"):
        verify_identity_placement(
            root2,
            cut_id="value-L01",
            editorial_master=_master(root2, suffix="d"),  # type: ignore[arg-type]
        )


def test_cross_episode_or_path_escape_is_rejected(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    audit_a = tmp_path / "outside-audit.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(audit_a, root=root, srt=srt, master=master, worker_id="worker-a")
    _write_audit(audit_b, root=root, srt=srt, master=master, worker_id="worker-b")
    with pytest.raises(IdentityPlacementError, match="escapes episode root"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=srt,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )

    other = tmp_path / "other-episode" / "highlights" / "srt"
    other.mkdir(parents=True)
    other_srt = other / "value-L01_tight_r001.srt"
    other_srt.write_text(srt.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(IdentityPlacementError, match="escapes episode root"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=other_srt,
            audit_a=audit_b,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )


def test_cut_srt_basename_must_bind_exact_cut_and_revision(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    wrong = srt.with_name("punch-L04_tight_r001.srt")
    wrong.write_bytes(srt.read_bytes())
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(audit_a, root=root, srt=wrong, master=master, worker_id="worker-a")
    _write_audit(audit_b, root=root, srt=wrong, master=master, worker_id="worker-b")
    with pytest.raises(IdentityPlacementError, match="exact canonical"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=wrong,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )


def test_older_tight_srt_revision_is_rejected(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    latest = srt.with_name("value-L01_tight_r002.srt")
    latest.write_bytes(srt.read_bytes())
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(audit_a, root=root, srt=srt, master=master, worker_id="worker-a")
    _write_audit(audit_b, root=root, srt=srt, master=master, worker_id="worker-b")
    with pytest.raises(IdentityPlacementError, match="not the latest canonical"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=srt,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )


def test_nonwinner_and_stale_shortlist_lineage_are_rejected(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(audit_a, root=root, srt=srt, master=master, worker_id="worker-a")
    _write_audit(audit_b, root=root, srt=srt, master=master, worker_id="worker-b")
    winners = root / "highlights" / "winners.json"
    payload = json.loads(winners.read_text(encoding="utf-8"))
    payload["winners"] = []
    winners.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IdentityPlacementError, match="must be one exact winner"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=srt,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )

    root2, cut_dir2, srt2 = _cut(tmp_path / "stale")
    master2 = _master(root2)
    candidates = root2 / "highlights" / "candidates.json"
    stale = json.loads(candidates.read_text(encoding="utf-8"))
    stale["editorial_master_lineage"]["content_hash"] = "d" * 64
    candidates.write_text(json.dumps(stale), encoding="utf-8")
    audit_a2 = cut_dir2 / "identity-audit-a.json"
    audit_b2 = cut_dir2 / "identity-audit-b.json"
    _write_audit(audit_a2, root=root2, srt=srt2, master=master2, worker_id="worker-a")
    _write_audit(audit_b2, root=root2, srt=srt2, master=master2, worker_id="worker-b")
    with pytest.raises(IdentityPlacementError, match="lineage is stale"):
        accept_identity_placement(
            root2,
            cut_id="value-L01",
            cut_srt=srt2,
            audit_a=audit_a2,
            audit_b=audit_b2,
            editorial_master=master2,  # type: ignore[arg-type]
        )


def test_tampered_materialization_is_rejected(tmp_path: Path) -> None:
    root, cut_dir, srt = _cut(tmp_path)
    master = _master(root)
    receipt = root / "highlights" / "materialization" / "value-L01.json"
    tampered = json.loads(receipt.read_text(encoding="utf-8"))
    tampered["source_range"]["start_sec"] = 11.0
    receipt.write_text(json.dumps(tampered), encoding="utf-8")
    audit_a = cut_dir / "identity-audit-a.json"
    audit_b = cut_dir / "identity-audit-b.json"
    _write_audit(audit_a, root=root, srt=srt, master=master, worker_id="worker-a")
    _write_audit(audit_b, root=root, srt=srt, master=master, worker_id="worker-b")
    with pytest.raises(IdentityPlacementError, match="materialization is not valid"):
        accept_identity_placement(
            root,
            cut_id="value-L01",
            cut_srt=srt,
            audit_a=audit_a,
            audit_b=audit_b,
            editorial_master=master,  # type: ignore[arg-type]
        )

@pytest.mark.parametrize(
    ("start", "message"),
    [(8.0, "before accepted guest speech"), (45.1, "drifted beyond accepted guest cue")],
)
def test_guest_card_timestamp_drift_fails_closed(
    tmp_path: Path, start: float, message: str
) -> None:
    root, _srt, _audit_a, _audit_b, master = _accepted(tmp_path)
    with pytest.raises(IdentityPlacementError, match=message):
        verify_identity_placement(
            root,
            cut_id="value-L01",
            guest_namecard_start=start,
            guest_namecard_end=start + 5.2,
            editorial_master=master,  # type: ignore[arg-type]
        )
