"""A fresh agent must route Podcast work through the ADR-063 release contract."""

import json
import re
import subprocess
from pathlib import Path

from agents.brook.podcast_subtitles.memo_bundled_runner import (
    MemoBundledRunnerExecutionReceiptV1,
)
from scripts import podcast_subtitle_v2_evidence as evidence
from scripts import podcast_subtitle_v2_simple_step7 as simple_step7

SKILL = Path(".claude/skills/podcast-pipeline/SKILL.md")
HIGHLIGHT_SKILL = Path(".claude/skills/highlight-cut/SKILL.md")
LEGACY_HEADING = "## Explicit legacy forensic only"
HIGHLIGHT_LEGACY_HEADING = "## Explicit legacy forensic inputs"
RUNBOOK = Path(
    ".claude/skills/podcast-pipeline/references/"
    "memo-dual-audit-production-runbook.md"
)
CANONICAL_TRANSCRIBE_SKILL = Path(r"E:\nakama\.agents\skills\transcribe\SKILL.md")
VENV_PYTHON = Path(r"E:\nakama\.venv-v2\Scripts\python.exe")
ADR_063 = Path("docs/decisions/ADR-063-podcast-subtitle-production-simplification.md")


def _sections() -> tuple[str, str, str]:
    text = SKILL.read_text(encoding="utf-8")
    assert LEGACY_HEADING in text
    production, legacy = text.split(LEGACY_HEADING, maxsplit=1)
    return text, production, legacy


def test_skill_exposes_memo_dual_audit_as_the_only_default_subtitle_route() -> None:
    text, production, _legacy = _sections()
    required = (
        "podcast-subtitle-memo-dual-audit-release-request-v1",
        "podcast-subtitle-memo-dual-audit-release-v1",
        "podcast-subtitle-memo-dual-audit-release-export-v1",
        "podcast-subtitle-memo-dual-audit-audio-decisions-v1",
        "podcast-subtitle-memo-dual-audit-major-audio-plan-v1",
        "podcast-subtitle-memo-dual-audit-asr-provider-output-v1",
        "podcast-subtitle-memo-dual-audit-major-asr-run-v1",
        "podcast-subtitle-memo-dual-audit-release-status-v1",
        "podcast-subtitle-stage5-memo-dual-audit-handoff-v1",
        "memo-dual-audit-v1",
        "subtitle-release/memo-dual-audit-v1/",
        "podcast_subtitle_release.py init",
        "podcast_subtitle_release.py status",
        "podcast_subtitle_release.py seal",
        "podcast_subtitle_release.py finalize",
        "ready_to_finalize",
        "complete",
        "release.srt",
        "release-ledger.json",
        "export-manifest.json",
        "STAGE5-HANDOFF.json",
    )
    for marker in required:
        assert marker in production, f"production route missing: {marker}"

    assert text.count(LEGACY_HEADING) == 1

    init_block = production.split("podcast_subtitle_release.py init", maxsplit=1)[1].split(
        "```", maxsplit=1
    )[0]
    assert '--episode-id (Split-Path "<episode>" -Leaf)' in init_block
    assert "episode folder basename" in production
    assert "fail-fast" in production
    assert "mutable diagnostic snapshot" in production
    assert "E:\\nakama\\.venv-v2\\Scripts\\python.exe" in production
    assert "python scripts/" not in production


def test_adr_063_is_active_without_claiming_operational_episode_smoke() -> None:
    adr = ADR_063.read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")
    assert "- Status: Accepted / Active" in adr
    assert "CODE CUTOVER GO" in adr
    assert "**38 passed**" in adr
    assert "**249 passed**" in adr
    assert "P0 = 0, P1 = 0" in adr
    assert "operational smoke remains pending" in adr
    assert "Accepted / Active" in skill
    assert "operational E2E" in skill
    assert "implementation cutover pending verification" not in adr + skill


