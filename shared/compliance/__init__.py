"""Pre-publish compliance scan for Taiwan health content regulations (ADR-005b §10).

Usopp runs `compliance.scan(draft)` right before WP publish as defense-in-depth.
Brook compose already runs a first scan; this module is the same pipeline so
Brook can also import it for consistency.

Returns `PublishComplianceGateV1`; if any flag is True the publisher must
reopen the approval queue row for explicit HITL ack rather than auto-publish.
"""

from shared.compliance.medical_claim_vocab import scan

__all__ = ["scan"]
