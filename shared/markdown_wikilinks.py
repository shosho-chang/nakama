"""WikilinkResolver — maps `[[target]]` to an internal URL or None (broken).

Bridge surfaces share one resolver instance per request scope; each surface
registers prefix routes (e.g. `pubmed-` → `/bridge/source/`) or arbitrary
callables. The resolver is itself callable so it can be passed directly to
`shared.markdown.render_markdown(wikilink_resolver=...)`.
"""

from __future__ import annotations

from typing import Callable, List, Optional

Resolver = Callable[[str], Optional[str]]


class WikilinkResolver:
    def __init__(self) -> None:
        self._resolvers: List[Resolver] = []

    def register(self, resolver: Resolver) -> None:
        self._resolvers.append(resolver)

    def register_prefix(self, prefix: str, url_prefix: str) -> None:
        def _resolver(target: str) -> Optional[str]:
            return url_prefix + target if target.startswith(prefix) else None

        self._resolvers.append(_resolver)

    def resolve(self, target: str) -> Optional[str]:
        for r in self._resolvers:
            url = r(target)
            if url is not None:
                return url
        return None

    __call__ = resolve
