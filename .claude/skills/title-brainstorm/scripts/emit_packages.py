#!/usr/bin/env python3
"""emit_packages.py — 吃 title-brainstorm --batch 輸出，驗證並落檔。

用法:
    python scripts/emit_packages.py <packaging_dir> < input.json

輸入 JSON (stdin):
    {
      "episode": "20260723-xieboran",   # ASCII slug — packages.json / vault 子目錄名
      "cut_id": "punch-L1",
      "format": "long" | "short",
      "information_origin": "full_text" | "one_liner",
      "visual_recipe": "podcast" | "youtube_host" | "youtube_book",
      "aspect": "16:9",
      "citations": [],
      "brand_flags": [],
      "titles": [
        {
          "text": "...",
          "archetype_id": "T-A1",
          "angle_combo": ["好奇缺口"],
          "payoff": "...",
          "cite": "srt/punch-L1_r003.srt#12",
          "rank": 1
        }
      ],
      "title_trace": { ... }   # 完整推導鏈，寫入 title_trace.json
    }

輸出 (寫到 <packaging_dir>/ + 複製到 vault):
    title_trace.json       — 完整推導鏈（always）
    packages.json          — short: valid PackagesFileV1; long: titles-only draft

環境變數:
    VAULT_PATH  — vault root (e.g. E:/Shosho LifeOS)
                  若未設，跳過 vault copy 並印 WARNING
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- repo root on sys.path so we can import shared modules ---
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[3]  # scripts/ → title-brainstorm/ → skills/ → .claude/ → repo
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.thumbnail_playbook import load_playbook_index  # noqa: E402

try:
    from pydantic import ValidationError  # noqa: E402

    from shared.schemas.packaging import (  # noqa: E402
        CutV1,
        PackagesFileV1,
        TitleV1,
    )
except ImportError as exc:
    sys.exit(f"emit_packages: import error — {exc}\n(run from repo root: PYTHONPATH=. python ...)")


# --------------------------------------------------------------------------
# Grade gate
# --------------------------------------------------------------------------

_DF_GRADES = {"D", "F"}


def _load_df_title_archetypes() -> set[str]:
    """Return set of title archetype IDs with D or F brand-fit grade."""
    try:
        idx = load_playbook_index()
    except Exception:  # noqa: BLE001
        return set()
    return {a.id for a in idx.title_archetypes if a.brand_fit_grade in _DF_GRADES}


# --------------------------------------------------------------------------
# Core emit
# --------------------------------------------------------------------------


def emit(
    input_data: dict,
    packaging_dir: Path,
    *,
    vault_path: Path | None = None,
) -> dict:
    """Validate titles, write title_trace.json + packages.json.

    Returns a summary dict with keys: 'titles_ok', 'df_rejected', 'files'.
    Raises ValueError on schema violations.
    """
    episode: str = input_data["episode"]
    # vault 落點目錄名：ADR-054 D10 用 ASCII slug（`20260723-xieboran`），不是 CJK
    # 的 `episode` 欄。attach_packages.py 一直吃 `--episode-slug`，emit 端卻沿用
    # `episode` → 同一集會生出兩個 vault 目錄（2026-07-29 謝伯讓集實際踩到）。
    episode_slug: str = input_data.get("episode_slug") or episode
    cut_id: str = input_data["cut_id"]
    fmt: str = input_data["format"]
    info_origin: str = input_data.get("information_origin", "full_text")
    visual_recipe: str = input_data.get("visual_recipe", "podcast")
    aspect: str = input_data.get("aspect", "16:9")
    citations: list = input_data.get("citations", [])
    brand_flags: list = input_data.get("brand_flags", [])
    raw_titles: list[dict] = input_data["titles"]
    title_trace: dict = input_data.get("title_trace", {})
    generated_at = datetime.now(timezone.utc).isoformat()

    df_ids = _load_df_title_archetypes()
    df_rejected: list[dict] = []
    accepted_raw: list[dict] = []
    for t in raw_titles:
        if t.get("archetype_id") in df_ids:
            df_rejected.append(t)
        else:
            accepted_raw.append(t)

    if df_rejected:
        rejected_ids = [t.get("archetype_id") for t in df_rejected]
        sys.stderr.write(
            f"emit_packages: D/F-grade archetype 已剔除 {rejected_ids} (brand credibility gate)\n"
        )

    # Validate each TitleV1 — pydantic raises ValidationError on bad shape
    titles: list[TitleV1] = []
    for raw in accepted_raw:
        try:
            titles.append(TitleV1.model_validate(raw))
        except ValidationError as exc:
            raise ValueError(f"TitleV1 validation failed: {exc}") from exc

    packaging_dir.mkdir(parents=True, exist_ok=True)

    # Always write title_trace.json
    trace_path = packaging_dir / "title_trace.json"
    trace_out = {
        "episode": episode,
        "cut_id": cut_id,
        "generated_at": generated_at,
        "title_trace": title_trace,
        "titles": [t.model_dump() for t in titles],
    }
    trace_path.write_text(json.dumps(trace_out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write packages.json — **merge by cut_id, never whole-file overwrite**。
    # ADR-054 D14 是「逐支處理」：一集有 3 長 + 3~4 短，每支各跑一次本 script。
    # 舊版兩個分支都 write(cuts=[單一 cut])，跑第二支就把第一支的標題與**已 render
    # 的 packages** 一起抹掉（含 vault SoT）。2026-07-29 謝伯讓集差點全毀，靠 agent
    # 改用 per-cut 子目錄才閃過。
    packages_path = packaging_dir / "packages.json"

    if fmt == "short":
        new_cut = CutV1(
            cut_id=cut_id,
            format="short",
            information_origin=info_origin,
            visual_recipe=visual_recipe,
            aspect=aspect,
            titles=titles,
            packages=[],
            citations=citations,
            brand_flags=brand_flags,
            thumbnail=None,
        ).model_dump()
    else:
        # Long cut — titles-only 草稿；packages 由 S5 thumbnail brainstorm 補。
        # 不放 `_draft` / `_note` 這類額外欄位：CutV1 是 extra_forbid，留著會讓
        # attach_packages 的整檔驗證炸掉（packages 空 → 用 len 判斷即可）。
        new_cut = {
            "cut_id": cut_id,
            "format": fmt,
            "information_origin": info_origin,
            "visual_recipe": visual_recipe,
            "aspect": aspect,
            "citations": citations,
            "brand_flags": brand_flags,
            "titles": [t.model_dump() for t in titles],
            "packages": [],
        }

    existing: dict = {}
    if packages_path.exists():
        try:
            existing = json.loads(packages_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # 壞損不靜默重建——重建等於把別支的成果丟掉
            raise ValueError(
                f"{packages_path} 不是合法 JSON（{exc}）。修好或改名備份後再跑，"
                "本 script 不會覆寫壞損檔。"
            ) from exc

    cuts: list[dict] = list(existing.get("cuts", []))
    for i, c in enumerate(cuts):
        if c.get("cut_id") == cut_id:
            cuts[i] = new_cut
            break
    else:
        cuts.append(new_cut)

    merged = {
        "episode": episode,
        "generated_at": generated_at,
        "cuts": cuts,
    }

    # 寫檔前驗證：長片在本階段本來就還沒有 packages（S5 才補），這些草稿跳過；
    # 其餘（短片、已配好封面的長片）必須通過 S1 schema，才不會把壞資料寫進別支。
    drafts = {c["cut_id"] for c in cuts if c.get("format") == "long" and not c.get("packages")}
    PackagesFileV1.model_validate(
        {**merged, "cuts": [c for c in cuts if c["cut_id"] not in drafts]}
    )

    packages_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    written_files = [str(trace_path), str(packages_path)]

    # Copy to vault if VAULT_PATH is available
    vault_copies: list[str] = []
    if vault_path is not None:
        vault_ep_dir = vault_path / "Attachments" / "packaging" / episode_slug
        vault_ep_dir.mkdir(parents=True, exist_ok=True)
        for src in (trace_path, packages_path):
            dst = vault_ep_dir / src.name
            shutil.copy2(src, dst)
            vault_copies.append(str(dst))

    return {
        "titles_ok": len(titles),
        "df_rejected": len(df_rejected),
        "files": written_files,
        "vault_copies": vault_copies,
    }


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python scripts/emit_packages.py <packaging_dir> < input.json\n")
        return 2

    packaging_dir = Path(sys.argv[1])
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"emit_packages: JSON parse error — {exc}\n")
        return 1

    vault_env = os.environ.get("VAULT_PATH")
    vault_path = Path(vault_env) if vault_env else None
    if vault_path is None:
        sys.stderr.write("emit_packages: WARNING — VAULT_PATH 未設，跳過 vault copy\n")

    try:
        result = emit(input_data, packaging_dir, vault_path=vault_path)
    except (ValueError, KeyError) as exc:
        sys.stderr.write(f"emit_packages: ERROR — {exc}\n")
        return 1

    print(
        f"OK — {result['titles_ok']} 條標題已驗證"
        + (f"，{result['df_rejected']} 條 D/F-grade 已剔除" if result["df_rejected"] else "")
    )
    for f in result["files"]:
        print(f"  → {f}")
    for f in result["vault_copies"]:
        print(f"  → vault: {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
