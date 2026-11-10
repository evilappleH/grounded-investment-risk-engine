import json

import pytest

from investment_agents.price_watch.config import load_rules


def test_config_supports_multiple_rules(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"rules": [
        {"rule_id": "buy-1", "ticker": "aos", "side": "BUY_REVIEW", "at_or_below": 55},
        {"rule_id": "sell-1", "ticker": "AOS", "side": "SELL_REVIEW", "at_or_above": 85},
    ]}), encoding="utf-8")
    rules = load_rules(path)
    assert len(rules) == 2 and all(rule.ticker == "AOS" for rule in rules)


def test_duplicate_rule_ids_are_rejected(tmp_path):
    path = tmp_path / "rules.json"
    row = {"rule_id": "same", "ticker": "AOS", "side": "BUY_REVIEW", "at_or_below": 55}
    path.write_text(json.dumps({"rules": [row, row]}), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_rules(path)
