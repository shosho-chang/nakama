"""youtube_auth 多頻道 token：路徑、scope、頻道身分驗證；analytics fetch 的純函式。"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import youtube_analytics_fetch as fetch  # noqa: E402
import youtube_auth  # noqa: E402
from publish_upload import YOUTUBE_PUBLISH_SCOPES  # noqa: E402


def _resp(*items):
    return {"items": [{"id": cid, "snippet": {"title": t}} for cid, t in items]}


def test_podcast_token_path_unchanged_for_publish_line(tmp_path):
    # 發布線（publish_upload / Bridge publish_review）寫死讀 youtube_token.json
    assert youtube_auth.token_path("podcast", tmp_path) == tmp_path / "youtube_token.json"
    assert youtube_auth.token_path("main", tmp_path) == tmp_path / "youtube_token_main.json"


def test_each_channel_has_its_own_token_file():
    files = [c["token_file"] for c in youtube_auth.CHANNELS.values()]
    assert len(files) == len(set(files))


def test_scopes_main_includes_publish_and_analytics():
    s = youtube_auth.scopes_for("main")
    assert YOUTUBE_PUBLISH_SCOPES <= set(s)
    assert youtube_auth.ANALYTICS_SCOPE in s


def test_scopes_podcast_default_is_publish_only():
    assert set(youtube_auth.scopes_for("podcast")) == set(YOUTUBE_PUBLISH_SCOPES)
    assert youtube_auth.ANALYTICS_SCOPE in youtube_auth.scopes_for("podcast", analytics=True)


def test_verify_channel_accepts_expected():
    cid = youtube_auth.CHANNELS["main"]["channel_id"]
    assert youtube_auth.verify_channel(_resp((cid, "主頻道")), "main") == cid


@pytest.mark.parametrize(
    "resp",
    [
        _resp((youtube_auth.CHANNELS["podcast"]["channel_id"], "研究所")),  # 選錯頻道
        _resp(),  # 帳號沒有頻道
        _resp((youtube_auth.CHANNELS["main"]["channel_id"], "a"), ("UCother", "b")),
    ],
)
def test_verify_channel_rejects_wrong_or_ambiguous(resp):
    with pytest.raises(youtube_auth.ChannelMismatchError):
        youtube_auth.verify_channel(resp, "main")


def test_report_rows_maps_column_headers():
    resp = {
        "columnHeaders": [{"name": "video"}, {"name": "views"}],
        "rows": [["abc", 10], ["def", 5]],
    }
    assert fetch.report_rows(resp) == [{"video": "abc", "views": 10}, {"video": "def", "views": 5}]
    assert fetch.report_rows({"columnHeaders": [{"name": "x"}]}) == []


def test_write_csv_utf8_sig_and_union_fields(tmp_path):
    p = tmp_path / "a.csv"
    n = fetch.write_csv(p, [{"a": 1}, {"b": "中文"}])
    assert n == 2
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines()[0] == "a,b"


def test_runner_records_error_and_continues():
    run = fetch.Runner()

    def boom():
        raise ValueError("nope")

    assert run.attempt("x", boom, default=[]) == []
    assert run.attempt("y", lambda: 3) == 3
    assert run.errors and run.errors[0]["query"] == "x"
