"""A-share research metadata must not inherit crypto seed defaults."""
from app.services.market.assets import normalize_asset_row
from app.services.market.symbol_search import dedupe_symbol_results


def test_ashare_logical_asset_keeps_identity_and_repairs_legacy_crypto_class():
    source = {"id": 111, "asset_key": "cnstock:000001:cnstock", "market": "CNStock",
              "symbol": "000001", "name": "平安银行", "asset_class": "crypto",
              "product_type": "cnstock"}
    row = normalize_asset_row(source)
    assert row["asset_id"] == row["id"] == 111
    assert row["asset_key"] == source["asset_key"]
    assert row["market"] == "CNStock" and row["symbol"] == "000001"
    assert row["asset_class"] == "equity" and row["product_type"] == "cnstock"
    assert source["asset_class"] == "crypto"


def test_missing_ashare_metadata_defaults_to_equity_without_inventing_asset_ids():
    row = dedupe_symbol_results([{"market": "CNStock", "symbol": "000001", "name": "平安银行"}], 10)[0]
    assert row["asset_class"] == "equity" and row["product_type"] == "cnstock"
    assert "asset_id" not in row


def test_explicit_equity_subclass_and_product_identity_are_preserved():
    source = {"id": 42, "market": "CNStock", "symbol": "510300", "asset_class": "etf", "product_type": "etf"}
    assert normalize_asset_row(source)["asset_class"] == "etf"
    assert normalize_asset_row(source)["product_type"] == "etf"
    row = dedupe_symbol_results([source], 10)[0]
    assert row["asset_class"] == row["product_type"] == "etf"


def test_identical_tickers_in_different_markets_remain_distinct():
    rows = dedupe_symbol_results([
        {"market": "CNStock", "symbol": "000001"},
        {"market": "HKStock", "symbol": "000001"},
    ], 10)
    assert len(rows) == 2
    assert {row["market"] for row in rows} == {"CNStock", "HKStock"}


def test_crypto_product_classification_and_exact_context_are_unchanged():
    source = {"id": 7, "market": "Crypto", "symbol": "AAPL/USDT",
              "asset_class": "equity", "product_type": "stock_perpetual",
              "exchange_id": "okx", "market_type": "swap", "instrument_id": "AAPL-USDT-SWAP"}
    row = dedupe_symbol_results([source], 10)[0]
    assert row["asset_class"] == "equity" and row["product_type"] == "stock_perpetual"
    assert (row["market"], row["exchange_id"], row["market_type"], row["instrument_id"]) == ("Crypto", "okx", "swap", "AAPL-USDT-SWAP")
    assert normalize_asset_row(source)["asset_id"] == 7
    bare = dedupe_symbol_results([{"market": "Crypto", "symbol": "BTC/USDT"}], 10)[0]
    assert bare["asset_class"] == bare["product_type"] == "crypto"
