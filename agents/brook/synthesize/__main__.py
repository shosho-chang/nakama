"""CLI shim: ``python -m agents.brook.synthesize <slug> --topic ... --keyword ...``.

Useful for manual triggering and post-deploy smoke tests on the VPS — the
Sunny route is the production path. Reads ``--keyword`` repeatedly (one per
flag) so spaces in keywords don't need shell escaping. Topic is a single
string; pass via ``--topic`` to keep it explicit.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import synthesize


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agents.brook.synthesize",
        description="Brook synthesize — multi-query 廣搜 + outline draft.",
    )
    parser.add_argument("slug", help="Project slug (e.g. creatine-cognitive)")
    parser.add_argument("--topic", required=True, help="Trad-Chinese topic sentence")
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        dest="keywords",
        help="One keyword per flag; repeat for multiple",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = synthesize(args.slug, args.topic, args.keywords)
    print(
        json.dumps(
            {
                "slug": result.slug,
                "pool_sources": len(result.evidence_pool),
                "outline_sections": len(result.outline_draft),
                "store_path": str(
                    __import__("shared.brook_synthesize_store", fromlist=["store_path"]).store_path(
                        result.slug
                    )
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
