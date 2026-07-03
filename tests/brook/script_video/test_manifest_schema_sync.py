"""Schema-sync guard: video/src/parser/types.ts ↔ agents/brook/script_video/manifest.py.

The Manifest schema is defined twice — TypeScript (authoritative, parser side)
and Pydantic (Python mirror, emitter side) — and until this test the only sync
mechanism was a header comment ("Changes here must be mirrored"). Drift used to
surface only at runtime deserialisation. This test makes drift a CI failure:

- TS side: ``video/scripts/emit-manifest-schema.mjs`` uses the TypeScript
  compiler API (semantic checker, not regex) to emit a normalized JSON
  description of every exported type alias in types.ts.
- Python side: this module builds the same normalized description from the
  Pydantic models via ``model_fields`` introspection.
- The two are compared field by field.

Comparison rules (direction-aware — TS parser is the *producer* of
manifest.json, Pydantic is the *consumer*):

1. The set of named types must match exactly, both directions.
2. Per type, the set of field names must match exactly, both directions.
   (Pydantic v2 silently ignores unknown JSON keys, so a TS-only field would
   otherwise be silent data loss.)
3. Field types must match exactly (int and float both normalize to "number"
   since TS only has ``number``).
4. Optionality: a field the producer may omit (TS ``?``) must have a default
   on the consumer side — that mismatch is drift. The reverse (Python default
   where TS always emits, e.g. the ``type`` discriminator literals) is safe
   and allowed.
5. Nullability: a field the producer may set to ``null`` must accept ``None``
   on the consumer side. The reverse (Python tolerates ``None`` where TS never
   emits it, e.g. ``Citation.author``) is safe and allowed.

Skips gracefully (same convention as test_pipeline_e2e) when node or
``video/node_modules`` are unavailable; CI installs both before pytest runs,
so drift is always red on CI.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import types
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from agents.brook.script_video import manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VIDEO_DIR = _REPO_ROOT / "video"
_EMIT_SCRIPT = _VIDEO_DIR / "scripts" / "emit-manifest-schema.mjs"

# Pydantic model name → types.ts alias name, where they differ.
_PY_TO_TS_NAME = {"ManifestCutPoint": "CutPoint"}


# ---------------------------------------------------------------------------
# TS side — run the extraction script
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ts_schema() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not available")
    if not (_VIDEO_DIR / "node_modules" / "typescript").exists():
        pytest.skip("video/ deps not installed — run `npm install --prefix video`")

    proc = subprocess.run(
        ["node", str(_EMIT_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, f"emit-manifest-schema.mjs failed:\n{proc.stderr}"
    return json.loads(proc.stdout)["types"]


# ---------------------------------------------------------------------------
# Python side — normalize the Pydantic models into the same descriptor shape
# ---------------------------------------------------------------------------


def _describe_annotation(ann: object) -> dict:
    """Normalize a field annotation into the shared descriptor JSON shape."""
    # Annotated[X, …] (pydantic Tag / Discriminator metadata) → unwrap.
    if hasattr(ann, "__metadata__"):
        return _describe_annotation(ann.__origin__)  # type: ignore[attr-defined]

    origin = typing.get_origin(ann)

    if origin in (typing.Union, types.UnionType):
        args = typing.get_args(ann)
        nullable = type(None) in args
        members = [_describe_annotation(a) for a in args if a is not type(None)]
        desc = dict(members[0]) if len(members) == 1 else {"kind": "union", "members": members}
        if nullable:
            desc["nullable"] = True
        return desc

    if origin is typing.Literal:
        values = [{"kind": "literal", "value": v} for v in typing.get_args(ann)]
        return values[0] if len(values) == 1 else {"kind": "union", "members": values}

    if origin is list:
        (item,) = typing.get_args(ann)
        return {"kind": "array", "items": _describe_annotation(item)}

    if ann is dict or origin is dict:
        return {"kind": "object"}
    if ann is str:
        return {"kind": "string"}
    if ann is bool:  # before int — bool subclasses int
        return {"kind": "boolean"}
    if ann in (int, float):  # TS only has `number`
        return {"kind": "number"}
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return {"kind": "ref", "name": _PY_TO_TS_NAME.get(ann.__name__, ann.__name__)}

    return {"kind": "unhandled", "text": repr(ann)}


def _describe_model(model: type[BaseModel]) -> dict:
    fields = {}
    for name, field in model.model_fields.items():
        fields[name] = {
            "type": _describe_annotation(field.annotation),
            "optional": not field.is_required(),
        }
    return {"kind": "object", "fields": fields}


def _py_schema() -> dict:
    """All Pydantic models defined in manifest.py, keyed by their TS alias name."""
    out = {}
    for obj in vars(manifest).values():
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            if obj.__module__ != manifest.__name__:
                continue  # imported, not part of the mirror
            out[_PY_TO_TS_NAME.get(obj.__name__, obj.__name__)] = _describe_model(obj)
    # The Scene union alias — Annotated[Union[…], Discriminator] — has no
    # runtime class; describe it directly to mirror the TS `Scene` alias.
    out["Scene"] = _describe_annotation(manifest.Scene)
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _canonical(desc: dict) -> str:
    return json.dumps(desc, sort_keys=True, ensure_ascii=False)


def _compare_desc(path: str, ts_desc: dict, py_desc: dict, drift: list[str]) -> None:
    ts_d, py_d = dict(ts_desc), dict(py_desc)
    ts_nullable = ts_d.pop("nullable", False)
    py_nullable = py_d.pop("nullable", False)
    if ts_nullable and not py_nullable:
        drift.append(f"{path}: types.ts allows null but manifest.py does not accept None")
    # py-only nullability is allowed: the consumer tolerating None the
    # producer never emits is not drift (rule 5).

    ts_kind, py_kind = ts_d.get("kind"), py_d.get("kind")
    if "unhandled" in (ts_kind, py_kind):
        drift.append(
            f"{path}: descriptor not understood by the sync test "
            f"(ts={ts_desc!r}, py={py_desc!r}) — extend the normalizers"
        )
        return
    if ts_kind != py_kind:
        drift.append(f"{path}: kind mismatch — types.ts {ts_desc!r} vs manifest.py {py_desc!r}")
        return

    if ts_kind == "literal":
        if ts_d.get("value") != py_d.get("value"):
            drift.append(
                f"{path}: literal value mismatch — "
                f"types.ts {ts_d.get('value')!r} vs manifest.py {py_d.get('value')!r}"
            )
    elif ts_kind == "ref":
        if ts_d.get("name") != py_d.get("name"):
            drift.append(
                f"{path}: ref mismatch — "
                f"types.ts {ts_d.get('name')!r} vs manifest.py {py_d.get('name')!r}"
            )
    elif ts_kind == "array":
        _compare_desc(f"{path}[]", ts_d["items"], py_d["items"], drift)
    elif ts_kind == "union":
        ts_members = sorted(ts_d["members"], key=_canonical)
        py_members = sorted(py_d["members"], key=_canonical)
        if len(ts_members) != len(py_members):
            drift.append(
                f"{path}: union arity mismatch — "
                f"types.ts {len(ts_members)} members vs manifest.py {len(py_members)}"
            )
            return
        for i, (ts_m, py_m) in enumerate(zip(ts_members, py_members)):
            _compare_desc(f"{path}|{i}", ts_m, py_m, drift)
    # string / number / boolean / object: kind equality is sufficient.


def _compare_field(path: str, ts_field: dict, py_field: dict, drift: list[str]) -> None:
    if ts_field["optional"] and not py_field["optional"]:
        drift.append(
            f"{path}: optional in types.ts (parser may omit it) "
            f"but required in manifest.py — deserialisation would raise"
        )
    # py-only default where TS always emits is allowed (rule 4).
    _compare_desc(path, ts_field["type"], py_field["type"], drift)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_type_inventory_matches(ts_schema):
    """Every exported types.ts alias has a Pydantic mirror, and vice versa."""
    py_schema = _py_schema()
    missing_in_py = sorted(set(ts_schema) - set(py_schema))
    missing_in_ts = sorted(set(py_schema) - set(ts_schema))
    assert not missing_in_py and not missing_in_ts, (
        f"schema type inventory drift — "
        f"in types.ts but not manifest.py: {missing_in_py}; "
        f"in manifest.py but not types.ts: {missing_in_ts}"
    )


def test_fields_types_and_optionality_match(ts_schema):
    """Field names, types, optionality and nullability agree on both sides."""
    py_schema = _py_schema()
    drift: list[str] = []

    for type_name in sorted(set(ts_schema) & set(py_schema)):
        ts_type, py_type = ts_schema[type_name], py_schema[type_name]

        if ts_type["kind"] != py_type["kind"]:
            drift.append(
                f"{type_name}: kind mismatch — "
                f"types.ts {ts_type['kind']} vs manifest.py {py_type['kind']}"
            )
            continue

        if ts_type["kind"] == "union":
            _compare_desc(type_name, ts_type, py_type, drift)
            continue

        ts_fields, py_fields = ts_type["fields"], py_type["fields"]
        for name in sorted(set(ts_fields) - set(py_fields)):
            drift.append(
                f"{type_name}.{name}: in types.ts but missing from manifest.py "
                f"— Pydantic would silently drop it"
            )
        for name in sorted(set(py_fields) - set(ts_fields)):
            drift.append(f"{type_name}.{name}: in manifest.py but missing from types.ts")
        for name in sorted(set(ts_fields) & set(py_fields)):
            _compare_field(f"{type_name}.{name}", ts_fields[name], py_fields[name], drift)

    assert not drift, "Manifest schema drift (types.ts ↔ manifest.py):\n- " + "\n- ".join(drift)
