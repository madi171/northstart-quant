from copy import deepcopy
from datetime import date, timedelta
import math
import statistics

import pytest
from sqlalchemy import func, select
from app.backtest import simulate
from app.contracts import Bar
from app.db import Job, Strategy
from app.indicators import ema, realized_vol, rsi, efficiency_ratio, kama, zscore, channel_signal
from app.schemas import StrategyInput, StrategyTemplateInput, RunInput
from app.services import enqueue
from app.strategy import parse, target, StrategyError
from app.strategy_templates import list_templates, instantiate_template


def prices(count=320, descending=False):
    return [100 * math.exp((-.001 if descending else .001) * index + .002 * math.sin(index)) for index in range(count)]


def market(values):
    return [Bar((date(2024, 1, 1) + timedelta(days=index)).isoformat(), value * .998, value * 1.01,
                value * .99, value, 10000) for index, value in enumerate(values)]


def body(template, key='template-request'):
    return StrategyTemplateInput(name=template['name'], code=template['code'], params=deepcopy(template['params']),
                                 notes=template['limitations'], client_key=key)


def test_ema_sma_seed_and_recursive_update():
    assert ema([1, 2, 3, 4], 3) == pytest.approx(3)
    assert ema([1, 2, 3, 4], 1) == 4


def test_realized_vol_is_sample_simple_return_annualized():
    values = [100, 110, 99, 108.9]
    assert realized_vol(values, 3) == pytest.approx(statistics.stdev([.1, -.1, .1]) * math.sqrt(252))
    assert realized_vol([100] * 21, 20) == 0


def test_wilder_rsi_and_flat_market():
    assert rsi([100] * 30, 14) == 50
    assert rsi(list(range(1, 31)), 14) == 100
    assert rsi(list(range(30, 0, -1)), 14) == 0
    assert rsi([10, 11, 10, 11, 10], 2) == pytest.approx(37.5)


def test_efficiency_ratio_directionless_and_bounded():
    assert efficiency_ratio([1, 2, 3, 4], 3) == 1
    assert efficiency_ratio([4, 3, 2, 1], 3) == 1
    assert efficiency_ratio([1, 2, 1, 2, 1], 4) == 0
    assert efficiency_ratio([100] * 21, 20) == 0


