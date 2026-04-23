"""Usopp WordPress publisher — main state machine (ADR-005b §1 / §2 / §4 / §10).

Consumes `DraftV1` from approval_queue (ADR-006) and drives it through the
publish state machine:

    claimed → media_ready → post_draft → seo_ready → validated
        → published → cache_purged → done

Crash recovery: `publish_jobs` row is updated after each step; re-entering
`publish(request, ...)` with the same draft_id reads the row and resumes from
the last committed state. Orphaned WP posts (crash after create_post but
before DB write) are adopted via `wp.find_by_meta("nakama_draft_id", ...)`
thanks to ADR-005b §2 two-layer idempotency.

Not in this module (Slice C will add):
    - Daemon loop (`python -m agents.usopp`)
    - /healthz WP connectivity probe
    - E2E test against Docker WP staging

Public entry:
    `Publisher(wp_client).publish(request, approval_queue_id, operation_id)`
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from agents.franky.alert_router import dispatch as dispatch_alert
from shared import compliance
from shared.approval_queue import mark_failed as approval_mark_failed
from shared.approval_queue import mark_published as approval_mark_published
from shared.litespeed_purge import purge_url
from shared.locks import advisory_lock
from shared.log import get_logger
from shared.schemas.external.seopress import SEOpressWritePayloadV1
from shared.schemas.external.wordpress import WpPostV1
from shared.schemas.franky import AlertV1
from shared.schemas.publishing import (
    DraftV1,
    PublishComplianceGateV1,
    PublishRequestV1,
    PublishResultV1,
)
from shared.seopress_writer import write_seopress
from shared.state import _get_conn
from shared.wordpress_client import WordPressClient

logger = get_logger("nakama.usopp.publisher")

_ACTION_TO_STATUS = {
    "publish": "published",
    "schedule": "scheduled",
    "draft_only": "draft_only",
}


class PublisherError(RuntimeError):
    """Base for publisher failures that should mark the job failed."""


class CategoryNotMappedError(PublisherError):
    """Draft's primary_category slug is absent from the WP category map (ADR-005b §6)."""


class ValidationMismatchError(PublisherError):
    """Fetched WP post's nakama_draft_id doesn't match the draft we published."""


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------


