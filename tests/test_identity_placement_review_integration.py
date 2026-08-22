from __future__ import annotations

from pathlib import Path

import pytest

from scripts import run_short_review


class _Selection:
    def identity(self) -> dict[str, object]:
        return {
            "contract": "podcast-identity-placement-v1",
            "content_hash": "a" * 64,
        }


def test_review_verifies_every_guest_namecard_before_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, float, float, object]] = []
    master = object()

    def fake_verify(
        _episode: Path,
        *,
        cut_id: str,
        guest_namecard_start: float,
        guest_namecard_end: float,
        editorial_master: object,
    ) -> _Selection:
        calls.append(
            (cut_id, guest_namecard_start, guest_namecard_end, editorial_master)
        )
        return _Selection()

    monkeypatch.setattr(run_short_review, "verify_identity_placement", fake_verify)
    lineage = run_short_review._verify_guest_identity_events(
        tmp_path,
        "value-L01",
        [
            {"type": "hero-title", "t0": 1.0, "t1": 3.0},
            {"type": "guest-namecard", "t0": 43.0, "t1": 48.2},
        ],
        master,
    )
    assert calls == [("value-L01", 43.0, 48.2, master)]
    assert lineage == {
        "contract": "podcast-identity-placement-v1",
        "content_hash": "a" * 64,
    }


def test_review_does_not_add_identity_gate_without_guest_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("identity verifier should not run")

    monkeypatch.setattr(run_short_review, "verify_identity_placement", forbidden)
    assert (
        run_short_review._verify_guest_identity_events(
            tmp_path,
            "value-L01",
            [{"type": "hero-title", "t0": 1.0, "t1": 3.0}],
            object(),
        )
        is None
    )
