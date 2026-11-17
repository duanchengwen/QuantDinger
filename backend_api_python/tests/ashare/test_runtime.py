import json

import pytest

from datetime import datetime, timezone

from app.ashare.runtime import load_config, reviewed_rules


def test_configuration_rejects_real_execution_and_automatic_promotion(tmp_path):
    path = tmp_path / "config.json"
    base = {"mode": "live", "tenant_id": "local", "account_id": "demo", "symbols": ["sz.000001"]}
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="paper"):
        load_config(path)
    base.update(mode="paper", ai_auto_promotion=True)
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="promotion"):
        load_config(path)


def test_rules_expire_at_shanghai_trading_date_boundary():
    config = {"rules": {"sz.000001": {"reviewed": True, "evidence": "operator-check",
              "trading_day": "2026-11-17", "is_trading_day": True, "st": False,
              "suspended": False, "limit_down": "9", "limit_up": "11"}}}
    assert reviewed_rules(config, "sz.000001", datetime(2026, 11, 17, 2, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="expired"):
        reviewed_rules(config, "sz.000001", datetime(2026, 11, 17, 16, tzinfo=timezone.utc))