def test_s3_and_s4_runbook_has_exact_ordered_producers_and_contracts() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    s3 = runbook.split("## S3", maxsplit=1)[1].split("## S4", maxsplit=1)[0]
    s4 = runbook.split("## S4", maxsplit=1)[1]

    s3_ordered = (
        "run-memo-bundled",
        "repair-memo-srt",
        "prepare-recognition",
        "accept-recognition",
        "prepare-cues",
        "accept-cues",
        " status `",
    )
    s3_positions = [s3.index(marker) for marker in s3_ordered]
    assert s3_positions == sorted(s3_positions)
    release_source = Path("scripts/podcast_subtitle_release.py").read_text(encoding="utf-8")
    memo_version = re.search(
        r'recognition\.get\("memo_version"\) != "([^"]+)"', release_source
    )
    assert memo_version is not None
    assert f'--memo-version "{memo_version.group(1)}"' in s3
    assert "ggml-large-v2.bin" in runbook
    assert "memo-recognition-worker-audit-v1" in s3
    assert "memo-cue-worker-audit-v1" in s3
    assert "--episode-root $episode" in s3
    assert s3.count("--audit-a") == 2
    assert s3.count("--audit-b") == 2
    assert "--reviewer" not in s3
    assert "--confirm-reviewed" not in s3
    assert '"ready":true' in s3
    assert "memo-recognition.composite.execution.srt" in runbook
    assert "memo-recognition.repaired.srt" in runbook
    assert "memo-recognition.repair.v1.json" in runbook
    assert "memo-srt-zero-duration-repair-v1" in s3
    assert "memo-bundled-runner-execution-v1" in s3
    assert "memo-recognition.execution.v1.json" in runbook
    execution_flags = (
        "--memo-execution-receipt",
        "--memo-output-srt",
        "--memo-stdout",
        "--memo-stderr",
    )
    prepare = s3.split("### 2. Prepare recognition evidence", maxsplit=1)[1].split(
        "Dispatch two independent", maxsplit=1
    )[0]
    for flag in execution_flags:
        assert prepare.count(flag) == 1
    assert "--memo-runner $memoRunner" in prepare
    assert "--memo-model $memoModel" in prepare
    assert "memo_execution_receipt_sha256" in s3
    normalized_s3 = " ".join(s3.split())
    for marker in (
        "identical typed execution reference",
        "receipt/runner/model/input/SRT/stdout/stderr",
        "A clean release SRT must equal the sealed Memo output",
        "repaired release must bind its raw source hash",
    ):
        assert marker in normalized_s3
    assert "_fresh_verify_memo_execution" in release_source
    assert 'recognition.get("memo_execution_receipt")' in release_source
    assert '"memo_execution_receipt_sha256": execution_ref.sha256' in release_source
    repair_definition = s3.split("$repairLineageArgs = @(", maxsplit=1)[1].split(
        ")", maxsplit=1
    )[0]
    assert repair_definition.index('"--raw-source-export"') < repair_definition.index(
        '"--repair-receipt"'
    )
    downstream = s3.split("### 2. Prepare recognition evidence", maxsplit=1)[1]
    assert downstream.count("@repairLineageArgs `") == 4

    s4_ordered = (
        '"agent": "A"',
        "merge-official",
        '"contract": "podcast-subtitle-memo-dual-audit-arbitration-v1"',
        "apply-official-arbitration",
        "release `seal`",
        "awaiting_major_dual_asr",
    )
    s4_positions = [s4.index(marker) for marker in s4_ordered]
    assert s4_positions == sorted(s4_positions)
    for marker in (
        '"cues_reviewed"',
        '"major_risk"',
        '"cue_numbers"',
        '"start"',
        '"end"',
        '"original"',
        '"proposed"',
        '"category"',
        '"confidence"',
        '"evidence"',
        '"needs_audio"',
        "exactly cover every base queue component",
        "cannot invent proposal authority",
    ):
        assert marker in s4


def test_s3_documented_execution_and_worker_schemas_match_runtime() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    schema = runbook.split(
        "<!-- runtime-schema:memo-execution-receipt:start -->", maxsplit=1
    )[1].split(
        "<!-- runtime-schema:memo-execution-receipt:end -->", maxsplit=1
    )[0]
    documented_execution_fields = set(
        json.loads(schema.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0])
    )
    assert documented_execution_fields == set(
        MemoBundledRunnerExecutionReceiptV1.model_fields
    )

    s3 = runbook.split("## S3", maxsplit=1)[1].split("## S4", maxsplit=1)[0]
    recognition_marker = '"contract": "memo-recognition-worker-audit-v1"'
    recognition_prefix = s3.split(recognition_marker, maxsplit=1)[0]
    recognition_start = recognition_prefix.rfind("```json") + len("```json")
    recognition_sample = s3[recognition_start:].split("```", maxsplit=1)[0]
    recognition_fields = set(json.loads(recognition_sample))
    assert recognition_fields == set(evidence._MemoRecognitionWorkerAuditV1.model_fields)


def test_runbook_closed_category_enums_match_runtime() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    def documented(name: str) -> frozenset[str]:
        section = runbook.split(f"<!-- runtime-enum:{name}:start -->", maxsplit=1)[1]
        section = section.split(f"<!-- runtime-enum:{name}:end -->", maxsplit=1)[0]
        payload = section.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
        return frozenset(json.loads(payload))

    assert documented("safe") == simple_step7._SAFE_CATEGORY_ALLOWLIST
    assert documented("major") == simple_step7._OFFICIAL_MAJOR_CATEGORIES


