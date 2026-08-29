"""Exact-run Editorial Master sampling and deterministic Long face-safe placement."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..editorial_master import verify_editorial_master
from ._context import EditorialCutContext
from ._materialization_fusion import (
    CanonicalAuthorityError,
    VerifiedEditorialMasterContract,
    VerifiedEditorialMasterContractCache,
)
from ._store import ProductionStoreError, _FilesystemProductionStore
from ._visual_assets import FacePlacementRequest, FaceSafePlacement

_PINNED_OPENCV_VERSION = "5.0.0"
_PINNED_HAAR_SHA256 = "0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0"
_PINNED_HAAR_BYTES = 930_127
_PINNED_HAAR_RECEIPT_HASH = "083922eb3c2a641b0cd14c9cdfeabf91c8417340ed1958205f9e7d860bf55dbc"
_HAAR_ACQUISITION_CONTRACT = "nakama.finished-cut-face-model-acquisition.v1"
_HAAR_MODEL_NAME = "haarcascade_frontalface_default.xml"
_HAAR_RECEIPT_NAME = "OPENCV-HAAR-ACQUISITION.json"


class FacePlacementError(ValueError):
    """Exact Master evidence cannot authorize a safe person-inset placement."""


@dataclass(frozen=True, slots=True)
class PinnedOpenCvHaarModel:
    """Receipt-bound repo asset used by every production Python environment."""

    path: Path
    sha256: str
    opencv_version: str

    @classmethod
    def verify(
        cls,
        asset_root: str | Path,
        *,
        expected_receipt_content_hash: str = _PINNED_HAAR_RECEIPT_HASH,
    ) -> PinnedOpenCvHaarModel:
        configured_root = Path(asset_root)
        if configured_root.is_symlink():
            raise FacePlacementError("OpenCV face-model asset root cannot be a symlink")
        try:
            root = configured_root.resolve(strict=True)
        except OSError as exc:
            raise FacePlacementError("pinned OpenCV face-model assets are missing") from exc
        model_candidate = root / _HAAR_MODEL_NAME
        receipt_candidate = root / _HAAR_RECEIPT_NAME
        if model_candidate.is_symlink() or receipt_candidate.is_symlink():
            raise FacePlacementError("OpenCV face-model assets cannot be symlinks")
        try:
            model = model_candidate.resolve(strict=True)
            receipt_path = receipt_candidate.resolve(strict=True)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            model_bytes = model.read_bytes()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FacePlacementError("pinned OpenCV face-model receipt is unreadable") from exc
        expected_keys = {
            "acquired_at",
            "bytes",
            "content_hash",
            "contract",
            "opencv_version",
            "purpose",
            "sha256",
            "source_python",
            "upstream_filename",
        }
        if (
            not root.is_dir()
            or not model.is_file()
            or not receipt_path.is_file()
            or not isinstance(receipt, dict)
            or set(receipt) != expected_keys
        ):
            raise FacePlacementError("pinned OpenCV face-model receipt schema is invalid")
        claimed_hash = receipt["content_hash"]
        unsigned = {key: value for key, value in receipt.items() if key != "content_hash"}
        actual_model_hash = hashlib.sha256(model_bytes).hexdigest()
        if (
            claimed_hash != _canonical_hash(unsigned)
            or claimed_hash != expected_receipt_content_hash
            or receipt["contract"] != _HAAR_ACQUISITION_CONTRACT
            or receipt["opencv_version"] != _PINNED_OPENCV_VERSION
            or receipt["upstream_filename"] != _HAAR_MODEL_NAME
            or receipt["bytes"] != _PINNED_HAAR_BYTES
            or receipt["bytes"] != len(model_bytes)
            or receipt["sha256"] != _PINNED_HAAR_SHA256
            or actual_model_hash != _PINNED_HAAR_SHA256
            or not isinstance(receipt["source_python"], str)
            or not receipt["source_python"].strip()
            or not isinstance(receipt["acquired_at"], str)
            or not receipt["acquired_at"].strip()
            or receipt["purpose"] != "offline face-safe placement for Long person inset"
        ):
            raise FacePlacementError("pinned OpenCV face-model acquisition identity drift")
        return cls(
            path=model,
            sha256=actual_model_hash,
            opencv_version=_PINNED_OPENCV_VERSION,
        )


@dataclass(frozen=True, slots=True)
class DecodedMasterFrame:
    source_time_sec: float
    width: int
    height: int
    pixels: object

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.source_time_sec)
            or self.source_time_sec < 0
            or type(self.width) is not int
            or self.width <= 0
            or type(self.height) is not int
            or self.height <= 0
            or self.pixels is None
        ):
            raise FacePlacementError("decoded Editorial Master frame is invalid")


@dataclass(frozen=True, slots=True)
class DetectedFace:
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float

    def __post_init__(self) -> None:
        values = (self.x0, self.y0, self.x1, self.y1, self.confidence)
        if (
            not all(math.isfinite(value) for value in values)
            or not 0 <= self.x0 < self.x1 <= 1
            or not 0 <= self.y0 < self.y1 <= 1
            or not 0 <= self.confidence <= 1
        ):
            raise FacePlacementError("detected face geometry is invalid")


@dataclass(frozen=True, slots=True)
class FaceDetectionResult:
    faces: tuple[DetectedFace, ...]
    trustworthy: bool


class FacePlacementContextResolver(Protocol):
    """Resolve only the context persisted for one exact command/run pair."""

    def resolve(
        self,
        *,
        run_id: str,
        command_id: str,
        episode_id: str,
        cut_id: str,
    ) -> EditorialCutContext: ...


class StoredRunFacePlacementContextResolver:
    """Read one exact persisted run context; never search by episode/cut."""

    def __init__(self, run_store_root: str | Path) -> None:
        self._run_store_root = Path(run_store_root).resolve()

    def resolve(
        self,
        *,
        run_id: str,
        command_id: str,
        episode_id: str,
        cut_id: str,
    ) -> EditorialCutContext:
        if not self._run_store_root.is_dir():
            raise FacePlacementError("Finished Cut run store is unavailable")
        try:
            stored = _FilesystemProductionStore(self._run_store_root).load_run(command_id)
        except ProductionStoreError as exc:
            raise FacePlacementError("Finished Cut run authority is invalid") from exc
        if stored is None:
            raise FacePlacementError("Finished Cut command authority is unavailable")
        command = stored.command
        context = stored.view.editorial_context
        if stored.view.run_id != run_id or stored.view.command_id != command_id:
            raise FacePlacementError("persisted run identity mismatch")
        if command.command_id != command_id:
            raise FacePlacementError("persisted command identity mismatch")
        if (
            command.episode_id != episode_id
            or command.cut_id != cut_id
            or command.format != "long"
            or context.episode_id != episode_id
            or context.cut_id != cut_id
            or context.format != command.format
            or not context.editorial_master_id
            or not context.tight_cut_id
        ):
            raise FacePlacementError("persisted context authority mismatch")
        command_master = getattr(command, "editorial_master_id", context.editorial_master_id)
        command_tight = getattr(command, "tight_cut_id", context.tight_cut_id)
        if command_master != context.editorial_master_id or command_tight != context.tight_cut_id:
            raise FacePlacementError("persisted context Master identity mismatch")
        return context


class EditorialMasterVideoResolver(Protocol):
    def resolve(
        self,
        *,
        episode_id: str,
        editorial_master_id: str,
    ) -> VerifiedEditorialMasterContract: ...


class _EditorialMasterSelection(Protocol):
    video_path: Path
    content_hash: str
    receipt: dict[str, object]


class _EditorialMasterVerifier(Protocol):
    def __call__(
        self,
        episode_root: str | Path,
        *,
        expected_episode_id: str | None = None,
        expected_content_hash: str | None = None,
    ) -> _EditorialMasterSelection: ...


class FilesystemEditorialMasterVideoResolver:
    """Production Adapter over ADR-064's exact, hash-bound Master verifier."""

    def __init__(
        self,
        episodes_root: str | Path,
        *,
        cache_root: str | Path,
        verifier: _EditorialMasterVerifier = verify_editorial_master,
    ) -> None:
        self._episodes_root = Path(episodes_root).resolve()
        self._cache_root = Path(cache_root).resolve()
        self._verifier = verifier

    def resolve(
        self,
        *,
        episode_id: str,
        editorial_master_id: str,
    ) -> VerifiedEditorialMasterContract:
        if (
            not episode_id
            or not editorial_master_id
            or any(character in episode_id for character in "/\\\r\n\t")
        ):
            raise FacePlacementError("Editorial Master lookup identity is invalid")
        try:
            master = VerifiedEditorialMasterContractCache(
                episode_root=self._episodes_root / episode_id,
                cache_path=(
                    self._cache_root / episode_id / f"{editorial_master_id}.verified-master.v1.json"
                ),
                verifier=self._verifier,
            ).load(
                episode_id=episode_id,
                editorial_master_content_hash=editorial_master_id,
            )
        except CanonicalAuthorityError as exc:
            raise FacePlacementError("ADR-064 Editorial Master verification failed") from exc
        return master


