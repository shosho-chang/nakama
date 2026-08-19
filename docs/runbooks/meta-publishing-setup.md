# Meta publishing setup and supervised probes

Stage anchor: **Stage 6 Publishing**. This runbook enables the live gates for
Instagram Reels/Carousel and Facebook Page Reels/multi-photo posts. Automated
tests use fake transports and do not prove that a Meta App is production-ready.

## 1. Meta assets and account topology

1. Create or select a Meta App owned by the same Business Portfolio as the Page.
2. Configure Facebook Login for Business and request only the Page/Instagram
   publishing permissions required by the Graph API flows used here.
3. Confirm the Instagram account is Professional and linked to
   `META_PAGE_ID`; record its Graph user ID as `META_IG_USER_ID`.
4. Generate a Page access token for the operator-owned test Page. Never paste
   the token into Bridge, screenshots, job JSON, CLI arguments, or shell history.
5. Pick and pin an API version in `META_GRAPH_API_VERSION`. Upgrade it as an
   explicit maintenance change after rerunning all probes; the code never guesses
   the current Graph version.

Required desktop-worker environment variables:

```dotenv
META_GRAPH_API_VERSION=
META_PAGE_ID=
META_IG_USER_ID=
META_PAGE_ACCESS_TOKEN=
```

The credential probe is read-only:

```powershell
python scripts/meta_publish_probe.py credentials
```

It must return both the Facebook Page and Instagram Professional identity. A
missing variable, permission error, missing linked IG identity, or unexpected ID
is a failed gate; do not proceed by changing the expected identity in code.

## 2. Dedicated short-lived R2 staging

Create a bucket used only for temporary social-publishing media. Do not reuse
`R2_BUCKET_NAME` or `NAKAMA_R2_BACKUP_BUCKET`. Give its token object put/get/delete
permissions only for that bucket and apply a lifecycle rule that removes stale
objects after one day as a backstop.

```dotenv
META_MEDIA_R2_ACCOUNT_ID=
META_MEDIA_R2_ACCESS_KEY_ID=
META_MEDIA_R2_SECRET_ACCESS_KEY=
META_MEDIA_R2_BUCKET=
META_MEDIA_PUBLIC_BASE_URL=
```

`META_MEDIA_PUBLIC_BASE_URL` is optional. Without it the worker generates a
short-lived presigned GET URL. Object keys are random and never contain partner
filenames. The worker deletes staged objects after a terminal success or failure;
the bucket lifecycle handles abandoned crash artifacts.

## 3. Probe order

All commands except `credentials` are dry-run unless `--execute` is present.
Use rights-cleared test assets and the test Page/account only.

```powershell
# Instagram Reel: container → FINISHED → media_publish → permalink
python scripts/meta_publish_probe.py ig-reel --media-url "<short-lived-url>" `
  --caption "[E2E] IG Reel" --execute

# Instagram Carousel: child containers → parent → FINISHED → publish
python scripts/meta_publish_probe.py ig-carousel `
  --media-url "<page-1-url>" --media-url "<page-2-url>" `
  --caption "[E2E] IG Carousel" --execute

# Facebook Page Reel: start → binary upload → finish → reconcile
python scripts/meta_publish_probe.py fb-reel --file "<59-second-mp4>" `
  --caption "[E2E] FB Reel 59s" --execute

# Facebook Page multi-photo: unpublished photos → one feed post
python scripts/meta_publish_probe.py fb-multi-photo `
  --media-url "<page-1-url>" --media-url "<page-2-url>" `
  --caption "[E2E] FB multi-photo" --execute
```

Repeat the Facebook Reel probe with a 74-second fixture only as a supervised
capability investigation. The production eligibility gate remains fail-closed at
60 seconds until both the current official limit and the real Page response are
recorded and deliberately reviewed. The worker never auto-crops or transcodes it.

For each successful probe record the timestamp, app/API version, Page/IG identity,
external media/post ID, permalink, fixture duration/hash, and any platform warning.
Do not store tokens with the result.

## 4. YouTube Community boundary

The YouTube Data API has no Community-post insert endpoint. Carousel jobs therefore
produce a browser handoff containing the caption, up to ten PNGs, and the Studio
target URL. A target remains pending until an authenticated browser/human returns a
post ID or permalink. Never checkpoint it as published merely because the handoff
was generated.

## 5. Rollback and incident rules

- Disable live writes by omitting `--execute`; dry-run must remain side-effect free.
- Rotate the Page token or R2 token if it may have appeared in output. Application
  logs intentionally report only missing setting names and platform IDs.
- A platform failure is isolated to that target. Retry only failed targets; never
  delete or recreate a successful Instagram/Facebook/YouTube post as compensation.
- External post deletion is manual in the platform UI and is not implemented by
  these scripts.
