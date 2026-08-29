from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.brook.script_video.finished_cut_production import (
    _codex_semantic as codex_semantic_module,
)
from agents.brook.script_video.finished_cut_production._assets import (
    AssetKind,
    WorkerCatalogItem,
)
from agents.brook.script_video.finished_cut_production._codex_semantic import (
    CodexProcessResult,
    CodexSemanticAdapter,
    NamedMedia,
    StagePacket,
    SubprocessCodexProcessRunner,
)
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
)
from agents.brook.script_video.finished_cut_production._derived_assets import (
    BuiltComponentAsset,
)
from agents.brook.script_video.finished_cut_production._records import (
    EventPlacementCandidates,
    EventRecord,
    StageRequest,
)
from agents.brook.script_video.finished_cut_production._worker_packet import (
    expected_format_policy,
)


@dataclass(frozen=True, slots=True)
class _ProcessCall:
    argv: tuple[str, ...]
    cwd: Path
    prompt: str
    timeout_sec: float
    packet: dict[str, object]
    schema: dict[str, object]
    media: dict[str, bytes]


class _CodexRunner:
    def __init__(self, response: dict[str, object] | str) -> None:
        self._response = response
        self.calls: list[_ProcessCall] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        prompt: str,
        timeout_sec: float,
    ) -> CodexProcessResult:
        packet = json.loads((cwd / "packet.json").read_text(encoding="utf-8"))
        schema_path = Path(argv[argv.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        media = {
            row["logical_name"]: (cwd / "media" / row["logical_name"]).read_bytes()
            for row in packet["media"]
        }
        self.calls.append(_ProcessCall(argv, cwd, prompt, timeout_sec, packet, schema, media))
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        raw_response = (
            self._response if isinstance(self._response, str) else json.dumps(self._response)
        )
        output_path.write_text(raw_response, encoding="utf-8")
        return CodexProcessResult(returncode=0)


class _PacketMaterializer:
    def __init__(self, packet: StagePacket) -> None:
        self._packet = packet
        self.requests: list[StageRequest] = []

    def materialize(self, request: StageRequest) -> StagePacket:
        self.requests.append(request)
        if self._packet.format_policy is None:
            return replace(
                self._packet,
                format_policy=expected_format_policy(request.format, request.stage),
            )
        return self._packet


class _BoundaryRunner:
    def __init__(self, result: CodexProcessResult, *, write_output: bool) -> None:
        self._result = result
        self._write_output = write_output
        self.calls: list[_ProcessCall] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        prompt: str,
        timeout_sec: float,
    ) -> CodexProcessResult:
        packet = json.loads((cwd / "packet.json").read_text(encoding="utf-8"))
        schema_path = Path(argv[argv.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.calls.append(_ProcessCall(argv, cwd, prompt, timeout_sec, packet, schema, {}))
        if self._write_output:
            output_path = Path(argv[argv.index("--output-last-message") + 1])
            output_path.write_text("{}", encoding="utf-8")
        return self._result


def test_windows_codex_resolution_prefers_npm_cmd_over_windowsapps_shim() -> None:
    observed: list[str] = []
    npm_cmd = r"C:\Users\Shosho\AppData\Roaming\npm\codex.cmd"
    windowsapps = r"C:\Program Files\WindowsApps\OpenAI.Codex.exe"

    def _which(candidate: str) -> str | None:
        observed.append(candidate)
        return {
            "codex.cmd": npm_cmd,
            "codex.exe": windowsapps,
            "codex": windowsapps,
        }.get(candidate)

    resolved = codex_semantic_module._resolve_codex_executable(
        None,
        platform_name="nt",
        which=_which,
    )

    assert resolved == npm_cmd
    assert observed == ["codex.cmd"]


def test_windows_codex_resolution_uses_appdata_npm_when_path_omits_cmd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    npm_cmd = appdata / "npm" / "codex.cmd"
    npm_cmd.parent.mkdir(parents=True)
    npm_cmd.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))

    resolved = codex_semantic_module._resolve_codex_executable(
        None,
        platform_name="nt",
        which=lambda _candidate: None,
    )

    assert resolved == str(npm_cmd)


def test_windows_codex_resolution_survives_denied_npm_metadata_probe(monkeypatch) -> None:
    appdata = Path(r"C:\Users\operator\AppData\Roaming")
    monkeypatch.setenv("APPDATA", str(appdata))

    def _deny_metadata(_path: Path) -> bool:
        raise PermissionError("sandbox denied metadata probe")

    monkeypatch.setattr(Path, "is_file", _deny_metadata)

    resolved = codex_semantic_module._resolve_codex_executable(
        None,
        platform_name="nt",
        which=lambda _candidate: None,
    )

    assert resolved == str(appdata / "npm" / "codex.cmd")


def test_windows_codex_resolution_rejects_only_windowsapps_candidates(monkeypatch) -> None:
    monkeypatch.delenv("APPDATA", raising=False)

    with pytest.raises(FileNotFoundError, match="safe Codex executable"):
        codex_semantic_module._resolve_codex_executable(
            None,
            platform_name="nt",
            which=lambda candidate: rf"C:\Program Files\WindowsApps\{candidate}",
        )


def _director_request() -> StageRequest:
    context = EditorialCutContext(
        episode_id="episode-001",
        cut_id="value-L02",
        format="long",
        editorial_master_id="master-current",
        tight_cut_id="tight-current",
        duration_sec=540.0,
        source_ranges=(CutSourceRange(100.0, 640.0),),
        cues=(
            CueAnchor("cue-001", "真正重要的第一句", 0.0, 2.0, "section-01"),
            CueAnchor("cue-002", "接續的完整論點", 2.0, 5.0, "section-01"),
        ),
        sections=(CanonicalSection("section-01", "開場論點", 0.0),),
    )
    return StageRequest(
        run_id="run-current",
        request_id="request-current-director",
        command_id="approved-cut:current",
        episode_id="episode-001",
        cut_id="value-L02",
        format="long",
        stage="director",
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id=None,
        editorial_context=context,
    )


def _director_response(request: StageRequest) -> dict[str, object]:
    return {
        "schema": request.schema,
        "run_id": request.run_id,
        "request_id": request.request_id,
        "episode_id": request.episode_id,
        "cut_id": request.cut_id,
        "format": request.format,
        "stage": request.stage,
        "attempt": request.attempt,
        "scope": request.scope,
        "event_id": request.event_id,
        "parent_acceptance_id": request.parent_acceptance_id,
        "events": [
            {
                "event_id": "event-001",
                "master_cue_ids": ["cue-001", "cue-002"],
                "intent": "保留完整論點並建立清楚的開場 hook",
                "display": "真正重要的是選擇權",
                "semantic_kind": "hero_title",
                "intentional_aroll": False,
            }
        ],
    }


def _dp_request() -> StageRequest:
    asset_ref = "asset-sha256:" + "b" * 64
    event = EventRecord(
        event_id="event-001",
        master_cue_ids=("cue-001", "cue-002"),
        text_hash="a" * 64,
        intent="保留完整論點並建立清楚的開場 hook",
        text="真正重要的第一句\n接續的完整論點",
        t0=0.0,
        t1=5.0,
        section_id="section-01",
        display="真正重要的是選擇權",
        semantic_kind="b_roll",
    )
    return StageRequest(
        run_id="run-current",
        request_id="request-current-dp",
        command_id="approved-cut:current",
        episode_id="episode-001",
        cut_id="value-L02",
        format="long",
        stage="dp",
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id="acceptance-current-director",
        events=(event,),
        worker_asset_refs=(asset_ref,),
        worker_catalog_items=(
            WorkerCatalogItem(
                reference=asset_ref,
                kind=AssetKind.STOCK,
                visual_summary="焦頭爛額的照顧者同時處理工作與家庭責任",
                width=1920,
                height=1080,
                duration_sec=12.5,
            ),
        ),
        placement_candidates=(
            EventPlacementCandidates(
                event_id="event-001",
                cues=(
                    CueAnchor("cue-001", "真正重要的第一句", 0.0, 2.0, "section-01"),
                    CueAnchor("cue-002", "接續的完整論點", 2.0, 5.0, "section-01"),
                ),
            ),
        ),
    )


def _dp_response(request: StageRequest) -> dict[str, object]:
    return {
        "schema": request.schema,
        "run_id": request.run_id,
        "request_id": request.request_id,
        "episode_id": request.episode_id,
        "cut_id": request.cut_id,
        "format": request.format,
        "stage": request.stage,
        "attempt": request.attempt,
        "scope": request.scope,
        "event_id": request.event_id,
        "parent_acceptance_id": request.parent_acceptance_id,
        "events": [
            {
                "event_id": "event-001",
                "implementation_kind": "stock_video",
                "lane": "b_roll",
                "asset_ref": "asset-sha256:" + "b" * 64,
                "placement_cue_ids": ["cue-002"],
            }
        ],
    }


def test_dp_packet_exposes_exact_event_scoped_cue_candidates_for_placement() -> None:
    request = _dp_request()
    catalog = [
        {
            "reference": request.worker_asset_refs[0],
            "kind": "stock",
            "visual_summary": "焦頭爛額的照顧者同時處理工作與家庭責任",
            "width": 1920,
            "height": 1080,
            "duration_sec": 12.5,
        }
    ]
    runner = _CodexRunner(_dp_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="dp", payload={"catalog": catalog}, media=())
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is not None
    assert proposal.events[0].placement_cue_ids == ("cue-002",)
    assert "smallest useful contiguous cue subset" in runner.calls[0].prompt
    assert "no longer than 8 seconds" in runner.calls[0].prompt
    assert "no longer than 12 seconds" in runner.calls[0].prompt
    assert runner.calls[0].packet["request"]["placement_candidates"] == [  # type: ignore[index]
        {
            "event_id": "event-001",
            "cues": [
                {
                    "cue_id": "cue-001",
                    "text": "真正重要的第一句",
                    "t0": 0.0,
                    "t1": 2.0,
                    "section_id": "section-01",
                },
                {
                    "cue_id": "cue-002",
                    "text": "接續的完整論點",
                    "t0": 2.0,
                    "t1": 5.0,
                    "section_id": "section-01",
                },
            ],
        }
    ]


def _visual_request() -> StageRequest:
    source_ref = "asset-sha256:" + "b" * 64
    inspection_ref = "asset-sha256:" + "c" * 64
    context = _director_request().editorial_context
    assert context is not None
    placement = context.derive_visual_placement(
        semantic_cue_ids=("cue-001", "cue-002"),
        placement_cue_ids=("cue-002",),
        semantic_kind="b_roll",
    )
    event = EventRecord(
        event_id="event-001",
        master_cue_ids=("cue-001", "cue-002"),
        text_hash="a" * 64,
        intent="保留完整論點並建立清楚的開場 hook",
        asset_ref=source_ref,
        text="真正重要的第一句\n接續的完整論點",
        t0=0.0,
        t1=5.0,
        section_id="section-01",
        display="真正重要的是選擇權",
        semantic_kind="b_roll",
        implementation_kind="stock_video",
        lane="b_roll",
        visual_placement=placement,
    )
    return StageRequest(
        run_id="run-current",
        request_id="request-current-visual",
        command_id="approved-cut:current",
        episode_id="episode-001",
        cut_id="value-L02",
        format="long",
        stage="visual_review",
        attempt=1,
        scope="full_stage",
        event_id=None,
        parent_acceptance_id="acceptance-current-dp",
        events=(event,),
        built_components=(
            BuiltComponentAsset(
                component_id="component:event-001",
                event_id="event-001",
                source_asset_ref=source_ref,
                final_asset_ref=source_ref,
                inspection_ref=inspection_ref,
                recipe_identity=None,
            ),
        ),
    )


def _visual_response(request: StageRequest) -> dict[str, object]:
    return {
        "schema": request.schema,
        "run_id": request.run_id,
        "request_id": request.request_id,
        "episode_id": request.episode_id,
        "cut_id": request.cut_id,
        "format": request.format,
        "stage": request.stage,
        "attempt": request.attempt,
        "scope": request.scope,
        "event_id": request.event_id,
        "parent_acceptance_id": request.parent_acceptance_id,
        "events": [{"event_id": "event-001", "status": "approved"}],
    }


def test_director_dispatches_current_request_in_one_isolated_read_only_workspace() -> None:
    request = _director_request()
    runner = _CodexRunner(_director_response(request))
    materializer = _PacketMaterializer(StagePacket(stage="director", payload={}, media=()))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=materializer,
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is not None
    assert proposal.events[0].event_id == "event-001"
    assert proposal.events[0].master_cue_ids == ("cue-001", "cue-002")
    assert materializer.requests == [request]
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call.argv[:3] == ("codex-test", "exec", "--ignore-user-config")
    assert ("--sandbox", "read-only") == (
        call.argv[call.argv.index("--sandbox")],
        call.argv[call.argv.index("--sandbox") + 1],
    )
    assert "--ephemeral" in call.argv
    assert "--add-dir" not in call.argv
    assert "--cd" not in call.argv
    schema_path = Path(call.argv[call.argv.index("--output-schema") + 1])
    output_path = Path(call.argv[call.argv.index("--output-last-message") + 1])
    assert schema_path.parent == call.cwd
    assert output_path.parent == call.cwd
    assert call.packet["request"]["request_id"] == request.request_id  # type: ignore[index]
    assert call.packet["format_policy"] == expected_format_policy("long", "director")
    assert "format_policy" in call.prompt
    assert "format_policy.editorial_brief" in call.prompt
    assert (
        "master_cue_ids must be unique, in packet cue order, contiguous, and entirely "
        "within one canonical section"
    ) in call.prompt
    assert (
        "For every section with transition_before=true, return exactly one chapter event "
        "whose master_cue_ids contains only that section's first cue"
    ) in call.prompt
    assert (
        "Never use semantic_kind=chapter for any other event, and never infer a chapter "
        "boundary from semantics"
    ) in call.prompt
    assert call.schema["properties"]["stage"] == {  # type: ignore[index]
        "type": "string",
        "const": "director",
    }
    event_schema = call.schema["properties"]["events"]["items"]  # type: ignore[index]
    assert set(event_schema["properties"]) == {  # type: ignore[index]
        "event_id",
        "master_cue_ids",
        "intent",
        "display",
        "semantic_kind",
        "intentional_aroll",
    }
    workspace = call.cwd
    assert not workspace.exists()


def test_director_rejects_materializer_metadata_before_codex_dispatch() -> None:
    request = _director_request()
    runner = _CodexRunner(_director_response(request))
    materializer = _PacketMaterializer(
        StagePacket(
            stage="director",
            payload={
                "prior_title": "K 型發展",
                "legacy_path": "G:/episode/highlights/visual-pipeline/revisions/r001",
            },
            media=(),
        )
    )
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=materializer,
        codex_executable="codex-test",
    )

    outcome = adapter.dispatch(request)

    assert outcome.state == "failed"
    assert outcome.reason_code == "semantic_packet_rejected"
    assert runner.calls == []


