"""CLI wrapper for the production Memo corrected-text projection contract."""

from __future__ import annotations

from agents.brook.podcast_subtitles.memo_projection import (  # noqa: F401
    AlignmentMetrics,
    BoundaryRetentionError,
    MemoCue,
    ProjectedCue,
    ProjectionResult,
    SourceToken,
    apply_boundary_merge_proposals,
    load_boundary_merge_proposals,
    main,
    parse_srt,
    project_tokens_to_memo_cues,
    render_srt,
)

if __name__ == "__main__":
    raise SystemExit(main())
