from datetime import date, timedelta

import pytest

from app import strategy_study as study
from app.backtest import simulate
from app.contracts import Bar
from app.strategy import parse, target
from app.strategy_templates import list_templates


def baseline():
    return next(row for row in list_templates() if row['id'] == 'adaptive-kama')


def market():
    first = date(2023, 10, 16)
    rows = []
    for index in range(1100):
        day = first + timedelta(days=index)
        if day.weekday() < 5:
            price = 100 + len(rows) * .1
            rows.append(Bar(day.isoformat(), price, price + 2, price - 2, price + 1, 100000))
    return rows


def test_finite_candidate_set_is_reproducible_and_valid():
    rows = study.candidates(baseline())
    assert len(rows) == 15
    assert len({row['key'] for row in rows}) == 15
    assert len({row['fingerprint'] for row in rows}) == 15
    assert rows == study.candidates(baseline())
    assert rows[0]['code'] == baseline()['code']
    assert all(row['params'].get('weight') == .3 for row in rows)


def test_selection_uses_total_return_risk_and_real_activity_not_annual_return():
    def row(key, total, annual, drawdown=-.1, trades=1):
        return {'key': key, 'metrics': {'total_return': total, 'annual_return': annual,
                                     'max_drawdown': drawdown, 'trade_count': trades, 'sharpe': .5}}
    rows = [row('qualified', .1, .2), row('annual-only', .05, .9), row('risk-fail', .3, .4, -.2),
            row('inactive', .4, .5, trades=0), row('constant-weight', .5, .7)]
    assert study.select(rows, 'metrics', .15)['key'] == 'qualified'
    assert study.select([rows[2], rows[3], rows[4]], 'metrics', .15) is None


def test_ema_candidate_executes_and_all_trades_follow_signal_day():
    candidate = next(row for row in study.candidates(baseline()) if row['key'] == 'ema-50-200')
    rows = market()
    report = simulate(rows[300:400], candidate['code'], candidate['params'], warmup_bars=rows[:300])
    assert report['metrics']['total_return'] > 0
    assert report['trades']
    assert all(trade['signal_date'] < trade['date'] for trade in report['trades'])
    fn = parse(candidate['code'])
    assert target(fn, [], candidate['params']) == 0
    assert 0 <= target(fn, [bar.close for bar in rows[:300]], candidate['params']) <= .3


def test_study_inherits_conditions_uses_only_prior_warmup_and_returns_evidence(monkeypatch):
    calls = []
    def fake(part, code, params, warmup_bars=None, **options):
        assert part and warmup_bars
        assert all(bar.date < part[0].date for bar in warmup_bars)
        assert options['max_weight'] == .3 and options['lot'] == 1
        calls.append((part[0].date, part[-1].date, options))
        value = .1 if params.get('fast') == 50 else .04
        return {'metrics': {'total_return': value, 'annual_return': value * 2, 'max_drawdown': -.05,
                            'sharpe': 1, 'trade_count': 2},
                'curve': [{'equity': 100000 + index * 10 + (index % 2) * 2} for index in range(len(part))],
                'initial_equity': options['initial'],
                'trades': [], 'last_date': part[-1].date, 'last_close': part[-1].close}
    monkeypatch.setattr(study, 'simulate', fake)
    summary, reports = study.evaluate(market(), baseline(), dict(initial=100000, lot=1, fee_bps=5,
                                                               slippage_bps=5, max_weight=.3),
                                      '2025-01-01', '2026-10-13')
    assert summary['selected_key'] == 'ema-50-200'
    assert summary['selected_using_2025_only'] == 'ema-50-200'
    assert summary['candidate_count'] == 15 and len(reports) == 15
    assert summary['warmup_end'] < summary['evaluation_start']
    assert len(summary['segments']) == 4
    assert calls[45][2]['fee_bps'] == 10 and calls[45][2]['slippage_bps'] == 10
    assert '不是独立样本外' in summary['note']


def test_study_requires_prior_history():
    with pytest.raises(ValueError, match='预热'):
        study.evaluate(market()[300:], baseline(), {}, '2025-01-01', '2026-10-13')


def test_pnl_attribution_distinguishes_round_trips_and_rebalancing():
    report = {'metrics': {'total_return': .02}, 'trades': [
        {'date': '2025-01-02', 'side': 'buy', 'quantity': 10, 'price': 100, 'fee': 1},
        {'date': '2025-01-03', 'side': 'buy', 'quantity': 1, 'price': 100, 'fee': .1},
        {'date': '2025-02-03', 'side': 'sell', 'quantity': 11, 'price': 120, 'fee': 1.32}],
        'last_date': '2025-02-03', 'last_close': 120}
    result = study.pnl_attribution(report, 10000)
    assert result['execution_count'] == 3 and result['closed_episodes'] == 1
    assert result['episodes'][0]['pnl'] == pytest.approx(217.58)
    assert result['return_excluding_largest_profit'] == pytest.approx(.02 - 217.58 / 10000)


def test_block_bootstrap_is_deterministic():
    report = {'initial_equity': 100, 'curve': [{'equity': 100 + index + (index % 3)} for index in range(100)]}
    assert study.bootstrap_sharpe(report, samples=100) == study.bootstrap_sharpe(report, samples=100)


def test_bootstrap_includes_initial_to_first_close_return():
    report = {'initial_equity': 100, 'curve': [{'equity': 85} for _ in range(10)]}
    result = study.bootstrap_sharpe(report, samples=100, block=10)
    assert result['interval'] == pytest.approx([-252 ** .5 / 10 ** .5] * 2)


def test_bootstrap_without_initial_equity_is_not_silently_rebased():
    result = study.bootstrap_sharpe({'curve': [{'equity': 85} for _ in range(10)]})
    assert result['interval'] is None
    assert '缺少初始资金' in result['note']
