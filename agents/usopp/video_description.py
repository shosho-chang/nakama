"""影片描述欄組裝（video-publishing-plan Q5、ADR-055 slice 2）。

描述欄四段結構（修修 2026-07-26 凍結）：

    ┌─ 變動（LLM 產、修修在審核頁改）  hook 兩三句
    ├─ 變動（長片才有）              ⏱ 分章（從轉場卡自動生成）
    ├─ 變動（citations 從 packaging 交接檔搬）  本集引用
    └─ 固定（templates/video_description_footer.md，40 支共用）

設計要點：

- **分章零人工**：長片的章節 = broll.json 的 transition_title items
  （t0 + title）——視覺轉場卡與描述欄分章天生同源（同一份企劃檔），
  不可能漂移。00:00 固定為「開場」。
- **citations 不 parse 散文**：ADR-054 §7 裁決 packaging skill 從
  review_brandlens.json 搬進 packages.json，發布層零 parse。
- **固定段獨立成檔**：改一次 CTA 套用全部，不重生 40 支文案。
- hook 由 LLM（Claude session，吃 `data/brook/style-profiles-fable5/`
  voice profile）代筆——Stage 6 平台文案在 LLM 代筆邊界內
  （ADR-027 只管 Stage 4 原子文章正文；先例：FB/IG renderer）。

與 WP 那條線（publisher.py）平行、不共用零件（ADR-055 D2）。
Tests：tests/test_video_description.py。
"""

from __future__ import annotations

import json
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parent / "templates"
FOOTER_FILE = _TEMPLATES / "video_description_footer.md"


def fmt_ts(sec: float) -> str:
    """YT 分章時間戳：MM:SS（>1h 才 H:MM:SS——長片 8–12min 用不到但防呆）。"""
    s = int(sec)
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m:02d}:{ss:02d}"


def chapters_from_broll(broll_items: list[dict]) -> list[tuple[float, str]]:
    """長片分章 = 滿版轉場卡的 (t0, title)，前加 00:00 開場。

    YT 分章規則：首章必須 00:00、至少 3 章、遞增——轉場卡 <2 個時回空
    （寧可不分章也不出殘缺章節表）。"""
    marks = [
        (float(it["t0"]), str(it["vars"]["title"]))
        for it in broll_items
        if it.get("comp") == "transition_title"
    ]
    marks.sort()
    if len(marks) < 2:
        return []
    return [(0.0, "開場")] + marks


def load_citations(packages: dict, cut_id: str) -> list[str]:
    """packaging 交接檔的 citations（ADR-054 §7：上游已從 brandlens JSON 搬好）。"""
    cut = next((c for c in packages.get("cuts", []) if c.get("cut_id") == cut_id), None)
    if cut is None:
        raise ValueError(f"{cut_id} 不在 packages.json——packaging 段還沒跑這支")
    return list(cut.get("citations") or [])


def chosen_package(packages: dict, approval: dict, cut_id: str) -> dict:
    """approval.primary_package 指向的那組（title + 縮圖）——「已決定」而非候選。"""
    ap = next((a for a in approval.get("approvals", []) if a.get("cut_id") == cut_id), None)
    if ap is None or not ap.get("approved"):
        raise ValueError(f"{cut_id} 未在 approval.json 核准——先過 packaging gate")
    rank = int(ap["primary_package"])
    cut = next(c for c in packages["cuts"] if c["cut_id"] == cut_id)
    title = next((t for t in cut["titles"] if t.get("rank") == rank), None)
    pkg = next((p for p in cut.get("packages", []) if p.get("title_rank") == rank), None)
    if title is None:
        raise ValueError(f"{cut_id} primary_package={rank} 對不到 titles")
    return {
        "title": title["text"],
        "thumbnail": (pkg or {}).get("thumbnail_png"),
        "title_rank": rank,
    }


def find_packaging_dir(vault: Path, episode: str) -> Path:
    """episode 資料夾名 → packaging 目錄（slug 不可推導，scan packages.json 的
    episode 欄位機器對應——「20260723 謝伯讓」↔「20260723-xieboran」）。"""
    root = vault / "Attachments" / "packaging"
    for d in sorted(root.iterdir()) if root.exists() else []:
        pj = d / "packages.json"
        if pj.exists():
            try:
                if json.loads(pj.read_text(encoding="utf-8")).get("episode") == episode:
                    return d
            except (json.JSONDecodeError, OSError):
                continue
    raise ValueError(f"vault 找不到 episode「{episode}」的 packaging 交接檔（{root}）")


def build_description(
    hook: str,
    chapters: list[tuple[float, str]],
    citations: list[str],
    footer: str,
) -> str:
    """四段組裝。空段整段省略（短片無分章；沒引用就沒有「本集引用」）。"""
    blocks = [hook.strip()]
    if chapters:
        blocks.append("\n".join(f"⏱ {fmt_ts(t)} {title}" for t, title in chapters))
    if citations:
        blocks.append("本集引用：\n" + "\n".join(f"・{c}" for c in citations))
    if footer.strip():
        blocks.append(footer.strip())
    return "\n\n".join(b for b in blocks if b)


def load_footer() -> str:
    """固定段模板。剝掉 <!-- --> 註解——YT 描述不解析 HTML，註解會原樣顯示。"""
    if not FOOTER_FILE.exists():
        raise FileNotFoundError(f"固定段模板不存在: {FOOTER_FILE}")
    text = FOOTER_FILE.read_text(encoding="utf-8")
    import re

    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
