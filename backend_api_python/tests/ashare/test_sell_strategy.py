"""Sell strategies preserve separate human decisions, settlement and notional caps."""
from decimal import Decimal

import pytest

from app.ashare.strategy_versions import normalize_config
from app.ashare.strategy_versions import StrategyVersions
from app.ashare.web_store import WebStore
from test_managed_worker import account, instant, quote  # noqa: F401


BACKTEST_REFERENCE = {'run_id': 'b' * 32, 'result_sha256': 'c' * 64}


def sell_config(**changes):
    return {'mode': 'paper', 'kind': 'sell_once_above', 'symbol': 'sz.000001', 'quantity': 100,
            'threshold': '10', 'limit_price': '9.99', 'max_cash': '1200',
            'research_reference': {'run_id': 'd' * 32, 'input_hash': 'e' * 64, 'output_hash': 'f' * 64},
            'backtest_reference': dict(BACKTEST_REFERENCE), **changes}


def test_sell_config_requires_exact_backtest_reference_and_preserves_immutable_binding():
    normalized = normalize_config(sell_config())
    assert normalized['threshold'] == '10.00'
    assert normalized['backtest_reference'] == BACKTEST_REFERENCE
    for reference in (None, {}, {'run_id': 'b' * 31, 'result_sha256': 'c' * 64},
                      {**BACKTEST_REFERENCE, 'passed': True}):
        with pytest.raises(ValueError, match='backtest_reference'):
            normalize_config(sell_config(backtest_reference=reference))
    missing = sell_config()
    del missing['backtest_reference']
    with pytest.raises(ValueError, match='backtest_reference'):
        normalize_config(missing)
    missing = sell_config()
    del missing['research_reference']
    with pytest.raises(ValueError, match='research_reference'):
        normalize_config(missing)


def test_legacy_manual_buy_remains_compatible_and_optional_backtest_binding_is_strict():
    config = {key: value for key, value in sell_config(kind='buy_once_below').items()
              if key != 'backtest_reference'}
    assert normalize_config(config)['kind'] == 'buy_once_below'
    assert normalize_config({**config, 'backtest_reference': BACKTEST_REFERENCE})['backtest_reference'] == BACKTEST_REFERENCE


def prepared(account, *, quantity=200, cap='2400', seed_day=17, seed=True):
    from pathlib import Path
    from app.ashare.research import ResearchStore
    from app.ashare.schema import MIGRATIONS
    from app.ashare.strategy_backtest_store import save_result
    from test_research import Adapter, Themes, News, SYMBOL

    connection, _, _ = account
    with connection, connection.cursor() as cursor:
        for name in MIGRATIONS:
            cursor.execute((Path(__file__).parents[2] / 'migrations' / name).read_text())
    store = WebStore(connection, 'user_42')
    store.initialize('10000')
    store.save_settings([SYMBOL], 5, {'topics': ['ai']})
    research = ResearchStore(connection, 'user_42')
    research.request_run('sell-test-research')
    run = research.run_once(Adapter(), Themes(), News())
    reference = {key: run[value] for key, value in
                 (('run_id', 'id'), ('input_hash', 'input_hash'), ('output_hash', 'output_hash'))}
    config = normalize_config(sell_config(symbol=SYMBOL, quantity=quantity, max_cash=cap,
                                         research_reference=reference))
    report = {'status': 'candidate', 'execution_allowed': False, 'reason_code': 'controlled_execution_fixture',
              'evidence': {'source': 'synthetic_test_fixture_not_actual_backtest'}, 'metrics': {},
              'config': {key: value for key, value in config.items()
                         if key not in {'research_reference', 'backtest_reference'}}}
    with connection, connection.cursor() as cursor:
        config['backtest_reference'] = save_result(cursor, *store.key, run['id'], report)

    def tick(day=18, second=2, **changes):
        return {**quote(instant(day=day, second=second)), 'symbol': SYMBOL, **changes}

    def review(day=18, **changes):
        return store.review({'symbol': SYMBOL, 'reviewed': True, 'evidence': 'controlled test fixture',
                             'trading_day': f'2026-11-{day}', 'is_trading_day': True,
                             'suspended': False, 'st': False, 'limit_up': '11', 'limit_down': '9',
                             'reviewed_by': 42, 'approval_method': 'explicit_human_review',
                             'reviewed_at': instant(day=day).isoformat(), **changes})

    if seed:
        store.broker.place_order('manual-test-seed', SYMBOL, 'buy', 200, Decimal('10'),
                                 instant(day=seed_day), instant(day=seed_day).date())
        store.broker.on_quote(tick(day=seed_day, second=1), review(day=seed_day), instant(day=seed_day, second=1))
    rules = review()
    versions = StrategyVersions(connection, *store.key, clock=lambda: instant(day=18, second=1))
    draft = versions.create(config, 'sell-test-version', 42)

    def decide(action):
        return versions.decide(draft['id'], draft['config_sha256'], action, True, 42)

    def submit(second=2, **changes):
        now = instant(day=18, second=second)
        versions.clock = lambda: now
        value = tick(second=second, **changes)
        store.broker.on_quote(value, rules, now)
        return versions.submit_on_quote(value, rules)

    return store, versions, draft, rules, decide, submit, tick, review


