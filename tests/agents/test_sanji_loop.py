"""Sanji level projection wiring tests."""

from agents.sanji import rules
from agents.sanji.loop import level_fields
from agents.sanji.reconcile import _restamp_levels


def _threshold(level: int) -> int:
    return dict(rules.LEVEL_THRESHOLDS)[level]


def test_level_fields_keeps_existing_contract_and_adds_empty_tier_below_threshold():
    fields = level_fields(0)

    assert set(fields) == {
        "level_after",
        "level_label",
        "tier_label",
        "level_min_xp",
        "next_level_xp",
        "next_level_label",
    }
    assert fields["tier_label"] == rules.tier_for(fields["level_after"]) == ""


def test_level_fields_changes_tier_at_first_configured_boundary():
    tier_level = min(rules.TIER_OF_LEVEL)
    boundary = _threshold(tier_level)

    below = level_fields(boundary - 1)
    reached = level_fields(boundary)

    assert below["tier_label"] == rules.tier_for(tier_level - 1)
    assert reached["level_after"] == tier_level
    assert reached["tier_label"] == rules.tier_for(tier_level)
    assert reached["tier_label"] != below["tier_label"]


def test_level_fields_uses_rules_for_highest_level_tier():
    highest_level = max(level for level, _ in rules.LEVEL_THRESHOLDS)
    fields = level_fields(_threshold(highest_level))

    assert fields["level_after"] == highest_level
    assert fields["tier_label"] == rules.tier_for(highest_level)


def test_reconcile_backfills_missing_tier_for_existing_balance():
    tier_level = min(rules.TIER_OF_LEVEL)
    xp_total = _threshold(tier_level)
    wanted = level_fields(xp_total)

    class Client:
        def __init__(self):
            self.calls = 0
            self.restamped = []

        def balances(self, _after_user_id, *, limit):
            assert limit == 200
            self.calls += 1
            if self.calls > 1:
                return {"items": []}
            return {
                "items": [
                    {
                        "user_id": 42,
                        "xp_total": xp_total,
                        "level": wanted["level_after"],
                        "level_label": wanted["level_label"],
                        "tier_label": "",
                        "level_min_xp": wanted["level_min_xp"],
                        "next_level_xp": wanted["next_level_xp"],
                        "next_level_label": wanted["next_level_label"],
                    }
                ]
            }

        def restamp_levels(self, items):
            self.restamped.extend(items)
            return {"updated": len(items)}

    client = Client()
    result = _restamp_levels(None, client, None)

    assert result == {"scanned": 1, "restamped": 1}
    assert client.restamped == [{"user_id": 42, **wanted}]
