"""cleanup — mistake removal（ADR-050 D3 選配前置 stage）.

兩種模式：

- **script-anchored（主線，2026-07-04 起）**：單擊掌 marker + WhisperX 字級
  transcript + 完整逐字稿。刪除範圍由文字決定（retake 指紋回溯），並可產
  文字全對的 corrected SRT。見 ``script_align``。
- **audio-only（legacy）**：double-clap marker + 音訊 VAD 回溯
  （``detect_clap_markers``）。無逐字稿時的 fallback；已知限制：失敗 take
  前只隔靜音的正確內容會被誤刪。

兩者都輸出 ripple-delete FCPXML，DaVinci import 後接管實際切點（不另出
乾淨 mp4 — ADR-015 凍結語意，ADR-050 沿用）。
"""

from agents.brook.script_video.cleanup.clap_impulse import (
    ClapEvent,
    NgMarker,
    detect_claps,
    merge_ng_markers,
)
from agents.brook.script_video.cleanup.cuts import CutPoint
from agents.brook.script_video.cleanup.mistake_removal import (
    detect_clap_markers,
    detect_single_claps,
)
from agents.brook.script_video.cleanup.ripple_fcpxml import emit_ripple_timeline
from agents.brook.script_video.cleanup.script_align import (
    Word,
    correct_srt,
    detect_script_anchored_cuts,
    load_words,
    remap_words_through_cuts,
)
from agents.brook.script_video.cleanup.script_coverage import (
    CleanPlan,
    build_clean_plan,
    build_srt,
    verify_plan,
)

__all__ = [
    "ClapEvent",
    "CleanPlan",
    "CutPoint",
    "NgMarker",
    "Word",
    "build_clean_plan",
    "build_srt",
    "correct_srt",
    "detect_clap_markers",
    "detect_claps",
    "detect_script_anchored_cuts",
    "detect_single_claps",
    "emit_ripple_timeline",
    "load_words",
    "merge_ng_markers",
    "remap_words_through_cuts",
    "verify_plan",
]
