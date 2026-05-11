"""Brook synthesize server-side store (ADR-021 §4).

Reader/writer for `data/brook_synthesize/{project_slug}.json`. The store is
write-once-on-create by Brook synthesize (#459) and mutated incrementally by
the Sunny `/api/projects/{slug}/synthesize` route as the user reviews the
outline draft.

Storage layout:

    <data_root>/brook_synthesize/<slug>.json

`data_root` resolution (matches `shared.kb_hybrid_search` / `shared.doc_index`):

    1. `NAKAMA_BROOK_SYNTHESIZE_DIR` env (full path) — explicit override
    2. `NAKAMA_DATA_DIR` env (data dir, `brook_synthesize/` appended) — VPS
    3. `<repo_root>/data/brook_synthesize/` — local dev fallback

The on-disk file is the single source of truth — there is no DB row, no
cache. A per-slug `threading.Lock` protects against torn writes when the
Sunny route processes two POSTs concurrently for the same project (mirrors
`shared.annotation_store` pattern).

Public API (deep module — caller does not see JSON / Path internals):

    store_path(slug)               — Path to the on-disk file (may not exist)
    exists(slug) -> bool           — does the store exist?
    read(slug) -> BrookSynthesizeStore  — raises StoreNotFoundError when missing
    write(store)                   — full-replace write; bumps updated_at
    create(store)                  — write only when the slug does not yet exist
    append_user_action(slug, action) -> BrookSynthesizeStore
    update_outline_final(slug, sections) -> BrookSynthesizeStore

`append_user_action` and `update_outline_final` are the two mutate paths the
API exposes; both raise `StoreNotFoundError` when the slug has never been
materialised by Brook synthesize (#459) — the API surfaces this as 404, per
the ADR-021 §4 "store must be created by Brook synthesize flow" rule.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from shared.log import get_logger
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    OutlineSection,
    UserAction,
)

logger = get_logger("nakama.brook_synthesize_store")

SCHEMA_VERSION = 1

# Per-slug locks keyed by absolute path string. Same idea as
# `shared/annotation_store.py`: serialise concurrent writes within one
# process. Cross-process contention is not in scope (single Sunny worker).
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


class StoreNotFoundError(LookupError):
    """`read` / mutator could not find the store file for the given slug."""


class StoreAlreadyExistsError(FileExistsError):
    """`create` was called for a slug that already has a store on disk."""


# ── Path resolution ──────────────────────────────────────────────────────────


def _data_dir() -> Path:
    """Resolve the directory holding `{slug}.json` files.

    Resolution order matches the rest of the codebase (`kb_hybrid_search`
    et al.) so VPS and local dev share one convention. The directory is
    created lazily — caller does not need to mkdir before reading.
    """
    explicit = os.environ.get("NAKAMA_BROOK_SYNTHESIZE_DIR")
    if explicit:
        return Path(explicit)
    data_dir_env = os.environ.get("NAKAMA_DATA_DIR")
    if data_dir_env:
        return Path(data_dir_env) / "brook_synthesize"
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / "data" / "brook_synthesize"


def store_path(slug: str) -> Path:
    """Return the on-disk Path for a slug. May not exist yet."""
    if not slug or "/" in slug or "\\" in slug or slug in (".", ".."):
        # Defence-in-depth — the route layer also validates, but a bad slug
        # here would let a caller traverse outside `data/brook_synthesize/`.
        raise ValueError(f"invalid slug: {slug!r}")
    return _data_dir() / f"{slug}.json"


def exists(slug: str) -> bool:
    return store_path(slug).is_file()


# ── Locks ────────────────────────────────────────────────────────────────────


def _lock_for(slug: str) -> threading.RLock:
    """Reentrant lock — `append_user_action` / `update_outline_final` hold the
    lock while calling `write()` which also takes it. Plain `Lock` would
    deadlock the same thread."""
    key = str(store_path(slug))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


# ── Time ─────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Serialise / deserialise ──────────────────────────────────────────────────


def _serialize(store: BrookSynthesizeStore) -> str:
    return json.dumps(store.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _deserialize(raw: str, *, slug: str) -> BrookSynthesizeStore:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"brook_synthesize/{slug}.json: corrupt JSON ({exc})") from exc
    try:
        return BrookSynthesizeStore.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"brook_synthesize/{slug}.json: schema mismatch ({exc})") from exc


# ── Public API ───────────────────────────────────────────────────────────────


def read(slug: str) -> BrookSynthesizeStore:
    """Load the store for `slug`. Raises `StoreNotFoundError` when absent."""
    path = store_path(slug)
    if not path.is_file():
        raise StoreNotFoundError(f"brook_synthesize store not found for slug={slug!r}")
    raw = path.read_text(encoding="utf-8")
    return _deserialize(raw, slug=slug)


def write(store: BrookSynthesizeStore) -> BrookSynthesizeStore:
    """Full-replace write. Sets `updated_at` to now (UTC ISO)."""
    slug = store.project_slug
    path = store_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = store.model_copy(update={"updated_at": _now_iso()})
    with _lock_for(slug):
        # Atomic-ish write: write to .tmp then rename so a crash mid-write
        # does not leave a half-baked JSON on disk.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(_serialize(fresh), encoding="utf-8")
        os.replace(tmp, path)
    logger.info("brook_synthesize.write slug=%s path=%s", slug, path)
    return fresh


def create(store: BrookSynthesizeStore) -> BrookSynthesizeStore:
    """Write only if no file exists yet for `store.project_slug`.

    This is the entry point Brook synthesize (#459) will call. The API
    layer never calls this — it only mutates existing stores.
    """
    if exists(store.project_slug):
        raise StoreAlreadyExistsError(
            f"brook_synthesize store already exists for slug={store.project_slug!r}"
        )
    return write(store)


def append_user_action(slug: str, action: UserAction) -> BrookSynthesizeStore:
    """Append one `UserAction` and re-serialise. Raises `StoreNotFoundError`."""
    with _lock_for(slug):
        current = read(slug)
        next_actions = list(current.user_actions) + [action]
        updated = current.model_copy(update={"user_actions": next_actions})
        return write(updated)


def update_outline_final(slug: str, sections: list[OutlineSection]) -> BrookSynthesizeStore:
    """Replace `outline_final`. Raises `StoreNotFoundError` when missing."""
    with _lock_for(slug):
        current = read(slug)
        updated = current.model_copy(update={"outline_final": list(sections)})
        return write(updated)


__all__ = [
    "SCHEMA_VERSION",
    "StoreAlreadyExistsError",
    "StoreNotFoundError",
    "append_user_action",
    "create",
    "exists",
    "read",
    "store_path",
    "update_outline_final",
    "write",
]
