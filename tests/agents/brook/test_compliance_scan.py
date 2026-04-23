"""Tests for agents/brook/compliance_scan.py."""

from __future__ import annotations

from agents.brook.compliance_scan import (
    scan_draft_compliance,
    scan_publish_gate,
)


def test_clean_text_passes_both_scans():
    text = "這篇文章介紹一些生活作息建議，內容僅供參考，請諮詢醫師。"
    gate = scan_publish_gate(text)
    assert gate.medical_claim is False
    assert gate.absolute_assertion is False
    assert gate.matched_terms == []

    snapshot = scan_draft_compliance(text)
    assert snapshot.claims_no_therapeutic_effect is True
    assert snapshot.has_disclaimer is True
    assert snapshot.detected_blacklist_hits == []


def test_medical_claim_detected():
    text = "這個方法可以治癒癌症。"
    gate = scan_publish_gate(text)
    assert gate.medical_claim is True
    assert any("治癒" in term for term in gate.matched_terms)

    snapshot = scan_draft_compliance(text)
    assert snapshot.claims_no_therapeutic_effect is False
    assert snapshot.detected_blacklist_hits


def test_absolute_assertion_detected():
    text = "這個方法 100% 有效！"
    gate = scan_publish_gate(text)
    assert gate.absolute_assertion is True
    assert gate.matched_terms


def test_both_flags_can_trigger_together():
    text = "這個方法絕對有效，可以治癒糖尿病。"
    gate = scan_publish_gate(text)
    assert gate.medical_claim is True
    assert gate.absolute_assertion is True


def test_missing_disclaimer_flagged():
    text = "這篇文章介紹生活作息，無醫療免責聲明。"
    snapshot = scan_draft_compliance(text)
    assert snapshot.has_disclaimer is False


def test_matched_terms_deduplicated():
    """同一 pattern 多次命中應去重呈現。"""
    text = "治癒癌症的秘方…再次強調可以治癒癌症！"
    gate = scan_publish_gate(text)
    # 兩次命中，matched_terms 只含一份
    assert len([t for t in gate.matched_terms if "癌症" in t]) == 1


def test_english_disclaimer_variant():
    text = "Disclaimer: This is not medical advice."
    snapshot = scan_draft_compliance(text)
    assert snapshot.has_disclaimer is True


def test_two_scans_agree_on_same_clean_input():
    """Brook enqueue + Usopp claim 各跑一次，結果必須一致（ADR-005b §10 defense in depth）。"""
    text = "純科普段落，沒有任何療效宣稱或絕對語。"
    gate_a = scan_publish_gate(text)
    gate_b = scan_publish_gate(text)
    assert gate_a.model_dump() == gate_b.model_dump()


def test_two_scans_agree_on_same_flagged_input():
    text = "這個方法 100% 有效，可以根治憂鬱。"
    gate_a = scan_publish_gate(text)
    gate_b = scan_publish_gate(text)
    assert gate_a.model_dump() == gate_b.model_dump()