class Publisher:
    """Stateful publisher bound to one WordPressClient + state.db connection.

    Single instance per Usopp daemon; thread-safe only via SQLite's internal
    mutex (single-worker Phase 1 — reliability.md §2).
    """

    def __init__(
        self,
        wp_client: WordPressClient,
        *,
        conn: sqlite3.Connection | None = None,
        category_map: dict[str, int] | None = None,
        tag_map: dict[str, int] | None = None,
    ) -> None:
        self.wp = wp_client
        self.conn = conn or _get_conn()
        self._category_map = category_map
        self._tag_map = tag_map

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def publish(
        self,
        request: PublishRequestV1,
        *,
        approval_queue_id: int,
        operation_id: str,
    ) -> PublishResultV1:
        draft = request.draft
        job = self._get_or_create_job(
            draft=draft,
            approval_queue_id=approval_queue_id,
            operation_id=operation_id,
        )

        # Terminal: already done / already failed.
        if job["state"] == "done":
            return self._success_result(
                job=job,
                action=request.action,
                already=True,
            )
        if job["state"] == "failed":
            return self._failed_result(job=job, operation_id=operation_id)

        # Compliance gate (ADR-005b §10) — runs on every entry including resume.
        flags = compliance.scan(draft)
        if flags.medical_claim or flags.absolute_assertion:
            self._handle_compliance_flag(
                job=job,
                draft=draft,
                approval_queue_id=approval_queue_id,
                flags=flags,
                operation_id=operation_id,
            )
            final = self._reload_job(job["id"])
            return self._failed_result(job=final, operation_id=operation_id)

        # Drive state machine.
        try:
            adopted = self._run_state_machine(
                job=job,
                request=request,
                operation_id=operation_id,
            )
        except PublisherError as exc:
            self._mark_failed(job["id"], reason=str(exc))
            approval_mark_failed(
                draft_id=approval_queue_id,
                error_log=f"publisher: {exc}",
                actor="usopp",
            )
            final = self._reload_job(job["id"])
            return self._failed_result(job=final, operation_id=operation_id)

        # Success.
        final = self._reload_job(job["id"])
        approval_mark_published(
            draft_id=approval_queue_id,
            execution_result={
                "post_id": final["post_id"],
                "permalink": final["permalink"],
                "seo_status": final["seo_status"],
                "cache_purged": bool(final["cache_purged"]),
                "adopted": adopted,
            },
            actor="usopp",
        )
        return self._success_result(
            job=final,
            action=request.action,
            already=adopted,
        )

    # ------------------------------------------------------------------
    # State machine body
    # ------------------------------------------------------------------

    def _run_state_machine(
        self,
        *,
        job: dict[str, Any],
        request: PublishRequestV1,
        operation_id: str,
    ) -> bool:
        """Drive the FSM from current state forward to done.

        Returns True if this run adopted an orphan WP post (short-circuit
        from claimed → done via find_by_meta), False otherwise.
        """
        draft = request.draft
        job_id = job["id"]
        adopted = False

        # Stage 1: advisory-locked idempotency probe + create_post (ADR-005b §2.1)
        if self._state_of(job_id) in ("claimed", "media_ready"):
            with advisory_lock(self.conn, key=f"usopp_draft_{draft.draft_id}", timeout_s=5.0):
                current = self._state_of(job_id)
                if current == "claimed":
                    existing = self.wp.find_by_meta(
                        "nakama_draft_id",
                        draft.draft_id,
                        operation_id=operation_id,
                    )
                    if existing is not None:
                        logger.info(
                            "publish adopting orphan WP post draft_id=%s post_id=%s op=%s",
                            draft.draft_id,
                            existing.id,
                            operation_id,
                        )
                        self._advance(
                            job_id,
                            "done",
                            post_id=existing.id,
                            permalink=existing.link,
                            seo_status="skipped",
                            cache_purged=0,
                            completed_at=_iso_now(),
                        )
                        return True

                    self._advance(
                        job_id,
                        "media_ready",
                        featured_media_id=request.featured_media_id,
                    )
                    current = "media_ready"

                if current == "media_ready":
                    post = self._create_draft_post(
                        draft=draft,
                        featured_media_id=request.featured_media_id,
                        operation_id=operation_id,
                    )
                    self._advance(job_id, "post_draft", post_id=post.id, permalink=post.link)

        # Stage 2: SEO write (three-tier fallback, ADR-005b §3)
        if self._state_of(job_id) == "post_draft":
            post_id = self._reload_job(job_id)["post_id"]
            seo_status = write_seopress(
                wp_client=self.wp,
                post_id=post_id,
                payload=_seopress_payload_from_draft(draft),
                operation_id=operation_id,
            )
            self._advance(job_id, "seo_ready", seo_status=seo_status)
            if seo_status == "skipped":
                self._alert_seopress_skipped(
                    post_id=post_id,
                    draft=draft,
                    operation_id=operation_id,
                )

        # Stage 3: validate by re-fetching the post (ADR-005b §4)
        if self._state_of(job_id) == "seo_ready":
            post_id = self._reload_job(job_id)["post_id"]
            fetched = self.wp.get_post(post_id, operation_id=operation_id)
            if fetched.meta.get("nakama_draft_id") != draft.draft_id:
                raise ValidationMismatchError(
                    f"post_id={post_id} meta nakama_draft_id mismatch: "
                    f"expected={draft.draft_id!r} got={fetched.meta.get('nakama_draft_id')!r}"
                )
            self._advance(job_id, "validated")

        # Stage 4: publish / schedule / keep-draft
        if self._state_of(job_id) == "validated":
            post_id = self._reload_job(job_id)["post_id"]
            if request.action == "publish":
                self.wp.update_post(post_id, status="publish", operation_id=operation_id)
            elif request.action == "schedule":
                if request.scheduled_at is None:
                    raise PublisherError("action=schedule requires scheduled_at")
                self.wp.update_post(
                    post_id,
                    status="future",
                    date_gmt=request.scheduled_at.astimezone(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    ),
                    operation_id=operation_id,
                )
            # action=draft_only: leave at status=draft
            final_post = self.wp.get_post(post_id, operation_id=operation_id)
            self._advance(job_id, "published", permalink=final_post.link)

        # Stage 5: cache purge (non-blocking, only for action=publish live)
        if self._state_of(job_id) == "published":
            cache_purged = False
            if request.action == "publish":
                permalink = self._reload_job(job_id)["permalink"]
                if permalink:
                    cache_purged = purge_url(
                        permalink,
                        wp_client=self.wp,
                        operation_id=operation_id,
                    )
            self._advance(job_id, "cache_purged", cache_purged=1 if cache_purged else 0)

        # Stage 6: mark done
        if self._state_of(job_id) == "cache_purged":
            self._advance(job_id, "done", completed_at=_iso_now())

        return adopted

    # ------------------------------------------------------------------
    # WP post creation
    # ------------------------------------------------------------------

    def _create_draft_post(
        self,
        *,
        draft: DraftV1,
        featured_media_id: int | None,
        operation_id: str,
    ) -> WpPostV1:
        cat_map = self._get_category_map(operation_id=operation_id)
        primary_id = cat_map.get(draft.primary_category)
        if primary_id is None:
            raise CategoryNotMappedError(
                f"primary_category={draft.primary_category!r} not in WP category map "
                f"(known slugs: {sorted(cat_map.keys())[:10]}...)"
            )
        category_ids = [primary_id]
        for slug in draft.secondary_categories:
            sec_id = cat_map.get(slug)
            if sec_id is not None:
                category_ids.append(sec_id)
            else:
                logger.warning(
                    "secondary_category slug=%s not mapped — skipping (draft_id=%s op=%s)",
                    slug,
                    draft.draft_id,
                    operation_id,
                )

        tag_map = self._get_tag_map(operation_id=operation_id)
        tag_ids: list[int] = []
        for slug in draft.tags:
            tid = tag_map.get(slug)
            if tid is not None:
                tag_ids.append(tid)
            else:
                logger.warning(
                    "tag slug=%s not in WP tag whitelist — skipping (draft_id=%s op=%s)",
                    slug,
                    draft.draft_id,
                    operation_id,
                )

        return self.wp.create_post(
            title=draft.title,
            content=draft.content.raw_html,
            status="draft",
            slug=draft.slug_candidates[0],
            excerpt=draft.excerpt,
            categories=category_ids,
            tags=tag_ids,
            featured_media=featured_media_id,
            meta={"nakama_draft_id": draft.draft_id},
            operation_id=operation_id,
        )

    def _get_category_map(self, *, operation_id: str) -> dict[str, int]:
        if self._category_map is None:
            terms = self.wp.list_categories(operation_id=operation_id)
            self._category_map = {t.slug: t.id for t in terms}
        return self._category_map

    def _get_tag_map(self, *, operation_id: str) -> dict[str, int]:
        if self._tag_map is None:
            terms = self.wp.list_tags(operation_id=operation_id)
            self._tag_map = {t.slug: t.id for t in terms}
        return self._tag_map

    # ------------------------------------------------------------------
    # publish_jobs row helpers
    # ------------------------------------------------------------------

    def _get_or_create_job(
        self,
        *,
        draft: DraftV1,
        approval_queue_id: int,
        operation_id: str,
    ) -> dict[str, Any]:
        existing = self.conn.execute(
            "SELECT * FROM publish_jobs WHERE draft_id = ?",
            (draft.draft_id,),
        ).fetchone()
        if existing is not None:
            return dict(existing)

        now = _iso_now()
        cur = self.conn.execute(
            """INSERT INTO publish_jobs
               (draft_id, approval_queue_id, operation_id, state,
                state_updated_at, claimed_at, retry_count)
               VALUES (?, ?, ?, 'claimed', ?, ?, 0)""",
            (draft.draft_id, approval_queue_id, operation_id, now, now),
        )
        self.conn.commit()
        return self._reload_job(cur.lastrowid)

    def _reload_job(self, job_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM publish_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return dict(row)

    def _state_of(self, job_id: int) -> str:
        row = self.conn.execute(
            "SELECT state FROM publish_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return row["state"]

    def _advance(self, job_id: int, new_state: str, **fields: Any) -> None:
        set_parts = ["state = ?", "state_updated_at = ?"]
        params: list[Any] = [new_state, _iso_now()]
        for col, val in fields.items():
            set_parts.append(f"{col} = ?")
            params.append(val)
        params.append(job_id)
        sql = f"UPDATE publish_jobs SET {', '.join(set_parts)} WHERE id = ?"
        self.conn.execute(sql, params)
        self.conn.commit()

    def _mark_failed(self, job_id: int, *, reason: str) -> None:
        self._advance(
            job_id,
            "failed",
            failure_reason=reason,
            completed_at=_iso_now(),
        )

    # ------------------------------------------------------------------
    # Compliance handling
    # ------------------------------------------------------------------

    def _handle_compliance_flag(
        self,
        *,
        job: dict[str, Any],
        draft: DraftV1,
        approval_queue_id: int,
        flags: PublishComplianceGateV1,
        operation_id: str,
    ) -> None:
        reason = f"compliance_flag_requires_explicit_review: {flags.matched_terms}"
        logger.warning(
            "publish blocked by compliance draft_id=%s flags=%s op=%s",
            draft.draft_id,
            flags.matched_terms,
            operation_id,
        )
        self.conn.execute(
            "UPDATE publish_jobs SET compliance_flags = ? WHERE id = ?",
            (flags.model_dump_json(), job["id"]),
        )
        self.conn.commit()
        self._mark_failed(job["id"], reason=reason)
        approval_mark_failed(
            draft_id=approval_queue_id,
            error_log=reason,
            actor="usopp",
        )

    # ------------------------------------------------------------------
    # Alerting
    # ------------------------------------------------------------------

    def _alert_seopress_skipped(
        self,
        *,
        post_id: int | None,
        draft: DraftV1,
        operation_id: str,
    ) -> None:
        alert = AlertV1(
            rule_id="seopress_skipped",
            severity="critical",
            title="SEOPress 寫入失敗（Fallback B）",
            message=(
                f"draft_id={draft.draft_id} post_id={post_id} SEO meta 跳過，"
                "publish 照發但 SEO 留空。請 24h 內人工補。"
            ),
            fired_at=datetime.now(timezone.utc),
            dedup_key=f"seopress_skipped_post_{post_id}",
            operation_id=operation_id,
            context={
                "post_id": post_id if post_id is not None else 0,
                "draft_id": draft.draft_id,
                "site": self.wp._site_id,
            },
        )
        try:
            dispatch_alert(alert)
        except Exception as exc:  # pragma: no cover — alert must never abort publish
            logger.error(
                "seopress_skipped alert dispatch failed op=%s err=%s",
                operation_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _success_result(
        self,
        *,
        job: dict[str, Any],
        action: str,
        already: bool,
    ) -> PublishResultV1:
        if already:
            status = "already_published"
        else:
            status = _ACTION_TO_STATUS.get(action, "published")
        return PublishResultV1(
            status=status,  # type: ignore[arg-type]
            post_id=job["post_id"],
            permalink=job["permalink"],
            seo_status=(job["seo_status"] or "skipped"),
            cache_purged=bool(job["cache_purged"]),
            failure_reason=None,
            operation_id=job["operation_id"],
            completed_at=datetime.now(timezone.utc),
        )

    def _failed_result(
        self,
        *,
        job: dict[str, Any],
        operation_id: str,
    ) -> PublishResultV1:
        return PublishResultV1(
            status="failed",
            post_id=job["post_id"],
            permalink=job["permalink"],
            seo_status=(job["seo_status"] or "skipped"),
            cache_purged=bool(job["cache_purged"]),
            failure_reason=job["failure_reason"] or "unknown",
            operation_id=operation_id,
            completed_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _seopress_payload_from_draft(draft: DraftV1) -> SEOpressWritePayloadV1:
    # SEOPress title field caps at 70; DraftV1.title caps at 120.
    # Policy: truncate at 70 for SEO slot only — WP post.title stays untouched.
    seo_title = draft.title if len(draft.title) <= 70 else draft.title[:69] + "…"
    return SEOpressWritePayloadV1(
        title=seo_title,
        description=draft.meta_description,
        focus_keyword=draft.focus_keyword,
        canonical="",
    )
