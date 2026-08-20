from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "thousand_sunny/templates/bridge/publish_calendar.html").read_text(
    encoding="utf-8"
)
LIST_TEMPLATE = (ROOT / "thousand_sunny/templates/bridge/publish_list.html").read_text(
    encoding="utf-8"
)
CSS = (ROOT / "thousand_sunny/static/shosho/publish-calendar.css").read_text(encoding="utf-8")
ROUTER = (ROOT / "thousand_sunny/routers/publish_calendar.py").read_text(encoding="utf-8")
DOMAIN = (ROOT / "shared/publish_calendar.py").read_text(encoding="utf-8")


def test_publish_surfaces_have_bidirectional_calendar_navigation_and_active_state() -> None:
    assert 'href="/bridge/publish/calendar"' in LIST_TEMPLATE
    assert 'href="/bridge/publish" aria-current="page"' in LIST_TEMPLATE
    assert 'aria-label="發布檢視"' in TEMPLATE
    assert 'aria-current="page">發布月曆' in TEMPLATE


def test_desktop_calendar_is_semantic_sunday_first_month_grid() -> None:
    assert '<table class="pc-month-grid">' in TEMPLATE
    assert '<th scope="col">' in TEMPLATE
    assert 'datetime="{{ day.value.isoformat() }}"' in TEMPLATE
    assert "週日為第一欄" in TEMPLATE
    assert "calendar.Calendar(firstweekday=6)" not in TEMPLATE


def test_mobile_390_contract_uses_readable_agenda_without_horizontal_scroll() -> None:
    assert '<ol class="pc-agenda"' in TEMPLATE
    assert 'class="pc-agenda__day"' in TEMPLATE
    assert "@media (max-width: 640px)" in CSS
    assert "body.sho .pc-month-grid," in CSS
    assert "display: none;" in CSS
    assert "overflow-x: clip;" in CSS
    assert "body.sho .pc-backlog__list" in CSS
    assert "flex-direction: column;" in CSS
    assert "overflow-wrap: anywhere;" in CSS
    assert "body.sho .pc-schedule__actions button" in CSS
    assert "width: 100%;" in CSS
    assert "grid-template-columns: minmax(0, 1fr);" in CSS


def test_calendar_shows_group_targets_phase_basis_and_podcast_youtube_identity() -> None:
    for token in (
        "item.content_type",
        "item.targets",
        "target.platform_label",
        "target.status",
        "item.phase",
        "item.progress_label",
        "item.date_basis_label",
        "Podcast YouTube",
        "podcast_youtube.name",
        "podcast_youtube.handle",
        "podcast_youtube.channel_id",
        "Community handoff",
    ):
        assert token in TEMPLATE


def test_calendar_discloses_due_worker_health_and_native_arm_truth() -> None:
    for token in (
        "Short Due Dispatcher",
        "data-worker-health",
        "short_due_worker.last_run_at",
        "short_due_worker.last_success_at",
        "short_due_worker.consecutive_failures",
        'role="status"',
        'role="alert"',
        "Instagram 到點投遞",
        "publish_due.py --watch --execute",
    ):
        assert token in TEMPLATE
    assert "uploaded means public" not in TEMPLATE


def test_channel_identity_has_one_shared_source_of_truth() -> None:
    for constant in (
        "PODCAST_YOUTUBE_CHANNEL_NAME",
        "PODCAST_YOUTUBE_CHANNEL_HANDLE",
        "PODCAST_YOUTUBE_CHANNEL_ID",
    ):
        assert constant in DOMAIN
        assert constant in ROUTER
    assert "《張修修的不正常人類研究所》" not in TEMPLATE
    assert "@abnormal-human-research" not in TEMPLATE
    assert "UCvipegP35x3-OcAs--PgAig" not in TEMPLATE
    assert 'PODCAST_YOUTUBE_CHANNEL_NAME = "《張修修的不正常人類研究所》"' in DOMAIN
    assert 'PODCAST_YOUTUBE_CHANNEL_HANDLE = "@abnormal-human-research"' in DOMAIN
    assert 'PODCAST_YOUTUBE_CHANNEL_ID = "UCvipegP35x3-OcAs--PgAig"' in DOMAIN


def test_unknown_date_and_basis_copy_is_neutral_and_not_duplicate_status() -> None:
    assert "未列入月曆／日期未定" in TEMPLATE
    assert "item.date_basis_label" in TEMPLATE
    assert "item.date_basis or 'unscheduled'" not in TEMPLATE
    assert "待排程" not in TEMPLATE


def test_pipeline_and_campaign_anchor_controls_are_server_rendered_and_accessible() -> None:
    for token in (
        'class="pc-pipeline__rail"',
        "phase_counts",
        "一組內容 · 一個 Campaign Anchor · 各平台狀態獨立",
        'method="post"',
        'name="campaign_anchor_local"',
        'name="expected_anchor_token"',
        'type="datetime-local"',
        'name="operation" value="set"',
        'name="operation" value="clear"',
        "item.schedule_disabled_reason",
        'type="button" disabled',
    ):
        assert token in TEMPLATE


def test_schedule_intent_and_live_execution_are_distinct_semantic_controls() -> None:
    for token in (
        'class="pc-control pc-control--schedule"',
        "只設定發布意圖；不會核准、不會投遞。",
        'class="pc-control pc-control--execution"',
        'action="/bridge/publish/{{ item.episode | urlencode }}/'
        '{{ item.content_id | urlencode }}/approve-upload"',
        'name="return_to"',
        "核准並投遞三平台",
        "只重試此平台",
        "真實平台 side effect",
        "檢查素材與文案",
        "開啟三平台發布工作",
        "browser/manual handoff",
        'target="_blank" rel="noopener noreferrer"',
    ):
        assert token in TEMPLATE

    for selector in (
        ".pc-control--execution",
        ".pc-execution__reason",
        ".pc-execution button:focus-visible",
        ".pc-retry button:focus-visible",
        ".pc-control--execution > button:disabled",
    ):
        assert selector in CSS


def test_calendar_designs_empty_warning_loading_focus_hover_disabled_and_reduced_motion() -> None:
    for state in (
        "pc-state--empty",
        "pc-state--warning",
        "pc-loading",
        'aria-busy="false"',
        'aria-disabled="true"',
        ":hover",
        ":active",
        ":focus-visible",
        "@media (prefers-reduced-motion: reduce)",
    ):
        assert state in TEMPLATE or state in CSS
    assert "var(--sho-focus)" in CSS


def test_calendar_uses_only_existing_design_tokens_and_no_ai_slop_defaults() -> None:
    assert "var(--sho-" in CSS
    assert "--pc-" not in CSS
    assert "#" not in CSS
    assert "rgb(" not in CSS.lower()
    assert "linear-gradient" not in CSS.lower()
    assert "inter" not in CSS.lower()
    assert "roboto" not in CSS.lower()
    assert "border-radius: 16px" not in CSS


def test_calendar_is_server_rendered_and_router_has_no_platform_adapter_imports() -> None:
    assert "publish-calendar.js" not in TEMPLATE
    assert 'method="get" action="/bridge/publish/calendar"' in TEMPLATE
    for forbidden in (
        "agents.usopp",
        "social_publish",
        "publish_upload",
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import boto",
        "from boto",
    ):
        assert forbidden not in ROUTER.lower()
