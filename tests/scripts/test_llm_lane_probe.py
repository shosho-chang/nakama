"""Tests for scripts/llm_lane_probe.py（ADR-070 S0 探針）。

不起任何子進程、不呼叫 LLM：``claude_agent_sdk.query`` 與 ``one_call`` 都換成
「被呼叫就讓測試失敗」的版本，``--dry-run`` 必須在這種條件下照樣跑完。
"""

from __future__ import annotations

import json
import os

import claude_agent_sdk
import pytest

from scripts import llm_lane_probe as probe

_OAUTH = "sk-ant-oat01-SECRET-oauth"
_OPENROUTER = "sk-or-v1-SECRET-openrouter"


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """不讀 .env、不動全域 log handler、任何 LLM / CLI 呼叫都直接讓測試失敗。"""
    monkeypatch.setattr(probe, "_load_env", lambda: None)
    monkeypatch.setattr(probe, "_route_logs_to_stderr", lambda: None)

    def _explode(*a, **kw):
        raise AssertionError("dry-run / 單元測試不准呼叫 LLM 或 CLI")

    monkeypatch.setattr(claude_agent_sdk, "query", _explode)
    monkeypatch.setattr(probe, "one_call", _explode)
    monkeypatch.setattr(probe, "run_cli_version", _explode)
    for key in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "NAMI_SDK_OAUTH_TOKEN",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def _run(capsys, argv: list[str]) -> tuple[int, dict | None, str]:
    code = probe.main(argv)
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else None
    return code, payload, captured.out + captured.err


# ── aliases：binary 解析 ────────────────────────────────────────────────

_FAKE_BLOB = (
    b"\x00\x11latest_per_family\x00\x00"  # 字串表裡的裸名字：不是物件
    b"junk;Lrg={schema_version:0,latest_per_family:{},alias_migration:{}};"  # 空的預設值
    b'xx aliases:{opus:{default:"claude-opus-5",per_provider:{bedrock:"claude-opus-5",'
    b'gateway:"claude-opus-4-7"}},haiku:{default:"claude-haiku-4-5"}},defaults:{},'
    b'best:"fable",latest_per_family:{fable:"claude-fable-5",opus:"claude-opus-5",'
    b'sonnet:"claude-sonnet-5",haiku:"claude-haiku-4-5"},alias_migration:{}}});'
    b"schema:latest_per_family:Gn(N(),N()).default({})"
)


def test_parse_alias_tables_takes_the_non_empty_match():
    tables = probe.parse_alias_tables(_FAKE_BLOB)
    assert tables["latest_per_family"] == {
        "fable": "claude-fable-5",
        "opus": "claude-opus-5",
        "sonnet": "claude-sonnet-5",
        "haiku": "claude-haiku-4-5",
    }
    assert tables["non_empty_matches"] == 1
    assert tables["other_matches"] == 1  # 那個 {} 預設值
    assert tables["aliases"]["opus"]["per_provider"]["gateway"] == "claude-opus-4-7"
    assert tables["aliases"]["haiku"] == {"default": "claude-haiku-4-5"}
    assert tables["best"] == "fable"


def test_parse_alias_tables_absent():
    tables = probe.parse_alias_tables(b"nothing here latest_per_family:{}")
    assert tables["latest_per_family"] is None
    assert tables["aliases"] is None


def test_balanced_object_ignores_braces_inside_strings():
    blob = b'x{a:"}{",b:{c:"\\"}"}}tail'
    assert probe._balanced_object(blob, 1) == b'{a:"}{",b:{c:"\\"}"}}'
    assert probe.js_object_to_python('{a:"}{",b:{c:"x"}}') == {"a": "}{", "b": {"c": "x"}}


def test_aliases_subcommand_on_fake_binary(tmp_path, capsys, monkeypatch):
    fake_cli = tmp_path / "claude.exe"
    fake_cli.write_bytes(_FAKE_BLOB)
    monkeypatch.setattr(
        probe, "run_cli_version", lambda path: {"ok": True, "stdout": "9.9.9 (Claude Code)"}
    )
    code, payload, _ = _run(capsys, ["aliases", "--cli-path", str(fake_cli)])
    assert code == 0
    assert payload["llm_calls"] == 0
    assert payload["cli_version"]["stdout"] == "9.9.9 (Claude Code)"
    assert payload["latest_per_family"]["haiku"] == "claude-haiku-4-5"
    assert payload["binary_bytes"] == len(_FAKE_BLOB)


