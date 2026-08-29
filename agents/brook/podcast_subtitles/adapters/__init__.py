"""Concrete Adapters for the Podcast Subtitle V2 internal seams.

Optional GPU/provider dependencies are imported only inside Adapter methods, so
this package remains importable in orchestration, test, and CPU-only runtimes.
"""

from .arbitration import GeminiAudioArbiterAdapter
from .audio_audit import GeminiAudioAuditAdapter
from .correction import LLMCorrectorAdapter
from .faster_whisper_recognition import FasterWhisperRecognizerAdapter
from .fixtures import (
    FixtureArbiterAdapter,
    FixtureAudioAuditorAdapter,
    FixtureCorrectorAdapter,
    FixtureNormalizerAdapter,
    FixtureRecognizerAdapter,
    FixtureReferenceRetrieverAdapter,
    FixtureSemanticAnalyzerAdapter,
)
from .memo_recognition import (
    MemoRecognitionAcceptanceReceiptV1,
    MemoRecognitionManifestV1,
    MemoRecognitionTokenV1,
    MemoRecognizerAdapter,
    load_memo_recognition_manifest,
)
from .normalized_handoff import (
    NormalizedAudioHandoffManifestV1,
    VerifiedNormalizedAudioHandoffAdapter,
    wav_duration_ms,
)
from .recognition import (
    Qwen3ASRRecognizerAdapter,
    WhisperXRecognizerAdapter,
    WordsJsonImportManifest,
    WordsJsonRecognizerAdapter,
)
from .reference import (
    ExtractedPassage,
    LocalReferenceRetriever,
    ReferenceExactLookupRequest,
    ReferenceIndex,
    ReferenceSourceSpec,
)
from .semantic import SemanticAnalyzerAdapter
from .speaker import MicEnergySpeakerAttributor
from .speech_coverage import (
    FFmpegSpeechCoverageAnalyzer,
    FixtureSpeechCoverageAnalyzer,
)

__all__ = [
    "FixtureArbiterAdapter",
    "FixtureAudioAuditorAdapter",
    "FixtureCorrectorAdapter",
    "FixtureNormalizerAdapter",
    "FixtureReferenceRetrieverAdapter",
    "FixtureRecognizerAdapter",
    "FixtureSemanticAnalyzerAdapter",
    "FixtureSpeechCoverageAnalyzer",
    "FFmpegSpeechCoverageAnalyzer",
    "FasterWhisperRecognizerAdapter",
    "MemoRecognitionAcceptanceReceiptV1",
    "MemoRecognitionManifestV1",
    "MemoRecognitionTokenV1",
    "MemoRecognizerAdapter",
    "NormalizedAudioHandoffManifestV1",
    "VerifiedNormalizedAudioHandoffAdapter",
    "wav_duration_ms",
    "GeminiAudioArbiterAdapter",
    "GeminiAudioAuditAdapter",
    "LLMCorrectorAdapter",
    "SemanticAnalyzerAdapter",
    "ExtractedPassage",
    "LocalReferenceRetriever",
    "MicEnergySpeakerAttributor",
    "ReferenceExactLookupRequest",
    "ReferenceIndex",
    "ReferenceSourceSpec",
    "Qwen3ASRRecognizerAdapter",
    "WhisperXRecognizerAdapter",
    "WordsJsonImportManifest",
    "WordsJsonRecognizerAdapter",
    "load_memo_recognition_manifest",
]
