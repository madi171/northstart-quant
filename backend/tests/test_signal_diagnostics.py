from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
import math

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.backtest import simulate
from app.contracts import Bar
from app.signal_diagnostics import report_with_signal_diagnostics
from app.strategy import parse, target
from app.strategy_templates import list_templates


ZERO_CODE = 'def target(history, params):\n return 0'
LONG_CODE = 'def target(history, params):\n return 0.3'


def bars(count=120, trend=False, volume=10000):
    return [Bar((date(2024, 1, 1) + timedelta(days=index)).isoformat(),
                100 * math.exp(.005 * index) if trend else 100,
                102 * math.exp(.005 * index) if trend else 102,
                99 * math.exp(.005 * index) if trend else 99,
                101 * math.exp(.005 * index) if trend else 101, volume)
            for index in range(count)]


def by_key(diagnostic):
    return {item['key']: item['days'] for item in diagnostic['zero_return_branches']}


@pytest.mark.parametrize('template_id,warmup', [('adaptive-kama', 60), ('regime-adaptive-ensemble', 99)])
def test_two_templates_legitimate_zero_signal_on_flat_market(template_id, warmup):
    template = next(item for item in list_templates() if item['id'] == template_id)
    result = simulate(bars(), template['code'], template['params'])
    diagnostic = result['signal_diagnostics']
    assert result['metrics']['total_return'] == 0 and result['metrics']['trade_count'] == 0
    assert diagnostic['status'] == 'no_signal'
    assert diagnostic['warmup_days'] == warmup
    assert diagnostic['raw_positive_days'] == diagnostic['positive_target_days'] == diagnostic['eligible_signal_days'] == 0
    assert by_key(diagnostic)['warmup'] == warmup
    assert sum(item['days'] for item in diagnostic['zero_return_branches']) == 120
    filtered = 'low_efficiency' if template_id == 'adaptive-kama' else 'reversion_filtered'
    assert by_key(diagnostic)[filtered] == 120 - warmup


@pytest.mark.parametrize('template_id', ['adaptive-kama', 'regime-adaptive-ensemble'])
def test_templates_trade_in_uptrend_not_structurally_always_zero(template_id):
    template = next(item for item in list_templates() if item['id'] == template_id)
    result = simulate(bars(trend=True), template['code'], template['params'])
    diagnostic = result['signal_diagnostics']
    assert diagnostic['status'] == 'traded'
    assert diagnostic['raw_positive_days'] > 0
    assert result['metrics']['trade_count'] > 0
    assert sum(item['days'] for item in diagnostic['zero_return_branches']) + diagnostic['raw_positive_days'] == 120
    assert all(trade['signal_date'] < trade['date'] for trade in result['trades'])


@pytest.mark.parametrize('template_id', ['adaptive-kama', 'regime-adaptive-ensemble'])
def test_all_warmup_explains_zero_without_changing_thresholds(template_id):
    template = next(item for item in list_templates() if item['id'] == template_id)
    params = deepcopy(template['params'])
    result = simulate(bars(40), template['code'], params)
    assert result['signal_diagnostics']['warmup_days'] == 40
    assert '全区间仍在预热' in result['signal_diagnostics']['summary']
    assert params == template['params']


def test_interpreter_trace_does_not_change_targets_or_inputs():
    template = next(item for item in list_templates() if item['id'] == 'regime-adaptive-ensemble')
    fn = parse(template['code'])
    values = [bar.close for bar in bars(trend=True)]
    original = list(values)
    for index in range(1, len(values)):
        trace = {}
        assert target(fn, values[:index], template['params'], trace) == target(fn, values[:index], template['params'])
        assert 'return_line' in trace
    assert values == original


@pytest.mark.parametrize('template_id', ['adaptive-kama', 'regime-adaptive-ensemble'])
def test_comment_only_template_variants_keep_business_diagnostics(template_id):
    template = next(item for item in list_templates() if item['id'] == template_id)
    plain = '\n'.join(line for line in template['code'].splitlines() if not line.lstrip().startswith('#')) + '\n'
    baseline = simulate(bars(40), template['code'], template['params'])
    for code in (plain, '# 注释不改变信号。\n' + template['code']):
        result = simulate(bars(40), code, template['params'])
        assert result['metrics'] == baseline['metrics']
        assert result['trades'] == baseline['trades']
        assert result['signal_diagnostics']['warmup_days'] == 40
        assert by_key(result['signal_diagnostics'])['warmup'] == 40


def test_custom_code_has_only_literal_return_labels():
    template = next(item for item in list_templates() if item['id'] == 'adaptive-kama')
    result = simulate(bars(), template['code'].replace('return 0.0', 'return 0'), template['params'])
    diagnostic = result['signal_diagnostics']
    assert diagnostic['warmup_days'] is None
    assert all(item['key'].startswith('return_') for item in diagnostic['zero_return_branches'])


def test_last_day_signal_has_no_next_day_execution():
    code = 'def target(history, params):\n if len(history) < 120:\n  return 0\n return 0.3'
    result = simulate(bars(), code, {})
    diagnostic = result['signal_diagnostics']
    assert diagnostic['status'] == 'last_day_only'
    assert diagnostic['positive_target_days'] == 1
    assert diagnostic['eligible_signal_days'] == 0
    assert result['trades'] == []


