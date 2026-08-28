"""Mutable Stage 5 coordination for long-highlight production.

The orchestrator keeps semantic work behind a small runner port.  It coordinates
parallel mining and review, pauses for a human winner, then advances only the
unfinished downstream stages.  State is deliberately editable before approval;
the specialised Director, DP, Resolve, and Packaging implementations remain
adapters rather than being duplicated here.
"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

RUN_STATUSES = {"pending", "running", "needs_review", "approved", "failed"}
MINERS = ("story", "punch", "value")
REVIEWERS = ("azhe", "kevin", "shufen", "renee")


class StageRunner(Protocol):
    """Port implemented by fixture, agent, or production stage adapters."""

    def run(
        self,
        stage: str,
        *,
        event_id: str | None,
        payload: dict[str, Any],
    ) -> Mapping[str, Any]: ...


class StagePending(RuntimeError):
    """A stage request exists but no response is available yet."""


@dataclass(frozen=True)
class SourceInput:
    episode_id: str
    srt_path: Path
    media_path: Path
    context_refs: tuple[str, ...] = ()


class DirectoryStageRunner:
    """File-backed adapter for externally completed semantic stage requests.

    No process or network is started.  Each call writes a replaceable request and
    consumes a JSON response if one is already present.  ``resume`` can therefore
    continue after an agent or another adapter supplies that response.
    """

    def __init__(self, exchange_dir: Path) -> None:
        self.exchange_dir = Path(exchange_dir)

    def run(
        self,
        stage: str,
        *,
        event_id: str | None,
        payload: dict[str, Any],
    ) -> Mapping[str, Any]:
        suffix = _safe_name(event_id or "all")
        request_path = self.exchange_dir / "requests" / stage / f"{suffix}.json"
        response_path = self.exchange_dir / "responses" / stage / f"{suffix}.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if not response_path.is_file():
            raise StagePending(f"waiting for {stage}/{event_id or 'all'}")
        value = json.loads(response_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{response_path} must contain a JSON object")
        return value


class LongHighlightOrchestrator:
    """Small mutable state machine for one Stage 5 long-highlight run."""

    def __init__(self, state_path: Path, runner: StageRunner) -> None:
        self.state_path = Path(state_path)
        self.runner = runner

    @classmethod
    def create(
        cls,
        state_path: Path,
        source: SourceInput,
        runner: StageRunner,
    ) -> LongHighlightOrchestrator:
        instance = cls(state_path, runner)
        state: dict[str, Any] = {
            "schema_version": 1,
            "episode_id": source.episode_id,
            "status": "pending",
            "source": {
                "srt_path": str(Path(source.srt_path)),
                "media_path": str(Path(source.media_path)),
                "context_refs": list(source.context_refs),
                "duration_sec": None,
            },
            "stages": {},
            "candidates": [],
            "quarantine": [],
            "reviews": {},
            "winner": None,
            "human": {"approved": False},
            "refs": {
                "candidates": f"{Path(state_path).name}#/candidates",
                "reviews": f"{Path(state_path).name}#/reviews",
                "winner": f"{Path(state_path).name}#/winner",
                "tighten": None,
                "director": None,
                "dp": None,
                "visual": None,
                "preview": None,
                "packaging": None,
            },
            "warnings": [],
            "retry_queue": [],
            "hard_blocker": None,
        }
        try:
            state["source"]["duration_sec"] = _read_source_duration(source)
        except (OSError, UnicodeError, ValueError) as exc:
            state["status"] = "failed"
            state["hard_blocker"] = "unreadable_source"
            state["warnings"].append(str(exc))
        instance._save(state)
        return instance

    @classmethod
    def load(
        cls,
        state_path: Path,
        runner: StageRunner,
    ) -> LongHighlightOrchestrator:
        instance = cls(state_path, runner)
        instance._load()
        return instance

    def status(self) -> dict[str, Any]:
        return self._load()

    def dry_run(self) -> dict[str, Any]:
        state = self._load()
        return {
            "episode_id": state["episode_id"],
            "current_status": state["status"],
            "parallel_miners": list(MINERS),
            "merge": "tolerant_merge",
            "parallel_reviewers": list(REVIEWERS),
            "human_gate": "winner_approval",
            "downstream": [
                "tighten",
                "director",
                "dp",
                "targeted_visual_review_fix",
                "resolve_preview",
                "packaging",
            ],
        }

    def resume(self) -> dict[str, Any]:
        state = self._load()
        if state["status"] == "failed":
            return state
        state["status"] = "running"
        self._save(state)

        if state["human"]["approved"] and state["winner"]:
            return self._resume_downstream(state)

        if not self._stage_done(state, "miners"):
            self._run_miners(state)
        if not state["candidates"]:
            state["status"] = "running"
            self._save(state)
            return state

        if not self._stage_done(state, "reviews"):
            self._run_reviews(state)

        if not state["human"]["approved"]:
            state["status"] = "needs_review"
            self._save(state)
            return state

        return self._resume_downstream(state)

    def approve_winner(
        self,
        candidate_id: str,
        corrections: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self._load()
        candidate = next((row for row in state["candidates"] if row["id"] == candidate_id), None)
        if candidate is None:
            raise ValueError(f"unknown candidate: {candidate_id}")
        winner = dict(candidate)
        if corrections:
            for key in ("title", "hook", "rationale", "sections", "t_start", "t_end"):
                if key in corrections:
                    winner[key] = corrections[key]
        if not _inside_source(winner, state["source"]["duration_sec"]):
            return self._hard_fail(state, "winner_out_of_range")
        state["winner"] = winner
        state["human"] = {"approved": True, "candidate_id": candidate_id}
        state["status"] = "running"
        self._save(state)
        return self._resume_downstream(state)

    def retry_event(self, stage: str, event_id: str) -> dict[str, Any]:
        state = self._load()
        key = {"stage": stage, "event_id": event_id}
        if key not in state["retry_queue"]:
            raise ValueError(f"event is not queued for retry: {stage}/{event_id}")
        if stage == "visual_fix":
            if not self._fix_visual_event(state, event_id):
                self._save(state)
                return state
        elif stage in {"director", "dp"}:
            if not self._retry_adopted_event(state, stage, event_id):
                self._save(state)
                return state
        else:
            raise ValueError(f"unsupported targeted retry stage: {stage}")
        state["retry_queue"] = [row for row in state["retry_queue"] if row != key]
        stage_key = "visual" if stage == "visual_fix" else stage
        events = state["stages"][stage_key]["events"]
        if all(row["status"] == "approved" for row in events.values()):
            state["stages"][stage_key]["status"] = "approved"
        self._save(state)
        if not state["human"]["approved"]:
            return state
        return self._resume_downstream(state)

    def adopt_existing(
        self,
        *,
        director: Mapping[str, Any] | None = None,
        dp: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Adopt usable semantic rows without executing a production mutation."""

        state = self._load()
        if director is not None:
            events = self._adopt_rows(director.get("events"), kind="director")
            state["stages"]["director"] = {
                "status": _event_stage_status(events),
                "events": events,
            }
            self._queue_pending_events(state, "director", events)
        if dp is not None:
            events = self._adopt_rows(dp.get("events", dp.get("implementations")), kind="dp")
            state["stages"]["dp"] = {
                "status": _event_stage_status(events),
                "events": events,
            }
            self._queue_pending_events(state, "dp", events)
        self._save(state)
        return state

    def adopt_winner(
        self,
        winner: Mapping[str, Any],
        *,
        tighten_ref: Path | str | None = None,
    ) -> dict[str, Any]:
        """Import an existing human-approved winner without running semantic stages."""

        state = self._load()
        if state["status"] == "failed":
            return state
        tighten_path: Path | None = None
        if tighten_ref is not None:
            tighten_path = Path(tighten_ref)
            try:
                if not tighten_path.is_file():
                    raise OSError("not a file")
                with tighten_path.open("rb") as stream:
                    stream.read(1)
            except OSError as exc:
                raise ValueError(f"tighten ref is not readable: {tighten_path}") from exc
        normalized, reason = _normalize_candidate(winner, "adopted")
        if normalized is None:
            raise ValueError(reason)
        normalized.pop("miner", None)
        if not _inside_source(normalized, state["source"]["duration_sec"]):
            return self._hard_fail(state, "winner_out_of_range")
        state["winner"] = normalized
        state["human"] = {
            "approved": True,
            "candidate_id": normalized["id"],
            "source": "adopted",
        }
        if tighten_path is not None:
            state["stages"]["tighten"] = {"status": "approved"}
            state["refs"]["tighten"] = str(tighten_path)
        state["status"] = "running"
        self._save(state)
        return state

    def _run_miners(self, state: dict[str, Any]) -> None:
        stage = state["stages"].setdefault("miners", {"status": "running", "events": {}})
        payload = {"source": state["source"], "episode_id": state["episode_id"]}
        outputs = self._parallel_run("mine", MINERS, payload)
        for miner in MINERS:
            result = outputs[miner]
            if isinstance(result, Exception):
                stage["events"][miner] = {"status": "failed", "warning": str(result)}
                state["warnings"].append(f"miner {miner} pending retry: {result}")
                continue
            stage["events"][miner] = {"status": "approved"}
            rows = result.get("candidates", [])
            if not isinstance(rows, list):
                rows = []
                state["warnings"].append(f"miner {miner} returned no candidate list")
            for raw in rows:
                candidate, reason = _normalize_candidate(raw, miner)
                if candidate is None:
                    rejected = {
                        "id": raw.get("id", "unknown") if isinstance(raw, Mapping) else "unknown",
                        "miner": miner,
                        "reason": reason,
                    }
                    state["quarantine"].append(rejected)
                    state["warnings"].append(f"candidate {rejected['id']} quarantined: {reason}")
                elif not any(row["id"] == candidate["id"] for row in state["candidates"]):
                    state["candidates"].append(candidate)
        stage["status"] = "approved" if state["candidates"] else "running"
        self._save(state)

    def _run_reviews(self, state: dict[str, Any]) -> None:
        stage = state["stages"].setdefault("reviews", {"status": "running", "events": {}})
        payload = {"candidates": state["candidates"], "source": state["source"]}
        outputs = self._parallel_run("review", REVIEWERS, payload)
        candidate_ids = {row["id"] for row in state["candidates"]}
        for reviewer in REVIEWERS:
            result = outputs[reviewer]
            if isinstance(result, Exception):
                stage["events"][reviewer] = {"status": "failed", "warning": str(result)}
                state["warnings"].append(f"reviewer {reviewer} unavailable: {result}")
                continue
            assessments = _normalize_assessments(result.get("assessments"), candidate_ids)
            state["reviews"][reviewer] = {
                "assessments": assessments,
                "notes": _text(result.get("notes")),
            }
            stage["events"][reviewer] = {"status": "approved"}
            assessed = {row["candidate_id"] for row in assessments}
            missing = sorted(candidate_ids - assessed)
            if missing:
                state["warnings"].append(
                    f"reviewer {reviewer} omitted assessments for {', '.join(missing)}"
                )
        stage["status"] = "approved"
        self._save(state)

    def _resume_downstream(self, state: dict[str, Any]) -> dict[str, Any]:
        for stage_name in ("tighten", "director", "dp"):
            if self._stage_done(state, stage_name):
                continue
            existing_events = state["stages"].get(stage_name, {}).get("events")
            if isinstance(existing_events, Mapping) and existing_events:
                self._queue_pending_events(state, stage_name, existing_events)
                state["status"] = "running"
                self._save(state)
                return state
            output = self._run_one(state, stage_name)
            if output is None:
                self._save(state)
                return state
            if stage_name == "tighten":
                tightened, reason = _normalize_candidate(output.get("winner"), "tighten")
                if tightened is None:
                    self._soft_pending(state, stage_name, reason)
                    return state
                raw_winner = output.get("winner")
                if isinstance(raw_winner, Mapping) and isinstance(state["winner"], Mapping):
                    for key in ("title", "hook", "rationale", "sections"):
                        if key not in raw_winner:
                            tightened[key] = state["winner"].get(key, tightened[key])
                if not _inside_source(tightened, state["source"]["duration_sec"]):
                    return self._hard_fail(state, "winner_out_of_range")
                state["winner"] = tightened
            if stage_name in {"director", "dp"}:
                self._store_stage_events(state, stage_name, output.get("events"))
            if stage_name == "dp" and _contains_unplayable(output.get("events")):
                return self._hard_fail(state, "unplayable_asset")
            self._approve_stage(state, stage_name, output)
            self._save(state)

        if not self._stage_done(state, "visual"):
            if not self._run_visual(state):
                self._save(state)
                return state

        return self._run_readiness(state)

    def _run_visual(self, state: dict[str, Any]) -> bool:
        output = self._run_one(state, "visual_review", persist_as="visual")
        if output is None:
            return False
        events = output.get("events", [])
        if not isinstance(events, list):
            events = []
        visual_stage = state["stages"].setdefault("visual", {"status": "running", "events": {}})
        visual_stage["status"] = "running"
        visual_stage.setdefault("events", {})
        for row in events:
            if not isinstance(row, Mapping) or not _text(row.get("id")):
                continue
            event_id = _text(row["id"])
            passed = _text(row.get("status")).lower() in {"pass", "passed", "approved"}
            visual_stage["events"][event_id] = {
                "status": "approved" if passed else "failed",
                "reason": _text(row.get("reason")),
            }
        state["refs"]["visual"] = _text(output.get("ref")) or state["refs"]["visual"]
        failed_ids = [
            event_id
            for event_id, row in visual_stage["events"].items()
            if row["status"] == "failed"
        ]
        for event_id in failed_ids:
            if not self._fix_visual_event(state, event_id):
                key = {"stage": "visual_fix", "event_id": event_id}
                if key not in state["retry_queue"]:
                    state["retry_queue"].append(key)
        if state["retry_queue"]:
            state["status"] = "running"
            return False
        visual_stage["status"] = "approved"
        return True

    def _fix_visual_event(self, state: dict[str, Any], event_id: str) -> bool:
        payload = self._runner_payload(state, "visual_fix")
        payload["target_event_id"] = event_id
        try:
            fix_output = self.runner.run("visual_fix", event_id=event_id, payload=payload)
        except Exception as exc:
            state["stages"]["visual"]["events"][event_id] = {
                "status": "failed",
                "reason": str(exc),
            }
            return False

        raw_event = fix_output.get("event")
        if not isinstance(raw_event, Mapping):
            state["stages"]["visual"]["events"][event_id] = {
                "status": "failed",
                "reason": "visual fix returned no updated event",
            }
            return False
        normalized = _normalize_event_aliases(raw_event)
        if _text(normalized.get("id")) != event_id:
            state["stages"]["visual"]["events"][event_id] = {
                "status": "failed",
                "reason": "visual fix event id does not match target",
            }
            return False
        selected = _select_event_fields(normalized)
        if len(selected) <= 1:
            state["stages"]["visual"]["events"][event_id] = {
                "status": "failed",
                "reason": "visual fix event has no usable execution fields",
            }
            return False
        if _contains_unplayable([normalized]):
            self._hard_fail(state, "unplayable_asset")
            return False

        dp_events = state["stages"].get("dp", {}).get("events", {})
        target = dp_events.get(event_id)
        if not isinstance(target, Mapping):
            return False
        old_data = target.get("data")
        merged_data = dict(old_data) if isinstance(old_data, Mapping) else {}
        merged_data.update(selected)
        dp_events[event_id] = {"status": "approved", "data": merged_data}
        self._save(state)

        review_payload = self._runner_payload(state, "visual_review")
        review_payload["target_event_id"] = event_id
        try:
            review = self.runner.run("visual_review", event_id=event_id, payload=review_payload)
        except Exception as exc:
            state["stages"]["visual"]["events"][event_id] = {
                "status": "failed",
                "reason": str(exc),
            }
            return False
        passed = _text(review.get("status")).lower() in {"pass", "passed", "approved"}
        state["stages"]["visual"]["events"][event_id] = {
            "status": "approved" if passed else "failed",
            "reason": _text(review.get("reason")),
        }
        targeted_ref = _text(review.get("ref"))
        if targeted_ref:
            state["refs"]["visual"] = targeted_ref
        return passed

    def _retry_adopted_event(
        self,
        state: dict[str, Any],
        stage: str,
        event_id: str,
    ) -> bool:
        payload = self._runner_payload(state, stage)
        payload["target_event_id"] = event_id
        try:
            output = self.runner.run(stage, event_id=event_id, payload=payload)
        except Exception as exc:
            state["stages"][stage]["events"][event_id] = {
                "status": "pending",
                "warning": str(exc),
            }
            return False
        raw = output.get("event", output)
        if not isinstance(raw, Mapping):
            return False
        raw = _normalize_event_aliases(raw)
        if stage == "director":
            usable = _valid_range(raw)
        else:
            if _contains_unplayable([raw]):
                self._hard_fail(state, "unplayable_asset")
                return False
            usable = bool(
                raw.get("asset")
                or raw.get("asset_ref")
                or raw.get("candidates")
                or raw.get("selection")
                or raw.get("selections")
                or raw.get("selected_asset")
            )
        if not usable:
            return False
        state["stages"][stage]["events"][event_id] = {
            "status": "approved",
            "data": _select_event_fields(raw),
        }
        return True

    def _run_readiness(self, state: dict[str, Any]) -> dict[str, Any]:
        remaining = [
            name for name in ("resolve_preview", "packaging") if not self._stage_done(state, name)
        ]
        if remaining:
            payload = self._runner_payload(state, "readiness")
            outputs = self._parallel_run_direct(remaining, payload)
            for stage_name in remaining:
                output = outputs[stage_name]
                if isinstance(output, Exception):
                    self._soft_pending(state, stage_name, str(output))
                    continue
                if stage_name == "resolve_preview":
                    if output.get("destructive") is True:
                        return self._hard_fail(state, "destructive_resolve")
                    preview_ref = _text(output.get("preview_ref"))
                    if not preview_ref:
                        return self._hard_fail(state, "no_preview")
                    state["refs"]["preview"] = preview_ref
                else:
                    state["refs"]["packaging"] = _text(output.get("ref")) or None
                self._approve_stage(state, stage_name, output)
        if self._stage_done(state, "resolve_preview") and self._stage_done(state, "packaging"):
            state["status"] = "approved"
        else:
            state["status"] = "running"
        self._save(state)
        return state

    def _run_one(
        self,
        state: dict[str, Any],
        stage_name: str,
        *,
        persist_as: str | None = None,
    ) -> Mapping[str, Any] | None:
        try:
            return self.runner.run(
                stage_name,
                event_id=None,
                payload=self._runner_payload(state, stage_name),
            )
        except (StagePending, Exception) as exc:
            self._soft_pending(state, persist_as or stage_name, str(exc))
            return None

    def _parallel_run(
        self,
        stage: str,
        event_ids: Sequence[str],
        payload: dict[str, Any],
    ) -> dict[str, Mapping[str, Any] | Exception]:
        results: dict[str, Mapping[str, Any] | Exception] = {}
        with ThreadPoolExecutor(max_workers=len(event_ids)) as pool:
            futures = {
                pool.submit(self.runner.run, stage, event_id=event_id, payload=payload): event_id
                for event_id in event_ids
            }
            for future in as_completed(futures):
                event_id = futures[future]
                try:
                    results[event_id] = future.result()
                except Exception as exc:  # event-local; the other semantic work remains useful
                    results[event_id] = exc
        return results

    def _parallel_run_direct(
        self,
        stages: Sequence[str],
        payload: dict[str, Any],
    ) -> dict[str, Mapping[str, Any] | Exception]:
        results: dict[str, Mapping[str, Any] | Exception] = {}
        with ThreadPoolExecutor(max_workers=len(stages)) as pool:
            futures = {
                pool.submit(self.runner.run, stage, event_id=None, payload=payload): stage
                for stage in stages
            }
            for future in as_completed(futures):
                stage = futures[future]
                try:
                    results[stage] = future.result()
                except Exception as exc:
                    results[stage] = exc
        return results

    def _runner_payload(self, state: Mapping[str, Any], stage_name: str) -> dict[str, Any]:
        return {
            "episode_id": state["episode_id"],
            "stage": stage_name,
            "source": state["source"],
            "candidates": state["candidates"],
            "reviews": state["reviews"],
            "winner": state["winner"],
            "refs": state["refs"],
            "stages": state["stages"],
        }

    def _store_stage_events(
        self,
        state: dict[str, Any],
        stage_name: str,
        raw_events: Any,
    ) -> None:
        if not isinstance(raw_events, list):
            return
        events: dict[str, Any] = {}
        for index, row in enumerate(raw_events):
            if not isinstance(row, Mapping):
                continue
            event_id = _text(row.get("id")) or f"event-{index + 1}"
            events[event_id] = {"status": "approved", "data": _select_event_fields(row)}
        state["stages"].setdefault(stage_name, {})["events"] = events

    def _adopt_rows(self, raw_events: Any, *, kind: str) -> dict[str, Any]:
        events: dict[str, Any] = {}
        if not isinstance(raw_events, list):
            return events
        for index, raw in enumerate(raw_events):
            if not isinstance(raw, Mapping):
                event_id = f"event-{index + 1}"
                events[event_id] = {"status": "pending"}
                continue
            normalized = _normalize_event_aliases(raw)
            event_id = _text(normalized.get("id")) or f"event-{index + 1}"
            failed = _text(normalized.get("status")).lower() in {
                "failed",
                "missing",
                "pending",
            }
            if kind == "director":
                usable = _valid_range(normalized) and not failed
            else:
                has_asset = bool(
                    normalized.get("asset")
                    or normalized.get("asset_ref")
                    or normalized.get("candidates")
                    or normalized.get("selection")
                    or normalized.get("selections")
                    or normalized.get("selected_asset")
                )
                usable = has_asset and not failed and not _contains_unplayable([normalized])
            events[event_id] = {
                "status": "approved" if usable else "pending",
                "data": _select_event_fields(normalized) if usable else None,
            }
        return events

    @staticmethod
    def _queue_pending_events(
        state: dict[str, Any],
        stage: str,
        events: Mapping[str, Mapping[str, Any]],
    ) -> None:
        for event_id, row in events.items():
            key = {"stage": stage, "event_id": event_id}
            if row.get("status") != "approved" and key not in state["retry_queue"]:
                state["retry_queue"].append(key)

    def _approve_stage(
        self,
        state: dict[str, Any],
        stage_name: str,
        output: Mapping[str, Any],
    ) -> None:
        stage = state["stages"].setdefault(stage_name, {})
        stage["status"] = "approved"
        ref_key = {
            "tighten": "tighten",
            "director": "director",
            "dp": "dp",
        }.get(stage_name)
        if ref_key:
            state["refs"][ref_key] = _text(output.get("ref")) or None

    def _soft_pending(self, state: dict[str, Any], stage_name: str, warning: str) -> None:
        state["stages"].setdefault(stage_name, {})["status"] = "pending"
        state["status"] = "running"
        state["warnings"].append(f"{stage_name} pending: {warning}")
        self._save(state)

    def _hard_fail(self, state: dict[str, Any], blocker: str) -> dict[str, Any]:
        state["status"] = "failed"
        state["hard_blocker"] = blocker
        self._save(state)
        return state

    @staticmethod
    def _stage_done(state: Mapping[str, Any], stage_name: str) -> bool:
        return state.get("stages", {}).get(stage_name, {}).get("status") == "approved"

    def _load(self) -> dict[str, Any]:
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("status") not in RUN_STATUSES:
            raise ValueError(f"invalid orchestrator state: {self.state_path}")
        return value

    def _save(self, state: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def _normalize_candidate(raw: Any, miner: str) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw, Mapping):
        return None, "candidate is not an object"
    candidate_id = _text(raw.get("id"))
    if not candidate_id:
        return None, "candidate id is missing"
    try:
        start = float(raw["t_start"])
        end = float(raw["t_end"])
    except (KeyError, TypeError, ValueError):
        return None, "candidate time range is malformed"
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        return None, "candidate time range is malformed"
    sections = raw.get("sections")
    return (
        {
            "id": candidate_id,
            "miner": miner,
            "t_start": start,
            "t_end": end,
            "title": _text(raw.get("title")),
            "hook": _text(raw.get("hook")),
            "rationale": _text(raw.get("rationale")),
            "sections": list(sections) if isinstance(sections, list) else [],
        },
        "",
    )


