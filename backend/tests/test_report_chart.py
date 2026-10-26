import copy
from dataclasses import asdict
from datetime import date, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.backtest import simulate
from app.contracts import Bar
from app.report_chart import PRICE_FIELDS, report_with_prices


@pytest.fixture
def report_fixture(tmp_path, monkeypatch):
    from app import providers
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    bars = [Bar((date(2024, 1, 1) + timedelta(days=index)).isoformat(),
                100 + index, 103 + index, 99 + index, 102 + index, 10000 + index)
            for index in range(80)]
    result = simulate(bars, 'def target(history, params):\n return 0.1', {}, slippage_bps=10)
    result['snapshot'] = 'snapshots/report.parquet'
    path = tmp_path / result['snapshot']
    path.parent.mkdir()
    pq.write_table(pa.Table.from_pylist([asdict(bar) for bar in bars]), path)
    return result, bars, path


def legacy_report(result):
    report = copy.deepcopy(result)
    for point in report['curve']:
        for key in PRICE_FIELDS:
            point.pop(key)
    return report


def test_new_curve_preserves_daily_market_and_execution_data(report_fixture):
    result, bars, _ = report_fixture
    for point, bar in zip(result['curve'], bars):
        assert point['date'] == bar.date
        assert {key: point[key] for key in PRICE_FIELDS} == {key: getattr(bar, key) for key in PRICE_FIELDS}
    first = result['trades'][0]
    assert first['date'] == bars[1].date
    assert first['signal_date'] == bars[0].date
    assert first['price'] == round(bars[1].open * 1.001, 6)
    assert first['price'] != result['curve'][1]['close']


def test_complete_report_does_not_read_snapshot(report_fixture, monkeypatch):
    report, _, _ = report_fixture
    monkeypatch.setattr(pq, 'read_table', lambda *args: pytest.fail('new report must not read snapshot'))
    assert report_with_prices(report) is report


def test_legacy_prices_are_from_verified_original_snapshot(report_fixture):
    result, _, _ = report_fixture
    legacy = legacy_report(result)
    before = copy.deepcopy(legacy)
    enriched = report_with_prices(legacy)
    assert enriched == result
    assert legacy == before
    assert enriched['metrics'] == legacy['metrics']
    assert enriched['trades'] == legacy['trades']


@pytest.mark.parametrize('failure', ['missing', 'hash', 'corrupt', 'path', 'date', 'no_snapshot'])
def test_unverifiable_snapshot_preserves_old_report(report_fixture, failure):
    result, _, path = report_fixture
    report = legacy_report(result)
    if failure == 'missing':
        path.unlink()
    elif failure == 'hash':
        report['data_hash'] = 'mismatch'
    elif failure == 'corrupt':
        path.write_bytes(b'bad parquet')
    elif failure == 'path':
        report['snapshot'] = '../../outside.parquet'
    elif failure == 'date':
        report['curve'][0]['date'] = '2020-01-01'
    else:
        report.pop('snapshot')
    before = copy.deepcopy(report)
    enriched = report_with_prices(report)
    assert enriched['chart_warning']
    assert {key: value for key, value in enriched.items() if key != 'chart_warning'} == before
    assert report == before
    assert all('close' not in point for point in enriched['curve'])


def test_empty_report_is_unchanged():
    assert report_with_prices(None) is None
    assert report_with_prices({}) == {}


def test_job_details_enrich_without_persisting(database, report_fixture):
    from app.db import Job
    from app.main import get_job
    report, _, _ = report_fixture
    legacy = legacy_report(report)
    with database.begin() as db:
        db.add(Job(id='chart-test', kind='backtest', status='succeeded', payload={'spec': {}}, result=legacy))
    response = get_job('chart-test')
    assert response['result']['curve'] == report['curve']
    with database() as db:
        saved = db.get(Job, 'chart-test').result
        assert saved == legacy
        assert 'close' not in saved['curve'][0]