def test_kama_matches_independent_formula():
    values = [100 + math.sin(index) + index * .1 for index in range(80)]
    period, fast, slow = 10, 2, 30
    expected = statistics.mean(values[:period])
    for index in range(period, len(values)):
        noise = sum(abs(values[j] - values[j - 1]) for j in range(index - period + 1, index + 1))
        er = abs(values[index] - values[index - period]) / noise if noise else 0
        smoothing = (er * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
        expected += smoothing * (values[index] - expected)
    assert kama(values, period, fast, slow) == pytest.approx(expected)
    assert kama([100] * 100, 10, 2, 30) == 100


def test_zscore_uses_previous_window_not_current_extreme():
    assert zscore([100, 101, 102, 103, 150], 4) == pytest.approx((150 - 101.5) / statistics.stdev([100, 101, 102, 103]))
    assert zscore([100] * 21, 20) == 0


def test_channel_excludes_today_and_keeps_virtual_position_until_exit():
    assert channel_signal([100, 101, 102, 103], 3, 2) == 1
    assert channel_signal([100, 101, 102, 103, 102.5], 3, 2) == 1
    assert channel_signal([100, 101, 102, 103, 102.5, 101], 3, 2) == 0
    assert channel_signal([100, 101, 102, 103, 102.5, 101, 105], 3, 2) == 1
    assert channel_signal([100] * 30, 10, 5) == 0


def test_channel_deques_match_direct_reference():
    values = [100 + math.sin(index * .4) * 8 + index * .05 for index in range(200)]
    for end in range(12, len(values)):
        state = 0
        for index in range(11, end):
            if not state and values[index] > max(values[index - 11:index]):
                state = 1
            elif state and values[index] < min(values[index - 4:index]):
                state = 0
        assert channel_signal(values[:end], 11, 4) == state


@pytest.mark.parametrize('function,args', [(ema,(20,)), (realized_vol,(20,)), (rsi,(14,)),
                                          (efficiency_ratio,(20,)), (kama,(20,2,30)),
                                          (zscore,(20,)), (channel_signal,(20,10))])
def test_indicators_do_not_mutate_or_accept_unbounded_bad_inputs(function,args):
    values = prices(100)
    before = list(values)
    assert math.isfinite(function(values, *args))
    assert values == before
    for bad in ([], [100] * 9000, [100] * 20 + [math.inf], [100] * 20 + [0], [100] * 20 + ['bad']):
        with pytest.raises(ValueError):
            function(bad, *args)


@pytest.mark.parametrize('period', [0, -1, .5, math.nan, math.inf, 2001, True, '20'])
def test_invalid_periods_rejected(period):
    with pytest.raises(ValueError):
        ema(prices(100), period)


@pytest.mark.parametrize('source', [
    'def target(history, params):\n return ema.__globals__',
    'def target(history, params):\n ema = 1\n return 0',
    'def target(history, params):\n return ema(history, period=20)',
    'def target(history, params):\n return ema(*history)',
    'def target(history, params):\n fn = ema\n return fn(history,20)',
    'def target(history, params):\n return __import__("os")',
])
def test_indicator_extension_does_not_weaken_sandbox(source):
    with pytest.raises(StrategyError):
        parse(source)


@pytest.mark.parametrize('template', list_templates(), ids=lambda template:template['id'])
def test_every_template_valid_causal_and_next_day_traded(template):
    validated = StrategyInput(name=template['name'], code=template['code'], params=template['params'])
    values = prices()
    fn = parse(validated.code)
    assert target(fn, [], validated.params) == 0
    assert target(fn, values[:template['warmup'] - 1], validated.params) == 0
    assert 0 < target(fn, values, validated.params) <= template['params']['weight']
    assert target(fn, prices(descending=True), validated.params) == 0
    assert target(fn, [100] * 320, validated.params) == 0
    bars = market(values)
    result = simulate(bars, template['code'], template['params'], max_weight=.2)
    assert result['trades'] and all(math.isfinite(value) for value in result['metrics'].values())
    for trade in result['trades']:
        assert trade['signal_date'] < trade['date']
    mutated = bars[:280] + [Bar(bar.date, bar.open * 2, bar.high * 2, bar.low * 2, bar.close * 2, bar.volume) for bar in bars[280:]]
    changed = simulate(mutated, template['code'], template['params'], max_weight=.2)
    assert changed['curve'][:280] == result['curve'][:280]
    assert [trade for trade in changed['trades'] if trade['date'] < bars[280].date] == [trade for trade in result['trades'] if trade['date'] < bars[280].date]


def test_volatility_target_reduces_weight_for_noisy_uptrend():
    template = next(item for item in list_templates() if item['id']=='volatility-target-trend')
    calm = prices()
    noisy = [100 * math.exp(index * .002 + .04 * math.sin(index)) for index in range(320)]
    assert 0 < target(template['code'], noisy, template['params']) < target(template['code'], calm, template['params'])


def test_regime_ensemble_can_enter_mean_reversion_branch():
    template = next(item for item in list_templates() if item['id']=='regime-adaptive-ensemble')
    values = [100] * 300 + [130,129,128,127,126,125,124,123,122,121,120,119,118,117,116,115,114,113,112,111]
    params = {**template['params'], 'trend_er': .95, 'rsi_entry': 50, 'z_entry': 1, 'er_window': 30}
    assert efficiency_ratio(values,30) < .95
    assert values[-1] > ema(values,100)
    assert rsi(values,14) < 50
    assert zscore(values,20) < -1
    position = target(template['code'], values, params)
    assert 0 < position <= params['weight'] * params['reversion_scale']


def test_catalog_is_separate_and_cannot_be_mutated_by_response(database):
    from app.main import state
    with database() as db:
        before = db.scalar(select(func.count()).select_from(Strategy))
    response = state()
    assert len(response['strategy_templates']) == 5
    response['strategy_templates'][0]['params']['weight'] = 99
    assert list_templates()[0]['params']['weight'] == .3
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Strategy)) == before


