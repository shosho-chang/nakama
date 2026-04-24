"""Usopp publisher daemon — poll approval_queue → publisher.publish() (ADR-005b §1 / §4).

Single-worker Phase 1 daemon (reliability.md §2). Each cycle:

    1. claim_approved_drafts(worker_id, source_agent='brook', batch=N) — atomic
    2. for each claimed row: build PublishRequestV1 → Publisher.publish()
    3. sleep(poll_interval_s), interruptible by SIGTERM/SIGINT
    4. graceful shutdown on signal — finish current batch then exit

Resume-on-restart is handled **inside** `Publisher.publish()`: `publish_jobs`
rows carry per-step state, and `find_by_meta(nakama_draft_id)` adopts orphan
WP posts from partial runs. The daemon just re-feeds `claimed` approval_queue
rows; `reset_stale_claims()` (Franky 5-min cron) reclaims stuck rows to
`approved` so the next Usopp tick picks them up.

`update_post` action_type is skipped with mark_failed — Phase 1 publisher
only covers new-post creation (ADR-005b scope).
"""

from __future__ import annotations

import os
import signal
import socket
import time
from types import FrameType
from typing import Any

from pydantic import ValidationError

from agents.usopp.publisher import Publisher
from shared import approval_queue
from shared.log import get_logger
from shared.schemas.approval import PublishWpPostV1, UpdateWpPostV1
from shared.schemas.publishing import PublishRequestV1
from shared.state import _get_conn
from shared.wordpress_client import WordPressClient

logger = get_logger("nakama.usopp.daemon")

DEFAULT_POLL_INTERVAL_S: int = 30
DEFAULT_BATCH_SIZE: int = 5

# Usopp consumes drafts queued by Brook; queue table is partitioned by source_agent.
SOURCE_AGENT: str = "brook"