def test_positive_signals_blocked_by_zero_volume():
    result = simulate(bars(volume=0), LONG_CODE, {})
    diagnostic = result['signal_diagnostics']
    assert diagnostic['status'] == 'execution_blocked'
    assert diagnostic['zero_volume_days'] == diagnostic['eligible_signal_days'] == 119
    assert diagnostic['below_lot_days'] == 0
    assert result['trades'] == []


def test_positive_signals_blocked_by_board_lot():
    result = simulate(bars(), LONG_CODE, {}, initial=1000, lot=100)
    diagnostic = result['signal_diagnostics']
    assert diagnostic['status'] == 'execution_blocked'
    assert diagnostic['below_lot_days'] == 119
    assert diagnostic['zero_volume_days'] == diagnostic['insufficient_cash_days'] == 0
    assert result['trades'] == []


def test_fee_and_slippage_affordability_barrier():
    result = simulate(bars(), 'def target(history, params):\n return 1', {}, initial=100, max_weight=1, lot=1)
    diagnostic = result['signal_diagnostics']
    assert diagnostic['status'] == 'execution_blocked'
    assert diagnostic['insufficient_cash_days'] == 119
    assert diagnostic['below_lot_days'] == 0
    assert result['trades'] == []


def test_positive_signals_clipped_to_zero_by_risk():
    result = simulate(bars(), LONG_CODE, {}, max_weight=0)
    diagnostic = result['signal_diagnostics']
    assert diagnostic['status'] == 'risk_blocked'
    assert diagnostic['raw_positive_days'] == 120 and diagnostic['positive_target_days'] == 0
    assert result['trades'] == []


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    from app import providers
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    template = next(item for item in list_templates() if item['id'] == 'adaptive-kama')
    market = bars()
    result = simulate(market, template['code'], template['params'])
    result.pop('signal_diagnostics')
    result.update(snapshot='legacy.parquet', source={'name':'fixture', 'mode':'historical'})
    path = tmp_path / 'legacy.parquet'
    pq.write_table(pa.Table.from_pylist([asdict(bar) for bar in market]), path)
    payload = {'spec': {'start': market[0].date, 'end':market[-1].date, 'initial':100000, 'fee_bps':5, 'slippage_bps':5},
               'asset': {'lot':1}, 'risk':{'max_weight':.3}, 'strategy': {'code':template['code'], 'params':template['params']},
               'frozen_data': {'path':'legacy.parquet', 'hash':result['data_hash'], 'source':result['source']}}
    return result, payload, path


def test_old_report_replays_only_frozen_experiment_without_mutation(legacy, monkeypatch):
    from app import providers
    monkeypatch.setattr(providers, 'load_bars', lambda *args: pytest.fail('no live data allowed'))
    result, payload, _ = legacy
    before_result, before_payload = deepcopy(result), deepcopy(payload)
    enriched = report_with_signal_diagnostics(result, payload)
    assert enriched['signal_diagnostics']['replayed'] is True
    assert enriched['signal_diagnostics']['warmup_days'] == 60
    assert enriched['metrics'] == before_result['metrics'] and enriched['trades'] == before_result['trades']
    assert result == before_result and payload == before_payload
    assert {key:value for key,value in enriched.items() if key!='signal_diagnostics'} == before_result


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'hash', 'source', 'path', 'date', 'start', 'drift'])
def test_bad_old_experiments_do_not_invent_diagnostics(legacy, failure):
    result, payload, path = legacy
    if failure == 'missing':
        path.unlink()
    elif failure == 'corrupt':
        path.write_bytes(b'bad')
    elif failure == 'hash':
        payload['frozen_data']['hash'] = 'wrong'
    elif failure == 'source':
        payload['frozen_data']['source'] = {'name':'different'}
    elif failure == 'path':
        payload['frozen_data']['path'] = result['snapshot'] = '../../outside.parquet'
    elif failure == 'date':
        result['curve'][0]['date'] = '2020-01-01'
    elif failure == 'start':
        payload['spec']['start'] = '2030-01-01'
    else:
        payload['strategy']['code'] = LONG_CODE
    before = deepcopy(result)
    enriched = report_with_signal_diagnostics(result, payload)
    assert 'signal_diagnostics' not in enriched
    assert enriched['diagnostic_warning']
    assert {key:value for key,value in enriched.items() if key!='diagnostic_warning'} == before
    assert result == before


def test_existing_diagnostics_skip_snapshot_io(monkeypatch):
    report = simulate(bars(), ZERO_CODE, {})
    monkeypatch.setattr(pq, 'read_table', lambda *args: pytest.fail('must not read'))
    assert report_with_signal_diagnostics(report, {}) is report


def test_get_job_adds_old_diagnostic_read_only(database, legacy):
    from app.db import Job
    from app.main import get_job
    report, payload, _ = legacy
    with database.begin() as db:
        db.add(Job(id='diagnostic-test', kind='backtest', status='succeeded', payload=payload, result=report))
    response = get_job('diagnostic-test')
    assert response['result']['signal_diagnostics']['status'] == 'no_signal'
    with database() as db:
        row = db.get(Job, 'diagnostic-test')
        assert row.result == report and row.payload == payload