def _normalize_assessments(raw: Any, candidate_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    assessments: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        candidate_id = _text(row.get("candidate_id"))
        if candidate_id not in candidate_ids:
            continue
        score = row.get("score")
        assessments.append(
            {
                "candidate_id": candidate_id,
                "score": score if isinstance(score, (int, float)) else None,
                "notes": _text(row.get("notes")),
            }
        )
    return assessments


def _read_source_duration(source: SourceInput) -> float:
    srt_path = Path(source.srt_path)
    media_path = Path(source.media_path)
    if not srt_path.is_file() or not media_path.is_file():
        raise OSError("source SRT and media must both exist")
    with media_path.open("rb") as stream:
        if not stream.read(1):
            raise OSError("source media is empty")
    text = srt_path.read_text(encoding="utf-8")
    timestamps = re.findall(r"-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})", text)
    if not timestamps:
        raise ValueError("source SRT has no readable cue ranges")
    return max(_timestamp_seconds(value) for value in timestamps)


def _timestamp_seconds(value: str) -> float:
    hours, minutes, remainder = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(remainder)


def _inside_source(candidate: Mapping[str, Any], duration: float) -> bool:
    try:
        start = float(candidate["t_start"])
        end = float(candidate["t_end"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= float(duration)


def _valid_range(row: Mapping[str, Any]) -> bool:
    try:
        start = float(row["t_start"])
        end = float(row["t_end"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(start) and math.isfinite(end) and 0 <= start < end


def _contains_unplayable(raw_events: Any) -> bool:
    if not isinstance(raw_events, list):
        return False
    for event in raw_events:
        if not isinstance(event, Mapping):
            continue
        asset_rows: list[Any] = []
        if isinstance(event.get("asset"), Mapping):
            asset_rows.append(event["asset"])
        if isinstance(event.get("candidates"), list):
            asset_rows.extend(event["candidates"])
        if isinstance(event.get("selections"), list):
            asset_rows.extend(event["selections"])
        for key in ("selection", "selected_asset"):
            if isinstance(event.get(key), Mapping):
                asset_rows.append(event[key])
        if event.get("playable") is False:
            return True
        if any(
            isinstance(asset, Mapping) and asset.get("playable") is False for asset in asset_rows
        ):
            return True
    return False


def _select_event_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "id",
        "t_start",
        "t_end",
        "kind",
        "status",
        "request",
        "description",
        "category",
        "decision",
        "form",
        "negative_constraints",
        "on_screen_text",
        "quote",
        "rationale",
        "search_angles",
        "shots_hint",
        "asset",
        "asset_ref",
        "candidates",
        "selection",
        "selections",
        "selected_asset",
        "fixed_stock_authority",
        "visual_summary",
        "implementation_kind",
        "mode",
        "semantic_justification",
        "target_lane",
    )
    return {key: row[key] for key in fields if key in row}


def _normalize_event_aliases(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    if "id" not in normalized and "event_id" in normalized:
        normalized["id"] = normalized["event_id"]
    if "t_start" not in normalized and "t0" in normalized:
        normalized["t_start"] = normalized["t0"]
    if "t_end" not in normalized and "t1" in normalized:
        normalized["t_end"] = normalized["t1"]
    return normalized


def _event_stage_status(events: Mapping[str, Mapping[str, Any]]) -> str:
    return (
        "approved"
        if events and all(row["status"] == "approved" for row in events.values())
        else "pending"
    )


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "event"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "DirectoryStageRunner",
    "LongHighlightOrchestrator",
    "MINERS",
    "REVIEWERS",
    "SourceInput",
    "StagePending",
    "StageRunner",
]
