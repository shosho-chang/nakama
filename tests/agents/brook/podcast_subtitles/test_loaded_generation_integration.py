"""Integration and static guards for the named loaded-Generation boundary."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import get_type_hints

from agents.brook.podcast_subtitles.loaded_generation import LoadedGenerationState
from agents.brook.podcast_subtitles.module import PodcastSubtitleV2

ROOT = Path(__file__).resolve().parents[4]
PRODUCTION_FILES = (
    ROOT / "agents" / "brook" / "podcast_subtitles" / "module.py",
    ROOT / "agents" / "brook" / "podcast_subtitles" / "facade.py",
    ROOT / "agents" / "brook" / "podcast_subtitles" / "benchmark.py",
)
TEST_FILES = tuple(
    sorted((ROOT / "tests" / "agents" / "brook" / "podcast_subtitles").glob("test_*.py"))
)


def _is_load_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and (
        node.func.attr in {"_load_generation", "_load_native_generation"}
    )


def _loaded_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None or not _is_load_call(value):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def test_loaders_have_one_named_return_contract() -> None:
    hints = get_type_hints(PodcastSubtitleV2._load_generation)
    native_hints = get_type_hints(PodcastSubtitleV2._load_native_generation)

    assert hints["return"] is LoadedGenerationState
    assert native_hints["return"] is LoadedGenerationState
    assert "tuple" not in inspect.getsource(PodcastSubtitleV2._load_generation).split(
        ") ->", maxsplit=1
    )[1].split(":", maxsplit=1)[0]


def test_loaded_generation_calls_have_no_positional_consumers_or_unpacking() -> None:
    violations: list[str] = []
    for path in (*PRODUCTION_FILES, *TEST_FILES):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _loaded_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and (
                _is_load_call(node.value)
                or isinstance(node.value, ast.Name)
                and node.value.id in names
            ):
                violations.append(f"{path.name}:{node.lineno}: positional subscript")
            if isinstance(node, ast.Assign) and _is_load_call(node.value) and any(
                isinstance(target, (ast.Tuple, ast.List)) for target in node.targets
            ):
                violations.append(f"{path.name}:{node.lineno}: tuple unpack")

    assert violations == []


def test_named_state_exposes_original_exact_bytes_without_positional_backdoor() -> None:
    assert "__getitem__" not in LoadedGenerationState.__dict__
    assert "__iter__" not in LoadedGenerationState.__dict__
    annotations = get_type_hints(LoadedGenerationState)
    assert set(annotations) == {
        "result",
        "storage",
        "normalization",
        "recognition",
        "references",
        "speech_coverage",
        "audit",
    }