class MasterFrameReader(Protocol):
    def read(
        self,
        path: Path,
        *,
        source_time_sec: float,
        expected_frame_rate: float,
    ) -> DecodedMasterFrame: ...


class OpenCvMasterFrameReader:
    """Production random-access decoder for exact ADR-064 Master timestamps."""

    def __init__(self, *, cv2_module: Any | None = None) -> None:
        if cv2_module is None:
            try:
                import cv2 as cv2_module  # type: ignore[import-not-found, no-redef]
            except ImportError as exc:  # pragma: no cover - production dependency gate
                raise FacePlacementError("OpenCV is unavailable for Master sampling") from exc
        self._cv2 = cv2_module

    def read(
        self,
        path: Path,
        *,
        source_time_sec: float,
        expected_frame_rate: float,
    ) -> DecodedMasterFrame:
        if (
            not math.isfinite(source_time_sec)
            or source_time_sec < 0
            or not math.isfinite(expected_frame_rate)
            or expected_frame_rate <= 0
        ):
            raise FacePlacementError("Master frame source time is invalid")
        try:
            media = Path(path).resolve(strict=True)
        except OSError as exc:
            raise FacePlacementError("Editorial Master media is unavailable") from exc
        capture = self._cv2.VideoCapture(str(media))
        try:
            if not capture.isOpened():
                raise FacePlacementError("OpenCV could not open Editorial Master")
            if capture.set(self._cv2.CAP_PROP_POS_MSEC, source_time_sec * 1000.0) is not True:
                raise FacePlacementError("OpenCV could not seek Editorial Master")
            ok, pixels = capture.read()
            if ok is not True or pixels is None:
                raise FacePlacementError("OpenCV could not decode Editorial Master frame")
            shape = getattr(pixels, "shape", ())
            if not isinstance(shape, tuple) or len(shape) < 2:
                raise FacePlacementError("OpenCV returned invalid frame geometry")
            height, width = shape[:2]
            actual_ms = capture.get(self._cv2.CAP_PROP_POS_MSEC)
            fps = capture.get(self._cv2.CAP_PROP_FPS)
            if (
                isinstance(fps, bool)
                or not isinstance(fps, (int, float))
                or not math.isfinite(float(fps))
                or not math.isclose(
                    float(fps),
                    expected_frame_rate,
                    rel_tol=0,
                    abs_tol=1e-6,
                )
            ):
                raise FacePlacementError("OpenCV Master frame rate differs from ADR-064 receipt")
            tolerance = max(100.0, 2000.0 / fps) if fps and fps > 0 else 100.0
            if (
                not isinstance(actual_ms, (int, float))
                or not math.isfinite(float(actual_ms))
                or abs(float(actual_ms) - source_time_sec * 1000.0) > tolerance
            ):
                raise FacePlacementError("OpenCV decoded another Master timestamp")
            return DecodedMasterFrame(
                source_time_sec=source_time_sec,
                width=int(width),
                height=int(height),
                pixels=pixels,
            )
        finally:
            capture.release()


