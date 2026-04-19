"""Project Bootstrap CLI — skill-friendly wrapper around shared.lifeos_writer.

Invoked by the `project-bootstrap` Claude Code skill to create one LifeOS Project
file plus N Task files (default 3) with cross-linking wikilinks.

Usage:
    python scripts/run_project_bootstrap.py \
        --title "超加工食品" \
        --content-type research \
        --tasks "Literature Review" "Synthesis" "Write-up" \
        --area work --priority medium
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_vault_path  # noqa: E402
from shared.lifeos_writer import (  # noqa: E402
    CONTENT_TYPES,
    ProjectExistsError,
    create_project_with_tasks,
    default_task_names,
)


def _obsidian_uri(vault: Path, rel_path: str) -> str:
    vault_name = vault.name
    file_arg = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return f"obsidian://open?vault={quote(vault_name)}&file={quote(file_arg)}"


def main() -> int:
    p = argparse.ArgumentParser(description="Bootstrap a LifeOS Project + 3 Tasks.")
    p.add_argument("--title", required=True, help="Project title (becomes filename)")
    p.add_argument(
        "--content-type",
        required=True,
        choices=list(CONTENT_TYPES),
        help="Project content type",
    )
    p.add_argument(
        "--tasks",
        nargs="+",
        help="Task names (default: content_type preset of 3 tasks)",
    )
    p.add_argument("--area", default="work")
    p.add_argument("--priority", default="medium")
    p.add_argument("--status", default="active")
    p.add_argument("--search-topic", default=None)
    p.add_argument("--estimated-pomodoros", type=int, default=4)
    p.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Override vault path (default: config.yaml vault_path)",
    )
    p.add_argument(
        "--json-out",
        type=Path,
        help="Write result JSON here (default: stdout)",
    )
    args = p.parse_args()

    tasks = args.tasks or default_task_names(args.content_type)
    vault = args.vault if args.vault is not None else get_vault_path()

    try:
        result = create_project_with_tasks(
            args.title,
            args.content_type,
            tasks,
            vault=vault,
            area=args.area,
            priority=args.priority,
            status=args.status,
            search_topic=args.search_topic,
            estimated_pomodoros=args.estimated_pomodoros,
        )
    except ProjectExistsError as e:
        err = {"error": "ProjectExistsError", "detail": str(e)}
        _emit(err, args.json_out)
        return 2

    project_rel = str(result.project_path.relative_to(vault)).replace("\\", "/")
    task_rels = [str(p.relative_to(vault)).replace("\\", "/") for p in result.task_paths]

    payload = {
        "project_path": project_rel,
        "task_paths": task_rels,
        "content_type": args.content_type,
        "vault_abs_project": str(result.project_path),
        "obsidian_uri": _obsidian_uri(vault, project_rel),
    }
    _emit(payload, args.json_out)
    return 0


def _emit(data: dict, json_out: Path | None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if json_out:
        json_out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    raise SystemExit(main())
