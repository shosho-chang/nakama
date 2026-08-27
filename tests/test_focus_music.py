"""Tests for thousand_sunny.routers.focus_music (修修 2026-08-25).

The pomodoro dock's focus-music picker: /random degrades to available:false when
the library is missing (the timer must never block on music), /file serves only
files directly inside FOCUS_MUSIC_DIR (traversal-guarded).
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))
    monkeypatch.setenv("NAKAMA_RESEARCH_CACHE_DIR", str(tmp_path / "research_cache"))
    (tmp_path / "vault" / "TaskNotes" / "Tasks").mkdir(parents=True)

    music = tmp_path / "music"
    music.mkdir()
    (music / "a-blanket-of-stars-all-ambient-main-version-44352-05-27.mp3").write_bytes(
        b"ID3fake-mp3-bytes"
    )
    (music / "notes.txt").write_text("not audio", encoding="utf-8")
    monkeypatch.setenv("FOCUS_MUSIC_DIR", str(music))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.focus_music as fm_module

    importlib.reload(auth_module)
    importlib.reload(fm_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


class TestRandom:
    def test_picks_a_track_with_cleaned_title(self, client):
        d = client.get("/bridge/focus-music/random").json()
        assert d["available"] is True
        # Envato provenance tail stripped, kebab de-hyphenated
        assert d["title"] == "a blanket of stars all ambient"
        assert d["url"].startswith("/bridge/focus-music/file/")
        assert "notes.txt" not in d["url"]  # non-audio never picked

    def test_unconfigured_dir_degrades_not_errors(self, client, monkeypatch):
        monkeypatch.delenv("FOCUS_MUSIC_DIR")
        assert client.get("/bridge/focus-music/random").json() == {"available": False}

    def test_missing_dir_degrades_not_errors(self, client, monkeypatch, tmp_path):
        monkeypatch.setenv("FOCUS_MUSIC_DIR", str(tmp_path / "nope"))
        assert client.get("/bridge/focus-music/random").json() == {"available": False}


class TestKinds:
    """修修 2026-08-25: focus / uplifting 分類子資料夾（prefix 比對，如「focus music」）。"""

    def _lib(self, tmp_path, monkeypatch):
        lib = tmp_path / "lib"
        (lib / "focus music").mkdir(parents=True)
        (lib / "uplifting music").mkdir()
        (lib / "focus music" / "calm-track.mp3").write_bytes(b"calm")
        (lib / "uplifting music" / "hype-track.mp3").write_bytes(b"hype")
        monkeypatch.setenv("FOCUS_MUSIC_DIR", str(lib))
        return lib

    def test_kind_pools_resolve_to_subdirs(self, client, monkeypatch, tmp_path):
        self._lib(tmp_path, monkeypatch)
        up = client.get("/bridge/focus-music/random?kind=uplifting").json()
        assert up["available"] is True
        assert "hype-track" in up["url"] and "kind=uplifting" in up["url"]
        r = client.get(up["url"])
        assert r.status_code == 200 and r.content == b"hype"
        fo = client.get("/bridge/focus-music/random?kind=focus").json()
        assert "calm-track" in fo["url"]

    def test_kind_pools_are_isolated(self, client, monkeypatch, tmp_path):
        self._lib(tmp_path, monkeypatch)
        # a focus track must not be reachable through the uplifting pool
        assert (
            client.get("/bridge/focus-music/file/calm-track.mp3?kind=uplifting").status_code == 404
        )

    def test_flat_library_has_no_uplifting_pool(self, client):
        # the base fixture is a flat dir — focus keeps working, uplifting degrades
        assert client.get("/bridge/focus-music/random?kind=uplifting").json() == {
            "available": False
        }
        assert client.get("/bridge/focus-music/random?kind=focus").json()["available"] is True

    def test_bogus_kind_degrades(self, client):
        assert client.get("/bridge/focus-music/random?kind=..").json() == {"available": False}


class TestFile:
    def test_serves_audio_with_media_type(self, client):
        url = client.get("/bridge/focus-music/random").json()["url"]
        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/mpeg")
        assert r.content == b"ID3fake-mp3-bytes"

    def test_traversal_rejected(self, client, tmp_path):
        (tmp_path / "secret.mp3").write_bytes(b"outside")  # sibling of the library
        r = client.get("/bridge/focus-music/file/..%2Fsecret.mp3")
        assert r.status_code in (400, 404)  # 404 from guard, or 400 from path parsing

    def test_non_audio_file_rejected(self, client):
        assert client.get("/bridge/focus-music/file/notes.txt").status_code == 404

    def test_unknown_file_404(self, client):
        assert client.get("/bridge/focus-music/file/ghost.mp3").status_code == 404
