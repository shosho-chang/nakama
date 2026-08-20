"""Stage 6 desktop dispatcher for release targets and Carousel publish jobs.

Dry-run is the default.  External writes require the explicit ``--execute``
flag; credentials are read only inside the corresponding live adapter.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.usopp.media_staging import (  # noqa: E402
    MediaStager,
    MediaStagingConfig,
    MediaStagingError,
)
from agents.usopp.meta_graph import (  # noqa: E402
    MetaGraphClient,
    MetaGraphConfig,
    MetaGraphConfigurationError,
    MetaGraphError,
)
from agents.usopp.social_publish import (  # noqa: E402
    SHORT_PLATFORMS,
    AdapterResult,
    dispatch_release,
    ensure_short_targets,
)
from shared.release_store import get_release  # noqa: E402


def write_json_output(payload: Any, *, stream=None) -> None:
    """Write portable JSON even when the Windows console is not UTF-8."""
    output = stream or sys.stdout
    output.write(json.dumps(payload, ensure_ascii=True, indent=2) + "\n")


class UrllibMetaTransport:
    """Small authenticated Graph transport; token never enters call payloads/logs."""

    def __init__(self, config: MetaGraphConfig, *, timeout_seconds: float = 60.0) -> None:
        self.base_url = f"https://graph.facebook.com/{config.api_version}"
        self.access_token = config.page_access_token
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        values = dict(params if method.upper() == "GET" else data or {})
        values["access_token"] = self.access_token
        encoded = urllib.parse.urlencode(values).encode("utf-8")
        url = f"{self.base_url}/{path.lstrip('/')}"
        if method.upper() == "GET":
            url = f"{url}?{encoded.decode('ascii')}"
            body = None
        else:
            body = encoded
        request = urllib.request.Request(url, data=body, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MetaGraphError("Meta Graph returned a non-JSON response") from exc
        if not isinstance(value, dict):
            raise MetaGraphError("Meta Graph returned a non-object response")
        return value

    def upload_file(
        self,
        upload_url: str,
        file_path: Path,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        request_headers = {
            "Authorization": f"OAuth {self.access_token}",
            "Content-Type": "application/octet-stream",
            **dict(headers or {}),
        }
        request = urllib.request.Request(
            upload_url,
            data=Path(file_path).read_bytes(),
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MetaGraphError("Meta upload returned a non-JSON response") from exc
        if not isinstance(value, dict) or value.get("error"):
            raise MetaGraphError("Meta upload failed")
        return value


def build_meta_client() -> MetaGraphClient:
    config = MetaGraphConfig.from_env()
    return MetaGraphClient(config, UrllibMetaTransport(config))


_PLATFORM_LABELS = {
    "youtube": "YouTube Shorts",
    "instagram_reels": "Instagram Reels",
    "facebook_reels": "Facebook Reels",
}


def _adapter_setup_error(platform: str, component: str, exc: Exception) -> str:
    """Return a secret-free, actionable adapter construction failure."""

    safe_detail = ""
    if isinstance(exc, (MetaGraphConfigurationError, MediaStagingError)):
        safe_detail = f": {exc}"
    return (
        f"{_PLATFORM_LABELS.get(platform, platform)} adapter initialization failed "
        f"during {component} ({type(exc).__name__}){safe_detail}; "
        "run `python scripts/publish_dispatch.py --preflight --platform "
        f"{platform}` and fix the reported configuration/dependency"
    )


def build_short_adapters(
    selected: set[str] | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Build only selected adapters and retain per-target setup diagnostics."""

    requested = set(SHORT_PLATFORMS if selected is None else selected)
    adapters: dict[str, Any] = {}
    setup_errors: dict[str, str] = {}
    if "youtube" in requested:
        adapters["youtube"] = YouTubeShortAdapter()

    meta_platforms = requested.intersection({"instagram_reels", "facebook_reels"})
    client: MetaGraphClient | None = None
    if meta_platforms:
        try:
            client = build_meta_client()
        except Exception as exc:
            for platform in meta_platforms:
                setup_errors[platform] = _adapter_setup_error(
                    platform, "Meta Graph configuration", exc
                )

    if client is not None and "facebook_reels" in requested:
        adapters["facebook_reels"] = FacebookReelAdapter(client)
    if client is not None and "instagram_reels" in requested:
        try:
            adapters["instagram_reels"] = InstagramReelAdapter(
                client, MediaStager(MediaStagingConfig.from_env())
            )
        except Exception as exc:
            setup_errors["instagram_reels"] = _adapter_setup_error(
                "instagram_reels", "R2 media staging startup", exc
            )
    return adapters, setup_errors


