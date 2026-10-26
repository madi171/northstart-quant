from copy import deepcopy
from datetime import date, timedelta
import json
import math

import pytest
from sqlalchemy import func, select

from app.contracts import Bar
from app.db import Audit, Job, Order, Setting, Strategy
from app.frozen_market import freeze_bars
from app.reversal_study import evaluate, fingerprint, register_plan
from app.strategy_study_archive import archive_study


@pytest.fixture(scope='module')
def reversal_evidence():
    baseline = {'name': 'Fixture original', 'code': 'def target(history, params):\n    return 0.0', 'params': {}}
    options = dict(initial=100000, lot=1, fee_bps=5, slippage_bps=5, max_weight=.3)
    bars = []
    for index in range(1100):
        day = date(2023, 10, 16) + timedelta(days=index)
        if day.weekday() < 5:
            price = 100 + 8 * math.sin(len(bars) / 3)
            bars.append(Bar(day.isoformat(), price, price + 2, price - 2, price + .2, 100000))
    summary, reports = evaluate(bars, baseline, options, '2026-01-02', '2026-10-13')
    assert summary['selected_key'] is not None
    return baseline, options, bars, summary, reports


@pytest.fixture
def reversal_archive(database, tmp_path, monkeypatch, reversal_evidence):
    from app import providers, reversal_study
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    baseline, options, bars, summary, reports = deepcopy(reversal_evidence)
    frozen = freeze_bars(bars, {'name': 'synthetic test fixture', 'mode': 'historical'})
    plan = register_plan(baseline, options, frozen['hash'], '2026-01-02', '2026-10-13', 'research/reversal/plan.json')
    with database.begin() as db:
        strategy = Strategy(**baseline, status='approved')
        db.add(strategy)
        db.flush()
        payload = {'spec': {'kind': 'backtest', 'asset_id': 'test-asset', 'strategy_id': strategy.id,
                           'mode': 'historical', 'start': bars[0].date, 'end': bars[-1].date,
                           'initial': options['initial'], 'fee_bps': 5, 'slippage_bps': 5,
                           'max_drawdown': .15, 'client_key': 'source-reversal'},
                   'asset': {'id': 'test-asset', 'symbol': 'HK.TEST', 'currency': 'HKD', 'lot': 1},
                   'strategy': {'id': strategy.id, **baseline}, 'risk': {'max_weight': .3},
                   'providers': [], 'frozen_data': frozen}
        source = Job(kind='backtest', status='succeeded', payload=payload, result={'data_hash': frozen['hash']})
        db.add(source)
        db.flush()
        source_id, parent_id = source.id, strategy.id
    artifact = {'source_job_id': source_id, 'frozen_data': frozen, 'summary': summary,
                'reports': reports, 'plan_path': 'research/reversal/plan.json', 'plan_hash': fingerprint(plan)}
    path = tmp_path / 'research/reversal/comparison.json'
    path.write_text(json.dumps(artifact))
    def replay(replayed, original, settings, start, end, max_drawdown):
        assert replayed == bars and original['code'] == baseline['code'] and settings == options
        assert [start, end] == ['2026-01-02', '2026-10-13'] and max_drawdown == .15
        return deepcopy(summary), deepcopy(reports)
    monkeypatch.setattr(reversal_study, 'evaluate', replay)
    return {'artifact': artifact, 'path': path, 'source_id': source_id, 'parent_id': parent_id,
            'plan_path': tmp_path / 'research/reversal/plan.json', 'summary': summary, 'reports': reports}


def test_reversal_archives_real_report_without_orders_or_risk_changes(database, reversal_archive, monkeypatch):
    from app import runner
    monkeypatch.setattr(runner, 'load_bars', lambda *args: pytest.fail('offline replay must not fetch'))
    with database.begin() as db:
        risk = deepcopy(db.get(Setting, 'risk').value)
        source = deepcopy(db.get(Job, reversal_archive['source_id']).payload)
        job = archive_study(db, 'research/reversal/comparison.json', 'reversal-archive-idempotent')
        repeated = archive_study(db, 'research/reversal/comparison.json', 'reversal-archive-idempotent')
        assert job.id == repeated.id
        assert job.status == 'succeeded'
        candidate = db.get(Strategy, job.payload['strategy']['id'])
        assert candidate.status == 'candidate' and candidate.parent_id == reversal_archive['parent_id']
        assert candidate.provenance['study_type'] == 'reversal-2026-v1'
        assert candidate.provenance['plan_hash'] == reversal_archive['artifact']['plan_hash']
        assert '2026反转研究' in candidate.name and '15候选' not in candidate.name
        study = job.result['strategy_study']
        assert '事后选优' in study['conclusion'] and '100股一手' in study['conclusion']
        assert study['attribution']['closed_episodes'] >= 5
        assert study['doubled_cost_metrics']['total_return'] > 0
        assert job.payload['spec']['start'] == '2026-01-02'
        assert job.payload['providers'] == [] and job.payload['spec']['model_ids'] == []
        assert db.get(Job, reversal_archive['source_id']).payload == source
        assert db.get(Setting, 'risk').value == risk
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'strategy_study.archived')) == 1
        payload = deepcopy(job.payload)
        expected = deepcopy(job.result)
    replay = runner.run(payload)
    assert replay['metrics'] == expected['metrics']
    assert replay['trades'] == expected['trades']
    assert replay['curve'] == expected['curve']


@pytest.mark.parametrize('failure', ['plan_hash', 'plan_conditions', 'plan_path', 'unknown_type', 'report', 'summary'])
def test_reversal_archive_rejects_changed_evidence(database, reversal_archive, failure):
    artifact = deepcopy(reversal_archive['artifact'])
    if failure == 'plan_hash':
        artifact['plan_hash'] = 'wrong'
    elif failure == 'plan_conditions':
        path = reversal_archive['plan_path']
        plan = json.loads(path.read_text())
        plan['min_closed_episodes'] = 1
        path.write_text(json.dumps(plan))
        artifact['plan_hash'] = fingerprint(plan)
    elif failure == 'plan_path':
        artifact['plan_path'] = 'research/another/plan.json'
    elif failure == 'unknown_type':
        artifact['summary']['study_type'] = 'unknown'
    elif failure == 'report':
        artifact['reports'][artifact['summary']['selected_key']]['metrics']['total_return'] = 9
    else:
        artifact['summary']['selected_key'] = 'baseline-kama'
    reversal_archive['path'].write_text(json.dumps(artifact))
    with database() as db:
        before = db.scalar(select(func.count()).select_from(Strategy))
    with pytest.raises(ValueError):
        with database.begin() as db:
            archive_study(db, 'research/reversal/comparison.json', 'reversal-archive-rejection')
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Strategy)) == before
        assert db.scalar(select(func.count()).select_from(Job)) == 1
