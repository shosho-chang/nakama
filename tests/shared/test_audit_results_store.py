"""Tests for `shared/audit_results_store.py` — issue #232 acceptance.

Coverage:

- `insert_run` round-trips via `get_by_id` (typed suggestions).
- Multiple audits per (target_site, wp_post_id) → history retained;
  `latest_for_post` returns the newest by `audited_at`.
- `latest_for_url` (used by slice 5+ external audits).
- `update_suggestion` flips status / writes edited_value / sets reviewed_at.
- `update_suggestion` raises on missing audit / missing rule / bad inputs.
- `mark_exported` sets review_status='exported' + approval_queue_id.
- Naive-datetime guard on `insert_run`.

The tests use `tests/conftest.py::isolated_db` (autouse) so each test gets
its own SQLite tmpfile.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared import audit_results_store
from shared.schemas.seo_audit_review import AuditSuggestionV1

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _make_suggestion(
    rule_id: str = "M1",
    severity: str = "fail",
    title: str = "title 太短",
) -> AuditSuggestionV1:
    return AuditSuggestionV1(
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        title=title,
        current_value="actual value",
        suggested_value="expected value",
        rationale="why it matters",
    )


def _insert(
    *,
    target_site: str | None = "wp_shosho",
    wp_post_id: int | None = 42,
    url: str = "https://shosho.tw/example",
    grade: str = "B+",
    audited_at: datetime | None = None,
    suggestions: list[AuditSuggestionV1] | None = None,
) -> int:
    return audit_results_store.insert_run(
        url=url,
        target_site=target_site,
        wp_post_id=wp_post_id,
        focus_keyword="深層睡眠",
        audited_at=audited_at or _now_utc(),
        overall_grade=grade,  # type: ignore[arg-type]
        pass_count=12,
        warn_count=3,
        fail_count=1,
        skip_count=0,
        suggestions=suggestions or [_make_suggestion()],
        raw_markdown="# audit\n",
    )


# ---------------------------------------------------------------------------
# insert_run / get_by_id
# ---------------------------------------------------------------------------


class TestInsertGet:
    def test_insert_run_returns_id_and_get_round_trips(self):
        audit_id = _insert()
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        assert row["id"] == audit_id
        assert row["url"] == "https://shosho.tw/example"
        assert row["target_site"] == "wp_shosho"
        assert row["wp_post_id"] == 42
        assert row["overall_grade"] == "B+"
        assert row["pass_count"] == 12
        assert row["warn_count"] == 3
        assert row["fail_count"] == 1
        assert row["skip_count"] == 0
        assert row["review_status"] == "fresh"
        assert row["approval_queue_id"] is None
        assert len(row["suggestions"]) == 1
        s = row["suggestions"][0]
        assert isinstance(s, AuditSuggestionV1)
        assert s.rule_id == "M1"
        assert s.severity == "fail"
        assert s.status == "pending"

    def test_get_by_id_returns_none_for_missing(self):
        assert audit_results_store.get_by_id(999_999) is None

    def test_insert_rejects_naive_datetime(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            audit_results_store.insert_run(
                url="https://shosho.tw/x",
                target_site="wp_shosho",
                wp_post_id=1,
                focus_keyword="",
                audited_at=datetime(2026, 4, 29, 0, 0, 0),  # naive!
                overall_grade="A",
                pass_count=1,
                warn_count=0,
                fail_count=0,
                skip_count=0,
                suggestions=[],
                raw_markdown="",
            )

    def test_external_audit_with_null_target_site_and_wp_post_id(self):
        """Non-WP audits store target_site=None / wp_post_id=None."""
        audit_id = audit_results_store.insert_run(
            url="https://example.com/article",
            target_site=None,
            wp_post_id=None,
            focus_keyword="external",
            audited_at=_now_utc(),
            overall_grade="C",
            pass_count=5,
            warn_count=2,
            fail_count=2,
            skip_count=1,
            suggestions=[],
            raw_markdown="",
        )
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        assert row["target_site"] is None
        assert row["wp_post_id"] is None


# ---------------------------------------------------------------------------
# latest_for_post — history
# ---------------------------------------------------------------------------


class TestLatestForPost:
    def test_returns_none_when_never_audited(self):
        assert audit_results_store.latest_for_post("wp_shosho", 999) is None

    def test_returns_newest_when_multiple_audits(self):
        """Multiple audits per post are stored as history; latest wins."""
        old_t = _now_utc() - timedelta(days=2)
        new_t = _now_utc() - timedelta(minutes=5)
        old_id = _insert(audited_at=old_t, grade="C")
        new_id = _insert(audited_at=new_t, grade="A")

        latest = audit_results_store.latest_for_post("wp_shosho", 42)
        assert latest is not None
        assert latest["id"] == new_id
        assert latest["overall_grade"] == "A"
        # And the older one is still there as history (sanity).
        assert audit_results_store.get_by_id(old_id) is not None

    def test_only_returns_matching_post(self):
        _insert(target_site="wp_shosho", wp_post_id=42, grade="A")
        _insert(target_site="wp_shosho", wp_post_id=43, grade="C")
        a = audit_results_store.latest_for_post("wp_shosho", 42)
        b = audit_results_store.latest_for_post("wp_shosho", 43)
        assert a is not None and a["overall_grade"] == "A"
        assert b is not None and b["overall_grade"] == "C"

    def test_isolated_per_target_site(self):
        _insert(target_site="wp_shosho", wp_post_id=42, grade="A")
        _insert(target_site="wp_fleet", wp_post_id=42, grade="F")
        a = audit_results_store.latest_for_post("wp_shosho", 42)
        b = audit_results_store.latest_for_post("wp_fleet", 42)
        assert a is not None and a["overall_grade"] == "A"
        assert b is not None and b["overall_grade"] == "F"


class TestLatestForUrl:
    def test_returns_newest_for_external_url(self):
        old_t = _now_utc() - timedelta(days=1)
        new_t = _now_utc()
        audit_results_store.insert_run(
            url="https://example.com/x",
            target_site=None,
            wp_post_id=None,
            focus_keyword="",
            audited_at=old_t,
            overall_grade="C",
            pass_count=0,
            warn_count=0,
            fail_count=0,
            skip_count=0,
            suggestions=[],
            raw_markdown="",
        )
        new_id = audit_results_store.insert_run(
            url="https://example.com/x",
            target_site=None,
            wp_post_id=None,
            focus_keyword="",
            audited_at=new_t,
            overall_grade="A",
            pass_count=0,
            warn_count=0,
            fail_count=0,
            skip_count=0,
            suggestions=[],
            raw_markdown="",
        )
        latest = audit_results_store.latest_for_url("https://example.com/x")
        assert latest is not None
        assert latest["id"] == new_id

    def test_returns_none_for_unknown_url(self):
        assert audit_results_store.latest_for_url("https://nope.test/") is None


# ---------------------------------------------------------------------------
# update_suggestion
# ---------------------------------------------------------------------------


class TestUpdateSuggestion:
    def test_approve_flips_status(self):
        audit_id = _insert(suggestions=[_make_suggestion("M1"), _make_suggestion("H1", "warn")])
        audit_results_store.update_suggestion(
            audit_id=audit_id,
            rule_id="M1",
            status="approved",
        )
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        m1 = next(s for s in row["suggestions"] if s.rule_id == "M1")
        h1 = next(s for s in row["suggestions"] if s.rule_id == "H1")
        assert m1.status == "approved"
        assert m1.reviewed_at is not None
        assert m1.edited_value is None
        # The other suggestion stays untouched.
        assert h1.status == "pending"
        assert h1.reviewed_at is None
        # First touch flips review_status fresh → in_review.
        assert row["review_status"] == "in_review"

    def test_edit_writes_edited_value(self):
        audit_id = _insert()
        audit_results_store.update_suggestion(
            audit_id=audit_id,
            rule_id="M1",
            status="edited",
            edited_value="my own version",
        )
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        s = row["suggestions"][0]
        assert s.status == "edited"
        assert s.edited_value == "my own version"

    def test_edit_without_value_raises(self):
        audit_id = _insert()
        with pytest.raises(ValueError, match="edited_value is required"):
            audit_results_store.update_suggestion(
                audit_id=audit_id,
                rule_id="M1",
                status="edited",
                edited_value=None,
            )

    def test_missing_audit_raises(self):
        with pytest.raises(audit_results_store.AuditNotFoundError):
            audit_results_store.update_suggestion(audit_id=987_654, rule_id="M1", status="approved")

    def test_missing_rule_raises(self):
        audit_id = _insert(suggestions=[_make_suggestion("M1")])
        with pytest.raises(audit_results_store.SuggestionNotFoundError):
            audit_results_store.update_suggestion(
                audit_id=audit_id, rule_id="NOPE", status="approved"
            )

    def test_naive_reviewed_at_raises(self):
        audit_id = _insert()
        with pytest.raises(ValueError, match="timezone-aware"):
            audit_results_store.update_suggestion(
                audit_id=audit_id,
                rule_id="M1",
                status="approved",
                reviewed_at=datetime(2026, 4, 29, 0, 0, 0),
            )

    def test_in_review_status_persists_across_subsequent_updates(self):
        audit_id = _insert(suggestions=[_make_suggestion("M1"), _make_suggestion("H1", "warn")])
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="M1", status="approved")
        # second update — review_status was 'in_review', should NOT regress.
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="H1", status="rejected")
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        assert row["review_status"] == "in_review"


# ---------------------------------------------------------------------------
# mark_exported
# ---------------------------------------------------------------------------


class TestMarkExported:
    def test_sets_status_and_queue_id(self):
        audit_id = _insert()
        audit_results_store.mark_exported(audit_id, queue_id=7)
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        assert row["review_status"] == "exported"
        assert row["approval_queue_id"] == 7

    def test_missing_audit_raises(self):
        with pytest.raises(audit_results_store.AuditNotFoundError):
            audit_results_store.mark_exported(987_654, queue_id=1)

    def test_idempotent_overwrite(self):
        audit_id = _insert()
        audit_results_store.mark_exported(audit_id, queue_id=1)
        audit_results_store.mark_exported(audit_id, queue_id=2)
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        assert row["review_status"] == "exported"
        assert row["approval_queue_id"] == 2