class UsoppDaemon:
    """Poll-based publisher worker. One instance per process (Phase 1 single-worker)."""

    def __init__(
        self,
        *,
        wp_client: WordPressClient,
        publisher: Publisher,
        worker_id: str,
        poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.wp = wp_client
        self.publisher = publisher
        self.worker_id = worker_id
        self.poll_interval_s = poll_interval_s
        self.batch_size = batch_size
        self._shutdown: bool = False

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def request_shutdown(self, _signum: int, _frame: FrameType | None) -> None:
        # Idempotent: multiple SIGTERMs just re-flip the flag.
        if not self._shutdown:
            logger.info("usopp daemon shutdown requested (worker_id=%s)", self.worker_id)
        self._shutdown = True

    # ------------------------------------------------------------------
    # Single cycle
    # ------------------------------------------------------------------

    def run_once(self) -> int:
        """Claim one batch and dispatch each row. Returns count processed."""
        claimed = approval_queue.claim_approved_drafts(
            worker_id=self.worker_id,
            source_agent=SOURCE_AGENT,
            batch=self.batch_size,
        )
        for row in claimed:
            self._dispatch(row)
        return len(claimed)

    def _dispatch(self, row: dict[str, Any]) -> None:
        approval_queue_id: int = row["id"]
        payload = row["payload"]
        operation_id: str = row["operation_id"]

        # update_post is out of Phase 1 scope (ADR-005b covers new-post publish only).
        if isinstance(payload, UpdateWpPostV1):
            logger.warning(
                "usopp skipping update_post draft (Phase 1 scope): id=%s op=%s",
                approval_queue_id,
                operation_id,
            )
            approval_queue.mark_failed(
                draft_id=approval_queue_id,
                error_log="update_post not supported in Phase 1 publisher (ADR-005b scope)",
                actor=self.worker_id,
                increment_retry=False,
            )
            return

        if not isinstance(payload, PublishWpPostV1):  # pragma: no cover — union closed
            logger.error(
                "usopp unknown payload type: id=%s type=%s op=%s",
                approval_queue_id,
                type(payload).__name__,
                operation_id,
            )
            approval_queue.mark_failed(
                draft_id=approval_queue_id,
                error_log=f"unsupported payload type {type(payload).__name__}",
                actor=self.worker_id,
                increment_retry=False,
            )
            return

        reviewer = _lookup_reviewer(approval_queue_id)
        action = "schedule" if payload.scheduled_at is not None else "publish"

        try:
            request = PublishRequestV1(
                draft=payload.draft,
                action=action,
                scheduled_at=payload.scheduled_at,
                featured_media_id=None,
                reviewer=reviewer,
            )
        except ValidationError as exc:
            logger.error(
                "usopp PublishRequestV1 validation failed id=%s op=%s err=%s",
                approval_queue_id,
                operation_id,
                exc,
            )
            approval_queue.mark_failed(
                draft_id=approval_queue_id,
                error_log=f"publish request build failed: {exc}",
                actor=self.worker_id,
                increment_retry=False,
            )
            return

        try:
            result = self.publisher.publish(
                request,
                approval_queue_id=approval_queue_id,
                operation_id=operation_id,
            )
        except Exception as exc:  # noqa: BLE001 — daemon must survive publisher crashes
            # Publisher.publish() already traps known PublisherError / WP*Error paths
            # and marks the queue row failed itself. We catch *unexpected* exceptions
            # (e.g. schema bug, programming error) so one bad draft can't kill the loop.
            logger.exception(
                "usopp publish unexpected crash id=%s op=%s",
                approval_queue_id,
                operation_id,
            )
            try:
                approval_queue.mark_failed(
                    draft_id=approval_queue_id,
                    error_log=f"unexpected publisher crash: {type(exc).__name__}: {exc}",
                    actor=self.worker_id,
                    increment_retry=True,
                )
            except Exception:  # noqa: BLE001 — mark_failed itself may race with transition
                logger.exception(
                    "usopp mark_failed also failed id=%s op=%s",
                    approval_queue_id,
                    operation_id,
                )
            return

        logger.info(
            "usopp publish done draft_id=%s queue_id=%s status=%s post_id=%s op=%s",
            payload.draft.draft_id,
            approval_queue_id,
            result.status,
            result.post_id,
            operation_id,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.request_shutdown)
        signal.signal(signal.SIGINT, self.request_shutdown)
        logger.info(
            "usopp daemon start worker_id=%s poll_interval_s=%s batch=%s site=%s",
            self.worker_id,
            self.poll_interval_s,
            self.batch_size,
            self.wp.site_id,
        )
        while not self._shutdown:
            try:
                processed = self.run_once()
                if processed:
                    logger.info(
                        "usopp daemon cycle processed=%s worker_id=%s",
                        processed,
                        self.worker_id,
                    )
            except Exception:  # noqa: BLE001 — top-level guard; each row's error already caught
                logger.exception("usopp daemon cycle crashed; backing off")
            self._sleep_interruptible()
        logger.info("usopp daemon stopped worker_id=%s", self.worker_id)

    def _sleep_interruptible(self) -> None:
        """Sleep `poll_interval_s` in 1s increments so SIGTERM cuts the wait short."""
        remaining = float(self.poll_interval_s)
        while remaining > 0 and not self._shutdown:
            chunk = 1.0 if remaining > 1.0 else remaining
            time.sleep(chunk)
            remaining -= chunk


def _lookup_reviewer(approval_queue_id: int) -> str:
    """Read the approver's identity from approval_queue.reviewer.

    approve() writes this column on in_review→approved (transition's SET clause).
    Fallback "unknown" only used if the column is NULL — which shouldn't happen on an
    approved row but keeps PublishRequestV1 constructable rather than crashing the batch.
    """
    row = (
        _get_conn()
        .execute(
            "SELECT reviewer FROM approval_queue WHERE id = ?",
            (approval_queue_id,),
        )
        .fetchone()
    )
    reviewer = row["reviewer"] if row else None
    if not reviewer:
        logger.warning(
            "usopp daemon: no reviewer on approval_queue id=%s; using 'unknown'",
            approval_queue_id,
        )
        return "unknown"
    return reviewer


def _build_from_env() -> UsoppDaemon:
    site = os.environ.get("USOPP_TARGET_SITE", "wp_shosho")
    wp = WordPressClient.from_env(site)
    publisher = Publisher(wp)
    worker_id = os.environ.get("USOPP_WORKER_ID") or f"usopp-{socket.gethostname()}"
    poll_interval_s = int(os.environ.get("USOPP_POLL_INTERVAL_S", DEFAULT_POLL_INTERVAL_S))
    batch_size = int(os.environ.get("USOPP_BATCH_SIZE", DEFAULT_BATCH_SIZE))
    return UsoppDaemon(
        wp_client=wp,
        publisher=publisher,
        worker_id=worker_id,
        poll_interval_s=poll_interval_s,
        batch_size=batch_size,
    )


def main() -> None:
    _build_from_env().run()


if __name__ == "__main__":
    main()
