from dataclasses import replace
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.backtest import simulate
from app.contracts import Bar
from app.research_features import FeatureObservation, FeatureTimeline, freeze_features
from app.schemas import OptimizationInput, StrategyInput
from app.strategy import StrategyError, target


def observation(**overrides):
    return {'name':'fundamental.roe', 'category':'fundamental', 'symbol':'TEST',
            'observed_at':'2024-01-01T00:00:00Z', 'available_at':'2024-01-02T12:00:00Z',
            'value':.2, 'source':'licensed-test', 'max_age_days':3, **overrides}


def bar(day):
    return Bar(day, 100, 102, 99, 101, 10000)


CODE = '''def target(history, params):
    if feature("fundamental.roe", 0) > 0.1:
        return 0.3
    return 0.0
'''


def test_releases_are_hidden_until_later_day_and_expire():
    timeline = FeatureTimeline([observation()])
    assert 'fundamental.roe' not in timeline.at(bar('2024-01-02'))
    assert timeline.at(bar('2024-01-03'))['fundamental.roe'] == .2
    assert 'fundamental.roe' not in timeline.at(bar('2024-01-06'))
    assert timeline.at(bar('2024-01-07'))['price.volume'] == 10000


def test_release_at_midnight_is_excluded_at_exact_cutoff():
    row = observation()
    row['available_at'] = '2024-01-02T00:00:00Z'
    timeline = FeatureTimeline([row])
    assert 'fundamental.roe' not in timeline.at(bar('2024-01-02'))
    assert timeline.at(bar('2024-01-03'))['fundamental.roe'] == .2


def test_delayed_revisions_do_not_rewrite_history_or_replace_newer_period():
    original = observation()
    newer = {**original, 'observed_at':'2024-01-03T00:00:00Z', 'available_at':'2024-01-04T12:00:00Z', 'value':.4}
    revision = {**original, 'available_at':'2024-01-05T12:00:00Z', 'value':.1}
    timeline = FeatureTimeline([revision, newer, original])
    assert timeline.at(bar('2024-01-03'))['fundamental.roe'] == .2
    assert timeline.at(bar('2024-01-05'))['fundamental.roe'] == .4
    assert timeline.at(bar('2024-01-06'))['fundamental.roe'] == .4
    revised = FeatureTimeline([original, revision])
    assert revised.at(bar('2024-01-05'))['fundamental.roe'] == .2
    assert revised.at(bar('2024-01-06'))['fundamental.roe'] == .1


@pytest.mark.parametrize('override', [
    {'value':float('nan')}, {'value':float('inf')}, {'available_at':'2024-01-02'},
    {'available_at':'2023-12-31T00:00:00Z'}, {'max_age_days':0},
    {'name':'price.close'}, {'category':'secret'},
])
def test_invalid_features_are_rejected(override):
    with pytest.raises(ValidationError):
        FeatureObservation(**{**observation(), **override})


def test_hash_is_order_independent_and_time_zone_normalized():
    left = observation()
    right = {**left, 'name':'macro.rate', 'category':'macro', 'symbol':'*'}
    assert freeze_features([left, right]) == freeze_features([right, left])
    shifted = {**left, 'available_at':'2024-01-02T20:00:00+08:00'}
    assert freeze_features([left]) == freeze_features([shifted])
    with pytest.raises(ValueError, match='重复版本'):
        freeze_features([left, shifted])
    with pytest.raises(ValueError, match='一致'):
        freeze_features([left, {**left, 'source':'different', 'available_at':'2024-01-03T00:00:00Z'}])


def test_fractional_release_times_keep_chronological_order():
    first = {**observation(), 'available_at':'2024-01-02T12:00:00Z'}
    later = {**first, 'available_at':'2024-01-02T12:00:00.1Z', 'value':.4}
    assert FeatureTimeline([later, first]).at(bar('2024-01-03'))['fundamental.roe'] == .4


def test_feature_strategy_validates_and_has_no_cross_run_shared_state():
    StrategyInput(name='feature strategy', code=CODE, params={})
    assert target(CODE, [], {}, features={'fundamental.roe':.2}) == .3
    assert target(CODE, [], {}) == 0
    for invalid in ['feature("x")', 'feature(params["name"], 0)']:
        with pytest.raises(ValidationError, match='特征'):
            StrategyInput(name='invalid', code='def target(history, params):\n return ' + invalid, params={})


def test_future_feature_values_never_change_past_trades_and_missing_defaults_flat():
    bars = [bar((date(2024, 1, 1) + timedelta(days=i)).isoformat()) for i in range(10)]
    future = {**observation(), 'available_at':'2024-01-07T12:00:00Z'}
    a = simulate(bars, CODE, {}, feature_records=[future])
    b = simulate(bars, CODE, {}, feature_records=[{**future, 'value':-.1}])
    assert a['curve'][:7] == b['curve'][:7]
    assert a['trades'][0]['signal_date'] == '2024-01-08'
    assert a['trades'][0]['date'] == '2024-01-09'
    assert b['metrics']['trade_count'] == 0
    assert a['feature_coverage']['fundamental.roe'] == 3


def test_rebalancing_is_enforced_and_aligned_across_warmup_splits():
    bars = [replace(bar((date(2024,1,1)+timedelta(days=i)).isoformat()), close=101+i) for i in range(70)]
    code = 'def target(history, params):\n return .3'
    report = simulate(bars, code, {}, rebalance_every=20)
    assert all((date.fromisoformat(t['date']) - date(2024,1,2)).days % 20 == 0 for t in report['trades'])
    split = simulate(bars[30:], code, {}, warmup_bars=bars[:30], rebalance_every=20)
    assert split['trades'][0]['date'] == bars[41].date
    assert report['rebalance_every'] == 20


def test_input_feature_budget_and_trading_style_validation():
    request = dict(strategy_id='s', asset_id='a', start='2024-01-01', end='2024-12-31',
                   client_key='feature-request', model_ids=['m'], mode='demo')
    for field, value in [('trading_style','intraday'), ('max_changed_lines',0), ('max_param_changes',0), ('max_turnover',0)]:
        with pytest.raises(ValidationError):
            OptimizationInput(**request, **{field:value})
