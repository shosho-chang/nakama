from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.brook.script_video.finished_cut_production import _face_placement
from agents.brook.script_video.finished_cut_production._assets import WorkerSelectionCatalog
from agents.brook.script_video.finished_cut_production._commands import (
    ApprovedCutCommand,
    TargetedRevisionCommand,
)
from agents.brook.script_video.finished_cut_production._context import (
    CutSourceRange,
    EditorialCutContext,
)
from agents.brook.script_video.finished_cut_production._face_placement import (
    DecodedMasterFrame,
    DetectedFace,
    DeterministicFacialSafePlacement,
    FaceDetectionResult,
    FacePlacementError,
    FilesystemEditorialMasterVideoResolver,
    MediaPipeFaceDetector,
    OpenCvHaarFaceDetector,
    OpenCvMasterFrameReader,
    PinnedOpenCvHaarModel,
    StoredRunFacePlacementContextResolver,
)
from agents.brook.script_video.finished_cut_production._materialization_fusion import (
    VerifiedEditorialMasterContract,
)
from agents.brook.script_video.finished_cut_production._records import _ProductionRun
from agents.brook.script_video.finished_cut_production._store import _FilesystemProductionStore
from agents.brook.script_video.finished_cut_production._visual_assets import (
    FacePlacementRequest,
)


class _ContextResolver:
    def __init__(self, context: EditorialCutContext) -> None:
        self.context = context
        self.calls: list[tuple[str, str, str, str]] = []

    def resolve(
        self,
        *,
        run_id: str,
        command_id: str,
        episode_id: str,
        cut_id: str,
    ) -> EditorialCutContext:
        self.calls.append((run_id, command_id, episode_id, cut_id))
        return self.context


_MASTER_CONTENT_HASH = "a" * 64
_MASTER_MEDIA_SHA256 = "b" * 64


class _MasterResolver:
    def __init__(self, path: Path, *, duration_sec: float = 300.0) -> None:
        self.master = VerifiedEditorialMasterContract(
            episode_id="episode-1",
            editorial_master_content_hash=_MASTER_CONTENT_HASH,
            master_media_path=path,
            master_media_sha256=_MASTER_MEDIA_SHA256,
            master_media_bytes=path.stat().st_size,
            resolve_project_name="Project",
            editorial_master_timeline_name="Timeline",
            editorial_master_timeline_uid="timeline-1",
            frame_rate=30.0,
            duration_sec=duration_sec,
        )

    def resolve(
        self,
        *,
        episode_id: str,
        editorial_master_id: str,
    ) -> VerifiedEditorialMasterContract:
        assert (episode_id, editorial_master_id) == ("episode-1", _MASTER_CONTENT_HASH)
        return self.master


class _FrameReader:
    def __init__(self) -> None:
        self.source_times: list[float] = []

    def read(
        self,
        path: Path,
        *,
        source_time_sec: float,
        expected_frame_rate: float,
    ) -> DecodedMasterFrame:
        assert expected_frame_rate == 30.0
        self.source_times.append(source_time_sec)
        return DecodedMasterFrame(
            source_time_sec=source_time_sec,
            width=1920,
            height=1080,
            pixels=source_time_sec,
        )


class _Detector:
    def __init__(self, face: DetectedFace) -> None:
        self.face = face

    def detect(self, frame: DecodedMasterFrame) -> FaceDetectionResult:
        return FaceDetectionResult(faces=(self.face,), trustworthy=True)


class _SequenceDetector:
    def __init__(self, faces: tuple[tuple[DetectedFace, ...], ...]) -> None:
        self._faces = iter(faces)

    def detect(self, frame: DecodedMasterFrame) -> FaceDetectionResult:
        return FaceDetectionResult(faces=next(self._faces), trustworthy=True)


def _context() -> EditorialCutContext:
    return EditorialCutContext(
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id=_MASTER_CONTENT_HASH,
        tight_cut_id="tight-current",
        duration_sec=6.0,
        source_ranges=(CutSourceRange(100.0, 102.0), CutSourceRange(200.0, 204.0)),
        cues=(),
    )