def test_production_cli_help_smoke_uses_repo_venv() -> None:
    scripts = (
        "scripts/run_audio_prep.py",
        "scripts/podcast_subtitle_v2_evidence.py",
        "scripts/podcast_subtitle_v2_simple_step7.py",
        "scripts/podcast_subtitle_release.py",
        "scripts/run_highlight_cut.py",
        "scripts/run_cut_shortlist.py",
    )
    for script in scripts:
        result = subprocess.run(
            [str(VENV_PYTHON), script, "--help"],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_s3_runbook_flags_match_evidence_cli_help() -> None:
    script = "scripts/podcast_subtitle_v2_evidence.py"

    def help_for(command: str) -> str:
        result = subprocess.run(
            [str(VENV_PYTHON), script, command, "--help"],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    repair_help = help_for("repair-memo-srt")
    for flag in ("--source-export", "--output", "--receipt-output"):
        assert flag in repair_help

    prepare_help = help_for("prepare-recognition")
    for flag in (
        "--episode-root",
        "--memo-execution-receipt",
        "--memo-output-srt",
        "--memo-stdout",
        "--memo-stderr",
        "--raw-source-export",
        "--repair-receipt",
        "--memo-runner",
        "--memo-model",
    ):
        assert flag in prepare_help

    accept_help = help_for("accept-recognition")
    assert "--raw-source-export" in accept_help
    assert "--repair-receipt" in accept_help
    assert "--memo-execution-receipt" not in accept_help


def test_transcribe_entry_metadata_routes_to_adr_063() -> None:
    metadata = CANONICAL_TRANSCRIBE_SKILL.read_text(encoding="utf-8")
    production = metadata.split("## Legacy V1 appendix", maxsplit=1)[0]
    assert "# Transcribe — Memo Dual-Audit Release V1" in production
    assert "**Authoritative production instructions:**" in production
    assert "Memo Dual-Audit Release V1 production authority" in production


def test_skill_runs_to_long_highlight_shortlist_before_ordinary_human_gate() -> None:
    _text, production, _legacy = _sections()
    state_machine = production.split("## State machine", maxsplit=1)[1].split(
        "## S0–S2", maxsplit=1
    )[0]
    ordered_markers = (
        "S2 NORMALIZED",
        "S3 MEMO",
        "S4 TEXT AUDIT",
        "S5 MAJOR AUDIO",
        "S6 RELEASE",
        "S7 RESOLVE",
        "S8 HIGHLIGHTS",
        "Highlight shortlist review",
    )
    positions = [state_machine.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)

    required = (
        "Audio/Live-Mix.wav",
        "Audio/1_COMBO-1.wav",
        "Audio/2_COMBO-2.wav",
        "normalized-handoff.v1.json",
        "Memo",
        "兩份獨立全文 audit",
        "Faster-Whisper",
        "Qwen3-ASR",
        "build_resolve_project.py",
        "run_highlight_cut.py",
        "run_cut_shortlist.py",
        "long shortlist",
        "只列 candidates，不替使用者選 IDs",
    )
    for marker in required:
        assert marker in production, f"E2E-to-review route missing: {marker}"

    early_stop_conditions = ("wrong episode", "hash／coverage／timebase catastrophic failure")
    for marker in early_stop_conditions:
        assert marker in production


def test_podcast_route_performs_actual_resolve_build_then_complete_highlight_flow() -> None:
    _text, production, _legacy = _sections()
    route = production.split("## S7–S8", maxsplit=1)[1].split("## S9", maxsplit=1)[0]

    python310 = r"C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe"
    dry_run = 'scripts\\build_resolve_project.py "<episode>" --dry-run'
    probe = "from scripts.build_resolve_project import connect_resolve"
    actual_build = 'scripts\\build_resolve_project.py "<episode>"'
    lines = [line.strip() for line in route.splitlines()]
    assert dry_run in lines
    assert probe in route
    assert actual_build in lines
    probe_line = next(line.strip() for line in route.splitlines() if probe in line)
    assert lines.index(dry_run) < lines.index(probe_line) < lines.index(actual_build)
    assert route.count(python310) >= 3
    assert "py -3.10" not in route

    flow = route.split("Exact routing 是：", maxsplit=1)[1].split("```", maxsplit=2)[1]
    ordered = (
        '--mining-input',
        'highlights/miner-story.json',
        'highlights/miner-punch.json',
        'highlights/miner-value.json',
        '--merge-miners',
        'highlights/candidates.json',
        'highlights/review_azhe.json',
        'highlights/review_kevin.json',
        'highlights/review_shufen.json',
        'highlights/lens_brand.json',
        'highlights/lens_renee.json',
        'review schema/coverage/citation QA',
        '--format long',
        'Highlight shortlist review gate',
    )
    positions = [flow.index(marker) for marker in ordered]
    assert positions == sorted(positions)
    assert "啟用 `highlight-cut` skill" in route
    assert "HIGHLIGHT_PERSONA_REVIEW_NOT_IMPLEMENTED" in route


def test_major_audio_producers_run_in_order_before_finalize_without_human_gate() -> None:
    _text, production, _legacy = _sections()
    major = production.split("### Major-risk dual ASR", maxsplit=1)[1].split(
        "### Finalize", maxsplit=1
    )[0]
    finalize = production.split("### Finalize", maxsplit=1)[1].split(
        "## S7–S8", maxsplit=1
    )[0]

    ordered = (
        "prepare-major-audio",
        "--family faster",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        "--family qwen",
        "7278e1e70fe206f11671096ffdd38061171dd6e5",
        "c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
        "build-audio-decisions",
    )
    positions = [major.index(marker) for marker in ordered]
    assert positions == sorted(positions)
    assert "只載入模型一次" in major
    assert "沿用已完成 provider outputs" in major
    assert "不是 human gate" in major
    assert "transcript" in major and "segments" in major
    assert "podcast_subtitle_release.py finalize" in finalize


def test_highlight_skill_defines_executable_agent_owned_mining_and_review_contracts() -> None:
    text = HIGHLIGHT_SKILL.read_text(encoding="utf-8")
    before_legacy, legacy_and_after = text.split(HIGHLIGHT_LEGACY_HEADING, maxsplit=1)
    legacy, after_legacy = legacy_and_after.split("## Step 2.5", maxsplit=1)
    production = before_legacy + "## Step 2.5" + after_legacy

    sections = (
        "## Step 1 — 取得 mining input",
        "## Step 1.1 — agent-owned 3-miner dispatch",
        "## Step 1.2 — deterministic merge to candidates.json",
        "## Step 2 — agent-owned blind persona and lens review",
        "## Step 2.4 — long Highlight shortlist gate",
    )
    section_positions = [production.index(section) for section in sections]
    assert section_positions == sorted(section_positions)

    required_production = (
        "memo-dual-audit-v1",
        "STAGE5-HANDOFF.json",
        'run_highlight_cut.py "<episode>" --mining-input',
        "highlights/miner-story.json",
        "highlights/miner-punch.json",
        "highlights/miner-value.json",
        '"contract": "podcast-highlight-miner-output-v1"',
        '"source_srt_sha256"',
        '"subtitle_lineage"',
        "--merge-miners",
        "podcast-highlight-candidates-v1",
        "highlights/candidates.json",
        "highlights/review_azhe.json",
        "highlights/review_kevin.json",
        "highlights/review_shufen.json",
        "highlights/lens_brand.json",
        "highlights/lens_renee.json",
        '"source_sha256"',
        "quote citations",
        "只有五份 review outputs 都驗證通過",
        'run_cut_shortlist.py "<episode>" --format long',
        "唯一正常停點",
    )
    for marker in required_production:
        assert marker in production, f"highlight production route missing: {marker}"

    mining_command = next(
        line
        for line in production.splitlines()
        if "run_highlight_cut.py" in line and "--mining-input" in line
    )
    merge_command = next(
        line
        for line in production.splitlines()
        if "run_highlight_cut.py" in line and "--merge-miners" in line
    )
    assert "--subtitle-release-handoff" not in mining_command
    assert "--subtitle-release-handoff" not in merge_command
    assert "--degraded-release-handoff" not in production
    assert "Verified Projection" not in production
    assert "python scripts/" not in production
    assert "py -3.10" not in production
    assert "--degraded-release-handoff" in legacy
    assert "Formal Subtitle V2" in legacy


def test_formal_v2_and_degraded_routes_are_explicit_legacy_only() -> None:
    _text, production, legacy = _sections()
    legacy_only = (
        "Verified Projection",
        "Canonical Generation",
        "Semantic Units",
        "526 correction packets",
        "10%／30% sampling",
        "--degraded-release-handoff",
        "verify-legacy",
    )
    for marker in legacy_only:
        assert marker not in production, f"legacy marker leaked into default route: {marker}"
        assert marker in legacy, f"legacy boundary does not preserve: {marker}"

    forbidden_everywhere = (
        "prep 完成 → 下一步 subtitle-gen",
        "run_transcribe.py",
        "--no-auphonic",
        "--outtakes-from",
    )
    whole = production + legacy
    for marker in forbidden_everywhere:
        assert marker not in whole, f"skill still exposes obsolete production route: {marker}"
