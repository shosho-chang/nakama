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

    # Write packages.json
    # Short: full PackagesFileV1 (packages=[], thumbnail=null).
    # Long: titles-only draft (PackagesFileV1 schema intentionally not validated
    #       at this stage — packages are added by S5 thumbnail brainstorm).
    packages_path = packaging_dir / "packages.json"
    if fmt == "short":
        cut = CutV1(
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
        )
        pkg_file = PackagesFileV1(
            episode=episode,
            generated_at=generated_at,
            cuts=[cut],
        )
        packages_path.write_text(pkg_file.model_dump_json(indent=2), encoding="utf-8")
    else:
        # Long cut — titles-only draft; S5 will add packages
        draft = {
            "episode": episode,
            "generated_at": generated_at,
            "cuts": [
                {
                    "cut_id": cut_id,
                    "format": fmt,
                    "information_origin": info_origin,
                    "visual_recipe": visual_recipe,
                    "aspect": aspect,
                    "citations": citations,
                    "brand_flags": brand_flags,
                    "titles": [t.model_dump() for t in titles],
                    "packages": [],
                    "_draft": True,
                    "_note": "titles-only draft — packages added by S5 thumbnail brainstorm",
                }
            ],
        }
        packages_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")

    written_files = [str(trace_path), str(packages_path)]

    # Copy to vault if VAULT_PATH is available
    vault_copies: list[str] = []
    if vault_path is not None:
        vault_ep_dir = vault_path / "Attachments" / "packaging" / episode
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
