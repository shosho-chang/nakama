"""修復 KB/Wiki/Concepts/ 內 frontmatter 雙包夾 `---` 的破損頁。

根因：`shared/obsidian_writer.write_page` 在 `width` 修補前用 PyYAML default 80 char
fold，含字面 `---` 的長 source filename 被從中間切斷，下一行開頭恰好是 `---`，被
yaml loader 當 document separator 切開，導致 frontmatter 被截斷成兩段。

此 script 偵測該 pattern 並還原：
1. parse 第一段 yaml1 → frontmatter dict
2. lost segment 前段 raw text → 接回最後一個 source_refs item（用 `---` 連接）
3. lost segment 後段 yaml fragment → merge 進 frontmatter
4. 用 width=10**9 重 dump（與 obsidian_writer 一致）

預設 dry-run；要落地寫加 `--apply`。落地會先寫 `.bak` 同目錄。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

_KEY_LINE_RE = re.compile(r"^[a-z_][a-z0-9_]*:\s*")


def fix(content: str) -> tuple[str, str] | None:
    """嘗試修復 broken frontmatter；回傳 (new_content, summary) 或 None（不需 / 無法修）。"""
    if not content.startswith("---\n"):
        return None

    parts = content.split("\n---\n")
    if len(parts) < 3:
        return None

    yaml1_text = parts[0][len("---\n") :]
    try:
        fm = yaml.safe_load(yaml1_text)
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict):
        return None

    lost = parts[1]
    body_tail = "\n---\n".join(parts[2:])
    body = body_tail.lstrip("\n")

    lost_lines = lost.split("\n")
    yaml_start = None
    for i, line in enumerate(lost_lines):
        if _KEY_LINE_RE.match(line):
            yaml_start = i
            break

    if yaml_start is None:
        return None

    raw_lines = [ln for ln in lost_lines[:yaml_start] if ln.strip()]
    if len(raw_lines) > 1:
        return None
    raw_suffix = raw_lines[0].strip() if raw_lines else ""

    yaml2_text = "\n".join(lost_lines[yaml_start:])

    recovered_ref_idx: int | None = None
    if raw_suffix and isinstance(fm.get("source_refs"), list):
        for i, ref in enumerate(fm["source_refs"]):
            if isinstance(ref, str) and not ref.endswith(".md"):
                fm["source_refs"][i] = f"{ref}---{raw_suffix}"
                recovered_ref_idx = i
                break

    merged_keys: list[str] = []
    try:
        fm2 = yaml.safe_load(yaml2_text)
    except yaml.YAMLError:
        return None
    if isinstance(fm2, dict):
        for k, v in fm2.items():
            if k not in fm:
                fm[k] = v
                merged_keys.append(k)

    yaml_str = yaml.safe_dump(
        fm,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=10**9,
    ).rstrip()
    new_content = f"---\n{yaml_str}\n---\n\n{body}"

    summary_bits = []
    if recovered_ref_idx is not None:
        summary_bits.append(f"recovered source_refs[{recovered_ref_idx}] suffix={raw_suffix!r}")
    if merged_keys:
        summary_bits.append(f"merged keys={merged_keys}")
    if not summary_bits:
        summary_bits.append("no-op (structural reformat only)")
    return new_content, "; ".join(summary_bits)


def iter_targets(vault: Path, include_entities: bool) -> list[Path]:
    targets: list[Path] = []
    roots = [vault / "KB" / "Wiki" / "Concepts"]
    if include_entities:
        roots.append(vault / "KB" / "Wiki" / "Entities")
    for root in roots:
        if not root.exists():
            continue
        targets.extend(sorted(root.rglob("*.md")))
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault", type=Path, default=None, help="vault root（預設讀 shared.config.get_vault_path）"
    )
    parser.add_argument("--apply", action="store_true", help="實際寫入；預設 dry-run")
    parser.add_argument("--no-backup", action="store_true", help="apply 時不寫 .bak")
    parser.add_argument("--include-entities", action="store_true", help="同時掃 KB/Wiki/Entities")
    args = parser.parse_args(argv)

    if args.vault is None:
        from shared.config import get_vault_path

        vault = get_vault_path()
    else:
        vault = args.vault

    targets = iter_targets(vault, args.include_entities)
    fixed = 0
    skipped = 0

    for md in targets:
        content = md.read_text(encoding="utf-8")
        result = fix(content)
        if result is None:
            skipped += 1
            continue
        new_content, msg = result
        rel = md.relative_to(vault)
        if args.apply:
            if not args.no_backup:
                bak = md.with_suffix(md.suffix + ".bak")
                bak.write_text(content, encoding="utf-8")
            md.write_text(new_content, encoding="utf-8")
            print(f"[FIX] {rel}: {msg}")
        else:
            print(f"[DRY] {rel}: {msg}")
        fixed += 1

    mode = "apply" if args.apply else "dry-run"
    print(f"\nSummary ({mode}): fixed={fixed}, skipped={skipped}, vault={vault}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
