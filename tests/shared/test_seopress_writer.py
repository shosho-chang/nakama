"""Tests for shared.seopress_writer three-tier fallback (ADR-005b §3)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared.schemas.external.seopress import (
    SEOPressSchemaDriftError,
    SEOpressWritePayloadV1,
)
from shared.seopress_writer import write_seopress
from shared.wordpress_client import WPClientError, WPServerError


def _payload() -> SEOpressWritePayloadV1:
    return SEOpressWritePayloadV1(
        title="Page Title",
        description="A description at least fifty characters long blah blah blah blah blah.",
        focus_keyword="test-keyword",
        canonical="",
    )


def _mk_client(
    *,
    rest_behavior: str = "ok",  # "ok" | "drift" | "4xx" | "5xx"
    fallback_result: bool = True,
    fallback_raises: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock WordPressClient matching the seopress_writer interface."""
    client = MagicMock()

    if rest_behavior == "ok":
        client.write_seopress_meta.return_value = (True, "rest")
    elif rest_behavior == "drift":
        client.write_seopress_meta.side_effect = SEOPressSchemaDriftError("new field appeared")
    elif rest_behavior == "4xx":
        client.write_seopress_meta.side_effect = WPClientError("404 not found")
    elif rest_behavior == "5xx":
        client.write_seopress_meta.side_effect = WPServerError("503 unavailable")
    else:
        raise ValueError(rest_behavior)

    if fallback_raises is not None:
        client.write_seopress_fallback_meta.side_effect = fallback_raises
    else:
        client.write_seopress_fallback_meta.return_value = fallback_result

    return client


# ---------------------------------------------------------------------------
# Tier 1 happy path
# ---------------------------------------------------------------------------


def test_rest_path_returns_written():
    client = _mk_client(rest_behavior="ok")
    status = write_seopress(
        wp_client=client,
        post_id=42,
        payload=_payload(),
        operation_id="op_12345678",
    )
    assert status == "written"
    client.write_seopress_meta.assert_called_once()
    client.write_seopress_fallback_meta.assert_not_called()


# ---------------------------------------------------------------------------
# Tier 2 fallback (drift / 4xx / 5xx → fallback_meta)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("behavior", ["drift", "4xx", "5xx"])
def test_rest_fails_triggers_fallback_meta(behavior):
    client = _mk_client(rest_behavior=behavior, fallback_result=True)
    status = write_seopress(
        wp_client=client,
        post_id=42,
        payload=_payload(),
        operation_id="op_12345678",
    )
    assert status == "fallback_meta"
    client.write_seopress_meta.assert_called_once()
    client.write_seopress_fallback_meta.assert_called_once()


# ---------------------------------------------------------------------------
# Tier 3 skip (both paths fail)
# ---------------------------------------------------------------------------


def test_both_paths_fail_returns_skipped():
    client = _mk_client(rest_behavior="drift", fallback_result=False)
    status = write_seopress(
        wp_client=client,
        post_id=42,
        payload=_payload(),
        operation_id="op_12345678",
    )
    assert status == "skipped"


def test_fallback_raise_also_returns_skipped():
    client = _mk_client(
        rest_behavior="drift",
        fallback_raises=WPClientError("400 bad request"),
    )
    status = write_seopress(
        wp_client=client,
        post_id=42,
        payload=_payload(),
        operation_id="op_12345678",
    )
    assert status == "skipped"


# ---------------------------------------------------------------------------
# Order / side-effect sanity
# ---------------------------------------------------------------------------


def test_rest_called_before_fallback():
    client = _mk_client(rest_behavior="drift", fallback_result=True)
    write_seopress(
        wp_client=client,
        post_id=42,
        payload=_payload(),
        operation_id="op_12345678",
    )
    # Verify call order via call_args_list sequencing on the parent mock
    call_names = [c[0] for c in client.method_calls]
    assert call_names[0] == "write_seopress_meta"
    assert call_names[1] == "write_seopress_fallback_meta"


def test_payload_passed_through_to_both_methods():
    client = _mk_client(rest_behavior="drift", fallback_result=True)
    payload = _payload()
    write_seopress(
        wp_client=client,
        post_id=7,
        payload=payload,
        operation_id="op_11111111",
    )
    # REST call
    rest_kwargs = client.write_seopress_meta.call_args.kwargs
    assert rest_kwargs["post_id"] == 7
    assert rest_kwargs["payload"] is payload
    # Fallback call
    fb_kwargs = client.write_seopress_fallback_meta.call_args.kwargs
    assert fb_kwargs["post_id"] == 7
    assert fb_kwargs["payload"] is payload
