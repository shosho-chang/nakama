from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles import __main__ as cli
from agents.brook.podcast_subtitles.facade import StatusView
from agents.brook.podcast_subtitles.module import Interrupted, PodcastSubtitleV2
from agents.brook.podcast_subtitles.native_resolution import ResolveNativeRequest
from agents.brook.podcast_subtitles.production import ProductionConfigurationError


def _run_args(tmp_path: Path, *extra: str) -> list[str]:
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(3)
        target.setframerate(48_000)
        target.writeframes(b"\0" * 48_000 * 2 * 3)
    outline = tmp_path / "outline.txt"
    outline.write_text("episode interview outline", encoding="utf-8")
    outline_bytes = outline.read_bytes()
    manifest = tmp_path / "references.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "episode_id": "episode",
                "sources": [
                    {
                        "source_id": "episode-outline",
                        "kind": "interview_outline",
                        "path": outline.name,
                        "title": "Episode interview outline",
                        "version": "approved-v1",
                        "document_date": "undated",
                        "trust_tier": "contextual",
                        "sha256": hashlib.sha256(outline_bytes).hexdigest(),
                        "size_bytes": len(outline_bytes),
                        "author": None,
                        "publisher": None,
                        "authority": {
                            "schema_version": 1,
                            "logical_source_id": "logical:episode-outline",
                            "version_id": "approved-v1",
                            "version_status": "active",
                            "release_status": "approved",
                            "supersedes": [],
                            "source_kind": "interview_outline",
                            "trust_tier": "contextual",
                            "role": "contextual_reference",
                            "subject": {
                                "schema_version": 1,
                                "kind": "episode",
                                "stable_id": "episode",
                                "display_name": "Episode interview outline",
                            },
                            "owner": None,
                            "allowed_scopes": [],
                            "attestation": {
                                "schema_version": 1,
                                "confirmed": False,
                                "provenance": "none",
                                "attestor": None,
                                "record_sha256": None,
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return [
        "--episode-root",
        str(tmp_path),
        "--factory",
        "fixture:build",
        "--reference-manifest",
        str(manifest),
        "run",
        "--episode-id",
        "episode",
        "--source-audio",
        str(source),
        *extra,
    ]


def _reference_bound_factory(module: PodcastSubtitleV2):
    def factory(context):
        bundle = context.reference_bundle
        assert bundle is not None
        module._reference_retriever = bundle.retriever
        module._reference_retriever_identity = bundle.retriever_identity
        module._reference_parser_registry = bundle.parser_registry
        return module

    return factory


def test_cli_run_requires_reference_manifest_before_composition_or_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    composition_called = False
    factory_called = False

    def build_context(**_kwargs):
        nonlocal composition_called
        composition_called = True
        raise AssertionError("composition must not initialize")

    def load_factory(_spec: str):
        nonlocal factory_called
        factory_called = True
        raise AssertionError("factory must not initialize")

    monkeypatch.setattr(cli, "build_factory_context", build_context)
    monkeypatch.setattr(cli, "_load_factory", load_factory)
    with pytest.raises(SystemExit) as failed:
        cli.main(
            [
                "--episode-root",
                str(tmp_path),
                "run",
                "--episode-id",
                "episode",
                "--source-audio",
                str(source),
            ]
        )
    assert failed.value.code == 2
    assert "run requires an episode-specific --reference-manifest" in capsys.readouterr().err
    assert composition_called is False
    assert factory_called is False


def test_parser_defaults_to_trusted_production_factory(tmp_path: Path) -> None:
    args = cli._parser().parse_args(
        [
            "--episode-root",
            str(tmp_path),
            "status",
        ]
    )

    assert args.factory == "agents.brook.podcast_subtitles.production:build_production"


def test_cli_loads_program_root_dotenv_before_factory_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    module_path = repo_root / "agents" / "brook" / "podcast_subtitles" / "__main__.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# test module location\n", encoding="utf-8")
    (repo_root / ".env").write_text(
        "PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL=from-test-dotenv\n",
        encoding="utf-8",
    )
    outside_repo = tmp_path / "outside-repo"
    outside_repo.mkdir()
    monkeypatch.chdir(outside_repo)
    monkeypatch.setattr(cli, "__file__", str(module_path))
    monkeypatch.delenv("PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL", raising=False)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    observed: list[str | None] = []
    module = object.__new__(PodcastSubtitleV2)

    def factory(_context):
        observed.append(cli.os.environ.get("PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL"))
        return module

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def status(self) -> StatusView:
            return StatusView(state="complete")

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: factory)
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    assert (
        cli.main(
            [
                "--episode-root",
                str(tmp_path / "episode"),
                "--factory",
                "fixture:build",
                "status",
            ]
        )
        == 0
    )
    assert observed == ["from-test-dotenv"]


def test_cli_process_environment_wins_over_repo_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    module_path = repo_root / "agents" / "brook" / "podcast_subtitles" / "__main__.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# test module location\n", encoding="utf-8")
    (repo_root / ".env").write_text(
        "PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL=from-test-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "__file__", str(module_path))
    monkeypatch.setenv("PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL", "from-process")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    observed: list[str | None] = []
    module = object.__new__(PodcastSubtitleV2)

    def factory(_context):
        observed.append(cli.os.environ.get("PODCAST_SUBTITLE_V2_TEXT_AUDIT_MODEL"))
        return module

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def status(self) -> StatusView:
            return StatusView(state="complete")

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: factory)
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    assert (
        cli.main(
            [
                "--episode-root",
                str(tmp_path / "episode"),
                "--factory",
                "fixture:build",
                "status",
            ]
        )
        == 0
    )
    assert observed == ["from-process"]


def test_cli_missing_production_settings_remain_fail_closed_after_dotenv_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    module_path = repo_root / "agents" / "brook" / "podcast_subtitles" / "__main__.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# test module location\n", encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(module_path))
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    for name in tuple(cli.os.environ):
        if name.startswith("PODCAST_SUBTITLE_V2_"):
            monkeypatch.delenv(name)

    with pytest.raises(
        ProductionConfigurationError,
        match="PODCAST_SUBTITLE_V2_NORMALIZED_HANDOFF_MANIFEST.*required",
    ):
        cli.main(["--episode-root", str(tmp_path / "episode"), "status"])