def test_codex_rejects_a_caller_modified_format_policy_brief() -> None:
    request = _director_request()
    runner = _CodexRunner(_director_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="director",
                payload={},
                media=(),
                format_policy={"policy_id": "caller-controlled"},
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert runner.calls == []


def test_director_requires_current_editorial_context_before_codex_dispatch() -> None:
    request = replace(_director_request(), editorial_context=None)
    runner = _CodexRunner(_director_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="director", payload={}, media=())
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert runner.calls == []


def test_director_rejects_editorial_context_for_a_different_cut() -> None:
    request = _director_request()
    context = request.editorial_context
    assert context is not None
    request = replace(request, editorial_context=replace(context, cut_id="value-L99"))
    runner = _CodexRunner(_director_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="director", payload={}, media=())
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert runner.calls == []


def test_targeted_director_packet_contains_only_current_event_and_feedback() -> None:
    current_event = EventRecord(
        event_id="event-001",
        master_cue_ids=("cue-001", "cue-002"),
        text_hash="a" * 64,
        intent="原本的完整論點",
        text="真正重要的第一句\n接續的完整論點",
        t0=0.0,
        t1=5.0,
        section_id="section-01",
        display="原本的 Hero",
        semantic_kind="hero_title",
    )
    request = replace(
        _director_request(),
        scope="event_retry",
        event_id="event-001",
        parent_acceptance_id="acceptance-current-director",
        events=(current_event,),
        feedback="詞意不完整，請保留同一段 cue 但重寫顯示文字。",
    )
    runner = _CodexRunner(_director_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="director", payload={}, media=())
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is not None
    request_packet = runner.calls[0].packet["request"]
    assert isinstance(request_packet, dict)
    assert request_packet["feedback"] == request.feedback
    assert request_packet["current_event"] == {
        "event_id": "event-001",
        "master_cue_ids": ["cue-001", "cue-002"],
        "text_hash": "a" * 64,
        "intent": "原本的完整論點",
        "text": "真正重要的第一句\n接續的完整論點",
        "t0": 0.0,
        "t1": 5.0,
        "section_id": "section-01",
        "display": "原本的 Hero",
        "semantic_kind": "hero_title",
        "intentional_aroll": False,
    }
    assert "echo current_event.master_cue_ids exactly" in runner.calls[0].prompt


def test_dp_uses_current_director_events_and_only_neutral_catalog_metadata() -> None:
    request = _dp_request()
    asset_ref = request.worker_asset_refs[0]
    catalog = [
        {
            "reference": asset_ref,
            "kind": "stock",
            "visual_summary": "焦頭爛額的照顧者同時處理工作與家庭責任",
            "width": 1920,
            "height": 1080,
            "duration_sec": 12.5,
        }
    ]
    runner = _CodexRunner(_dp_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="dp", payload={"catalog": catalog}, media=())
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is not None
    assert proposal.events[0].implementation_kind == "stock_video"
    assert proposal.events[0].asset_ref == asset_ref
    assert proposal.events[0].placement_cue_ids == ("cue-002",)
    call = runner.calls[0]
    request_packet = call.packet["request"]
    assert isinstance(request_packet, dict)
    assert request_packet["current_events"] == [
        {
            "event_id": "event-001",
            "master_cue_ids": ["cue-001", "cue-002"],
            "text_hash": "a" * 64,
            "intent": "保留完整論點並建立清楚的開場 hook",
            "text": "真正重要的第一句\n接續的完整論點",
            "t0": 0.0,
            "t1": 5.0,
            "section_id": "section-01",
            "display": "真正重要的是選擇權",
            "semantic_kind": "b_roll",
            "intentional_aroll": False,
        }
    ]
    assert call.packet["stage_input"] == {"catalog": catalog}
    event_schema = call.schema["properties"]["events"]["items"]  # type: ignore[index]
    assert set(event_schema["properties"]) == {  # type: ignore[index]
        "event_id",
        "implementation_kind",
        "lane",
        "asset_ref",
        "placement_cue_ids",
    }
    assert "Renee" not in call.prompt
    assert "Brand" not in call.prompt
    assert "format_policy.editorial_brief" in call.prompt


def test_dp_rejects_retired_supporting_title_response_and_never_advertises_it() -> None:
    request = _dp_request()
    response = _dp_response(request)
    event = response["events"][0]  # type: ignore[index]
    event["implementation_kind"] = "supporting_title"  # type: ignore[index]
    event["lane"] = "supporting_title"  # type: ignore[index]
    event["asset_ref"] = None  # type: ignore[index]
    runner = _CodexRunner(response)
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="dp",
                payload={
                    "catalog": [
                        {
                            "reference": request.worker_catalog_items[0].reference,
                            "kind": "stock",
                            "visual_summary": request.worker_catalog_items[0].visual_summary,
                            "width": 1920,
                            "height": 1080,
                            "duration_sec": 12.5,
                        }
                    ]
                },
                media=(),
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert "supporting_title" not in json.dumps(runner.calls[0].schema, sort_keys=True)
    assert "supporting_title" not in runner.calls[0].prompt


def test_dp_rejects_editorial_context_that_belongs_only_to_director() -> None:
    request = replace(_dp_request(), editorial_context=_director_request().editorial_context)
    asset_ref = request.worker_asset_refs[0]
    runner = _CodexRunner(_dp_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="dp",
                payload={
                    "catalog": [
                        {
                            "reference": asset_ref,
                            "kind": "stock",
                            "visual_summary": "焦頭爛額的照顧者",
                            "width": 1920,
                            "height": 1080,
                            "duration_sec": 8.0,
                        }
                    ]
                },
                media=(),
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert runner.calls == []


def test_dp_rejects_catalog_metadata_that_differs_from_current_request() -> None:
    request = _dp_request()
    asset_ref = request.worker_asset_refs[0]
    runner = _CodexRunner(_dp_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="dp",
                payload={
                    "catalog": [
                        {
                            "reference": asset_ref,
                            "kind": "stock",
                            "visual_summary": "與 current request 不同的描述",
                            "width": 1920,
                            "height": 1080,
                            "duration_sec": 12.5,
                        }
                    ]
                },
                media=(),
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert runner.calls == []


def test_dp_rejects_path_shaped_catalog_reference_before_codex_dispatch() -> None:
    path_shaped_ref = "asset-sha256:../../highlights/visual-pipeline/revisions/r001"
    request = replace(_dp_request(), worker_asset_refs=(path_shaped_ref,))
    runner = _CodexRunner(_dp_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="dp",
                payload={
                    "catalog": [
                        {
                            "reference": path_shaped_ref,
                            "kind": "stock",
                            "visual_summary": "不應被接受的舊素材",
                            "width": 1920,
                            "height": 1080,
                            "duration_sec": 8.0,
                        }
                    ]
                },
                media=(),
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert runner.calls == []


def test_dp_rejects_absolute_episode_path_hidden_in_catalog_summary() -> None:
    request = _dp_request()
    asset_ref = request.worker_asset_refs[0]
    runner = _CodexRunner(_dp_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="dp",
                payload={
                    "catalog": [
                        {
                            "reference": asset_ref,
                            "kind": "stock",
                            "visual_summary": (
                                "G:/episode/highlights/visual-pipeline/revisions/r001"
                            ),
                            "width": 1920,
                            "height": 1080,
                            "duration_sec": 8.0,
                        }
                    ]
                },
                media=(),
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert runner.calls == []


def test_visual_reviews_final_component_preview_bytes_not_source_self_description() -> None:
    request = _visual_request()
    current_build = request.built_components[0]
    final_asset_ref = current_build.final_asset_ref
    inspection_ref = current_build.inspection_ref
    assert inspection_ref is not None
    built_components = [
        {
            "component_id": "component:event-001",
            "event_id": "event-001",
            "final_asset_ref": final_asset_ref,
            "inspection_ref": inspection_ref,
        }
    ]
    runner = _CodexRunner(_visual_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="visual_review",
                payload={"built_components": built_components},
                media=(
                    NamedMedia(
                        logical_name="component-event-001.png",
                        mime_type="image/png",
                        inspection_kind="contact_sheet",
                        bytes=b"final rendered component preview",
                        inspection_ref=inspection_ref,
                        for_asset_ref=final_asset_ref,
                    ),
                ),
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is not None
    assert proposal.events[0].event_id == "event-001"
    assert proposal.events[0].status == "approved"
    call = runner.calls[0]
    assert call.packet["stage_input"] == {"built_components": built_components}
    assert call.packet["media"] == [
        {
            "logical_name": "component-event-001.png",
            "mime_type": "image/png",
            "inspection_kind": "contact_sheet",
            "inspection_ref": inspection_ref,
            "for_asset_ref": final_asset_ref,
        }
    ]
    assert call.media == {"component-event-001.png": b"final rendered component preview"}
    request_packet = call.packet["request"]
    assert isinstance(request_packet, dict)
    assert request_packet["current_events"][0]["implementation_kind"] == "stock_video"
    assert request_packet["current_events"][0]["visual_placement"] == {
        "placement_cue_ids": ["cue-002"],
        "t0": 2.0,
        "t1": 5.0,
        "section_id": "section-01",
        "final_asset_ref": final_asset_ref,
    }
    assert "source_description" not in json.dumps(call.packet)
    event_schema = call.schema["properties"]["events"]["items"]  # type: ignore[index]
    assert set(event_schema["properties"]) == {"event_id", "status"}  # type: ignore[index]
    assert "final rendered component" in call.prompt
    assert "Judge every event independently" in call.prompt
    assert "never cascade one failure" in call.prompt
    assert "transparent pixels appear black" in call.prompt
    assert "format_policy.editorial_brief" in call.prompt


def test_visual_uses_final_asset_as_inspection_key_when_build_has_no_derivative() -> None:
    request = _visual_request()
    current_build = request.built_components[0]
    final_asset_ref = current_build.final_asset_ref
    request = replace(
        request,
        built_components=(replace(current_build, inspection_ref=None),),
    )
    runner = _CodexRunner(_visual_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="visual_review",
                payload={
                    "built_components": [
                        {
                            "component_id": "component:event-001",
                            "event_id": "event-001",
                            "final_asset_ref": final_asset_ref,
                            "inspection_ref": final_asset_ref,
                        }
                    ]
                },
                media=(
                    NamedMedia(
                        logical_name="component-event-001.png",
                        mime_type="image/png",
                        inspection_kind="preview_frame",
                        bytes=b"preview derived from final asset",
                        inspection_ref=final_asset_ref,
                        for_asset_ref=final_asset_ref,
                    ),
                ),
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is not None
    assert proposal.events[0].status == "approved"
    assert "inspection_ref" not in proposal.events[0].__slots__


def test_visual_rejects_component_media_not_bound_to_current_core_build() -> None:
    request = _visual_request()
    forged_final_ref = "asset-sha256:" + "d" * 64
    inspection_ref = request.built_components[0].inspection_ref
    assert inspection_ref is not None
    runner = _CodexRunner(_visual_response(request))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(
                stage="visual_review",
                payload={
                    "built_components": [
                        {
                            "component_id": "component:event-001",
                            "event_id": "event-001",
                            "final_asset_ref": forged_final_ref,
                            "inspection_ref": inspection_ref,
                        }
                    ]
                },
                media=(
                    NamedMedia(
                        logical_name="component-event-001.png",
                        mime_type="image/png",
                        inspection_kind="contact_sheet",
                        bytes=b"forged preview",
                        inspection_ref=inspection_ref,
                        for_asset_ref=forged_final_ref,
                    ),
                ),
            )
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None
    assert runner.calls == []


def test_boolean_attempt_cannot_impersonate_the_current_integer_attempt() -> None:
    request = _director_request()
    response = _director_response(request)
    response["attempt"] = True
    runner = _CodexRunner(response)
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="director", payload={}, media=())
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)

    assert proposal is None


@pytest.mark.parametrize(
    "corruption",
    ["wrong_run", "wrong_request", "wrong_stage", "extra_field", "malformed_json"],
)
def test_wrong_envelope_extra_field_and_malformed_json_are_rejected(
    corruption: str,
) -> None:
    request = _director_request()
    response: dict[str, object] | str = _director_response(request)
    if corruption == "malformed_json":
        response = '{"schema":'
    else:
        assert isinstance(response, dict)
        if corruption == "wrong_run":
            response["run_id"] = "run-stale"
        elif corruption == "wrong_request":
            response["request_id"] = "request-stale"
        elif corruption == "wrong_stage":
            response["stage"] = "dp"
        else:
            response["legacy_status"] = "approved"
    runner = _CodexRunner(response)
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="director", payload={}, media=())
        ),
        codex_executable="codex-test",
    )

    proposal = adapter.proposal_for(request)
    repeated = adapter.proposal_for(request)

    assert proposal is None
    assert repeated is None
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    ("result", "write_output", "diagnostic_code"),
    [
        (CodexProcessResult(returncode=2, stderr="x" * 10_000), True, "process_failed"),
        (CodexProcessResult(returncode=None, timed_out=True), False, "process_timeout"),
        (CodexProcessResult(returncode=0), False, "output_missing"),
    ],
)
def test_process_failure_timeout_and_missing_output_never_redispatch_same_request(
    result: CodexProcessResult,
    write_output: bool,
    diagnostic_code: str,
) -> None:
    request = _director_request()
    runner = _BoundaryRunner(result, write_output=write_output)
    materializer = _PacketMaterializer(StagePacket(stage="director", payload={}, media=()))
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=materializer,
        codex_executable="codex-test",
    )

    first = adapter.dispatch(request)
    second = adapter.dispatch(request)

    assert first.state == "failed"
    assert first.reason_code == f"semantic_{diagnostic_code}"
    assert second == first
    assert len(runner.calls) == 1
    assert materializer.requests == [request]
    assert not runner.calls[0].cwd.exists()
    assert adapter.diagnostics[-1].code == diagnostic_code
    assert len(adapter.diagnostics[-1].detail) <= 512


