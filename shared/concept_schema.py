"""Concept page schema validator for ADR-020 S7.

validate_v3_concept_page(frontmatter):
  Validates that a concept page's parsed YAML frontmatter meets the v3
  schema contract.  Pages with schema_version < 3 are accepted as-is
  (backward compat — they pre-date en_source_terms).
"""

from __future__ import annotations


def validate_v3_concept_page(frontmatter: dict) -> tuple[bool, list[str]]:
    """Validate concept page frontmatter against the v3 schema.

    Args:
        frontmatter: Parsed YAML dict from the concept page.

    Returns:
        (is_valid, errors) — errors is an empty list when valid.

    Schema v3 requirements:
        - ``en_source_terms`` must be present and be a list of strings.

    Pages with ``schema_version`` < 3 (or absent) are accepted without
    checking ``en_source_terms``.
    """
    errors: list[str] = []
    schema_version = frontmatter.get("schema_version", 1)

    if (schema_version or 1) >= 3:
        if "en_source_terms" not in frontmatter:
            errors.append("en_source_terms is required for schema_version >= 3 but is missing")
        else:
            terms = frontmatter["en_source_terms"]
            if not isinstance(terms, list):
                errors.append(f"en_source_terms must be a list, got {type(terms).__name__}")
            else:
                bad = [t for t in terms if not isinstance(t, str)]
                if bad:
                    errors.append(
                        "en_source_terms items must all be strings; "
                        f"found non-string values: {bad!r}"
                    )

    return len(errors) == 0, errors
