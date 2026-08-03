#!/usr/bin/env python3
"""packaging_brief.py — 寫 gate 的「這支在講什麼」速覽（ADR-054 D11 UI 零 LLM）。

修修 2026-07-30 在 gate 上的原話：「審 util-L4 的時候我不太清楚這支影片在講
什麼，所以我也沒辦法判斷。」board 原本只給封面＋標題，零內容脈絡。

分工照 D11 的硬約束（VPS 叫不到桌機 Cowork）：
- **內容判讀在 Cowork**（讀該支 SRT、寫一句話／論證骨架／代表原話）
- **本 script 只做機械層**：驗形狀 → 落檔 working set + vault 雙落點
- **board 只讀**，不生成

用法：

    python scripts/packaging_brief.py <packaging_dir> --cut <cut_id> \
        --episode-slug <slug> < brief.json

輸入 JSON（stdin）：

    {
      "one_liner": "這 10 分鐘在講什麼",
      "beats":   [{"at": "03:40", "what": "給第三條路：兩派都是功利主義…"}, ...],
      "quotes":  [{"at": "01:35", "speaker": "謝伯讓", "text": "逐字引用"}, ...],
      "duration": "10:16",           # 選填
      "caution": "02:24 那句是轉述極端派立場，不是他的主張"   # 選填
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.config import get_vault_path  # noqa: E402

_TS_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def build_brief(data: dict, cut_id: str) -> dict:
    """驗形狀並補 metadata。壞形狀 fail loud——半套速覽比沒有更誤導。"""
    one_liner = (data.get("one_liner") or "").strip()
    if not one_liner:
        raise ValueError("one_liner 不可為空——速覽的價值就在那一句")

    beats = data.get("beats") or []
    if not isinstance(beats, list) or not beats:
        raise ValueError("beats 至少要一拍（格式 [{'at','what'}]）")
    clean_beats = []
    for i, b in enumerate(beats):
        at = str(b.get("at", "")).strip()
        what = str(b.get("what", "")).strip()
        if not _TS_RE.match(at):
            raise ValueError(f"beats[{i}].at 必須是 mm:ss 或 hh:mm:ss，收到 {at!r}")
        if not what:
            raise ValueError(f"beats[{i}].what 不可為空")
        clean_beats.append({"at": at, "what": what})

    quotes = data.get("quotes") or []
    if not isinstance(quotes, list):
        raise ValueError("quotes 必須是 list")
    clean_quotes = []
    for i, q in enumerate(quotes):
        at = str(q.get("at", "")).strip()
        text = str(q.get("text", "")).strip()
        if not _TS_RE.match(at):
            raise ValueError(f"quotes[{i}].at 必須是 mm:ss 或 hh:mm:ss，收到 {at!r}")
        if not text:
            raise ValueError(f"quotes[{i}].text 不可為空")
        clean_quotes.append({"at": at, "speaker": str(q.get("speaker", "")).strip(), "text": text})

    return {
        "cut_id": cut_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "one_liner": one_liner,
        "duration": str(data.get("duration", "")).strip() or None,
        "beats": clean_beats,
        "quotes": clean_quotes,
        "caution": (data.get("caution") or "").strip() or None,
    }


def write_brief(packaging_dir: Path, cut_id: str, episode_slug: str, data: dict) -> list[Path]:
    if not _SLUG_RE.match(episode_slug) or not _SLUG_RE.match(cut_id):
        raise ValueError("episode_slug / cut_id 只准 ASCII 檔名字元（ADR-054 D10）")
    brief = build_brief(data, cut_id)
    payload = json.dumps(brief, ensure_ascii=False, indent=2) + "\n"

    # vault root 必須**已經存在**才寫。config.yaml 的 `/home/...` 在 Windows 會被
    # 解成 `E:\home\...` 影子目錄，`mkdir(parents=True)` 會一路把它建出來、看起來
    # 成功但 board 永遠讀不到（2026-07-30 從 worktree 跑本 script 沒 .env 就踩到；
    # 同款根因見 memory/claude/reference_nakama_local_db_path.md）。
    vault_root = get_vault_path()
    if not vault_root.is_dir():
        raise ValueError(
            f"vault root 不存在：{vault_root}\n"
            "（從 worktree 執行時常見原因是該目錄沒有 .env；設 VAULT_PATH 或從 repo 根跑）"
        )

    written: list[Path] = []
    for root in (
        packaging_dir / "briefs",
        vault_root / "Attachments" / "packaging" / episode_slug / "briefs",
    ):
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{cut_id}.json"
        path.write_text(payload, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("packaging_dir", type=Path)
    ap.add_argument("--cut", required=True, dest="cut_id")
    ap.add_argument("--episode-slug", required=True)
    args = ap.parse_args(argv)

    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"packaging_brief: stdin JSON 解析失敗 — {exc}\n")
        return 1

    try:
        written = write_brief(args.packaging_dir, args.cut_id, args.episode_slug, data)
    except (ValueError, KeyError) as exc:
        sys.stderr.write(f"packaging_brief: ERROR — {exc}\n")
        return 1

    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
