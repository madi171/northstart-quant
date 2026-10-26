from copy import deepcopy
from datetime import date, timedelta
import math

import pytest

from app.contracts import Bar
from app.reversal_study import candidates, evaluate, fingerprint, qualifies, register_plan
from app.strategy import target


BASELINE = {'code': 'def target(history, params):\n    return 0.0', 'params': {}}
OPTIONS = dict(initial=100000, lot=1, fee_bps=5, slippage_bps=5, max_weight=.3)


def fixture_bars():
    bars = []
    for index in range(1100):
        day = date(2023, 10, 16) + timedelta(days=index)
        if day.weekday() < 5:
            price = 100 + 8 * math.sin(len(bars) / 3)
            bars.append(Bar(day.isoformat(), price, price + 2, price - 2, price + .2, 100000))
    return bars


def test_fixed_unique_candidates_are_safe_and_long_only():
    rows = candidates(BASELINE)
    assert len(rows) == 19
    assert len({row['key'] for row in rows}) == 19
    assert len({row['fingerprint'] for row in rows}) == 19
    for row in rows:
        for size in (0, 20, 100, 500):
            history = [100 + 10 * math.sin(index / 3) for index in range(size)]
            assert 0 <= target(row['code'], history, row['params']) <= .3


def test_reversal_signal_is_prefix_causal_and_has_no_date_schedule():
    history = [100 + 5 * math.sin(index / 3) for index in range(120)]
    future = history + [10, 10000, 1]
    for row in candidates(BASELINE)[1:]:
        before = target(row['code'], history, row['params'])
        assert target(row['code'], future[:len(history)], row['params']) == before
        assert 'len(history) %' not in row['code']
        assert '2026' not in row['code']


@pytest.mark.parametrize('failure', ['zero_return', 'double_cost', 'episodes', 'drawdown'])
def test_selection_rejects_weak_evidence(failure):
    row = {'metrics': {'total_return': .01, 'max_drawdown': -.05},
           'attribution': {'closed_episodes': 5}, 'doubled_cost_metrics': {'total_return': .001}}
    assert qualifies(row)
    if failure == 'zero_return':
        row['metrics']['total_return'] = 0
    elif failure == 'double_cost':
        row['doubled_cost_metrics']['total_return'] = -.001
    elif failure == 'episodes':
        row['attribution']['closed_episodes'] = 4
    else:
        row['metrics']['max_drawdown'] = -.16
    assert not qualifies(row)


def test_plan_cannot_change_after_registration(tmp_path, monkeypatch):
    from app import providers
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    first = register_plan(BASELINE, OPTIONS, 'fixture-hash', '2026-01-02', '2026-10-13', 'research/test/plan.json')
    assert len(first['candidates']) == 19
    assert register_plan(BASELINE, OPTIONS, 'fixture-hash', '2026-01-02', '2026-10-13', 'research/test/plan.json') == first
    with pytest.raises(ValueError, match='冻结'):
        register_plan(BASELINE, {**OPTIONS, 'max_weight': .4}, 'fixture-hash',
                      '2026-01-02', '2026-10-13', 'research/test/plan.json')


def test_research_uses_only_2026_for_selection_and_retains_costs():
    summary, reports = evaluate(fixture_bars(), BASELINE, OPTIONS, '2026-01-02', '2026-10-13')
    assert summary['candidate_count'] == 19
    assert summary['objective'] == 'year_2026_total_return'
    assert summary['conditions'] == OPTIONS
    assert summary['evaluation_start'] == '2026-01-02'
    assert '回顾性拟合' in summary['note']
    assert len(reports) == 19
    qualified = [row for row in summary['rows'] if row['eligible']]
    if qualified:
        assert summary['selected_key'] == max(qualified, key=lambda row: row['metrics']['total_return'])['key']
        assert summary['attribution']['closed_episodes'] >= 5
        assert summary['doubled_cost_metrics']['total_return'] > 0
        assert len(summary['lot_sensitivity']) == 2
    for report in reports.values():
        assert report['curve'][0]['date'] == '2026-01-02'
        assert all(trade['signal_date'] < trade['date'] for trade in report['trades'])
        assert all(point['equity'] > 0 for point in report['curve'])
    assert fingerprint(candidates(BASELINE)) == summary['candidate_set_hash']


def test_no_qualifying_strategy_is_not_fabricated():
    flat = [Bar(bar.date, 100, 101, 99, 100, 100000) for bar in fixture_bars()]
    summary, _ = evaluate(flat, BASELINE, OPTIONS, '2026-01-02', '2026-10-13')
    assert summary['selected_key'] is None
    assert summary['positive_candidate_count'] == 0
