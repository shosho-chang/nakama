"""Loader for ``config/target-keywords.yaml`` — read-only helper.

Wraps ``shared.schemas.seo.TargetKeywordListV1`` so anyone reading the file
goes through one validated entry-point. Writers (Zoro / Usopp / 修修 CLI)
own their own write paths under filelock per ADR-008 §6 — this module is
deliberately read-only.

Used by:
    - ``thousand_sunny.routers.bridge`` (SEO 中控台 §2 target keyword list)
    - future bridge / agent code that needs the canonical list

The Franky GSC daily cron has its own ``agents.franky.jobs.gsc_daily.load_keywords``
that pre-dated this module; it stays where it is to avoid cross-package dep
churn (cron runs without bridge pkgs loaded).  Both helpers are equivalent
in semantics and validate against the same Pydantic schema.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from shared.schemas.seo import TargetKeywordListV1

logger = logging.getLogger("nakama.target_keywords")


def default_path() -> Path:
    """Repo-rooted ``config/target-keywords.yaml``.

    Resolves relative to this module's location so the bridge process can
    boot from any cwd (uvicorn / gunicorn / pytest / interactive).
    """
    return Path(__file__).resolve().parent.parent / "config" / "target-keywords.yaml"


def load_target_keywords(path: Optional[Path] = None) -> Optional[TargetKeywordListV1]:
    """Read + validate ``config/target-keywords.yaml``.

    Returns ``None`` when the file is missing — callers (bridge UI) render
    an empty-state in that case rather than crash. Truly malformed YAML or
    schema-invalid contents raise ``yaml.YAMLError`` /
    ``pydantic.ValidationError``: those are config bugs the operator must
    see, not silent UI fallbacks.

    Empty contents (file exists but is empty / has only comments) raise
    a ``pydantic.ValidationError`` because the schema requires
    ``updated_at``; the cron has the same surface for the same reason
    (``agents.franky.jobs.gsc_daily.load_keywords``).
    """
    p = path or default_path()
    if not p.is_file():
        logger.info("target_keywords_yaml_missing path=%s", p)
        return None
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return TargetKeywordListV1.model_validate(raw)


__all__ = ["default_path", "load_target_keywords"]