def test_cli_invalid_dotenv_production_setting_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    module_path = repo_root / "agents" / "brook" / "podcast_subtitles" / "__main__.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("# test module location\n", encoding="utf-8")
    required_paths = {
        "NORMALIZED_HANDOFF_MANIFEST": "handoff.json",
        "MEMO_RECOGNITION_MANIFEST": "memo-recognition.json",
        "MEMO_RECOGNITION_SOURCE_EXPORT": "memo-recognition.srt",
        "MEMO_RECOGNITION_ACCEPTANCE_RECEIPT": "memo-recognition-acceptance.json",
        "MEMO_CUE_SOURCE_EXPORT": "memo-cues.srt",
        "MEMO_CUE_ACCEPTANCE_RECEIPT": "memo-cues-acceptance.json",
    }
    dotenv_lines = [
        *(f"PODCAST_SUBTITLE_V2_{name}={value}" for name, value in required_paths.items()),
        "PODCAST_SUBTITLE_V2_ENABLE_QWEN_CORROBORATION=true",
        "PODCAST_SUBTITLE_V2_QWEN_MODEL_REVISION=main",
    ]
    (repo_root / ".env").write_text("\n".join(dotenv_lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(cli, "__file__", str(module_path))
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    for name in tuple(cli.os.environ):
        if name.startswith("PODCAST_SUBTITLE_V2_"):
            monkeypatch.delenv(name)

    with pytest.raises(
        ProductionConfigurationError,
        match="PODCAST_SUBTITLE_V2_QWEN_MODEL_REVISION.*exact commit revision",
    ):
        cli.main(["--episode-root", str(tmp_path / "episode"), "status"])


def test_cli_global_help_lists_all_operator_commands_without_initializing_providers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("--help must not load environment or initialize providers")

    monkeypatch.setattr(cli, "_load_repo_environment", forbidden)
    monkeypatch.setattr(cli, "_load_factory", forbidden)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])

    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    assert "{run,status,review,decide,decide-native,project}" in help_text
    assert "compare" not in help_text


