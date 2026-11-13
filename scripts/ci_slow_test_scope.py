"""決定 PR CI 要不要跑 ``@pytest.mark.slow`` 測試。

slow 測試是少數幾個「天生就重」的整合測試檔（真的 GenerationStore、每次 load 都
重驗所有 artifact hash），約佔全套測試時間一半。PR 上只有在改動可能影響它們時才跑；
push 到 main 永遠全跑（不經過這支 script）。

「可能影響」= 改到 slow 測試檔本身，或它們 **傳遞 import 到的任何 repo 內 .py**
（用 AST 靜態解析，連函式內的 lazy import 都算），或這些模組所在 package 底下的
非 .py 資料檔，或全域設定（pyproject / requirements / conftest / CI 本身）。
import 範圍每次 CI 現算，不用手動維護清單。

用法（CI）::

    git diff --name-only HEAD^1 HEAD > changed.txt
    python scripts/ci_slow_test_scope.py changed.txt >> "$GITHUB_OUTPUT"

輸出一行 ``pytest_args=...``：要跑 slow 時為空字串，否則為 ``-m "not slow"``。
判斷理由印到 stderr（CI log 看得到）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TOP_PACKAGES = ("agents", "gateway", "scripts", "shared", "tests", "thousand_sunny")
SLOW_MARKER = "pytest.mark.slow"
# 改到這些就一定全跑：依賴、pytest/coverage 設定、CI 本身、本 script。
ALWAYS_FULL_FILES = {
    "pyproject.toml",
    ".github/workflows/ci.yml",
    "scripts/ci_slow_test_scope.py",
}
ALWAYS_FULL_PREFIXES = ("requirements",)


def slow_test_files() -> list[PurePosixPath]:
    files = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        if SLOW_MARKER in path.read_text(encoding="utf-8"):
            files.append(PurePosixPath(path.relative_to(ROOT).as_posix()))
    return files


def _candidates(module: str) -> list[PurePosixPath]:
    """一個 module 名稱可能對應的 repo 檔案（含沿途 package 的 __init__.py）。"""

    parts = module.split(".")
    if parts[0] not in LOCAL_TOP_PACKAGES:
        return []
    out = []
    for i in range(1, len(parts) + 1):
        base = PurePosixPath(*parts[:i])
        out.append(base / "__init__.py")
    base = PurePosixPath(*parts)
    out.append(base.with_name(base.name + ".py"))
    return out


def _imports(path: PurePosixPath) -> list[str]:
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # 相對 import：level 1 = 本檔所在 package，每多一級往上一層
                package = list(path.parts[:-1])
                package = package[: len(package) - (node.level - 1)]
                base = ".".join(package)
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            if not module:
                continue
            modules.append(module)
            # `from pkg import name`：name 可能是 submodule
            modules.extend(f"{module}.{alias.name}" for alias in node.names)
    return modules


def import_closure(start: list[PurePosixPath]) -> set[PurePosixPath]:
    """所有 slow 測試（含其目錄鏈上的 conftest.py）傳遞 import 的 repo 檔案。

    回傳集合也包含「import 得到但檔案不存在」的候選路徑——PR 刪掉某個被 import 的
    模組時，刪除路徑一樣會命中。
    """

    queue = list(start)
    for test_file in start:
        for parent in [test_file.parent, *test_file.parent.parents]:
            conftest = parent / "conftest.py"
            if (ROOT / conftest).is_file():
                queue.append(conftest)
    seen: set[PurePosixPath] = set()
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        if not (ROOT / path).is_file():
            continue
        for module in _imports(path):
            for candidate in _candidates(module):
                if candidate not in seen:
                    queue.append(candidate)
    return seen


def decide(changed: list[str]) -> tuple[bool, str]:
    slow_files = slow_test_files()
    if not slow_files:
        return False, "沒有任何 slow 測試"
    closure = import_closure(slow_files)
    existing_dirs = {path.parent for path in closure if (ROOT / path).is_file()}
    for raw in changed:
        name = PurePosixPath(raw.strip())
        if not raw.strip():
            continue
        text = name.as_posix()
        if text in ALWAYS_FULL_FILES or name.name == "conftest.py":
            return True, f"{text} 是全域設定"
        if text.startswith(ALWAYS_FULL_PREFIXES):
            return True, f"{text} 是依賴清單"
        if name in closure:
            return True, f"{text} 在 slow 測試的 import 範圍內"
        if name.suffix != ".py":
            # package 底下的資料檔（json / txt / yaml …）：跟 closure 內模組同一個
            # package（或其子目錄），且該 package 至少兩層深（避免 shared/、tests/
            # 這種頂層目錄一律命中）。
            for directory in existing_dirs:
                if len(directory.parts) >= 2 and (
                    name.parent == directory or directory in name.parents
                ):
                    return True, f"{text} 是 {directory.as_posix()} 底下的資料檔"
    return False, f"改動都不在 {len(slow_files)} 個 slow 測試檔的 import 範圍內"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: ci_slow_test_scope.py <changed-files.txt>", file=sys.stderr)
        return 2
    sys.stderr.reconfigure(encoding="utf-8")  # Windows console 預設不是 UTF-8
    changed = Path(argv[1]).read_text(encoding="utf-8").splitlines()
    run_slow, reason = decide(changed)
    print(f"slow tests: {'RUN' if run_slow else 'SKIP'} — {reason}", file=sys.stderr)
    print("pytest_args=" + ("" if run_slow else '-m "not slow"'))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
