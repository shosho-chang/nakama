"""Property-based FSM test for shared/approval_queue.py (Phase 6 Slice 2).

Hypothesis ``RuleBasedStateMachine`` random-walks legal edges of
``ALLOWED_TRANSITIONS``. Invariants verified at every step:

- (a) status ∈ ``ALL_STATUSES`` (8-element enum)
- (b) terminal sticky — once ``published`` / ``rejected`` lands, downstream history
      contains only ``archived``
- (c) claimed reachability — any history with ``claimed`` traces back through
      ``approved`` (the only edge that produces it)
- (d) DB CHECK list ≡ ``ALL_STATUSES`` — runtime double-check on top of the
      module-import-time ``assert``

Bonus rule: any (from, to) outside ``ALLOWED_TRANSITIONS`` must raise
``IllegalStatusTransitionError`` — confirms the gate isn't accidentally lenient.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from shared import approval_queue, gutenberg_builder
from shared.approval_queue import (
    ALL_STATUSES,
    ALLOWED_TRANSITIONS,
    IllegalStatusTransitionError,
)
from shared.schemas.approval import PublishWpPostV1
from shared.schemas.publishing import (
    BlockNodeV1,
    DraftComplianceV1,
    DraftV1,
    PublishComplianceGateV1,
)

_DB_CHECK_STATUSES = frozenset(
    {
        "pending",
        "in_review",
        "approved",
        "rejected",
        "claimed",
        "published",
        "failed",
        "archived",
    }
)


def _make_payload() -> PublishWpPostV1:
    ast = [BlockNodeV1(block_type="paragraph", content="property test body")]
    draft = DraftV1(
        draft_id="draft_20260422T223000_a1b2c3",
        created_at=datetime.now(timezone.utc),
        agent="brook",
        operation_id="op_a1b2c3d4",
        title="Property test article",
        slug_candidates=["property-test"],
        content=gutenberg_builder.build(ast),
        excerpt="An excerpt of at least twenty characters present here.",
        primary_category="blog",
        focus_keyword="property-test",
        meta_description=(
            "A meta description that is at least fifty chars long to pass validator."
        ),
        compliance=DraftComplianceV1(
            schema_version=1,
            claims_no_therapeutic_effect=True,
            has_disclaimer=False,
        ),
        style_profile_id="blog@0.1.0",
    )
    return PublishWpPostV1(
        action_type="publish_post",
        target_site="wp_shosho",
        draft=draft,
        compliance_flags=PublishComplianceGateV1(medical_claim=False),
        reviewer_compliance_ack=False,
    )


class ApprovalQueueFSM(RuleBasedStateMachine):
    """Drive a single draft through random legal transitions; check invariants."""

    @initialize()
    def setup(self):
        self.draft_id = approval_queue.enqueue(
            source_agent="brook",
            payload_model=_make_payload(),
            operation_id="op_a1b2c3d4",
            initial_status="pending",
        )
        self.history: list[str] = ["pending"]

    def _current(self) -> str:
        row = approval_queue.get_by_id(self.draft_id)
        assert row is not None, f"draft {self.draft_id} vanished mid-walk"
        return row["status"]

    @rule(data=st.data())
    def make_legal_transition(self, data):
        cur = self._current()
        legal = ALLOWED_TRANSITIONS.get(cur, set())
        if not legal:
            return
        target = data.draw(st.sampled_from(sorted(legal)))
        kwargs: dict = {}
        if target == "failed":
            kwargs["error_log"] = "property-test induced failure"
        if target == "published":
            kwargs["execution_result"] = {"post_id": 1, "permalink": "/x"}
        if cur == "failed" and target == "pending":
            kwargs["clear_failure"] = True
        approval_queue.transition(
            draft_id=self.draft_id,
            from_status=cur,
            to_status=target,
            actor="property-test",
            **kwargs,
        )
        self.history.append(target)

    @rule(data=st.data())
    def illegal_transition_raises(self, data):
        cur = self._current()
        legal = ALLOWED_TRANSITIONS.get(cur, set())
        illegal = ALL_STATUSES - legal - {cur}
        if not illegal:
            return
        target = data.draw(st.sampled_from(sorted(illegal)))
        with pytest.raises(IllegalStatusTransitionError):
            approval_queue.transition(
                draft_id=self.draft_id,
                from_status=cur,
                to_status=target,
                actor="property-test",
            )

    @invariant()
    def status_in_enum(self):
        assert self._current() in ALL_STATUSES

    @invariant()
    def db_check_matches_allowed(self):
        assert ALL_STATUSES == _DB_CHECK_STATUSES

    @invariant()
    def archived_is_sink(self):
        if self._current() == "archived":
            assert ALLOWED_TRANSITIONS["archived"] == set()

    @invariant()
    def published_only_archives(self):
        if "published" in self.history:
            i = self.history.index("published")
            tail = self.history[i + 1 :]
            assert all(s == "archived" for s in tail), (
                f"published→{tail!r} regression in history {self.history!r}"
            )

    @invariant()
    def rejected_only_archives(self):
        if "rejected" in self.history:
            i = self.history.index("rejected")
            tail = self.history[i + 1 :]
            assert all(s == "archived" for s in tail), (
                f"rejected→{tail!r} regression in history {self.history!r}"
            )

    @invariant()
    def claimed_traces_through_approved(self):
        if "claimed" in self.history:
            idx = self.history.index("claimed")
            assert "approved" in self.history[:idx], (
                f"claimed at idx {idx} but no approved before it: {self.history!r}"
            )


ApprovalQueueFSM.TestCase.settings = settings(
    max_examples=100,
    stateful_step_count=20,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

TestApprovalQueueFSM = ApprovalQueueFSM.TestCase