def publish_dependency_preflight(selected: set[str] | None) -> dict[str, Any]:
    """Validate selected live dependencies without network or state mutation."""

    requested = set(SHORT_PLATFORMS if selected is None else selected)
    checks: list[dict[str, Any]] = []

    def config_check(component: str, loader: Callable[[], Any], action: str) -> None:
        try:
            loader()
        except Exception as exc:
            detail = (
                str(exc)
                if isinstance(exc, (MetaGraphConfigurationError, MediaStagingError))
                else type(exc).__name__
            )
            checks.append(
                {
                    "component": component,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "message": detail,
                    "action": action,
                }
            )
        else:
            checks.append({"component": component, "ok": True})

    if requested.intersection({"instagram_reels", "facebook_reels"}):
        config_check(
            "Meta Graph configuration",
            MetaGraphConfig.from_env,
            "set the named META_* variables in the supervised runtime",
        )
    if "instagram_reels" in requested:
        config_check(
            "R2 media staging configuration",
            MediaStagingConfig.from_env,
            "set the named META_MEDIA_R2_* variables for the staging bucket",
        )
        if importlib.util.find_spec("boto3") is None:
            checks.append(
                {
                    "component": "boto3 dependency",
                    "ok": False,
                    "error_type": "ModuleNotFoundError",
                    "message": "boto3 is not installed in this Python environment",
                    "action": "run this Python with `-m pip install -r requirements.txt`",
                }
            )
        else:
            checks.append(
                {
                    "component": "boto3 dependency",
                    "ok": True,
                    "action": (
                        "if missing, run this Python with `-m pip install -r requirements.txt`"
                    ),
                }
            )

    return {
        "preflight": True,
        "network_calls": False,
        "selected_platforms": sorted(requested),
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
    }


