"""Content-addressed export hash for foundry b-roll renders (ADR-038 §D2).

A beat's rendered mp4 lives at ``out/b_roll_<sha256[:16]>.mp4`` where the hash
is computed from inputs that fully determine the visual output:

1. ``EXPORT_VERSION`` constant (global cache-flush lever)
2. Sorted minimal beat fields (``broll_decision`` + ``layout`` +
   ``broll.component`` + ``broll.params`` + ``broll.render_target``)
3. SHA-256[:8] of the referenced layout YAML file content
4. SHA-256[:8] of the referenced composition HTML content
5. SHA-256[:8] of ``guardrails.yaml`` (covers planner-side invariants that
   bleed into render output via component params)

Panel review (2026-05-28 Codex+Gemini) flagged that minimal beat fields alone
are insufficient — editing a layout YAML's font-size or a composition HTML's
DOM keeps the beat dict identical and silently serves a stale cached mp4.
Layout + composition content digests close that hole.

The hash is deterministic and order-independent: dicts are serialised with
``sort_keys=True`` so callers can build the input dict in any order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from shared.video_line_versions import EXPORT_VERSION

# repo_root/agents/brook/script_video/export_hash.py → repo_root
_REPO_ROOT = Path(__file__).resolve().parents[3]

_SCRIPT_VIDEO_DIR = _REPO_ROOT / "agents" / "brook" / "script_video"
DEFAULT_LAYOUTS_DIR = _SCRIPT_VIDEO_DIR / "layouts"
DEFAULT_COMPOSITIONS_DIR = _REPO_ROOT / "video" / "compositions"
DEFAULT_GUARDRAILS_PATH = _SCRIPT_VIDEO_DIR / "guardrails.yaml"

# Beat fields that participate in the hash. Order in this tuple is irrelevant
# because we sort_keys=True when serialising; the tuple just documents intent.
_BEAT_HASH_FIELDS: tuple[str, ...] = (
    "broll_decision",
    "layout",
)
_BROLL_HASH_FIELDS: tuple[str, ...] = (
    "render_target",
    "component",
    "params",
)


@dataclass(frozen=True)
class HashContext:
    """Filesystem context resolving layout / composition / guardrails digests.

    Constructed once per pipeline invocation and reused across beats. Reading
    layout + composition files is cached implicitly via the dataclass — callers
    should rebuild the context if files change on disk during a run (rare).

    Args:
        layouts_dir: directory holding ``<layout>.yaml`` files.
        compositions_dir: directory holding ``<component>/index.html`` files.
        guardrails_path: path to foundry ``guardrails.yaml``.
    """

    layouts_dir: Path = DEFAULT_LAYOUTS_DIR
    compositions_dir: Path = DEFAULT_COMPOSITIONS_DIR
    guardrails_path: Path = DEFAULT_GUARDRAILS_PATH

    def layout_digest(self, layout_name: str) -> str:
        """Return SHA-256[:8] hex of the layout YAML file content.

        Missing layout files are not silently treated as empty — they raise so
        the operator notices a misconfigured storyboard before a stale render
        ships.
        """
        path = self.layouts_dir / f"{layout_name}.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"layout file not found for hash input: {path} (layout name: {layout_name!r})"
            )
        return _sha256_hex(path.read_bytes())[:8]

    def composition_digest(self, component: str) -> str:
        """Return SHA-256[:8] hex of the composition's ``index.html``.

        Missing composition dirs raise — same rationale as layout_digest.
        """
        path = self.compositions_dir / component / "index.html"
        if not path.exists():
            raise FileNotFoundError(
                f"composition html not found for hash input: {path} (component: {component!r})"
            )
        return _sha256_hex(path.read_bytes())[:8]

    def guardrails_digest(self) -> str:
        """Return SHA-256[:8] hex of guardrails.yaml.

        Guardrails missing is non-fatal (returns ``''``) because the file is
        optional in early-stage fixtures; a present-but-malformed file is the
        caller's problem.
        """
        if not self.guardrails_path.exists():
            return ""
        return _sha256_hex(self.guardrails_path.read_bytes())[:8]


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _select_beat_fields(beat: dict) -> dict:
    """Project a beat dict down to the subset that affects render output."""
    selected: dict = {k: beat.get(k) for k in _BEAT_HASH_FIELDS}
    broll = beat.get("broll") or {}
    selected["broll"] = {k: broll.get(k) for k in _BROLL_HASH_FIELDS}
    return selected


def compute_beat_hash(beat: dict, ctx: HashContext | None = None) -> str:
    """Compute the content-addressed hash for a beat's rendered b-roll.

    Returns a 16-char lowercase hex string suitable for embedding in a filename
    (``b_roll_<hash>.mp4``). Two beats with identical render-affecting inputs
    return identical hashes regardless of dict-key ordering.

    Args:
        beat: beat dict (post-``Beat.model_dump()`` shape; broll may be None
            for aroll-only beats, in which case the hash still varies with the
            beat's ``broll_decision`` and ``layout``).
        ctx: HashContext for layout/composition/guardrails file lookup. Default
            uses the repo-root layouts/ + video/compositions/ + guardrails.yaml.

    Raises:
        FileNotFoundError: layout or composition file missing — surfaces the
            misconfiguration loudly rather than silently hashing an empty
            digest.
    """
    ctx = ctx or HashContext()

    layout = beat.get("layout")
    if not layout:
        raise ValueError(f"beat {beat.get('beat_id')} missing 'layout' for hash")

    layout_digest = ctx.layout_digest(layout)

    broll = beat.get("broll") or {}
    component = broll.get("component")
    composition_digest = ctx.composition_digest(component) if component else ""

    guardrails_digest = ctx.guardrails_digest()

    payload = {
        "export_version": EXPORT_VERSION,
        "beat": _select_beat_fields(beat),
        "layout_digest": layout_digest,
        "composition_digest": composition_digest,
        "guardrails_digest": guardrails_digest,
    }
    serialised = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return _sha256_hex(serialised.encode("utf-8"))[:16]