def test_process_failure_diagnostic_retains_stderr_head_and_tail() -> None:
    request = _director_request()
    runner = _BoundaryRunner(
        CodexProcessResult(
            returncode=2,
            stderr="stderr-head:" + "e" * 2_000 + ":stderr-tail",
        ),
        write_output=False,
    )
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="director", payload={}, media=())
        ),
        codex_executable="codex-test",
    )

    outcome = adapter.dispatch(request)

    assert outcome.diagnostic is not None
    assert len(outcome.diagnostic) <= 512
    assert outcome.diagnostic.startswith("Codex exit=2; stderr=stderr-head:")
    assert outcome.diagnostic.endswith(":stderr-tail")
    assert adapter.diagnostics[-1].detail == outcome.diagnostic


def test_subprocess_runner_is_a_bounded_injected_codex_process_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = tuple(argv)
        observed.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout="s" * 10_000,
            stderr="stderr-head:" + "e" * 10_000 + ":stderr-tail",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = SubprocessCodexProcessRunner()

    result = runner.run(
        ("codex-test", "exec", "--ephemeral", "-"),
        cwd=tmp_path,
        prompt="bounded prompt",
        timeout_sec=12.0,
    )

    assert result.returncode == 0
    assert len(result.stdout) <= 4_096
    assert len(result.stderr) <= 4_096
    assert result.stderr.startswith("stderr-head:")
    assert result.stderr.endswith(":stderr-tail")
    assert observed["argv"] == ("codex-test", "exec", "--ephemeral", "-")
    assert observed["cwd"] == tmp_path
    assert observed["input"] == "bounded prompt"
    assert observed["timeout"] == 12.0
    assert observed["capture_output"] is True
    assert observed["text"] is True


