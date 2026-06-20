"""把影片 ``.vtt`` render 成並排的人讀 + LLM 可 ingest 逐字稿 ``.md``。

影片的機器原始檔是 ``KB/Raw/Videos/{id}.vtt``（帶時間碼、karaoke 重複，Obsidian
不顯示也難讀）。本模組把它洗成並排的 ``KB/Raw/Videos/{id}.md`` —— 時間碼段落
（``**[H:MM:SS]**`` 前綴），同時是：

- **人讀的乾淨全文逐字稿**（Obsidian 看得到、讀得順、可定位影片時刻）。
- **``/start-video`` ingest 的優先輸入**（已是乾淨 prose、frontmatter 帶真標題與頻道）。

``.md`` 是 ``.vtt`` 的衍生物，不是新來源：沒有任何 lister 掃 ``KB/Raw/Videos/`` 把它
當可 ingest 來源（reading_source resolver 只認 hardcode 的 ``.vtt`` 路徑，見
``shared/reading_source_registry.py`` ``_resolve_youtube``）。位置與理由見
``docs/VAULT-LAYOUT.md``。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from shared.literature_writer import _video_display_title
from shared.log import get_logger
from shared.obsidian_writer import write_page
from shared.utils import read_text
from shared.webvtt import webvtt_to_transcript_markdown

logger = get_logger("nakama.video_transcript")


def _video_channel(video_id: str, vault_path: Path) -> str:
    """manifest 的頻道名（內容創作者）當 ``author``；缺失 / 壞檔回空字串。"""
    manifest = vault_path / "Watchlist" / "youtube" / video_id / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return str(data.get("channel") or "").strip()
    except Exception:  # noqa: BLE001 — manifest 問題不阻斷 render
        return ""


def write_video_transcript_md(vault_path: Path, video_id: str) -> Path | None:
    """從 ``KB/Raw/Videos/{video_id}.vtt`` render 並排的人讀 ``.md``。

    回傳寫入的 ``.md`` 絕對路徑；找不到 ``.vtt`` 或洗出空內容 → 回 ``None``（不寫檔）。
    idempotent：每次都從 ``.vtt`` 重新產生（覆寫）—— ``.vtt`` 是唯一真相。
    """
    vtt_path = vault_path / "KB" / "Raw" / "Videos" / f"{video_id}.vtt"
    if not vtt_path.is_file():
        logger.warning(f"transcript md：找不到 .vtt（video_id={video_id}）")
        return None

    body = webvtt_to_transcript_markdown(read_text(vtt_path))
    if not body:
        logger.warning(f"transcript md：.vtt 洗出空內容（video_id={video_id}）")
        return None

    rel = f"KB/Raw/Videos/{video_id}.md"
    write_page(
        rel,
        frontmatter={
            "title": _video_display_title(f"youtube_{video_id}", vault_path),
            "type": "video_transcript",
            "source_type": "video",
            "video_id": video_id,
            # 內容創作者（頻道）；ingest 的 .md 分支讀 author → Source 的 original_author。
            "author": _video_channel(video_id, vault_path),
            "source": f"KB/Raw/Videos/{video_id}.vtt",
            "generated": str(date.today()),
            "generated_by": "agent_robin",
        },
        body=body,
    )
    logger.info(f"transcript md：已寫入 {rel}")
    return vault_path / rel
