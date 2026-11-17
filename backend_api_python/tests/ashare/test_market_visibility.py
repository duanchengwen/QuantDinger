"""The paper deployment must expose A-shares in upstream research pickers."""
from pathlib import Path

from flask import Flask
import yaml

from app.markets.registry import list_market_modules
from app.routes.market import get_market_types


def deployment_env():
    root = Path(__file__).parents[3]
    config = yaml.safe_load((root / "deploy/ashare/compose.yml").read_text())
    return config["services"]["api"]["environment"]


def test_paper_compose_exposes_cn_in_both_official_market_discovery_paths(monkeypatch):
    env = deployment_env()
    monkeypatch.delenv("ENABLED_MARKETS", raising=False)
    # Apply the actual deployment flag rather than inventing a test-only one.
    if "SHOW_CN_STOCK" in env:
        monkeypatch.setenv("SHOW_CN_STOCK", env["SHOW_CN_STOCK"])
    else:
        monkeypatch.delenv("SHOW_CN_STOCK", raising=False)
    app = Flask(__name__)
    app.add_url_rule("/api/market/types", view_func=get_market_types)
    result = app.test_client().get("/api/market/types").get_json()
    assert "CNStock" in [row["value"] for row in result["data"]]
    cn = next(row for row in list_market_modules(env={"SHOW_CN_STOCK": env.get("SHOW_CN_STOCK", "")})
              if row["key"] == "CNStock")
    assert cn["enabled"] is True
    assert "research" in cn["features"]
    assert "live" not in cn["features"]
    for flag in ("LIVE_TRADING_ENABLED", "AGENT_LIVE_TRADING_ENABLED", "AI_AUTO_PROMOTION_ENABLED"):
        assert env[flag] == "false"


def test_upstream_global_default_and_explicit_whitelist_are_preserved(monkeypatch):
    monkeypatch.delenv("ENABLED_MARKETS", raising=False)
    monkeypatch.delenv("SHOW_CN_STOCK", raising=False)
    app = Flask(__name__)
    with app.test_request_context():
        assert "CNStock" not in [row["value"] for row in get_market_types().get_json()["data"]]
    monkeypatch.setenv("SHOW_CN_STOCK", "true")
    monkeypatch.setenv("ENABLED_MARKETS", "USStock")
    with app.test_request_context():
        assert "CNStock" not in [row["value"] for row in get_market_types().get_json()["data"]]