def _request() -> FacePlacementRequest:
    return FacePlacementRequest(
        run_id="run-current",
        command_id="approved-cut:0123456789abcdef0123456789abcdef",
        episode_id="episode-1",
        cut_id="cut-1",
        event_id="event-person",
        t0=1.0,
        t1=5.0,
        target_width=1920,
        target_height=1080,
        source_width=800,
        source_height=1000,
    )


def test_face_placement_maps_cut_time_to_master_and_chooses_opposite_safe_side(
    tmp_path: Path,
) -> None:
    master_path = tmp_path / "MASTER.mp4"
    master_path.write_bytes(b"verified master fixture")
    contexts = _ContextResolver(_context())
    frames = _FrameReader()
    placement = DeterministicFacialSafePlacement(
        context_resolver=contexts,
        master_resolver=_MasterResolver(master_path),
        frame_reader=frames,
        face_detector=_Detector(DetectedFace(0.02, 0.08, 0.34, 0.72, 0.99)),
    ).place(_request())

    assert contexts.calls == [
        (
            "run-current",
            "approved-cut:0123456789abcdef0123456789abcdef",
            "episode-1",
            "cut-1",
        )
    ]
    assert frames.source_times[0] == pytest.approx(101.0)
    assert 201.9 < frames.source_times[-1] < 204.0
    assert any(source_time == pytest.approx(201.0) for source_time in frames.source_times)
    assert placement.x_ratio > 0.5
    assert placement.width_ratio <= 0.24
    assert placement.y_ratio + placement.height_ratio <= 0.84
    assert placement.avoids_faces is True


def test_face_placement_rejects_untrusted_detector_before_returning_coordinates(
    tmp_path: Path,
) -> None:
    class _UntrustedDetector:
        def detect(self, frame: DecodedMasterFrame) -> FaceDetectionResult:
            return FaceDetectionResult(faces=(), trustworthy=False)

    master_path = tmp_path / "MASTER.mp4"
    master_path.write_bytes(b"verified master fixture")
    placer = DeterministicFacialSafePlacement(
        context_resolver=_ContextResolver(_context()),
        master_resolver=_MasterResolver(master_path),
        frame_reader=_FrameReader(),
        face_detector=_UntrustedDetector(),
    )

    with pytest.raises(FacePlacementError, match="not trustworthy"):
        placer.place(_request())


def test_face_placement_rejects_when_any_sample_has_zero_face_evidence(
    tmp_path: Path,
) -> None:
    master_path = tmp_path / "MASTER.mp4"
    master_path.write_bytes(b"verified master fixture")
    placer = DeterministicFacialSafePlacement(
        context_resolver=_ContextResolver(_context()),
        master_resolver=_MasterResolver(master_path),
        frame_reader=_FrameReader(),
        face_detector=_SequenceDetector(
            (
                (DetectedFace(0.1, 0.1, 0.3, 0.7, 0.99),),
                (DetectedFace(0.1, 0.1, 0.3, 0.7, 0.99),),
                (),
                (DetectedFace(0.1, 0.1, 0.3, 0.7, 0.99),),
                (DetectedFace(0.1, 0.1, 0.3, 0.7, 0.99),),
            )
        ),
    )

    with pytest.raises(FacePlacementError, match="face evidence is not trustworthy"):
        placer.place(_request())


def test_face_placement_with_right_side_speaker_uses_left_safe_zone(tmp_path: Path) -> None:
    master_path = tmp_path / "MASTER.mp4"
    master_path.write_bytes(b"verified master fixture")
    placement = DeterministicFacialSafePlacement(
        context_resolver=_ContextResolver(_context()),
        master_resolver=_MasterResolver(master_path),
        frame_reader=_FrameReader(),
        face_detector=_Detector(DetectedFace(0.62, 0.06, 0.98, 0.76, 0.99)),
    ).place(_request())

    assert placement.x_ratio < 0.5


def test_face_placement_rejects_scene_changes_that_occupy_both_safe_sides(
    tmp_path: Path,
) -> None:
    master_path = tmp_path / "MASTER.mp4"
    master_path.write_bytes(b"verified master fixture")
    left = DetectedFace(0.01, 0.05, 0.36, 0.78, 0.99)
    right = DetectedFace(0.64, 0.05, 0.99, 0.78, 0.99)
    placer = DeterministicFacialSafePlacement(
        context_resolver=_ContextResolver(_context()),
        master_resolver=_MasterResolver(master_path),
        frame_reader=_FrameReader(),
        face_detector=_SequenceDetector(((left,), (left,), (right,), (right,), (right,))),
    )

    with pytest.raises(FacePlacementError, match="no trusted face-safe placement"):
        placer.place(_request())


