import math
import statistics

import pytest

from app.backtest import equity_returns, simulate
from app.contracts import Bar


HOLD = 'def target(history, params):\n    return 0.3'
PRIOR = [Bar('2024-01-01', 100, 100, 100, 100, 1000)]


def test_first_day_loss_is_in_risk_metrics_and_annualization():
    rows = [Bar('2024-01-02', 100, 100, 50, 50, 1000),
            Bar('2024-01-03', 50, 50, 50, 50, 1000),
            Bar('2024-01-04', 50, 50, 50, 50, 1000)]
    report = simulate(rows, HOLD, {}, initial=10000, fee_bps=0, slippage_bps=0, warmup_bars=PRIOR)
    returns = [-.15, 0, 0]
    assert report['initial_equity'] == 10000
    assert report['metrics_version'] == 2
    assert len(report['curve']) == len(rows)
    assert equity_returns(report['curve'], 10000) == pytest.approx(returns)
    metrics = report['metrics']
    assert metrics['total_return'] == pytest.approx(-.15)
    assert metrics['max_drawdown'] == pytest.approx(-.15)
    assert metrics['annual_return'] == pytest.approx(.85 ** (252 / 3) - 1)
    assert metrics['volatility'] == pytest.approx(statistics.stdev(returns) * math.sqrt(252))
    assert metrics['sharpe'] == pytest.approx(statistics.mean(returns) / statistics.stdev(returns) * math.sqrt(252))
    assert metrics['benchmark_return'] == -.5
    assert report['benchmark_entry'] == {'date': rows[0].date, 'price': 100}


def test_first_day_costs_and_terminal_mark_to_market_reconcile():
    rows = [Bar('2024-01-02', 100, 110, 100, 110, 1000),
            Bar('2024-01-03', 110, 110, 110, 110, 0)]
    report = simulate(rows, HOLD, {}, initial=10000, fee_bps=100, slippage_bps=100, warmup_bars=PRIOR)
    trade, = report['trades']
    assert trade['quantity'] == 30
    assert trade['price'] == 101
    assert trade['fee'] == 30.3
    expected = 10000 - 30 * 101 - 30.3 + 30 * 110
    assert report['curve'][-1]['equity'] == pytest.approx(expected)
    assert report['metrics']['total_return'] == pytest.approx(expected / 10000 - 1)
    assert equity_returns(report['curve'], 10000) == pytest.approx([expected / 10000 - 1, 0])
    assert all(trade['side'] == 'buy' for trade in report['trades'])


def test_completed_round_trip_reconciles_cash_costs_and_returns():
    rows = [Bar('2024-01-02', 100, 100, 100, 100, 1000),
            Bar('2024-01-03', 110, 110, 110, 110, 1000)]
    code = 'def target(history, params):\n    return 0.3 if len(history) == 1 else 0.0'
    report = simulate(rows, code, {}, initial=10000, fee_bps=100, slippage_bps=100, warmup_bars=PRIOR)
    buy, sell = report['trades']
    expected = 10000 - buy['quantity'] * buy['price'] - buy['fee'] + sell['quantity'] * sell['price'] - sell['fee']
    assert buy['quantity'] == sell['quantity'] == 30
    assert report['metrics']['trade_count'] == 2
    assert report['curve'][-1]['equity'] == pytest.approx(expected)
    assert report['metrics']['total_return'] == pytest.approx(expected / 10000 - 1)


@pytest.mark.parametrize('warmup', [None, PRIOR])
def test_benchmark_waits_for_first_executable_positive_volume_open(warmup):
    rows = [Bar('2024-01-02', 100, 150, 100, 150, 1000),
            Bar('2024-01-03', 200, 250, 200, 250, 0),
            Bar('2024-01-04', 300, 330, 300, 330, 1000)]
    report = simulate(rows, HOLD, {}, initial=10000, fee_bps=0, slippage_bps=0, warmup_bars=warmup)
    entry = rows[0] if warmup else rows[2]
    assert report['benchmark_entry'] == {'date': entry.date, 'price': entry.open}
    assert report['metrics']['benchmark_return'] == pytest.approx(rows[-1].close / entry.open - 1)
    if not warmup:
        assert [point['benchmark'] for point in report['curve'][:2]] == [10000, 10000]


def test_benchmark_remains_cash_if_no_executable_day():
    report = simulate([Bar('2024-01-02', 100, 150, 100, 150, 0)], HOLD, {}, warmup_bars=PRIOR)
    assert report['benchmark_entry'] is None
    assert report['metrics']['benchmark_return'] == 0


@pytest.mark.parametrize('rows', [[], PRIOR + PRIOR, list(reversed(PRIOR + [Bar('2024-01-02', 100, 100, 100, 100, 1)]))])
def test_engine_rejects_empty_duplicate_and_reversed_dates(rows):
    with pytest.raises(ValueError, match='严格升序'):
        simulate(rows, HOLD, {})
