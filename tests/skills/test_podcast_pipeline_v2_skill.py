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
ADR_064 = Path("docs/decisions/ADR-064-podcast-editorial-master-before-repurpose.md")


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


def test_editorial_master_gate_precedes_packaging_and_highlights() -> None:
    text, production, _legacy = _sections()
    assert "S7E EDITORIAL MASTER" in production
    assert "podcast-editorial-master-v1" in production
    assert "EDITORIAL_MASTER_RUNTIME_NOT_IMPLEMENTED" not in production
    assert "silent fallback" in production
    assert "--human-approved --approved-by" in production
    assert "S7P FULL PACKAGING" in production
    assert "cut_id=full" in production
    assert "不依賴\nHighlight winner" in production
    assert "不得阻塞 Highlight mining" in production
    assert "暗色書封中景" in production
    assert production.index("Actual build exit 0") < production.index(
        "podcast_editorial_master.py inspect"
    )
    exporter = (
        "podcast_editorial_master.py inspect",
        "podcast_editorial_master.py status",
        "podcast_editorial_master.py seal",
        "podcast_editorial_master.py verify",
    )
    exporter_positions = [production.index(marker) for marker in exporter]
    assert exporter_positions == sorted(exporter_positions)
    exporter_route = production.split("Human approval 之前", maxsplit=1)[1].split(
        "第一次 `status`", maxsplit=1
    )[0]
    python310 = r"C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe"
    assert exporter_route.count(python310) == 3
    assert (
        r"E:\nakama\.venv-v2\Scripts\python.exe "
        r'scripts\podcast_editorial_master.py status "<episode>"'
    ) in exporter_route
    assert production.index("podcast_editorial_master.py verify") < production.index(
        "cut_id=full"
    )
    assert production.index("cut_id=full") < production.index("--mining-input")


def test_adr_064_records_editorial_master_without_raw_fallback() -> None:
    adr = ADR_064.read_text(encoding="utf-8")
    assert "- Status: Accepted" in adr
    assert "podcast-editorial-master-v1" in adr
    assert "raw 1320.300–1323.140" in adr
    assert "value-L02` and `punch-L04` remain raw-derived" in adr
    assert "P9 implementation task prompt" in adr


def test_adr_063_is_active_with_clean_episode_operational_smoke() -> None:
    adr = ADR_063.read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")
    assert "- Status: Accepted / Active" in adr
    assert "CODE CUTOVER GO" in adr
    assert "**38 passed**" in adr
    assert "**249 passed**" in adr
    assert "P0 = 0, P1 = 0" in adr
    assert "20260805 林之晨" in adr
    assert "74121675c36d5201ac700625402da914f7ead0790620d1eb423d547859db2f98" in adr
    assert "da5ad24e962868db561bf617e9987f90679ea0623da3c5406d8384c342e08efb" in adr
    assert "4c8badcf05388f0a592b078563a639b8b87c65b9e843fa7174c28d05149aeede" in adr
    assert "5,720.397 s" in adr
    assert "no `winners.json` and no YouTube upload" in adr
    assert "Accepted / Active" in skill
    assert "clean operational E2E smoke" in skill
    assert "operational smoke remains pending" not in adr + skill
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
    assert "no trailing LF/newline" in s3
    assert "do not weaken the acceptance validator" in normalized_s3

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
        "include explicit JSON `null`",
        "`b_risks` is `list[str]`",
        "`accept_identical`",
        "`accept_single`",
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
        "scripts/podcast_editorial_master.py",
        "scripts/podcast_identity_placement.py",
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
        "podcast-editorial-master-v1",
        "EDITORIAL-MASTER.json",
        "master.srt",
        "master.mp4",
        'run_highlight_cut.py "<episode>" --mining-input',
        "highlights/miner-story.json",
        "highlights/miner-punch.json",
        "highlights/miner-value.json",
        '"contract": "podcast-highlight-miner-output-v2"',
        '"source_srt_sha256"',
        '"editorial_master_lineage"',
        "`status`／`srt_path`／`elapsed_sec`",
        "不得把 `elapsed_sec` 這類執行耗時混入 identity",
        "--merge-miners",
        "podcast-highlight-candidates-v2",
        "`sections`",
        "highlights/candidates.json",
        "highlights/review_azhe.json",
        "highlights/review_kevin.json",
        "highlights/review_shufen.json",
        "highlights/lens_brand.json",
        "highlights/lens_renee.json",
        '"source_sha256"',
        "`hashlib.sha256(...).hexdigest()` 的小寫 hex",
        "PowerShell `Get-FileHash` 的大寫顯示",
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