def test_dispatch_diagnostics_are_bounded_and_never_become_a_receipt_log() -> None:
    runner = _BoundaryRunner(CodexProcessResult(returncode=3), write_output=False)
    adapter = CodexSemanticAdapter(
        process_runner=runner,
        packet_materializer=_PacketMaterializer(
            StagePacket(stage="director", payload={}, media=())
        ),
        codex_executable="codex-test",
    )

    for index in range(40):
        request = replace(_director_request(), request_id=f"request-current-{index:02d}")
        assert adapter.proposal_for(request) is None

    assert len(adapter.diagnostics) == 32
    assert adapter.diagnostics[0].request_id == "request-current-08"
    assert adapter.diagnostics[-1].request_id == "request-current-39"
    assert len({call.cwd for call in runner.calls}) == 40


def test_fresh_adapter_dispatches_only_when_core_replays_an_outstanding_request() -> None:
    request = _director_request()
    first_runner = _CodexRunner(_director_response(request))
    second_runner = _CodexRunner(_director_response(request))
    packet = StagePacket(stage="director", payload={}, media=())

    first = CodexSemanticAdapter(
        process_runner=first_runner,
        packet_materializer=_PacketMaterializer(packet),
        codex_executable="codex-test",
    ).proposal_for(request)
    second = CodexSemanticAdapter(
        process_runner=second_runner,
        packet_materializer=_PacketMaterializer(packet),
        codex_executable="codex-test",
    ).proposal_for(request)

    assert first is not None
    assert second is not None
    assert len(first_runner.calls) == 1
    assert len(second_runner.calls) == 1


def test_production_adapter_has_no_file_discovery_or_static_response_dropbox() -> None:
    source = Path(codex_semantic_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    called_names.update(
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )

    assert {"glob", "rglob", "walk", "listdir", "scandir"}.isdisjoint(called_names)
    assert "responses/" not in source
    assert "static_response" not in source
