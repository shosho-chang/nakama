"""Tests for shared.pricing — Claude model pricing lookup and cost calc."""

from __future__ import annotations

import pytest

from shared import pricing


def test_family_default_opus():
    p = pricing.get_pricing("claude-opus-4-7")
    assert p.input_usd_per_mtok == 15.0
    assert p.output_usd_per_mtok == 75.0


def test_family_default_sonnet():
    p = pricing.get_pricing("claude-sonnet-4-6")
    assert p.input_usd_per_mtok == 3.0
    assert p.output_usd_per_mtok == 15.0


def test_family_default_haiku():
    p = pricing.get_pricing("claude-haiku-4-5")
    assert p.input_usd_per_mtok == 0.80
    assert p.output_usd_per_mtok == 4.0


def test_unknown_model_fallback_to_zero():
    p = pricing.get_pricing("gpt-4o")
    assert p.input_usd_per_mtok == 0.0
    assert p.output_usd_per_mtok == 0.0


def test_env_override_applies_globally(monkeypatch):
    monkeypatch.setenv("NAKAMA_PRICING_INPUT_USD_PER_MTOK", "0.5")
    monkeypatch.setenv("NAKAMA_PRICING_OUTPUT_USD_PER_MTOK", "1.5")
    monkeypatch.setenv("NAKAMA_PRICING_CACHE_READ_USD_PER_MTOK", "0.05")
    monkeypatch.setenv("NAKAMA_PRICING_CACHE_WRITE_USD_PER_MTOK", "0.75")

    p = pricing.get_pricing("claude-opus-4-7")  # would normally be 15/75
    assert p.input_usd_per_mtok == 0.5
    assert p.output_usd_per_mtok == 1.5
    assert p.cache_read_usd_per_mtok == 0.05
    assert p.cache_write_usd_per_mtok == 0.75


def test_env_override_requires_all_four_vars(monkeypatch):
    monkeypatch.setenv("NAKAMA_PRICING_INPUT_USD_PER_MTOK", "0.5")
    # missing the other three

    p = pricing.get_pricing("claude-sonnet-4-6")
    # should fall back to family default
    assert p.input_usd_per_mtok == 3.0


def test_calc_cost_opus_example():
    # Opus: $15 in, $75 out per 1M
    # 1000 input + 200 output = 15*1000/1e6 + 75*200/1e6 = 0.015 + 0.015 = 0.030
    cost = pricing.calc_cost(
        "claude-opus-4-7",
        input_tokens=1000,
        output_tokens=200,
    )
    assert cost == pytest.approx(0.030, abs=1e-9)


def test_calc_cost_includes_cache_tokens():
    # Sonnet: $3/M input, $15/M output, $0.30/M cache_read, $3.75/M cache_write
    cost = pricing.calc_cost(
        "claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=1000,
        cache_write_tokens=500,
    )
    expected = (100 * 3 + 50 * 15 + 1000 * 0.30 + 500 * 3.75) / 1_000_000
    assert cost == pytest.approx(expected, abs=1e-9)


def test_calc_cost_unknown_model_is_zero():
    assert pricing.calc_cost("gpt-4o", input_tokens=10000, output_tokens=10000) == 0.0


def test_to_dict_shape():
    d = pricing.get_pricing("claude-haiku-4-5").to_dict()
    assert set(d.keys()) == {
        "input_usd_per_mtok",
        "output_usd_per_mtok",
        "cache_read_usd_per_mtok",
        "cache_write_usd_per_mtok",
    }