def test_aliases_dry_run_does_not_execute_cli(tmp_path, capsys):
    fake_cli = tmp_path / "claude"
    fake_cli.write_bytes(_FAKE_BLOB)
    code, payload, _ = _run(capsys, ["aliases", "--dry-run", "--cli-path", str(fake_cli)])
    assert code == 0
    assert payload["dry_run"] is True
    assert "latest_per_family" not in payload  # 沒有真的掃
    assert payload["would"]


# ── --dry-run：不呼叫任何東西、不洩漏憑證 ──────────────────────────────


def test_latency_dry_run(capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _OAUTH)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    code, payload, raw = _run(capsys, ["latency", "--dry-run", "--n", "3"])
    assert code == 0
    assert payload["dry_run"] is True
    assert payload["llm_calls"] == 3
    assert payload["call"] == {
        "model": "haiku",
        "tools": [],
        "setting_sources": [],
        "max_turns": 1,
    }
    plan = payload["env_plan"]
    assert plan["lane"] == "subscription"
    assert plan["auth_source"] == "env:CLAUDE_CODE_OAUTH_TOKEN"
    assert set(plan["removed_from_inherited_env"]) == {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"}
    assert plan["options_env"] == {"CLAUDE_CODE_OAUTH_TOKEN": "<redacted>", "ANTHROPIC_API_KEY": ""}
    assert _OAUTH not in raw


def test_structured_dry_run_plans_three_attempts(capsys):
    code, payload, _ = _run(capsys, ["structured", "--dry-run"])
    assert code == 0
    assert payload["llm_calls"] == 3
    planned = [(c["schema_label"], c["max_turns"]) for c in payload["calls_planned"]]
    assert planned == [("draft-07", 1), ("draft-07", 3), ("2020-12", 3)]
    schemas = [c["output_format"]["schema"]["$schema"] for c in payload["calls_planned"]]
    assert schemas[0].startswith("http://json-schema.org/draft-07")
    assert schemas[2] == "https://json-schema.org/draft/2020-12/schema"


def test_openrouter_alias_dry_run_with_key(capsys, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", _OPENROUTER)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _OAUTH)
    code, payload, raw = _run(capsys, ["openrouter-alias", "--dry-run"])
    assert code == 0
    plan = payload["env_plan"]
    assert plan["lane"] == "openrouter"
    assert plan["removed_from_inherited_env"] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert plan["options_env"]["ANTHROPIC_BASE_URL"] == probe.OPENROUTER_BASE_URL
    assert plan["options_env"]["ANTHROPIC_AUTH_TOKEN"] == "<redacted>"
    assert plan["options_env"]["ANTHROPIC_API_KEY"] == ""
    assert plan["config_dir_isolated"] is True
    assert _OAUTH not in raw and _OPENROUTER not in raw


@pytest.mark.parametrize("dry_run", [True, False])
def test_openrouter_alias_without_key_exits_2(capsys, monkeypatch, dry_run):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _OAUTH)  # 有 OAuth 也不准拿來用
    argv = ["openrouter-alias", *(["--dry-run"] if dry_run else [])]
    code, payload, raw = _run(capsys, argv)
    assert code == 2
    assert payload is None
    assert "OPENROUTER_API_KEY" in raw


# ── fail-closed env 組裝 ────────────────────────────────────────────────


def _environ(**extra: str) -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "CLAUDE_CODE_OAUTH_TOKEN": _OAUTH,
        "NAMI_SDK_OAUTH_TOKEN": "sk-ant-oat01-nami",
        "ANTHROPIC_API_KEY": "sk-ant-api-dead",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        **extra,
    }


