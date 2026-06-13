"""N531 — llm_router override store + registry resolution 測試。

聚焦新增的解析層（override > env > registry > DEFAULT_MODELS）與 override store
的 roundtrip / 即時生效（mtime cache）。provider prefix 推導屬既有行為，僅輕點。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import shared.llm_router as r


@pytest.fixture(autouse=True)
def _isolated_overrides(tmp_path: Path, monkeypatch):
    """每測試指向獨立 override 檔，並清掉模組級 mtime cache，避免互相污染。"""
    monkeypatch.setenv("NAKAMA_MODEL_OVERRIDES", str(tmp_path / "model_overrides.json"))
    # 清掉可能殘留的 MODEL_* env，讓解析層乾淨
    for key in list(__import__("os").environ):
        if key.startswith("MODEL_") or key.startswith("AUTH_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(r, "_overrides_mtime", -1.0)
    monkeypatch.setattr(r, "_overrides_cache", {})
    yield


# ── override store roundtrip ──────────────────────────────────────────────────


def test_override_roundtrip():
    assert r.get_override("robin", "concept_merge") is None
    r.set_override("robin", "concept_merge", "gemini-2.5-pro")
    assert r.get_override("robin", "concept_merge") == "gemini-2.5-pro"
    r.clear_override("robin", "concept_merge")
    assert r.get_override("robin", "concept_merge") is None


def test_override_case_insensitive_agent():
    r.set_override("Robin", "default", "grok-4-fast")
    assert r.get_override("robin", "default") == "grok-4-fast"


def test_override_corrupt_file_returns_empty(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("NAKAMA_MODEL_OVERRIDES", str(p))
    monkeypatch.setattr(r, "_overrides_mtime", -1.0)
    assert r.get_override("robin", "default") is None  # 壞檔 → None，不丟


# ── 解析優先序：override > env > registry > DEFAULT_MODELS ─────────────────────


def test_resolution_registry_default_when_nothing_set():
    # robin/concept_merge 在 registry 宣告為 opus-4-7
    assert r.get_model("robin", "concept_merge") == "claude-opus-4-7"


def test_resolution_env_beats_registry(monkeypatch):
    monkeypatch.setenv("MODEL_ROBIN_CONCEPT_MERGE", "grok-4-fast")
    assert r.get_model("robin", "concept_merge") == "grok-4-fast"


def test_resolution_override_beats_env(monkeypatch):
    monkeypatch.setenv("MODEL_ROBIN_CONCEPT_MERGE", "grok-4-fast")
    r.set_override("robin", "concept_merge", "gemini-2.5-pro")
    assert r.get_model("robin", "concept_merge") == "gemini-2.5-pro"


def test_resolution_falls_through_to_default_models():
    # 未登記的 (agent, task) → DEFAULT_MODELS[task] / [default]
    assert r.get_model("nami", "tool_use") == r.DEFAULT_MODELS["tool_use"]
    assert r.get_model("nami", "totally_unknown_task") == r.DEFAULT_MODELS["default"]


def test_override_takes_effect_live_via_mtime():
    # 不重啟 process，set_override 後 get_model 立即反映（mtime cache reload）
    before = r.get_model("robin", "kb_search")
    assert before == "claude-haiku-4-5-20251001"
    r.set_override("robin", "kb_search", "claude-opus-4-7")
    assert r.get_model("robin", "kb_search") == "claude-opus-4-7"


# ── registry + list_model_sites ───────────────────────────────────────────────


def test_registry_default_lookup():
    assert r.registry_default("robin", "daily_review") == "claude-sonnet-4-5-20250929"
    assert r.registry_default("robin", "no_such_task") is None
    assert r.registry_default(None, "default") is None


def test_list_model_sites_covers_registry_and_marks_source(monkeypatch):
    rows = r.list_model_sites()
    keys = {(row["agent"], row["task"]) for row in rows}
    assert ("robin", "concept_merge") in keys
    # default source 標 registry
    cm = next(x for x in rows if x["agent"] == "robin" and x["task"] == "concept_merge")
    assert cm["source"] == "registry"
    assert cm["model"] == "claude-opus-4-7"
    assert cm["provider"] == "anthropic"
    # override 後 source 改 override
    r.set_override("robin", "concept_merge", "gemini-2.5-pro")
    cm2 = next(
        x for x in r.list_model_sites() if x["agent"] == "robin" and x["task"] == "concept_merge"
    )
    assert cm2["source"] == "override"
    assert cm2["provider"] == "google"


def test_list_model_sites_includes_manual_override_outside_registry():
    r.set_override("usopp", "newsletter", "claude-haiku-4-5")
    rows = r.list_model_sites()
    extra = next((x for x in rows if x["agent"] == "usopp" and x["task"] == "newsletter"), None)
    assert extra is not None
    assert extra["source"] == "override"
    assert extra["model"] == "claude-haiku-4-5"


# ── 壞檔 robustness（review 抓到：set/clear 裸 json.loads 會 500）─────────────


def test_set_override_on_corrupt_file_recovers(tmp_path, monkeypatch):
    p = tmp_path / "ov.json"
    p.write_text("{corrupt", encoding="utf-8")
    monkeypatch.setenv("NAKAMA_MODEL_OVERRIDES", str(p))
    monkeypatch.setattr(r, "_overrides_mtime", -1.0)
    # 壞檔不該 raise；set 後可讀回（壞內容被當空 dict 重建）
    r.set_override("robin", "concept_merge", "gemini-2.5-pro")
    assert r.get_override("robin", "concept_merge") == "gemini-2.5-pro"


def test_clear_override_on_corrupt_file_no_raise(tmp_path, monkeypatch):
    p = tmp_path / "ov.json"
    p.write_text("{corrupt", encoding="utf-8")
    monkeypatch.setenv("NAKAMA_MODEL_OVERRIDES", str(p))
    monkeypatch.setattr(r, "_overrides_mtime", -1.0)
    r.clear_override("robin", "concept_merge")  # 不該 raise


def test_clear_override_noop_does_not_write(tmp_path, monkeypatch):
    p = tmp_path / "ov.json"
    monkeypatch.setenv("NAKAMA_MODEL_OVERRIDES", str(p))
    monkeypatch.setattr(r, "_overrides_mtime", -1.0)
    r.clear_override("robin", "concept_merge")  # 無此 override
    assert not p.exists()  # 不曾寫檔（避免 spurious mtime）