def test_cli_help_documents_native_resolution(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("subcommand --help must not load environment or initialize providers")

    monkeypatch.setattr(cli, "_load_repo_environment", forbidden)
    monkeypatch.setattr(cli, "_load_factory", forbidden)
    with pytest.raises(SystemExit) as stopped:
        cli.main(["decide-native", "--help"])

    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    for option in (
        "--correction-acceptance-verdict",
        "--original-confirmation-authorization",
        "--correction-acceptance-policy",
        "--original-confirmation-policy",
    ):
        assert option in help_text
    assert "--replacement-text" not in help_text


def test_parser_accepts_two_explicit_labeled_tracks_and_alias(tmp_path: Path) -> None:
    host = tmp_path / "host track.wav"
    guest = tmp_path / "guest=track.wav"
    host.write_bytes(b"host")
    guest.write_bytes(b"guest")
    parser = cli._parser()

    args = parser.parse_args(
        _run_args(
            tmp_path,
            "--mic-track",
            f"HOST={host}",
            "--speaker-track",
            f"GUEST={guest}",
        )
    )
    tracks = tuple(args.speaker_tracks)
    cli._validate_speaker_tracks(parser, tracks)

    assert tuple(track.speaker_label for track in tracks) == ("HOST", "GUEST")
    assert tuple(track.path for track in tracks) == (host, guest)


@pytest.mark.parametrize("value", ["HOST", "=track.wav", "HOST="])
def test_parser_rejects_track_without_explicit_label_and_path(
    tmp_path: Path,
    value: str,
) -> None:
    with pytest.raises(SystemExit):
        cli._parser().parse_args(_run_args(tmp_path, "--mic-track", value))


def test_cli_rejects_one_track_before_factory_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = tmp_path / "host.wav"
    host.write_bytes(b"host")
    initialized = False

    def load_factory(_spec: str):
        nonlocal initialized
        initialized = True
        raise AssertionError("factory must not initialize")

    monkeypatch.setattr(cli, "_load_factory", load_factory)
    with pytest.raises(SystemExit):
        cli.main(_run_args(tmp_path, "--mic-track", f"HOST={host}"))

    assert initialized is False


@pytest.mark.parametrize("failure", ["duplicate_label", "duplicate_path", "missing"])
def test_cli_rejects_invalid_track_pair_before_factory_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    host = tmp_path / "host.wav"
    guest = tmp_path / "guest.wav"
    host.write_bytes(b"host")
    guest.write_bytes(b"guest")
    labels = ("HOST", "GUEST")
    paths = (host, guest)
    if failure == "duplicate_label":
        labels = ("HOST", "HOST")
    elif failure == "duplicate_path":
        paths = (host, host)
    else:
        paths = (host, tmp_path / "missing.wav")
    monkeypatch.setattr(
        cli,
        "_load_factory",
        lambda _spec: (_ for _ in ()).throw(AssertionError("factory must not initialize")),
    )

    with pytest.raises(SystemExit):
        cli.main(
            _run_args(
                tmp_path,
                "--mic-track",
                f"{labels[0]}={paths[0]}",
                "--mic-track",
                f"{labels[1]}={paths[1]}",
            )
        )


def test_cli_passes_tracks_to_create_request_without_filename_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = tmp_path / "anonymous-a.wav"
    second = tmp_path / "anonymous-b.wav"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    module = object.__new__(PodcastSubtitleV2)
    captured = []

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def run(self, request):
            captured.append(request)
            return {"status": "captured"}

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: _reference_bound_factory(module))
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    assert (
        cli.main(
            _run_args(
                tmp_path,
                "--mic-track",
                f"主持人={first}",
                "--mic-track",
                f"來賓={second}",
            )
        )
        == 0
    )

    assert len(captured) == 1
    assert tuple(track.speaker_label for track in captured[0].speaker_tracks) == (
        "主持人",
        "來賓",
    )
    assert tuple(track.path for track in captured[0].speaker_tracks) == (first, second)
    assert '"status": "captured"' in capsys.readouterr().out


def test_cli_decide_native_passes_exact_acceptance_artifact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = object.__new__(PodcastSubtitleV2)
    artifacts = {
        "verdict": b'{"verdict":"exact"}\r\n',
        "policy": b'{"policy":"exact"}\n',
        "audio": b'{"audio":"exact"}\r\n',
        "adjudication": b'{"adjudication":"exact"}\n',
        "authority": b'{"authority":"exact"}\r\n',
    }
    paths = {name: tmp_path / f"{name}.json" for name in artifacts}
    for name, path in paths.items():
        path.write_bytes(artifacts[name])
    captured: list[ResolveNativeRequest] = []

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def decide_native(self, request: ResolveNativeRequest):
            captured.append(request)
            return {"status": "captured"}

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: lambda _context: module)
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    assert (
        cli.main(
            [
                "--episode-root",
                str(tmp_path),
                "decide-native",
                "--generation-id",
                "a" * 64,
                "--correction-acceptance-verdict",
                str(paths["verdict"]),
                "--correction-acceptance-policy",
                str(paths["policy"]),
                "--human-audio-receipt",
                str(paths["audio"]),
                "--human-reference-adjudication",
                str(paths["adjudication"]),
                "--reference-authority-proof",
                str(paths["authority"]),
            ]
        )
        == 0
    )

    assert captured == [
        ResolveNativeRequest(
            generation_id="a" * 64,
            correction_acceptance_verdict=artifacts["verdict"],
            correction_acceptance_policy=artifacts["policy"],
            human_audio_receipts=(artifacts["audio"],),
            human_reference_adjudication=artifacts["adjudication"],
            reference_authority_proof=artifacts["authority"],
        )
    ]