def test_openrouter_child_env_has_no_oauth_token():
    environ = _environ(OPENROUTER_API_KEY=_OPENROUTER)
    plan = probe.build_openrouter_plan(environ, config_dir="/tmp/empty")
    child = plan.child_env(environ)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in child
    assert "NAMI_SDK_OAUTH_TOKEN" not in child
    assert "CLAUDE_CODE_USE_BEDROCK" not in child
    assert _OAUTH not in child.values()
    assert child["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert child["ANTHROPIC_AUTH_TOKEN"] == _OPENROUTER
    assert child["ANTHROPIC_API_KEY"] == ""
    assert child["CLAUDE_CONFIG_DIR"] == "/tmp/empty"
    assert child["PATH"] == "/usr/bin"


@pytest.mark.parametrize("key", ["", "   "])
def test_openrouter_plan_refuses_without_key(key):
    with pytest.raises(probe.ProbeRefused) as excinfo:
        probe.build_openrouter_plan(_environ(OPENROUTER_API_KEY=key))
    assert excinfo.value.code == 2


@pytest.mark.parametrize("bad_key", [_OAUTH, "sk-ant-oat01-another"])
def test_openrouter_plan_refuses_oauth_looking_key(bad_key):
    """OPENROUTER_API_KEY 誤設成 OAuth token（或長得像）也要擋。

    那等於把 OAuth token 送去 OpenRouter。
    """
    with pytest.raises(probe.ProbeRefused) as excinfo:
        probe.build_openrouter_plan(_environ(OPENROUTER_API_KEY=bad_key))
    assert excinfo.value.code == 3


def test_leak_check_after_scrub_still_compares_values():
    environ = _environ(OPENROUTER_API_KEY=_OPENROUTER)
    plan = probe.build_openrouter_plan(environ)
    oauth_values = probe.oauth_token_values(environ)
    scrubbed = {k: v for k, v in environ.items() if k not in plan.remove}
    scrubbed["SOME_OTHER_VAR"] = _OAUTH  # token 換個名字偷渡
    with pytest.raises(probe.ProbeRefused):
        probe.assert_no_oauth_leak(plan, scrubbed, oauth_values)


def test_subscription_child_env_strips_gateway_vars(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _OAUTH)
    environ = _environ(ANTHROPIC_AUTH_TOKEN="x", ANTHROPIC_BASE_URL="https://openrouter.ai/api")
    plan = probe.build_subscription_plan(environ)
    child = plan.child_env(environ)
    assert "ANTHROPIC_AUTH_TOKEN" not in child
    assert "ANTHROPIC_BASE_URL" not in child
    assert "CLAUDE_CODE_USE_BEDROCK" not in child
    assert child["ANTHROPIC_API_KEY"] == ""
    assert child["CLAUDE_CODE_OAUTH_TOKEN"] == _OAUTH


def test_subscription_plan_without_token_reports_cli_login(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    plan = probe.build_subscription_plan({})
    assert plan.options_env == {}
    assert plan.auth_source.startswith("cli_login")
    assert "不存在" in plan.auth_source


def test_scrubbed_environ_restores(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    with probe.scrubbed_environ(("ANTHROPIC_BASE_URL", "NOT_SET_KEY")) as removed:
        assert removed == ["ANTHROPIC_BASE_URL"]
        assert "ANTHROPIC_BASE_URL" not in os.environ
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://example.invalid"


# ── 統計與證據收集 ──────────────────────────────────────────────────────


def test_percentile_nearest_rank():
    values = [5.0, 1.0, 3.0, 2.0, 4.0]
    assert probe.percentile(values, 50) == 3.0
    assert probe.percentile(values, 95) == 5.0
    assert probe.percentile([], 50) is None


def test_collect_message_records_models_rate_limits_and_first_result():
    rec = probe.new_call_record()

    class AssistantMessage:  # noqa: N801
        model = "claude-haiku-4-5-20251001"
        error = None

    class RateLimitEvent:  # noqa: N801
        status = "allowed_warning"

    def result(text):
        return type(
            "ResultMessage",
            (),
            {
                "subtype": "success",
                "is_error": False,
                "num_turns": 2,
                "total_cost_usd": 0.001,
                "structured_output": {"answer": 5, "word": "five"},
                "result": text,
            },
        )()

    probe.collect_message(rec, AssistantMessage(), 0.5)
    probe.collect_message(rec, RateLimitEvent(), 0.6)
    probe.collect_message(rec, result("first"), 1.23456)
    probe.collect_message(rec, result("second"), 2.0)
    assert rec["models"] == ["claude-haiku-4-5-20251001"]
    assert rec["rate_limit_events"][0]["status"] == "allowed_warning"
    assert rec["ok"] is True
    assert rec["first_result_s"] == 1.235
    assert rec["result"]["num_turns"] == 2
    assert rec["result"]["structured_output_populated"] is True
    assert rec["result"]["result_text"] == "first"
    assert rec["extra_results"] == 1


def test_rss_sampler_reports_reason_without_psutil(monkeypatch):
    monkeypatch.setattr(probe, "psutil_unavailable_reason", lambda: "psutil 無法 import：X")
    with probe.RssSampler() as sampler:
        pass
    assert sampler.result() is None
    assert sampler.unavailable_reason == "psutil 無法 import：X"


def test_rejects_n_below_one(capsys):
    code, payload, _ = _run(capsys, ["latency", "--dry-run", "--n", "0"])
    assert code == 2 and payload is None


def test_parser_defaults():
    args = probe.build_parser().parse_args(["latency"])
    assert (args.model, args.n, args.timeout, args.dry_run) == ("haiku", 5, 120.0, False)
