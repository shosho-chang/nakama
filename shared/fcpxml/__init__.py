"""shared.fcpxml — single FCPXML builder seam (ADR-050 D2)."""

from shared.fcpxml.builder import (
    SUPPORTED_VERSIONS,
    Asset,
    Clip,
    FcpxmlVersion,
    Timeline,
    build_fcpxml,
    deterministic_uid,
    merge_ripple_segments,
    rational_duration,
    write_fcpxml,
)

__all__ = [
    "SUPPORTED_VERSIONS",
    "Asset",
    "Clip",
    "FcpxmlVersion",
    "Timeline",
    "build_fcpxml",
    "deterministic_uid",
    "merge_ripple_segments",
    "rational_duration",
    "write_fcpxml",
]
