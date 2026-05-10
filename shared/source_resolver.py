"""Registry-backed ``SourceResolver`` adapter (ADR-024 Slice 10 / N518a).

Production implementation of the ``SourceResolver`` Protocol declared in
``shared.promotion_review_service`` (#516). The resolver translates an
opaque ``source_id`` string into a ``ReadingSource`` by delegating to the
``ReadingSourceRegistry`` (#509).

Hard invariants (per N518 brief §4 + W7):

- The resolver NEVER parses ``source_id`` as a filesystem path. It only
  parses the namespace prefix (``ebook:`` / ``inbox:``) — that is the
  documented #509 N3 syntax, owned by the registry, not a path operation.
- Unknown ``source_id`` returns ``None`` (NOT raise) — matches the
  Protocol contract used by ``PromotionReviewService.start_review`` which
  raises its own ``ValueError`` when resolution returns ``None``.
- Malformed ``source_id`` (no namespace prefix, unknown namespace) returns
  ``None`` rather than raising — the resolver is a best-effort translator,
  not a validator. Routes already validate ``source_id`` shape via
  base64url decoding before reaching the service layer; if a ill-formed id
  slips through we return ``None`` and the service surfaces the
  registry-miss path. Letting ``ValueError`` propagate would re-shape the
  Protocol contract (``resolve`` must return ``ReadingSource | None``).
"""

from __future__ import annotations

from shared.reading_source_registry import (
    BookKey,
    InboxKey,
    ReadingSourceRegistry,
    SourceKey,
)
from shared.schemas.reading_source import ReadingSource

_EBOOK_NAMESPACE = "ebook:"
_INBOX_NAMESPACE = "inbox:"


class RegistrySourceResolver:
    """Adapter from ``source_id`` string → ``ReadingSource`` via registry.

    Constructed with a ``ReadingSourceRegistry`` instance. The resolver does
    no IO of its own — it only computes a ``SourceKey`` from the namespace
    prefix and delegates to ``registry.resolve(key)``.

    Used by ``PromotionReviewService.start_review`` and ``state_for`` to
    turn an opaque per-route ``source_id`` into the typed value-object
    upstream services consume.
    """

    def __init__(self, registry: ReadingSourceRegistry) -> None:
        self._registry = registry

    def resolve(self, source_id: str) -> ReadingSource | None:
        """Return the ``ReadingSource`` for ``source_id`` or ``None``.

        Returns ``None`` (does NOT raise) when:
        - ``source_id`` lacks a known namespace prefix;
        - the registry's underlying lookup returns ``None`` (missing book
          row, missing inbox file, frontmatter parse failure — registry
          owns the NB1 unified failure policy and already logs).
        """
        key = _make_source_key(source_id)
        if key is None:
            return None
        return self._registry.resolve(key)


def _make_source_key(source_id: str) -> SourceKey | None:
    """Translate ``source_id`` namespace into the matching ``SourceKey``.

    Per #509 N3, ``source_id`` is a namespace-qualified opaque identity:

    - ``ebook:{book_id}``                   → ``BookKey(book_id)``
    - ``inbox:{logical_original_path}``     → ``InboxKey(relative_path)``

    The split is a single ``str.split(":", 1)`` — we are NOT parsing the
    body as a filesystem path. The registry owns whatever validation is
    needed downstream (path-traversal guards live in
    ``ReadingSourceRegistry._resolve_inbox``).

    Returns ``None`` when the namespace is missing or unrecognized.
    """
    if not source_id:
        return None
    if source_id.startswith(_EBOOK_NAMESPACE):
        book_id = source_id[len(_EBOOK_NAMESPACE) :]
        if not book_id:
            return None
        return BookKey(book_id=book_id)
    if source_id.startswith(_INBOX_NAMESPACE):
        relative_path = source_id[len(_INBOX_NAMESPACE) :]
        if not relative_path:
            return None
        return InboxKey(relative_path=relative_path)
    return None
