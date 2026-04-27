"""Pre-publish compliance scan for Taiwan health content regulations (ADR-005b §10).

Two ways to call:
- `scan(draft) -> PublishComplianceGateV1` — defense-in-depth gate that
  Brook compose runs before enqueue and Usopp publisher runs again before
  WP publish. Operates on `DraftV1` (title + AST text + image alt etc.).
- `scan_text(text) -> PublishComplianceGateV1` — same gate, plain string in.
  Used by callers that don't have a full DraftV1 (Brook compose AST
  flatten, SEO audit on rendered HTML→text).
- `scan_draft_compliance(text) -> DraftComplianceV1` — compose-time
  snapshot combining the publish gate with disclaimer detection. Filled
  into `DraftV1.compliance` so Bridge HITL UI can show "did Brook avoid
  therapeutic claims, did Brook add a disclaimer".

Returns flag truthiness; if any flag is True the publisher must reopen
the approval queue row for explicit HITL ack rather than auto-publish.
"""

from shared.compliance.disclaimer import has_disclaimer
from shared.compliance.medical_claim_vocab import scan, scan_text
from shared.schemas.publishing import DraftComplianceV1


def scan_draft_compliance(text: str) -> DraftComplianceV1:
    """Compose-time snapshot — combines publish-gate vocab with disclaimer detection."""
    gate = scan_text(text)
    return DraftComplianceV1(
        claims_no_therapeutic_effect=not gate.medical_claim,
        has_disclaimer=has_disclaimer(text),
        detected_blacklist_hits=list(gate.matched_terms),
    )


__all__ = ["scan", "scan_text", "scan_draft_compliance", "has_disclaimer"]
