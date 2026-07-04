"""cleanup — 拍掌 marker mistake removal（ADR-050 D3 選配前置 stage）.

ADR-015 record-first pipeline 的唯一存活資產：修修錄影時唸錯拍兩下手，
這裡偵測 clap marker → 產 ripple-delete FCPXML，DaVinci import 後接管實際
切點（不另出乾淨 mp4 — ADR-015 凍結語意，ADR-050 沿用）。

輸出乾淨錄影後再走 /transcribe → storyboard pipeline（本套件的下游）。
CLI 掛載（`cleanup` subcommand）於 ADR-050 PR-5 接通。
"""

from agents.brook.script_video.cleanup.cuts import CutPoint
from agents.brook.script_video.cleanup.mistake_removal import detect_clap_markers
from agents.brook.script_video.cleanup.ripple_fcpxml import emit_ripple_timeline

__all__ = ["CutPoint", "detect_clap_markers", "emit_ripple_timeline"]
