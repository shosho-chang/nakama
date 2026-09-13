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
      "keywords": { ... },          # Step 2 的整份關鍵字研究；只有該集第一支要帶
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
    keywords.json          — 該集的關鍵字快取（第一支寫、後面幾支讀）

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
# 關鍵字快取（per-集一支）
# --------------------------------------------------------------------------

#: Step 2 的整份關鍵字研究落在這裡，整集共用。
KEYWORDS_CACHE_NAME = "keywords.json"


def _ensure_keywords_cache(packaging_dir: Path, input_data: dict) -> tuple[Path, str]:
    """確保 `<packaging_dir>/keywords.json` 存在，缺了就 fail closed。

    skill 的 Step 2 寫著「per-集一次快取」，但寫檔責任落在 agent 身上，於是
    **沒有任何 deterministic 保證**：20260901 蘇予昕 整集跑完連一份都沒有，
    20260721 呂冠緯 是跑到第二支才補上的。一集要跑 1 支完整節目 + 3 支長精華
    + 3 支短片，關鍵字查詢因此被重複到 7 次，而設計上只該查 1 次——那是
    packaging 線網路／LLM 用量最大的一塊。

    所以改由本 script 落檔：第一支把整份研究放進 `keywords` 一起送進來，之後
    幾支什麼都不用帶（檔案已經在了）。**缺檔又沒帶研究就直接擋下來**——
    「靜靜地再查一次網路」正是要根除的失效模式。要強制重查就先刪掉那個檔。
    """
    cache_path = packaging_dir / KEYWORDS_CACHE_NAME
    inline = input_data.get("keywords")
    if cache_path.is_file():
        if inline:
            sys.stderr.write(
                f"emit_packages: WARNING — {cache_path} 已存在，這次帶進來的 keywords 不會覆寫它。"
                "快取就是為了不要每支重查一次；真的要更新請先刪掉該檔再跑。\n"
            )
        return cache_path, "reused"
    if not isinstance(inline, dict) or not inline:
        raise ValueError(
            f"{cache_path} 不存在，而這次的輸入也沒有帶 `keywords`。"
            "該集第一支必須把 Step 2 的整份關鍵字研究放進 `keywords` 一起送進來，"
            "後面幾支才讀得到快取、不用重查網路。"
        )
    cache_path.write_text(json.dumps(inline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cache_path, "written"


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

    # 關鍵字快取先落地——它是整集共用的，不該綁在某一支的成敗上。
    keywords_path, keywords_state = _ensure_keywords_cache(packaging_dir, input_data)

    # Always write title_trace.json — **逐支一個子目錄**，不是扁平單檔。
    # ADR-054 D14「推導鏈逐支落地」，而 `title_trace_ref` 的形狀本來就是
    # `packaging/<cut_id>/title_trace.json`。舊版寫在 `packaging/title_trace.json`，
    # 跑第二支就把第一支的完整推導鏈整檔抹掉——跟下面 packages.json 那段血淚
    # （2026-07-29 謝伯讓集）是同一類 bug，只是當時只修了 packages 那一半。
    # 20260721 呂冠緯 的 `full` 那支已經有一份扁平的舊檔，改路徑之後它不會被動到。
    trace_dir = packaging_dir / cut_id
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / "title_trace.json"
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

    # 寫檔前**整檔**驗證，一支都不跳過。長片的 titles-only 草稿現在是合法狀態
    # （`CutV1` 改成「至多 3 個 package」，湊滿由 approve gate 守），所以不再需要
    # 把草稿挑出去才驗得過——那個例外本身就是漏洞：被跳過的那幾支等於沒驗。
    PackagesFileV1.model_validate(merged)

    packages_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    written_files = [str(trace_path), str(packages_path)]
    if keywords_state == "written":
        written_files.append(str(keywords_path))

    # Copy to vault if VAULT_PATH is available
    vault_copies: list[str] = []
    if vault_path is not None:
        vault_ep_dir = vault_path / "Attachments" / "packaging" / episode_slug
        vault_ep_dir.mkdir(parents=True, exist_ok=True)
        # 推導鏈跟 working set 一樣逐支放子目錄——只用 `src.name` 的話，三支長片
        # 會在 vault 裡搶同一個 title_trace.json，等於把 working set 剛修好的
        # 覆寫問題原封不動搬到 SoT 上。packages.json 是全集共用一份，照舊。
        trace_dst_dir = vault_ep_dir / cut_id
        trace_dst_dir.mkdir(parents=True, exist_ok=True)
        for src, dst in (
            (trace_path, trace_dst_dir / trace_path.name),
            (packages_path, vault_ep_dir / packages_path.name),
        ):
            shutil.copy2(src, dst)
            vault_copies.append(str(dst))

    return {
        "titles_ok": len(titles),
        "df_rejected": len(df_rejected),
        "files": written_files,
        "vault_copies": vault_copies,
        "keywords_cache": keywords_state,
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
        + (
            "，關鍵字快取沿用既有的（本支沒有重查網路）"
            if result["keywords_cache"] == "reused"
            else "，關鍵字快取已建立（後面幾支直接讀，不用重查）"
        )
    )
    for f in result["files"]:
        print(f"  → {f}")
    for f in result["vault_copies"]:
        print(f"  → vault: {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