class FaceDetector(Protocol):
    def detect(self, frame: DecodedMasterFrame) -> FaceDetectionResult: ...


class OpenCvHaarFaceDetector:
    """Pinned offline all-box face detector used by production composition."""

    def __init__(
        self,
        *,
        model_path: str | Path,
        expected_model_sha256: str = _PINNED_HAAR_SHA256,
        expected_opencv_version: str = _PINNED_OPENCV_VERSION,
        cv2_module: Any | None = None,
    ) -> None:
        if cv2_module is None:
            try:
                import cv2 as cv2_module  # type: ignore[import-not-found, no-redef]
            except ImportError as exc:  # pragma: no cover - production dependency gate
                raise FacePlacementError("OpenCV is unavailable for face detection") from exc
        if getattr(cv2_module, "__version__", None) != expected_opencv_version:
            raise FacePlacementError("OpenCV detector version drift")
        configured_model = Path(model_path)
        if configured_model.is_symlink():
            raise FacePlacementError("OpenCV face model cannot be a symlink")
        try:
            model = configured_model.resolve(strict=True)
            model_hash = hashlib.sha256(model.read_bytes()).hexdigest()
        except OSError as exc:
            raise FacePlacementError("pinned OpenCV face model is missing") from exc
        if not model.is_file() or model_hash != expected_model_sha256:
            raise FacePlacementError("pinned OpenCV face model identity drift")
        classifier = cv2_module.CascadeClassifier(str(model))
        if classifier is None or classifier.empty():
            raise FacePlacementError("pinned OpenCV face model could not initialize")
        self._cv2 = cv2_module
        self._classifier = classifier

    def detect(self, frame: DecodedMasterFrame) -> FaceDetectionResult:
        try:
            gray = self._cv2.cvtColor(frame.pixels, self._cv2.COLOR_BGR2GRAY)
            normalized = self._cv2.equalizeHist(gray)
            raw_faces = self._classifier.detectMultiScale(
                normalized,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(48, 48),
            )
        except Exception as exc:
            raise FacePlacementError("OpenCV all-face detection failed") from exc
        if raw_faces is None or len(raw_faces) == 0:
            return FaceDetectionResult(faces=(), trustworthy=False)
        faces: list[DetectedFace] = []
        try:
            for x, y, width, height in raw_faces:
                if width <= 0 or height <= 0 or x < 0 or y < 0:
                    return FaceDetectionResult(faces=(), trustworthy=False)
                x1 = min(frame.width, x + width)
                y1 = min(frame.height, y + height)
                faces.append(
                    DetectedFace(
                        float(x) / frame.width,
                        float(y) / frame.height,
                        float(x1) / frame.width,
                        float(y1) / frame.height,
                        1.0,
                    )
                )
        except (TypeError, ValueError):
            return FaceDetectionResult(faces=(), trustworthy=False)
        return FaceDetectionResult(faces=tuple(faces), trustworthy=True)