def test_identity_placement_route_is_quorum_bound_and_fail_closed() -> None:
    podcast, production, _legacy = _sections()
    highlight = HIGHLIGHT_SKILL.read_text(encoding="utf-8")
    required = (
        "podcast-identity-placement-v1",
        "podcast-identity-placement-worker-audit-v1",
        "podcast_identity_placement.py accept",
        "podcast_identity_placement.py emit-event",
        "podcast_identity_placement.py verify",
        '"worker_id"',
        '"editorial_master"',
        '"cut_srt"',
        '"accepted_guest_cue"',
        '"text_sha256"',
        "IDENTITY-PLACEMENT.json",
        "free-string",
        "同 worker",
        "stale hash",
        "path escape",
        "衝突／無法判定才回使用者",
        "43.0–48.2",
    )
    for marker in required:
        assert marker in production, f"podcast identity route missing: {marker}"

    highlight_required = (
        "podcast-identity-placement-worker-audit-v1",
        "podcast_identity_placement.py accept",
        "podcast_identity_placement.py emit-event",
        "podcast_identity_placement.py verify",
        "Editorial Master identity",
        "cut SRT",
        "same worker",
        "cross-episode/path escape",
        "只有兩 audit 衝突或皆無法可靠\n判斷才是 HITL",
        "43.0 秒",
        "48.2 秒",
        r"C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe",
        "inspect`、`seal`、`verify --live`",
    )
    for marker in highlight_required:
        assert marker in highlight, f"highlight identity route missing: {marker}"

    route = production.split("## S9", maxsplit=1)[1].split("## S10", maxsplit=1)[0]
    ordered = (
        "run_short_tighten.py",
        "podcast_identity_placement.py accept",
        "podcast_identity_placement.py emit-event",
        "podcast_identity_placement.py verify",
        "run_short_director.py",
        "run_short_broll.py",
        "run_short_titles.py",
        "run_short_review.py",
    )
    positions = [route.index(marker) for marker in ordered]
    assert positions == sorted(positions)
    assert "podcast_identity_placement.py status" in podcast
    assert '--name "<guest-name>" --title "<guest-title>"' in route
    assert "--guest-namecard-start" not in route
    assert '"subtitle_lineage"' not in production


def test_identity_skill_command_paths_match_runtime_contract() -> None:
    _text, production, _legacy = _sections()
    route = production.split(
        "scripts\\podcast_identity_placement.py accept", maxsplit=1
    )[1].split("scripts\\run_short_director.py", maxsplit=1)[0]
    assert (
        '--cut-srt "<episode>/highlights/srt/<winner-id>_tight_rNNN.srt"'
        in route
    )
    assert (
        '--audit-a "<episode>/highlights/identity-placement/'
        '<winner-id>/identity-audit-a.json"'
    ) in route
    assert (
        '--audit-b "<episode>/highlights/identity-placement/'
        '<winner-id>/identity-audit-b.json"'
    ) in route
    assert "highlights/review/<winner-id>/subs.srt" not in production


def test_podcast_visual_production_routes_agent_judgment_before_materializer() -> None:
    podcast = SKILL.read_text(encoding="utf-8")
    highlight = HIGHLIGHT_SKILL.read_text(encoding="utf-8")
    director = Path(".claude/skills/brook-director/SKILL.md").read_text(encoding="utf-8")
    dp = Path(".claude/skills/brook-dp/SKILL.md").read_text(encoding="utf-8")

    route = podcast.split("## S9", maxsplit=1)[1].split("## S10", maxsplit=1)[0]
    ordered = (
        "DIRECTOR-WORK.json",
        "brook-director",
        "DIRECTOR-PLAN.json",
        "brook-dp",
        "DP-FULFILLMENT.json",
        "SEMANTIC-AUDIT.json",
        "ready_to_materialize",
        "run_short_broll.py",
    )
    production_chain = route.split(
        "接著的 agent-owned receipt 順序固定為", maxsplit=1
    )[1]
    positions = [production_chain.index(marker) for marker in ordered]
    assert positions == sorted(positions)

    contracts = (
        "podcast-highlight-visual-work-packet-v1",
        "podcast-highlight-director-plan-v1",
        "podcast-highlight-dp-fulfillment-v1",
        "podcast-highlight-visual-semantic-audit-v1",
    )
    combined = "\n".join((route, highlight, director, dp))
    for contract in contracts:
        assert contract in combined

    for text in (podcast, highlight):
        assert "run_short_director.py` = camera/Timeline director" in text
        assert "run_short_broll.py` = materializer" in text
        assert "不代表 `brook-director` skill" in text
        assert "不代表 `brook-dp` skill" in text

    assert "highlights/visual-pipeline/<winner-id>" in route
    assert "缺少／stale／invalid" in route
    assert "只有 ambiguity 才是 HITL" in route


