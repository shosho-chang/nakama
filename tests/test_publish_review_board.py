"""發布審核的兩層清單：集數 → 這一集的成品 → 單支發布頁。

修修 2026-09-16：「在 Publish 那一邊，也是把所有的內容以 Project 來做分類，
跟 Packaging 一樣的做法，點進去之後再顯示說裡面有哪些內容已經 Ready for Publish。
然後再點進去，才是 publish 的畫面。」

原本 `/bridge/publish` 把所有集數的所有成品平鋪成一長串——一集三支長片＋三支短片
就是六列，兩集就看不完。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

PASSWORD = "test-web-password"
SECRET = "test-web-secret"

EP_A = "20260901 蘇予昕"
EP_B = "20260805 林之晨"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """兩集、四支成品：一支齊備、一支缺描述、一支缺成品檔、一支已上架。"""
    vault = tmp_path / "vault"
    (vault / "thumbs").mkdir(parents=True)
    (vault / "thumbs" / "cover.png").write_bytes(b"png")

    exports = {}
    for episode, cuts in ((EP_A, ("punch-L03", "punch-L02", "ghost-L09")), (EP_B, ("punch-L04",))):
        d = tmp_path / episode / "highlights" / "exports"
        d.mkdir(parents=True)
        for cut in cuts:
            path = d / f"{cut}.mp4"
            if cut != "ghost-L09":  # 這一支的檔案故意不存在
                path.write_bytes(b"video")
            exports[(episode, cut)] = path

    monkeypatch.setenv("WEB_PASSWORD", PASSWORD)
    monkeypatch.setenv("WEB_SECRET", SECRET)
    monkeypatch.delenv("NAKAMA_DEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "state.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.publish_review as pub_module
    from shared import release_store

    importlib.reload(auth_module)
    importlib.reload(pub_module)
    importlib.reload(app_module)
    importlib.reload(release_store)

    def seed(episode: str, cut: str, **target) -> None:
        rid = release_store.register_release(
            episode, cut, "long", str(exports[(episode, cut)]), work_title=f"{cut} 工作代號"
        )
        tid = release_store.ensure_target(rid, "youtube")
        if target:
            release_store.update_target(tid, **target)

    seed(
        EP_A,
        "punch-L03",
        title="退休不會解脫的幻覺",
        description="描述在這裡",
        thumbnail_path="thumbs/cover.png",
    )
    seed(EP_A, "punch-L02", title="你怕的不是失敗", thumbnail_path="thumbs/cover.png")
    seed(EP_A, "ghost-L09", title="沒有成品檔", description="x", thumbnail_path="thumbs/cover.png")
    seed(
        EP_B,
        "punch-L04",
        title="拖延不是懶",
        description="x",
        thumbnail_path="thumbs/cover.png",
        status="published",
        url="https://youtu.be/abc",
    )

    client = TestClient(app_module.app, follow_redirects=False)
    client.cookies.set("nakama_auth", auth_module.make_token(PASSWORD))
    return client


def test_root_lists_episodes_not_individual_cuts(env):
    """第一層是集數，不是成品——成品標題不該出現在這一頁。"""
    body = env.get("/bridge/publish").text

    assert EP_A in body
    assert EP_B in body
    assert "退休不會解脫的幻覺" not in body
    assert "/bridge/publish/20260901%20%E8%98%87%E4%BA%88%E6%98%95" in body


def test_root_counts_only_the_ready_ones(env):
    """「可以發布」是實際按得下去的那些，不是成品總數。

    蘇予昕那一集有三支：一支齊備、一支缺描述、一支缺成品檔 → 只有 1 支待發。
    """
    body = env.get("/bridge/publish").text

    assert "1 支待發" in body


def test_episode_page_says_what_each_cut_is_missing(env):
    """第二層要說得出「為什麼這支還不能發」，不是只標一個 PENDING。"""
    body = env.get(f"/bridge/publish/{EP_A}").text

    assert "可以發布" in body
    assert "退休不會解脫的幻覺" in body
    # 缺描述的那支要指名缺什麼
    assert "還缺：描述" in body
    # 缺成品檔的那支同理
    assert "還缺：成品檔" in body


def test_episode_page_links_into_the_single_cut_publish_screen(env):
    body = env.get(f"/bridge/publish/{EP_A}").text

    assert "/punch-L03" in body


def test_episode_page_marks_already_published(env):
    body = env.get(f"/bridge/publish/{EP_B}").text

    assert "已上架" in body
    assert "https://youtu.be/abc" in body


def test_unknown_episode_is_404_not_an_empty_page(env):
    assert env.get("/bridge/publish/20991231%20nobody").status_code == 404


def test_single_segment_routes_are_not_swallowed_by_the_episode_route(env):
    """`/{episode}` 只吃一段，會跟同前綴下的固定單段路徑相撞。

    FastAPI 照註冊順序比對，所以 `/{episode}` 必須留在 publish_review 的最後；
    `/calendar` 則是靠 app.py 先 include publish_calendar 擋在前面。這一條把兩件
    事都鎖住——它們一旦被吃掉，症狀是月曆頁變成「找不到這一集」，很難聯想到路由。
    """
    calendar = env.get("/bridge/publish/calendar")
    assert calendar.status_code == 200
    assert "20991231" not in calendar.text

    # /vault-thumb 沒帶參數會是 4xx，但必須是它自己的 handler 回的，不是 404 集數
    vault_thumb = env.get("/bridge/publish/vault-thumb")
    assert vault_thumb.status_code != 404 or "沒有登錄的成品" not in vault_thumb.text


def test_cut_page_still_opens(env):
    """第三層沒有改動——分層只是把入口拆開。"""
    assert env.get(f"/bridge/publish/{EP_A}/punch-L03").status_code == 200


def test_login_is_still_required(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_PASSWORD", PASSWORD)
    monkeypatch.setenv("WEB_SECRET", SECRET)
    monkeypatch.delenv("NAKAMA_DEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "state.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.publish_review as pub_module

    importlib.reload(auth_module)
    importlib.reload(pub_module)
    importlib.reload(app_module)
    client = TestClient(app_module.app, follow_redirects=False)

    assert client.get(f"/bridge/publish/{EP_A}").status_code == 302
