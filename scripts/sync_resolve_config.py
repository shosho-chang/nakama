r"""把 ADR-066 的 Resolve 綁定檔對齊**現在**的專案狀態。

每跑完一次物化，canonical timeline 的 UID 就會換一個：交易把原本那條改名成
`__fcp_backup__<cut>__…`、讓新做好的那條頂上原名。綁定檔裡的 `timeline_uid`
於是指向備份，下一次物化就會報 `resolve_project_identity_mismatch` 或
`canonical_binding_unknown`——而畫面上那條 timeline 明明好好的。

`project_uid` 也不是 Resolve 自己的專案 UID，是 locator（資料庫／資料夾／專案名）
的雜湊（`_resolve_fusion._synthetic_project_uid`）。手寫綁定檔的人幾乎一定會填
Resolve UI 上看到的那一個，然後卡在同一個錯誤碼上。

用法（一定要用 cp312 直譯器，3.14 會在 import 當下崩潰）：

    E:\nakama\.venv-v2\Scripts\python.exe scripts/sync_resolve_config.py \
        --config E:\nakama\data\finished-cut-runtime\config\resolve-<episode>.json

`--check` 只比對不寫入，適合放在跑物化之前確認。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RESOLVE_MODULES = Path(
    r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules"
)
if RESOLVE_MODULES.is_dir() and str(RESOLVE_MODULES) not in sys.path:
    sys.path.insert(0, str(RESOLVE_MODULES))

from agents.brook.script_video.finished_cut_production._resolve_fusion import (  # noqa: E402
    ResolveDatabaseIdentity,
    ResolveProjectLocator,
    _synthetic_project_uid,
    connect_resolve_scripting,
)


def _live_timelines(project_name: str) -> dict[str, str]:
    resolve = connect_resolve_scripting()
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    if project is None or project.GetName() != project_name:
        raise SystemExit(
            f"Resolve 現在開著的專案是 {project and project.GetName()!r}，不是 {project_name!r}"
        )
    return {
        project.GetTimelineByIndex(index).GetName(): project.GetTimelineByIndex(index).GetUniqueId()
        for index in range(1, project.GetTimelineCount() + 1)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="只回報差異，不寫入")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    locator = ResolveProjectLocator(
        episode_id=config["episode_id"],
        database=ResolveDatabaseIdentity(
            db_type=config["database"]["db_type"],
            db_name=config["database"]["db_name"],
            ip_address=config["database"].get("ip_address"),
        ),
        folder=config["folder"],
        project_name=config["project_name"],
    )
    drift: list[str] = []

    expected_project_uid = _synthetic_project_uid(locator)
    if config.get("project_uid") != expected_project_uid:
        drift.append(f"project_uid: {config.get('project_uid')} -> {expected_project_uid}")
        config["project_uid"] = expected_project_uid

    live = _live_timelines(config["project_name"])
    for cut in config["cuts"]:
        name = cut["timeline_name"]
        uid = live.get(name)
        if uid is None:
            raise SystemExit(f"專案裡找不到 timeline {name!r}（{cut['cut_id']}）")
        if cut["timeline_uid"] != uid:
            drift.append(f"{cut['cut_id']} timeline_uid: {cut['timeline_uid']} -> {uid}")
            cut["timeline_uid"] = uid

    if not drift:
        print("綁定檔與現在的專案一致")
        return 0
    for line in drift:
        print(line)
    if args.check:
        print("(--check：沒有寫入)")
        return 1
    payload = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    args.config.write_text(payload, encoding="utf-8")
    print(f"已更新 {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