def test_face_placement_rejects_cut_range_overflow_before_decoding_master(
    tmp_path: Path,
) -> None:
    master_path = tmp_path / "MASTER.mp4"
    master_path.write_bytes(b"verified master fixture")
    frames = _FrameReader()
    placer = DeterministicFacialSafePlacement(
        context_resolver=_ContextResolver(_context()),
        master_resolver=_MasterResolver(master_path),
        frame_reader=frames,
        face_detector=_SequenceDetector(((),)),
    )

    with pytest.raises(FacePlacementError, match="source mapping is invalid"):
        placer.place(replace(_request(), t1=6.1))

    assert frames.source_times == []


def test_stored_context_resolver_uses_exact_command_and_run_when_same_cut_has_many_runs(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "runs"
    store = _FilesystemProductionStore(store_root)
    context_one = _context()
    context_two = replace(
        context_one,
        editorial_master_id="d" * 64,
        tight_cut_id="tight-newer",
    )
    command_one = ApprovedCutCommand(
        command_id="approved-cut:" + "1" * 32,
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id=context_one.editorial_master_id,
        winner_id="winner-one",
        tight_cut_id=context_one.tight_cut_id,
    )
    command_two = ApprovedCutCommand(
        command_id="approved-cut:" + "2" * 32,
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        editorial_master_id=context_two.editorial_master_id,
        winner_id="winner-two",
        tight_cut_id=context_two.tight_cut_id,
    )
    for command, run_id, context in (
        (command_one, "run-one", context_one),
        (command_two, "run-two", context_two),
    ):
        store.create_run(
            command,
            _ProductionRun(
                run_id=run_id,
                command_id=command.command_id,
                editorial_context=context,
                status="pending",
                outstanding_request=None,
            ),
            WorkerSelectionCatalog(()),
        )
    resolver = StoredRunFacePlacementContextResolver(store_root)

    assert (
        resolver.resolve(
            run_id="run-two",
            command_id=command_two.command_id,
            episode_id="episode-1",
            cut_id="cut-1",
        )
        == context_two
    )
    with pytest.raises(FacePlacementError, match="run identity mismatch"):
        resolver.resolve(
            run_id="run-one",
            command_id=command_two.command_id,
            episode_id="episode-1",
            cut_id="cut-1",
        )


def test_stored_context_resolver_reads_targeted_revision_context_without_cut_guessing(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "runs"
    store = _FilesystemProductionStore(store_root)
    context = _context()
    command = TargetedRevisionCommand(
        command_id="targeted-revision:" + "3" * 32,
        current_release_id="release-current",
        episode_id="episode-1",
        cut_id="cut-1",
        format="long",
        event_id="event-person",
        feedback="Move the inset away from the current speaker.",
    )
    store.create_run(
        command,
        _ProductionRun(
            run_id="run-targeted",
            command_id=command.command_id,
            editorial_context=context,
            status="pending",
            outstanding_request=None,
        ),
        WorkerSelectionCatalog(()),
        base_release_id=command.current_release_id,
    )

    resolved = StoredRunFacePlacementContextResolver(store_root).resolve(
        run_id="run-targeted",
        command_id=command.command_id,
        episode_id="episode-1",
        cut_id="cut-1",
    )

    assert resolved == context


def test_filesystem_master_resolver_verifies_exact_adr064_identity_and_returns_video(
    tmp_path: Path,
) -> None:
    episodes_root = tmp_path / "episodes"
    video = episodes_root / "episode-1" / "MASTER.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"current master")
    calls: list[tuple[Path, str, str]] = []

    def verify(path, *, expected_episode_id, expected_content_hash):
        calls.append((Path(path), expected_episode_id, expected_content_hash))
        return SimpleNamespace(
            video_path=video,
            content_hash=_MASTER_CONTENT_HASH,
            receipt={
                "episode_id": "episode-1",
                "content_hash": _MASTER_CONTENT_HASH,
                "artifacts": {
                    "media": {
                        "bytes": video.stat().st_size,
                        "sha256": _MASTER_MEDIA_SHA256,
                    }
                },
                "project": {"name": "Project"},
                "timeline": {
                    "name": "Timeline",
                    "uid": "timeline-1",
                    "fps": "30",
                    "duration_sec": 300.0,
                },
            },
        )

    resolved = FilesystemEditorialMasterVideoResolver(
        episodes_root,
        cache_root=tmp_path / "cache",
        verifier=verify,
    ).resolve(
        episode_id="episode-1",
        editorial_master_id=_MASTER_CONTENT_HASH,
    )

    assert calls == [(video.parent, "episode-1", _MASTER_CONTENT_HASH)]
    assert resolved == VerifiedEditorialMasterContract(
        episode_id="episode-1",
        editorial_master_content_hash=_MASTER_CONTENT_HASH,
        master_media_path=video.resolve(),
        master_media_sha256=_MASTER_MEDIA_SHA256,
        master_media_bytes=video.stat().st_size,
        resolve_project_name="Project",
        editorial_master_timeline_name="Timeline",
        editorial_master_timeline_uid="timeline-1",
        frame_rate=30.0,
        duration_sec=300.0,
    )


def test_opencv_frame_reader_seeks_exact_source_time_and_releases_capture(
    tmp_path: Path,
) -> None:
    video = tmp_path / "MASTER.mp4"
    video.write_bytes(b"master")

    class _Pixels:
        shape = (1080, 1920, 3)

    class _Capture:
        def __init__(self) -> None:
            self.seek_ms = 0.0
            self.released = False

        def isOpened(self) -> bool:
            return True

        def set(self, key, value) -> bool:
            assert key == 1
            self.seek_ms = value
            return True

        def read(self):
            return True, _Pixels()

        def get(self, key):
            if key == 1:
                return self.seek_ms
            if key == 3:
                return 30.0
            raise AssertionError(key)

        def release(self) -> None:
            self.released = True

    capture = _Capture()
    cv2 = SimpleNamespace(
        CAP_PROP_POS_MSEC=1,
        CAP_PROP_FPS=3,
        VideoCapture=lambda path: capture,
    )

    frame = OpenCvMasterFrameReader(cv2_module=cv2).read(
        video,
        source_time_sec=12.5,
        expected_frame_rate=30.0,
    )

    assert frame.source_time_sec == pytest.approx(12.5)
    assert (frame.width, frame.height) == (1920, 1080)
    assert capture.seek_ms == pytest.approx(12_500.0)
    assert capture.released is True


def test_mediapipe_detector_returns_every_normalized_face_with_confidence() -> None:
    boxes = (
        SimpleNamespace(xmin=0.05, ymin=0.10, width=0.20, height=0.40),
        SimpleNamespace(xmin=0.68, ymin=0.08, width=0.22, height=0.44),
    )
    detections = [
        SimpleNamespace(
            score=[confidence],
            location_data=SimpleNamespace(relative_bounding_box=box),
        )
        for box, confidence in zip(boxes, (0.98, 0.92), strict=True)
    ]

    class _Model:
        def process(self, pixels):
            assert pixels == "rgb"
            return SimpleNamespace(detections=detections)

    media_pipe = SimpleNamespace(
        solutions=SimpleNamespace(
            face_detection=SimpleNamespace(FaceDetection=lambda **kwargs: _Model())
        )
    )
    cv2 = SimpleNamespace(COLOR_BGR2RGB=7, cvtColor=lambda pixels, code: "rgb")
    frame = DecodedMasterFrame(
        source_time_sec=10.0,
        width=1920,
        height=1080,
        pixels="bgr",
    )

    result = MediaPipeFaceDetector(
        mediapipe_module=media_pipe,
        cv2_module=cv2,
    ).detect(frame)

    assert result.trustworthy is True
    assert result.faces == (
        DetectedFace(0.05, 0.10, 0.25, 0.50, 0.98),
        DetectedFace(0.68, 0.08, 0.90, 0.52, 0.92),
    )


def test_opencv_haar_detector_uses_pinned_offline_model_and_returns_all_faces(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "haarcascade_frontalface_default.xml"
    model_path.write_bytes(b"pinned cascade")

    class _Classifier:
        def empty(self) -> bool:
            return False

        def detectMultiScale(self, pixels, **kwargs):
            assert pixels == "equalized"
            assert kwargs == {
                "scaleFactor": 1.1,
                "minNeighbors": 5,
                "minSize": (48, 48),
            }
            return ((96, 108, 384, 540), (1344, 54, 384, 540))

    classifier = _Classifier()
    cv2 = SimpleNamespace(
        __version__="5.0.0",
        COLOR_BGR2GRAY=6,
        CascadeClassifier=lambda path: classifier,
        cvtColor=lambda pixels, code: "gray",
        equalizeHist=lambda pixels: "equalized",
    )
    frame = DecodedMasterFrame(
        source_time_sec=10.0,
        width=1920,
        height=1080,
        pixels="bgr",
    )

    result = OpenCvHaarFaceDetector(
        model_path=model_path,
        expected_model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
        cv2_module=cv2,
    ).detect(frame)

    assert result.trustworthy is True
    assert result.faces == (
        DetectedFace(0.05, 0.10, 0.25, 0.60, 1.0),
        DetectedFace(0.70, 0.05, 0.90, 0.55, 1.0),
    )


def test_repo_pinned_opencv_face_model_has_exact_acquisition_receipt() -> None:
    assets = Path(_face_placement.__file__).resolve().parent / "assets"

    verified = PinnedOpenCvHaarModel.verify(assets)

    assert verified.path.name == "haarcascade_frontalface_default.xml"
    assert verified.path.stat().st_size == 930_127
    assert verified.sha256 == ("0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0")
    assert verified.opencv_version == "5.0.0"


@pytest.mark.parametrize("tamper", ["receipt", "model"])
def test_repo_pinned_opencv_face_model_rejects_receipt_or_media_drift(
    tmp_path: Path,
    tamper: str,
) -> None:
    source = Path(_face_placement.__file__).resolve().parent / "assets"
    copied = tmp_path / "assets"
    copied.mkdir()
    shutil.copy2(source / "haarcascade_frontalface_default.xml", copied)
    shutil.copy2(source / "OPENCV-HAAR-ACQUISITION.json", copied)
    if tamper == "model":
        (copied / "haarcascade_frontalface_default.xml").write_bytes(b"tampered")
    else:
        receipt_path = copied / "OPENCV-HAAR-ACQUISITION.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["purpose"] = "untrusted alternate purpose"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(FacePlacementError, match="identity drift"):
        PinnedOpenCvHaarModel.verify(copied)


@pytest.mark.parametrize("failure", ["missing", "tampered"])
def test_opencv_haar_detector_fails_closed_for_missing_or_tampered_model(
    tmp_path: Path,
    failure: str,
) -> None:
    model_path = tmp_path / "haarcascade_frontalface_default.xml"
    if failure == "tampered":
        model_path.write_bytes(b"tampered cascade")
    cv2 = SimpleNamespace(
        __version__="5.0.0",
        CascadeClassifier=lambda path: None,
    )

    with pytest.raises(FacePlacementError, match="model"):
        OpenCvHaarFaceDetector(
            model_path=model_path,
            expected_model_sha256="0" * 64,
            cv2_module=cv2,
        )


def test_opencv_haar_detector_marks_zero_detection_as_untrusted(tmp_path: Path) -> None:
    model_path = tmp_path / "haarcascade_frontalface_default.xml"
    model_path.write_bytes(b"pinned cascade")

    class _Classifier:
        def empty(self) -> bool:
            return False

        def detectMultiScale(self, pixels, **kwargs):
            return ()

    cv2 = SimpleNamespace(
        __version__="5.0.0",
        COLOR_BGR2GRAY=6,
        CascadeClassifier=lambda path: _Classifier(),
        cvtColor=lambda pixels, code: "gray",
        equalizeHist=lambda pixels: "equalized",
    )
    detector = OpenCvHaarFaceDetector(
        model_path=model_path,
        expected_model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
        cv2_module=cv2,
    )

    result = detector.detect(DecodedMasterFrame(10.0, 1920, 1080, "bgr"))

    assert result == FaceDetectionResult(faces=(), trustworthy=False)
