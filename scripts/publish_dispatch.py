"""Stage 6 desktop dispatcher for release targets and Carousel publish jobs.

Dry-run is the default.  External writes require the explicit ``--execute``
flag; credentials are read only inside the corresponding live adapter.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.usopp.media_staging import MediaStager, MediaStagingConfig  # noqa: E402
from agents.usopp.meta_graph import (  # noqa: E402
    MetaGraphClient,
    MetaGraphConfig,
    MetaGraphError,
)
from agents.usopp.social_publish import (  # noqa: E402
    AdapterResult,
    dispatch_release,
    ensure_short_targets,
)
from shared.release_store import get_release  # noqa: E402


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

    def __init__(self, client: MetaGraphClient) -> None:
        self.client = client

    def publish(self, *, release, target, idempotency_key, checkpoint):
        del idempotency_key
        state = _checkpoint(target)
        result = self.client.publish_facebook_reel(
            video_path=Path(str(release["file_path"])),
            description=str(target.get("description") or target.get("title") or ""),
            checkpoint=state,
            save_checkpoint=lambda value: checkpoint(value),
        )
        return AdapterResult(
            status="published",
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
        print(json.dumps({"dry_run": True, "targets": _release_plan(release, selected)}, indent=2))
        return 0

    adapters: dict[str, Any] = {}
    if selected is None or "youtube" in selected:
        adapters["youtube"] = YouTubeShortAdapter()
    try:
        client = build_meta_client()
    except Exception:
        client = None
    if client is not None and (selected is None or "facebook_reels" in selected):
        adapters["facebook_reels"] = FacebookReelAdapter(client)
    if client is not None and (selected is None or "instagram_reels" in selected):
        try:
            adapters["instagram_reels"] = InstagramReelAdapter(
                client, MediaStager(MediaStagingConfig.from_env())
            )
        except Exception:
            pass
    results = dispatch_release(release, adapters, only_platforms=selected)
    print(json.dumps({"dry_run": False, "results": results}, ensure_ascii=False, indent=2))
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
        print(json.dumps(_carousel_handoff(job), ensure_ascii=False, indent=2))
        return 2 if args.execute else 0
    if not args.execute:
        print(json.dumps({"dry_run": True, "job_id": job.job_id, "targets": unfinished}, indent=2))
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
    print(completed.model_dump_json(indent=2))
    return int(completed.status == "failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 6 multi-platform publish dispatcher")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--release", action="store_true", help="dispatch one Short release")
    mode.add_argument("--carousel-job", help="dispatch one approved Carousel job JSON")
    parser.add_argument("--episode")
    parser.add_argument("--cut")
    parser.add_argument("--platform", action="append")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--execute", action="store_true", help="allow external writes")
    execution.add_argument("--dry-run", action="store_true", help="print the plan only (default)")
    parser.add_argument("--youtube-community-permalink")
    parser.add_argument("--youtube-community-post-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.release:
        if not args.episode or not args.cut:
            raise SystemExit("--release requires --episode and --cut")
        return dispatch_short(args)
    return dispatch_carousel(args)


if __name__ == "__main__":
    raise SystemExit(main())