@pytest.mark.parametrize('template', list_templates(), ids=lambda item:item['id'])
def test_template_creation_idempotent_unapproved_and_backtest_ready(database, template):
    from app.db import Asset
    from app.main import create_template_strategy, approve_strategy
    request = body(template)
    first = create_template_strategy(template['id'], request)
    second = create_template_strategy(template['id'], body(template))
    assert first['id'] == second['id'] and first['status'] == 'candidate'
    assert first['provenance']['template_id'] == template['id']
    assert first['provenance']['template_version'] == 1
    with pytest.raises(ValueError, match='批准前'):
        approve_strategy(first['id'])
    with database.begin() as db:
        asset = Asset(symbol='US.TEMPLATE', name='Test', market='US', currency='USD', lot=1)
        db.add(asset);db.flush()
        spec = RunInput(strategy_id=first['id'], asset_id=asset.id, kind='backtest',
                        start='2024-01-01', end='2025-01-01', client_key='template-backtest')
        job = enqueue(db, spec)
        assert job.payload['strategy']['params'] == template['params']
        assert job.payload['strategy']['code'] == template['code']
    with pytest.raises(ValueError, match='已批准'):
        with database.begin() as db:
            enqueue(db, spec.model_copy(update={'kind':'signal', 'client_key':'template-signal'}))


def test_template_param_relationships_and_key_collision(database):
    template = list_templates()[1]
    with database.begin() as db:
        instantiate_template(db, template['id'], body(template))
    with pytest.raises(ValueError, match='幂等键'):
        with database.begin() as db:
            instantiate_template(db, list_templates()[0]['id'], body(list_templates()[0]))
    bad = body(template)
    bad.params = {**bad.params, 'fast': 150, 'slow':100}
    with pytest.raises(ValueError, match='快速'):
        with database.begin() as db:
            instantiate_template(db, template['id'], bad)
    bad = body(template)
    bad.params['weight'] = 2
    with pytest.raises(ValueError, match='参数'):
        with database.begin() as db:
            instantiate_template(db, template['id'], bad)
    with pytest.raises(ValueError):
        StrategyTemplateInput(**{**body(template).model_dump(), 'status':'approved'})


def test_customized_template_code_is_validated_and_tracked(database):
    template = list_templates()[1]
    request = body(template)
    request.code = 'def target(history, params):\n return 0'
    with database.begin() as db:
        row = instantiate_template(db, template['id'], request)
        assert row.provenance['customized_code'] is True
        assert row.code == request.code
    with pytest.raises(ValueError):
        StrategyTemplateInput(**{**body(template).model_dump(), 'code':'import os'})


@pytest.mark.parametrize('template', list_templates(), ids=lambda item:item['id'])
def test_each_template_runs_through_worker(database, template, tmp_path, monkeypatch):
    import hashlib
    import json
    from dataclasses import asdict
    import pyarrow as pa
    import pyarrow.parquet as pq
    from app import worker
    from app.db import Asset, Order
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    bars = market(prices())
    fingerprint = hashlib.sha256(json.dumps([asdict(bar) for bar in bars], sort_keys=True).encode()).hexdigest()
    path = tmp_path / 'fixture.parquet'
    pq.write_table(pa.Table.from_pylist([asdict(bar) for bar in bars]), path)
    with database.begin() as db:
        strategy = instantiate_template(db, template['id'], body(template))
        asset = Asset(symbol='US.TEMPLATETEST', name='Test', market='US', currency='USD', lot=1)
        db.add(asset); db.flush()
        spec = RunInput(strategy_id=strategy.id, asset_id=asset.id, start=bars[0].date,
                        end=bars[-1].date, client_key='worker-template-test')
        job = enqueue(db, spec)
        job.payload = {**job.payload, 'frozen_data': {'path': 'fixture.parquet', 'hash': fingerprint,
                       'source': {'name': 'isolated fixture', 'mode': 'demo'}}}
        job_id, strategy_id = job.id, strategy.id
    claimed = worker.claim()
    assert claimed[0] == job_id
    worker.execute(*claimed)
    with database() as db:
        job = db.get(Job, job_id)
        assert job.status == 'succeeded', job.error
        assert job.result['trades'] and job.result['data_hash'] == fingerprint
        assert job.result['strategy_id'] == strategy_id
        assert db.get(Strategy, strategy_id).status == 'candidate'
        assert db.scalar(select(func.count()).select_from(Order)) == 0