def test_cli_decide_native_passes_exact_original_confirmation_artifact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = object.__new__(PodcastSubtitleV2)
    authorization = tmp_path / "authorization.json"
    policy = tmp_path / "policy.json"
    receipt = tmp_path / "receipt.json"
    authority = tmp_path / "authority.json"
    authorization.write_bytes(b'{"authorization":"exact"}\r\n')
    policy.write_bytes(b'{"policy":"exact"}\n')
    receipt.write_bytes(b'{"receipt":"exact"}\r\n')
    authority.write_bytes(b'{"authority":"exact"}\n')
    captured: list[ResolveNativeRequest] = []

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def decide_native(self, request: ResolveNativeRequest):
            captured.append(request)
            return {"status": "captured"}

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: lambda _context: module)
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    assert (
        cli.main(
            [
                "--episode-root",
                str(tmp_path),
                "decide-native",
                "--generation-id",
                "b" * 64,
                "--original-confirmation-authorization",
                str(authorization),
                "--original-confirmation-policy",
                str(policy),
                "--human-original-confirmation-receipt",
                str(receipt),
                "--reference-authority-proof",
                str(authority),
            ]
        )
        == 0
    )

    assert captured == [
        ResolveNativeRequest(
            generation_id="b" * 64,
            original_confirmation_authorization=authorization.read_bytes(),
            original_confirmation_policy=policy.read_bytes(),
            human_original_confirmation_receipts=(receipt.read_bytes(),),
            reference_authority_proof=authority.read_bytes(),
        )
    ]


@pytest.mark.parametrize("failure", ["both_branches", "mixed_parents", "free_text"])
def test_cli_decide_native_rejects_ambiguous_or_free_text_input_before_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    artifact = tmp_path / "artifact.json"
    artifact.write_bytes(b"{}")
    initialized = False

    def load_factory(_spec: str):
        nonlocal initialized
        initialized = True
        raise AssertionError("factory must not initialize")

    args = [
        "--episode-root",
        str(tmp_path),
        "decide-native",
        "--generation-id",
        "c" * 64,
        "--correction-acceptance-verdict",
        str(artifact),
        "--correction-acceptance-policy",
        str(artifact),
    ]
    if failure == "both_branches":
        args.extend(["--original-confirmation-authorization", str(artifact)])
    elif failure == "mixed_parents":
        args.extend(["--original-confirmation-policy", str(artifact)])
    else:
        args.extend(["--replacement-text", "operator supplied text"])
    monkeypatch.setattr(cli, "_load_factory", load_factory)

    with pytest.raises(SystemExit) as failed:
        cli.main(args)

    assert failed.value.code == 2
    assert initialized is False


def test_cli_legacy_decide_rejects_native_authorization_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = tmp_path / "native-authorization.json"
    decision.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "authorization_kind": "correction_acceptance",
                "generation_id": "d" * 64,
            }
        ),
        encoding="utf-8",
    )
    module = object.__new__(PodcastSubtitleV2)
    legacy_called = False

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def decide(self, _generation_id, _decision):
            nonlocal legacy_called
            legacy_called = True
            raise AssertionError("native authorization must not reach legacy decide")

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: lambda _context: module)
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    with pytest.raises(ValueError):
        cli.main(
            [
                "--episode-root",
                str(tmp_path),
                "decide",
                "--generation-id",
                "d" * 64,
                "--decision-json",
                str(decision),
            ]
        )

    assert legacy_called is False


def test_cli_run_returns_nonzero_for_interrupted_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = object.__new__(PodcastSubtitleV2)

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def run(self, _request):
            return Interrupted(operation="create", reason="work packet pending")

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: _reference_bound_factory(module))
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    assert cli.main(_run_args(tmp_path)) == 2


@pytest.mark.parametrize(
    ("state", "expected_exit"),
    (("not_started", 2), ("partial", 2), ("complete", 0)),
)
def test_cli_status_exit_code_requires_complete_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    expected_exit: int,
) -> None:
    module = object.__new__(PodcastSubtitleV2)

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def status(self):
            return StatusView(state=state)

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: lambda _context: module)
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    assert (
        cli.main(
            [
                "--episode-root",
                str(tmp_path),
                "--factory",
                "fixture:build",
                "status",
            ]
        )
        == expected_exit
    )