class MediaPipeFaceDetector:
    """Production all-face detector; ambiguous detector output never becomes placement."""

    def __init__(
        self,
        *,
        mediapipe_module: Any | None = None,
        cv2_module: Any | None = None,
        minimum_confidence: float = 0.65,
    ) -> None:
        if not math.isfinite(minimum_confidence) or not 0 < minimum_confidence <= 1:
            raise FacePlacementError("MediaPipe confidence threshold is invalid")
        if mediapipe_module is None:
            try:
                import mediapipe as mediapipe_module  # type: ignore[import-not-found, no-redef]
            except ImportError as exc:  # pragma: no cover - production dependency gate
                raise FacePlacementError("MediaPipe is unavailable for face detection") from exc
        if cv2_module is None:
            try:
                import cv2 as cv2_module  # type: ignore[import-not-found, no-redef]
            except ImportError as exc:  # pragma: no cover - production dependency gate
                raise FacePlacementError("OpenCV is unavailable for face detection") from exc
        try:
            self._model = mediapipe_module.solutions.face_detection.FaceDetection(
                model_selection=1,
                min_detection_confidence=minimum_confidence,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise FacePlacementError("MediaPipe face detector could not initialize") from exc
        self._cv2 = cv2_module
        self._minimum_confidence = minimum_confidence

    def detect(self, frame: DecodedMasterFrame) -> FaceDetectionResult:
        try:
            rgb = self._cv2.cvtColor(frame.pixels, self._cv2.COLOR_BGR2RGB)
            result = self._model.process(rgb)
        except Exception as exc:
            raise FacePlacementError("MediaPipe face detection failed") from exc
        raw_detections = getattr(result, "detections", None)
        if raw_detections is None:
            return FaceDetectionResult(faces=(), trustworthy=True)
        if not isinstance(raw_detections, (list, tuple)):
            return FaceDetectionResult(faces=(), trustworthy=False)
        faces: list[DetectedFace] = []
        for detection in raw_detections:
            scores = getattr(detection, "score", None)
            location = getattr(detection, "location_data", None)
            box = getattr(location, "relative_bounding_box", None)
            if (
                not isinstance(scores, (list, tuple))
                or len(scores) != 1
                or isinstance(scores[0], bool)
                or not isinstance(scores[0], (int, float))
                or not math.isfinite(float(scores[0]))
                or float(scores[0]) < self._minimum_confidence
                or box is None
            ):
                return FaceDetectionResult(faces=(), trustworthy=False)
            try:
                x0 = max(0.0, float(box.xmin))
                y0 = max(0.0, float(box.ymin))
                x1 = min(1.0, float(box.xmin) + float(box.width))
                y1 = min(1.0, float(box.ymin) + float(box.height))
                faces.append(DetectedFace(x0, y0, x1, y1, float(scores[0])))
            except (AttributeError, TypeError, ValueError):
                return FaceDetectionResult(faces=(), trustworthy=False)
        return FaceDetectionResult(faces=tuple(faces), trustworthy=True)


@dataclass(frozen=True, slots=True)
class _Rectangle:
    x0: float
    y0: float
    x1: float
    y1: float

    def intersects(self, other: _Rectangle) -> bool:
        return not (
            self.x1 <= other.x0 or other.x1 <= self.x0 or self.y1 <= other.y0 or other.y1 <= self.y0
        )


class DeterministicFacialSafePlacement:
    """Map cut-local time to current Master frames and choose a proven safe side."""

    def __init__(
        self,
        *,
        context_resolver: FacePlacementContextResolver,
        master_resolver: EditorialMasterVideoResolver,
        frame_reader: MasterFrameReader,
        face_detector: FaceDetector,
    ) -> None:
        self._context_resolver = context_resolver
        self._master_resolver = master_resolver
        self._frame_reader = frame_reader
        self._face_detector = face_detector

    def place(self, request: FacePlacementRequest) -> FaceSafePlacement:
        context = self._context_resolver.resolve(
            run_id=request.run_id,
            command_id=request.command_id,
            episode_id=request.episode_id,
            cut_id=request.cut_id,
        )
        self._validate_context(request, context)
        master = self._master_resolver.resolve(
            episode_id=request.episode_id,
            editorial_master_id=context.editorial_master_id,
        )
        if (
            master.episode_id != context.episode_id
            or master.editorial_master_content_hash != context.editorial_master_id
            or len(master.master_media_sha256) != 64
            or not math.isfinite(master.frame_rate)
            or master.frame_rate <= 0
        ):
            raise FacePlacementError("Editorial Master resolver returned another identity")

        source_times = tuple(
            _map_cut_time(context, cut_time)
            for cut_time in _sample_cut_times(request.t0, request.t1)
        )
        if any(source_time >= master.duration_sec for source_time in source_times):
            raise FacePlacementError("mapped source time exceeds current Editorial Master")
        faces: list[DetectedFace] = []
        for source_time in source_times:
            frame = self._frame_reader.read(
                master.master_media_path,
                source_time_sec=source_time,
                expected_frame_rate=master.frame_rate,
            )
            if not math.isclose(frame.source_time_sec, source_time, rel_tol=0, abs_tol=1e-3):
                raise FacePlacementError(
                    "decoded frame timestamp differs from requested Master time"
                )
            detection = self._face_detector.detect(frame)
            if detection.trustworthy is not True or not detection.faces:
                raise FacePlacementError("face evidence is not trustworthy")
            faces.extend(detection.faces)
        return _choose_placement(request, tuple(faces))

    @staticmethod
    def _validate_context(
        request: FacePlacementRequest,
        context: EditorialCutContext,
    ) -> None:
        if (
            context.episode_id != request.episode_id
            or context.cut_id != request.cut_id
            or context.format != "long"
            or not context.editorial_master_id
            or not context.tight_cut_id
        ):
            raise FacePlacementError("persisted run context identity mismatch")
        spans = tuple(source.t1 - source.t0 for source in context.source_ranges)
        if (
            not spans
            or any(not math.isfinite(span) or span <= 0 for span in spans)
            or not math.isclose(sum(spans), context.duration_sec, rel_tol=0, abs_tol=1e-6)
            or request.t0 < 0
            or request.t1 <= request.t0
            or request.t1 > context.duration_sec
        ):
            raise FacePlacementError("cut-local source mapping is invalid")


def _sample_cut_times(t0: float, t1: float) -> tuple[float, ...]:
    duration = t1 - t0
    fractions = (0.0, 0.25, 0.5, 0.75, 1.0) if duration >= 4.0 else (0.0, 0.5, 1.0)
    end = math.nextafter(t1, t0)
    return tuple(end if fraction == 1.0 else t0 + duration * fraction for fraction in fractions)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _map_cut_time(context: EditorialCutContext, cut_time: float) -> float:
    remaining = cut_time
    for source in context.source_ranges:
        span = source.t1 - source.t0
        if remaining < span:
            return source.t0 + remaining
        remaining -= span
    raise FacePlacementError("cut-local time is outside current source ranges")


def _choose_placement(
    request: FacePlacementRequest,
    faces: tuple[DetectedFace, ...],
) -> FaceSafePlacement:
    width_ratio = min(request.max_width_ratio, 0.24)
    height_ratio = (
        width_ratio
        * request.target_width
        * request.source_height
        / request.source_width
        / request.target_height
    )
    available_height = request.protected_bottom_ratio - 0.08
    if height_ratio > available_height:
        width_ratio *= available_height / height_ratio
        height_ratio = available_height
    if width_ratio <= 0 or height_ratio <= 0:
        raise FacePlacementError("person inset geometry cannot fit safe canvas")
    y_ratio = 0.08
    margin = 0.04
    candidates = (
        _Rectangle(margin, y_ratio, margin + width_ratio, y_ratio + height_ratio),
        _Rectangle(1.0 - margin - width_ratio, y_ratio, 1.0 - margin, y_ratio + height_ratio),
    )
    expanded_faces = tuple(
        _Rectangle(
            max(0.0, face.x0 - 0.025),
            max(0.0, face.y0 - 0.025),
            min(1.0, face.x1 + 0.025),
            min(request.protected_bottom_ratio, face.y1 + 0.025),
        )
        for face in faces
    )
    safe = tuple(
        candidate
        for candidate in candidates
        if candidate.y1 <= request.protected_bottom_ratio
        and not any(candidate.intersects(face) for face in expanded_faces)
    )
    if not safe:
        raise FacePlacementError("no trusted face-safe placement is available")
    if len(safe) == 1:
        selected = safe[0]
    elif not faces:
        selected = safe[-1]
    else:
        face_centers = tuple((face.x0 + face.x1) / 2 for face in faces)
        selected = max(
            safe,
            key=lambda candidate: min(
                abs((candidate.x0 + candidate.x1) / 2 - center) for center in face_centers
            ),
        )
    return FaceSafePlacement(
        x_ratio=selected.x0,
        y_ratio=selected.y0,
        width_ratio=selected.x1 - selected.x0,
        height_ratio=selected.y1 - selected.y0,
        avoids_faces=True,
    )