def test_sell_only_fires_after_approval_activation_and_threshold_then_fills_once(account):  # noqa: F811
    from app.ashare.strategy_evaluation import evaluate_paper_strategy

    store, versions, draft, rules, decide, submit, tick, _ = prepared(account)
    assert draft['funding']['required_cash'] == 0
    assert draft['funding']['budget_requires_reviewed_upper_band'] is True
    assert submit() is None
    assert decide('approved')['execution_allowed'] is False
    assert submit(second=3) is None
    assert decide('activated')['execution_allowed'] is True
    assert submit(second=4, last='9.99') is None
    order = submit(second=5)
    assert order['side'] == 'sell' and order['status'] == 'open' and order['reserved_cash'] == 0
    now = instant(day=18, second=6)
    filled, = store.broker.on_quote(tick(second=6, bid='10.20', ask='10.21', last='10.20', bid_size=100), rules, now)
    assert filled['side'] == 'sell' and filled['quantity'] == 100 and filled['price'] == Decimal('10.20')
    assert filled['fee'] == Decimal('5.52')
    observed = evaluate_paper_strategy(store.connection, *store.key, draft['id'])
    assert observed['method'] == 'paper_sell_once_execution_v1'
    assert observed['execution']['side'] == 'sell' and observed['execution']['filled_quantity'] == 100
    assert observed['performance']['realized_pnl_cny'] is None
    assert observed['proposal'] is None
    assert observed['proposal_unavailable_reason'] == 'sell_expiry_review_not_supported'
    assert submit(second=7) is None
    assert versions.list(20)['versions'][0]['order']['filled'] == 200


@pytest.mark.parametrize('seed,seed_day', [(False, 17), (True, 18)])
def test_sell_cannot_short_or_dispose_same_day_shares(account, seed, seed_day):  # noqa: F811
    _, _, _, _, decide, submit, _, _ = prepared(account, seed=seed, seed_day=seed_day)
    decide('approved')
    decide('activated')
    with pytest.raises(ValueError, match='T\\+1'):
        submit()


def test_sell_submission_cap_uses_daily_upper_band_not_limit_floor(account):  # noqa: F811
    store, _, _, _, decide, submit, _, _ = prepared(account, quantity=100, cap='1050')
    decide('approved')
    decide('activated')
    with pytest.raises(ValueError, match='strategy_cash_cap_exceeded'):
        submit()
    assert len(store.broker.snapshot()['orders']) == 1


def test_sell_upper_band_absolute_notional_and_all_fees_fit_exact_cap(account):  # noqa: F811
    store, _, _, rules, decide, submit, tick, _ = prepared(account, cap='2206.12')
    decide('approved')
    decide('activated')
    submit()
    now = instant(day=18, second=3)
    fill, = store.broker.on_quote(tick(second=3, bid='11', ask='11', last='11', high='11'), rules, now)
    assert fill['price'] * fill['quantity'] + fill['fee'] == Decimal('2206.12')
    assert store.broker.snapshot()['positions'] == []


