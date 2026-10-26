from datetime import date, timedelta
import pytest
from app.strategy import target, parse, StrategyError, DEFAULT_CODE
from app.backtest import simulate
from app.contracts import Bar
from app.providers import DemoMarket, load_bars, validate_bars


def bars(count=80):
    return [Bar((date(2024, 1, 1) + timedelta(days=i)).isoformat(), 100 + i, 102 + i, 99 + i, 101 + i, 10000) for i in range(count)]


def test_strategy_cannot_access_io_or_imports():
    for code in ['import os\ndef target(history, params):\n return 0',
                 'def target(history, params):\n return __import__("os")',
                 'def target(history, params):\n return history.__class__',
                 'def target(history, params):\n while True:\n  return 0',
                 'def target(history, params):\n return 10 ** 1000000']:
        with pytest.raises(StrategyError):
            parse(code)


def test_strategy_bounded_output():
    with pytest.raises(StrategyError):
        target('def target(history, params):\n return 2', [100], {})


def test_zero_weight_preserves_capital():
    r = simulate(bars(), 'def target(history, params):\n return 0', {}, initial=100000)
    assert r['metrics']['total_return'] == 0
    assert r['trades'] == []


def test_signal_executes_next_day_not_same_close():
    r = simulate(bars(), 'def target(history, params):\n return 0.1', {}, fee_bps=0, slippage_bps=0)
    first = r['trades'][0]
    assert first['signal_date'] == bars()[0].date
    assert first['date'] == bars()[1].date
    assert first['price'] == bars()[1].open


def test_future_prices_do_not_change_past_trades():
    original = bars()
    changed = original[:60] + [Bar(b.date, b.open * 2, b.high * 2, b.low * 2, b.close * 2, b.volume) for b in original[60:]]
    p = {'fast': 5, 'slow': 15, 'weight': .2}
    a, b = simulate(original, DEFAULT_CODE, p), simulate(changed, DEFAULT_CODE, p)
    assert a['curve'][:60] == b['curve'][:60]
    assert [t for t in a['trades'] if t['date'] < original[60].date] == [t for t in b['trades'] if t['date'] < original[60].date]


def test_demo_prices_are_stable_across_query_windows():
    provider = DemoMarket()
    all_rows = provider.bars('US.SPY', '2024-01-01', '2024-12-31')
    subset = provider.bars('US.SPY', '2024-06-01', '2024-08-01')
    assert subset == [b for b in all_rows if '2024-06-01' <= b.date <= '2024-08-01']


def test_live_never_falls_back_to_demo():
    provider = {'id': 'demo', 'name': 'demo', 'kind': 'market', 'driver': 'demo', 'enabled': True, 'priority': 1, 'config': {}}
    with pytest.raises(ValueError, match='不会降级'):
        load_bars([provider], 'live', 'US.SPY', '2024-01-01', '2024-12-31')


def test_duplicate_bars_rejected():
    with pytest.raises(ValueError):
        validate_bars(bars() + [bars()[-1]])


def test_fees_and_slippage_reduce_returns():
    code = 'def target(history, params):\n return 0.2'
    no_cost = simulate(bars(), code, {}, fee_bps=0, slippage_bps=0)
    cost = simulate(bars(), code, {}, fee_bps=10, slippage_bps=10)
    assert cost['metrics']['total_return'] < no_cost['metrics']['total_return']
