"""一集的「乾淨逐字稿」是哪一份——所有衍生產物共用同一個答案。

ADR-064 之前，episode 根目錄的 `transcript.srt` 就是那份稿（subtitle-correct 的
產物）。ADR-064 之後，成品內容的唯一真值是 **Editorial Master**：完整節目經過人工
剪輯之後的那一版。兩者的差別不是格式，是**內容邊界**——被剪掉的重複、咳嗽、道歉
還留在舊的那份裡。20260805 的 `value-L01` 就曾經把已經剪掉的段落剪回精華片
（ADR-064 Context）。

短片線已經走 Editorial Master（`run_short_tighten._open_editorial_master`）。
社群輪播線卻還寫死根目錄的 `transcript.srt`——同一集拿到的是不同的稿，而且是
**含有已剪掉內容**的那一份。引用一句沒有播出去的話，正是 ADR-064 要防的事。

這支把「用哪一份」收斂成一個答案：

- 有 Editorial Master → 用它的 `master.srt`（經過驗證的收據鏈）
- 沒有 → 退回 `transcript.srt`（ADR-064 之前的集數，行為不變）

**不做的事**：不猜、不合併、不在兩份之間挑「比較長的那個」。找不到就 raise，
讓呼叫端停下來問，不要安靜地用次好的來源生產。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: ADR-064 之前的慣例位置。
LEGACY_SRT_NAME = "transcript.srt"


class EpisodeTranscriptError(RuntimeError):
    """找不到可用的逐字稿來源。"""


@dataclass(frozen=True)
class TranscriptSource:
    """一份逐字稿的來源與出處，出處要能寫進收據。"""

    srt_path: Path
    #: `editorial_master` 或 `legacy_transcript_srt`
    origin: str
    #: Editorial Master 的 lineage（legacy 來源為 None）
    lineage: dict | None = None


def resolve_transcript_srt(episode_dir: Path) -> TranscriptSource:
    """這一集該用的逐字稿 SRT。Editorial Master 優先。"""
    episode_dir = Path(episode_dir)
    receipt = episode_dir / "editorial-master" / "v1" / "EDITORIAL-MASTER.json"
    if receipt.is_file():
        from agents.brook.script_video.editorial_master import (
            EditorialMasterContractError,
            EditorialMasterRequest,
        )

        try:
            master = EditorialMasterRequest(
                episode_dir, expected_episode_id=episode_dir.name
            ).open()
        except EditorialMasterContractError as exc:
            # 有收據卻驗不過**不可以**退回 legacy——那等於在契約壞掉的時候
            # 偷偷改用含已剪內容的稿子，剛好是最危險的那個方向。
            raise EpisodeTranscriptError(
                f"Editorial Master 驗證失敗，不退回 {LEGACY_SRT_NAME}：{exc}"
            ) from exc
        return TranscriptSource(
            srt_path=Path(master.srt_path),
            origin="editorial_master",
            lineage=master.identity(),
        )

    legacy = episode_dir / LEGACY_SRT_NAME
    if legacy.is_file():
        return TranscriptSource(srt_path=legacy, origin="legacy_transcript_srt")

    raise EpisodeTranscriptError(
        f"{episode_dir} 既沒有 Editorial Master 也沒有 {LEGACY_SRT_NAME}——"
        "先跑 subtitle-correct 或建立 Editorial Master，不要用其他 SRT 頂替"
    )