def _checkpoint(target: Mapping[str, Any]) -> dict[str, Any]:
    raw = target.get("checkpoint_json")
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError("target checkpoint_json is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("target checkpoint_json must contain an object")
    return value


class YouTubeShortAdapter:
    platform = "youtube"

    def publish(self, *, release, target, idempotency_key, checkpoint):
        del idempotency_key, checkpoint
        from scripts.publish_upload import _load_yt, _upload_one
        from shared.config import get_vault_path

        try:
            result = _upload_one(
                _load_yt(), {"release": dict(release), "target": dict(target)}, get_vault_path()
            )
        except SystemExit as exc:
            raise RuntimeError(str(exc)) from exc
        return AdapterResult(status="uploaded", external_id=result["video_id"], url=result["url"])


class InstagramReelAdapter:
    platform = "instagram_reels"

    def __init__(self, client: MetaGraphClient, stager: MediaStager) -> None:
        self.client = client
        self.stager = stager

    def publish(self, *, release, target, idempotency_key, checkpoint):
        del idempotency_key
        staged = self.stager.stage_file(Path(str(release["file_path"])))
        state = _checkpoint(target)
        try:
            result = self.client.publish_instagram_reel(
                video_url=staged.url,
                caption=str(target.get("description") or target.get("title") or ""),
                checkpoint=state,
                save_checkpoint=lambda value: checkpoint(value),
            )
            return AdapterResult(
                status="published",
                external_id=result.external_id,
                url=result.permalink,
                checkpoint=result.checkpoint,
            )
        finally:
            self.stager.cleanup([staged.key])


class FacebookReelAdapter:
    platform = "facebook_reels"

    def __init__(
        self,
        client: MetaGraphClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.now = now

    def _future_anchor(self, target: Mapping[str, Any]) -> datetime | None:
        raw = target.get("publish_at")
        if raw in {None, ""}:
            return None
        try:
            anchor = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Campaign Anchor must be a valid timezone-aware datetime") from exc
        if anchor.tzinfo is None or anchor.utcoffset() is None:
            raise ValueError("Campaign Anchor must be timezone-aware")
        current = self.now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("adapter clock must be timezone-aware")
        anchor = anchor.astimezone(UTC)
        return anchor if anchor > current.astimezone(UTC) else None

    def publish(self, *, release, target, idempotency_key, checkpoint):
        del idempotency_key
        state = _checkpoint(target)
        scheduled_at = self._future_anchor(target)
        result = self.client.publish_facebook_reel(
            video_path=Path(str(release["file_path"])),
            description=str(target.get("description") or target.get("title") or ""),
            scheduled_at=scheduled_at,
            checkpoint=state,
            save_checkpoint=lambda value: checkpoint(value),
        )
        return AdapterResult(
            status=(
                "uploaded" if result.checkpoint.get("finish_mode") == "scheduled" else "published"
            ),
            external_id=result.external_id,
            url=result.permalink,
            checkpoint=result.checkpoint,
        )


def _release_plan(release: Mapping[str, Any], selected: set[str] | None) -> list[dict]:
    return [
        {
            "platform": target["platform"],
            "status": target["status"],
            "eligible": target["status"] != "ineligible",
            "selected": selected is None or target["platform"] in selected,
        }
        for target in release["targets"]
    ]


def dispatch_short(args: argparse.Namespace) -> int:
    release = get_release(args.episode, args.cut)
    if release is None:
        raise SystemExit(f"release not found: {args.episode}/{args.cut}")
    if release["format"] != "short":
        raise SystemExit("--release dispatcher currently accepts format=short only")
    ensure_short_targets(release)
    release = get_release(args.episode, args.cut)
    assert release is not None
    selected = set(args.platform or []) or None
    if not args.execute:
        write_json_output({"dry_run": True, "targets": _release_plan(release, selected)})
        return 0

    adapters, setup_errors = build_short_adapters(selected)
    results = dispatch_release(
        release,
        adapters,
        only_platforms=selected,
        adapter_setup_errors=setup_errors,
    )
    write_json_output({"dry_run": False, "results": results})
    return int(any(item["status"] == "failed" for item in results))


def _carousel_handoff(job) -> dict[str, Any]:
    return {
        "kind": "browser_handoff",
        "platform": "youtube_community",
        "state": "awaiting_receipt",
        "caption": job.caption,
        "asset_paths": [asset.image.path for asset in job.assets[:10]],
        "target_url": "https://www.youtube.com/",
    }


def dispatch_carousel(args: argparse.Namespace) -> int:
    from scripts.podcast_carousel_publish_job import (
        checkpoint_publish_target,
        claim_publish_job,
        complete_publish_job,
        load_publish_job,
        start_publish_target,
        unfinished_publish_platforms,
    )
    from shared.schemas.carousel_publish import CarouselPublishPlatformResult

    path = Path(args.carousel_job).resolve()
    job = load_publish_job(path)
    unfinished = unfinished_publish_platforms(job)
    if "youtube_community" in unfinished and not (
        args.youtube_community_permalink or args.youtube_community_post_id
    ):
        write_json_output(_carousel_handoff(job))
        return 2 if args.execute else 0
    if not args.execute:
        write_json_output({"dry_run": True, "job_id": job.job_id, "targets": unfinished})
        return 0

    capabilities = sorted(
        {
            capability
            for target in job.targets
            if target.platform in unfinished
            for capability in target.required_executor_capabilities
        }
    )
    job = claim_publish_job(
        path,
        executor="codex",
        executor_id="publish_dispatch",
        executor_capabilities=capabilities,
    )
    assert job.claim is not None
    token = job.claim.claim_token
    meta_platforms = {"instagram", "facebook_page"}.intersection(unfinished)
    client = build_meta_client() if meta_platforms else None
    stager = MediaStager(MediaStagingConfig.from_env()) if meta_platforms else None

    for platform in unfinished:
        job = start_publish_target(path, claim_token=token, platform=platform)
        state = next(item for item in job.target_states if item.platform == platform)
        target = next(item for item in job.targets if item.platform == platform)
        try:
            if platform == "youtube_community":
                receipt = args.youtube_community_post_id or args.youtube_community_permalink
                result = CarouselPublishPlatformResult(
                    platform=platform,
                    strategy=target.strategy,
                    status="published",
                    receipt_id=receipt,
                    permalink=args.youtube_community_permalink,
                    idempotency_key=state.idempotency_key,
                    attempt_id=state.attempt_id,
                    completed_at=datetime.now(UTC),
                )
            else:
                assert client is not None and stager is not None
                staged = stager.stage_files([Path(asset.image.path) for asset in job.assets])
                checkpoint: dict[str, Any] = {}
                try:
                    if platform == "instagram":
                        published = client.publish_instagram_carousel(
                            image_urls=[item.url for item in staged],
                            caption=job.caption,
                            checkpoint=checkpoint,
                            save_checkpoint=lambda value: checkpoint.update(value),
                        )
                    else:
                        published = client.publish_facebook_multi_photo(
                            image_urls=[item.url for item in staged],
                            message=job.caption,
                            checkpoint=checkpoint,
                            save_checkpoint=lambda value: checkpoint.update(value),
                        )
                finally:
                    stager.cleanup(item.key for item in staged)
                result = CarouselPublishPlatformResult(
                    platform=platform,
                    strategy=target.strategy,
                    status="published",
                    receipt_id=published.external_id,
                    permalink=published.permalink,
                    idempotency_key=state.idempotency_key,
                    attempt_id=state.attempt_id,
                    completed_at=datetime.now(UTC),
                )
        except Exception as exc:
            result = CarouselPublishPlatformResult(
                platform=platform,
                strategy=target.strategy,
                status="failed",
                error=str(exc)[:4000],
                idempotency_key=state.idempotency_key,
                attempt_id=state.attempt_id,
                completed_at=datetime.now(UTC),
            )
        checkpoint_publish_target(path, claim_token=token, result=result)

    job = load_publish_job(path)
    completed = complete_publish_job(path, claim_token=token, results=job.results)
    write_json_output(completed.model_dump(mode="json"))
    return int(completed.status == "failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 6 multi-platform publish dispatcher")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--release", action="store_true", help="dispatch one Short release")
    mode.add_argument("--carousel-job", help="dispatch one approved Carousel job JSON")
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="validate selected publish dependencies without network or state writes",
    )
    parser.add_argument("--episode")
    parser.add_argument("--cut")
    parser.add_argument("--platform", action="append", choices=SHORT_PLATFORMS)
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--execute", action="store_true", help="allow external writes")
    execution.add_argument("--dry-run", action="store_true", help="print the plan only (default)")
    parser.add_argument("--youtube-community-permalink")
    parser.add_argument("--youtube-community-post-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.preflight:
        payload = publish_dependency_preflight(set(args.platform or []) or None)
        write_json_output(payload)
        return int(not payload["ok"])
    if args.release:
        if not args.episode or not args.cut:
            raise SystemExit("--release requires --episode and --cut")
        return dispatch_short(args)
    return dispatch_carousel(args)


if __name__ == "__main__":
    raise SystemExit(main())
