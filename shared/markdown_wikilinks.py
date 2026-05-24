"""WikilinkResolver — maps `[[target]]` to an internal URL or None (broken).

Bridge surfaces share one resolver instance per request scope; each surface
registers prefix routes (e.g. `pubmed-` → `/bridge/source/`) or arbitrary
callables. The resolver is itself callable so it can be passed directly to
`shared.markdown.render_markdown(wikilink_resolver=...)`.
"""

from __future__ import annotations

import unicodedata
from typing import Callable, List, Optional
from urllib.parse import quote

Resolver = Callable[[str], Optional[str]]


class WikilinkResolver:
    def __init__(self) -> None:
        self._resolvers: List[Resolver] = []

    def register(self, resolver: Resolver) -> None:
        self._resolvers.append(resolver)

    def register_prefix(self, prefix: str, url_prefix: str) -> None:
        def _resolver(target: str) -> Optional[str]:
            if not target.startswith(prefix):
                return None
            return url_prefix + quote(target, safe="/")

        self._resolvers.append(_resolver)

    def resolve(self, target: str) -> Optional[str]:
        # Normalize to NFC so NFD-encoded targets (e.g. macOS Syncthing paths)
        # match resolvers registered with NFC strings.
        normalized = unicodedata.normalize("NFC", target)
        for r in self._resolvers:
            url = r(normalized)
            if url is not None:
                return url
        return None

    __call__ = resolve
