from __future__ import annotations

import pytest

from shared.thumbnail_reference_templates import REFERENCE_TEMPLATE_IDS
from shared.thumbnail_template_contracts import (
    PercentBox,
    find_overlap_rule,
    format_template_contract_for_prompt,
    get_template_contract,
    list_template_contracts,
    missing_contract_ids,
)


def test_every_reference_template_has_contract():
    assert missing_contract_ids() == set()
    assert {contract.template_id for contract in list_template_contracts()} == set(
        REFERENCE_TEMPLATE_IDS
    )


def test_contracts_have_required_slots_and_eval_rubric():
    for template_id in REFERENCE_TEMPLATE_IDS:
        contract = get_template_contract(template_id)
        assert contract.host_slots
        assert contract.payload_slots
        assert contract.z_order_policy[0] == "background"
        assert "reference_template_fit" in contract.vision_eval_rubric
        assert contract.cheap_eval_gates["max_protected_text_overlap_iou"] <= 0.02


def test_benefit_list_allows_hand_over_card_but_not_keywords():
    contract = get_template_contract("shosho_benefit_list_card")

    card_rule = find_overlap_rule(
        contract,
        foreground_role="host_hand",
        background_role="payload_card",
    )
    text_rule = find_overlap_rule(
        contract,
        foreground_role="host_hand",
        background_role="payload_text",
    )

    assert card_rule is not None
    assert card_rule.status == "allowed"
    assert card_rule.max_iou > 0.3
    assert card_rule.max_protected_text_iou <= 0.02

    assert text_rule is not None
    assert text_rule.status == "forbidden"
    assert text_rule.max_iou <= 0.02


def test_percent_box_converts_to_canvas_pixels_without_clamping():
    box = PercentBox(-0.05, 0.10, 0.50, 1.10)

    assert box.to_pixel_box((1280, 720)) == (-64, 72, 640, 792)


def test_prompt_format_is_compact_and_slot_based():
    text = format_template_contract_for_prompt("shosho_benefit_list_card")

    assert "template_id: shosho_benefit_list_card" in text
    assert "primary_host_slots" in text
    assert "primary_payload_slots" in text
    assert "host_hand over payload_card: allowed" in text
    assert len(text) < 2200


def test_unknown_template_contract_raises():
    with pytest.raises(KeyError):
        get_template_contract("missing-template")