def test_sell_partial_fill_actual_cap_survives_changed_band_and_preserves_manual_sells(account):  # noqa: F811
    store, _, _, rules, decide, submit, tick, review = prepared(account, cap='2250')
    decide('approved')
    decide('activated')
    order = submit()
    now = instant(day=18, second=3)
    assert store.broker.on_quote(tick(second=3, bid='10', ask='10', bid_size=100), rules, now)[0]['quantity'] == 100
    changed = review(limit_up='13')
    now = instant(day=18, second=4)
    high = tick(second=4, bid='12.99', ask='13', last='12.99', limit_up='13')
    assert store.broker.on_quote(high, changed, now) == []
    state = store.broker.snapshot()
    blocked = [row for row in state['audit'] if row['event'] == 'strategy_order_fill_blocked']
    assert blocked[-1]['details']['reason'] == 'strategy_cash_cap_exceeded'
    assert state['orders'][-1]['filled'] == 100
    store.broker.cancel(order['id'])
    manual = store.broker.place_order('manual-sell-improvement', high['symbol'], 'sell', 100, Decimal('9.99'),
                                    now, now.date())
    now = instant(day=18, second=5)
    fills = store.broker.on_quote({**high, 'exchange_time': now.isoformat(), 'received_at': now.isoformat(),
                                  'quote_id': now.isoformat()}, changed, now)
    assert fills[0]['order_id'] == manual['id'] and fills[0]['price'] == Decimal('12.99')


def test_sell_exact_backtest_terms_and_research_integrity_remain_required(account):  # noqa: F811
    store, versions, draft, rules, decide, submit, tick, _ = prepared(account)
    with pytest.raises(ValueError, match='strategy_backtest_unavailable'):
        versions.create({**draft['config'], 'threshold': '10.01'}, 'sell-terms-changed', 42)
    decide('approved')
    run_id = draft['config']['research_reference']['run_id']
    with store.connection, store.connection.cursor() as cursor:
        cursor.execute('UPDATE qd_ashare_research_runs SET summary=%s WHERE id=%s RETURNING summary',
                       ('corrupted research', run_id))
    with pytest.raises(ValueError, match='strategy_research_unavailable'):
        decide('activated')


@pytest.mark.parametrize('stage', ['approval', 'activation', 'submission', 'fill'])
def test_corrupted_backtest_evidence_blocks_every_authorization_and_fill_boundary(account, stage):  # noqa: F811
    store, _, draft, rules, decide, submit, tick, _ = prepared(account)
    if stage != 'approval':
        decide('approved')
    if stage in ('submission', 'fill'):
        decide('activated')
    if stage == 'fill':
        submit()
    with store.connection, store.connection.cursor() as cursor:
        # Controlled database-corruption injection in this test's private schema.
        cursor.execute('ALTER TABLE qd_ashare_strategy_backtests DISABLE TRIGGER strategy_backtests_append_only')
        cursor.execute("UPDATE qd_ashare_strategy_backtests SET report=jsonb_set(report,'{metrics}',%s::jsonb) WHERE id=%s",
                       ('{"corrupted":true}', draft['config']['backtest_reference']['run_id']))
        cursor.execute('ALTER TABLE qd_ashare_strategy_backtests ENABLE TRIGGER strategy_backtests_append_only')
    if stage == 'fill':
        now = instant(day=18, second=3)
        assert store.broker.on_quote(tick(second=3), rules, now) == []
        blocked = [item for item in store.broker.snapshot()['audit'] if item['event'] == 'strategy_order_fill_blocked']
        assert blocked[-1]['details']['reason'] == 'strategy_backtest_unavailable'
    else:
        action = {'approval': lambda: decide('approved'), 'activation': lambda: decide('activated'),
                  'submission': submit}[stage]
        with pytest.raises(ValueError, match='strategy_backtest_unavailable'):
            action()
    assert all(fill['side'] == 'buy' for fill in store.broker.snapshot()['fills'])