def test_visual_skill_contract_covers_every_content_visual_lane() -> None:
    podcast = SKILL.read_text(encoding="utf-8")
    highlight = HIGHLIGHT_SKILL.read_text(encoding="utf-8")
    director = Path(".claude/skills/brook-director/SKILL.md").read_text(encoding="utf-8")
    dp = Path(".claude/skills/brook-dp/SKILL.md").read_text(encoding="utf-8")
    combined = "\n".join((podcast, highlight, director, dp))

    assert "所有 content visuals" in combined
    assert "Stock／Hero／keyword／quote／chapter／card" in combined
    assert "on_screen_text" in director
    assert "`target_lane`" in dp
    assert "B-roll 與 title implementations" in dp
    for text in (podcast, highlight):
        assert "run_short_titles.py` = materializer" in text
        assert "結構性 badge／camera correction／guest namecard" in text


def test_visual_skills_name_exact_revision_commands_and_trusted_worker_order() -> None:
    podcast = SKILL.read_text(encoding="utf-8")
    highlight = HIGHLIGHT_SKILL.read_text(encoding="utf-8")
    director = Path(".claude/skills/brook-director/SKILL.md").read_text(encoding="utf-8")
    dp = Path(".claude/skills/brook-dp/SKILL.md").read_text(encoding="utf-8")
    combined = "\n".join((podcast, highlight, director, dp))

    for marker in (
        "PENDING.json",
        "CURRENT.json",
        "revisions/<revision-id>/",
        "podcast_highlight_visual_orchestrator.py",
        "podcast_highlight_visual_pipeline.py",
        "--revision-request",
        "--revision-id",
        "--proposal",
        "--worker-id",
        "--execution-id",
        "--session-id",
    ):
        assert marker in combined

    director_commands = next(
        block
        for block in re.findall(r"```powershell\n(.*?)```", director, flags=re.DOTALL)
        if " accept-director " in block
    )
    dp_commands = next(
        block
        for block in re.findall(r"```powershell\n(.*?)```", dp, flags=re.DOTALL)
        if " accept-dp " in block
    )
    ordered_commands = director_commands + dp_commands
    markers = (" init ", " accept-director ", " accept-dp ", " accept-audit ", " verify ")
    positions = [ordered_commands.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert "codex exec resume <DIRECTOR_SESSION_ID>" in director
    assert "proposal自報 identity一律拒絕" in podcast
    assert "DP另開不同 session" in director
    assert "accept-audit`成功才切 CURRENT" in highlight
    assert "同一 immutable request retry" in combined
    assert "generic agent handwrite receipts" in podcast


def test_finished_save_draft_skill_routes_producer_before_resolve_and_manifest_v2() -> None:
    podcast = SKILL.read_text(encoding="utf-8")
    route = podcast.split("Bridge finished review按", maxsplit=1)[1].split(
        "`accept` 之前", maxsplit=1
    )[0]
    ordered = (
        "immutable `request.json`",
        "generic agent",
        "run_visual_pipeline",
        "emit_audited_recipe",
        "preflight",
        "Resolve transaction",
        "nakama.finished_cut_review_manifest.v2",
    )
    positions = [route.index(marker) for marker in ordered]
    assert positions == sorted(positions)
    assert "context['request_path']" in route
    assert "保留上一 CURRENT" in route
    assert "不新增中途 approval" in route


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
