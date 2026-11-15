"""Load the visual category list (`categories.yaml`, the single source of truth)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CATEGORIES_PATH = Path(__file__).with_name("categories.yaml")

KINDS = frozenset({"overlay", "insert"})
LAYOUTS = frozenset({"full", "overlay", "either", "none"})
READINESS = frozenset({"ready", "partial", "unverified", "user", "future", "none"})
FIELDS = frozenset({"description", "dp_instruction", "onscreen_text", "source"})


@dataclass(frozen=True)
class Category:
    id: str
    number: int | None
    name: str
    when: str
    dp: str
    kind: str
    layout: str
    required: tuple[str, ...]
    readiness: str
    auto: bool
    density: bool
    covers: bool

    @property
    def label(self) -> str:
        return f"{self.number}. {self.name}" if self.number is not None else self.name

    @property
    def fixed_layout(self) -> str | None:
        """The layout every row of this category must use, or None if Director chooses."""
        return None if self.layout == "either" else self.layout


class CategoryListError(ValueError):
    """`categories.yaml` is malformed."""


def parse_categories(payload: object) -> dict[str, Category]:
    if not isinstance(payload, dict) or not isinstance(payload.get("categories"), list):
        raise CategoryListError("categories.yaml must have a top-level `categories` list")
    result: dict[str, Category] = {}
    for raw in payload["categories"]:
        if not isinstance(raw, dict):
            raise CategoryListError(f"category entry must be a mapping: {raw!r}")
        try:
            category = Category(
                id=str(raw["id"]),
                number=int(raw["number"]) if raw.get("number") is not None else None,
                name=str(raw["name"]),
                when=str(raw.get("when", "")),
                dp=str(raw.get("dp", "")),
                kind=str(raw["kind"]),
                layout=str(raw["layout"]),
                required=tuple(str(field) for field in raw.get("required") or ()),
                readiness=str(raw["readiness"]),
                auto=bool(raw.get("auto", False)),
                density=bool(raw.get("density", True)),
                covers=bool(raw.get("covers", True)),
            )
        except KeyError as exc:
            raise CategoryListError(f"category {raw.get('id')!r} is missing {exc}") from exc
        if category.id in result:
            raise CategoryListError(f"duplicate category id: {category.id}")
        if category.kind not in KINDS:
            raise CategoryListError(f"{category.id}: unknown kind {category.kind!r}")
        if category.layout not in LAYOUTS:
            raise CategoryListError(f"{category.id}: unknown layout {category.layout!r}")
        if category.readiness not in READINESS:
            raise CategoryListError(f"{category.id}: unknown readiness {category.readiness!r}")
        unknown = set(category.required) - FIELDS
        if unknown:
            raise CategoryListError(f"{category.id}: unknown required fields {sorted(unknown)}")
        result[category.id] = category
    return result


@lru_cache(maxsize=1)
def load_categories(path: Path = CATEGORIES_PATH) -> dict[str, Category]:
    return parse_categories(yaml.safe_load(path.read_text(encoding="utf-8")))
