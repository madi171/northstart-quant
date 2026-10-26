from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json

import pytest
from sqlalchemy import func, select

from app.backtest import simulate
from app.contracts import Bar
from app.db import Audit, Job, Order, Setting, Strategy
from app.frozen_market import freeze_bars
from app.schemas import StrategyRevisionInput
from app.strategy_revision import derive_strategy
from app.strategy_study_archive import archive_study


@pytest.fixture
def local_study(database, tmp_path, monkeypatch):
    from app import providers, strategy_study_archive
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    first = date(2023, 10, 16)
    bars = []
    for index in range(1100):
        day = first + timedelta(days=index)
        if day.weekday() < 5:
            price = 100 + len(bars) * .1
            bars.append(Bar(day.isoformat(), price, price + 2, price - 2, price + 1, 100000))
    warmup = [bar for bar in bars if bar.date < '2025-01-01']
    assessed = [bar for bar in bars if '2025-01-01' <= bar.date <= '2026-10-13']
    source = {'name': 'test historical fixture', 'mode': 'historical'}
    frozen = freeze_bars(bars, source)
    code = 'def target(history, params):\n    if len(history) < 240:\n        return 0.0\n    return params["weight"]'
    params = {'weight': .3}
    options = dict(initial=100000, lot=1, fee_bps=5, slippage_bps=5, max_weight=.3)
    report = simulate(assessed, code, params, warmup_bars=warmup, **options)
    summary = {'conditions': options, 'max_drawdown': .15, 'evaluation_start': assessed[0].date,
               'evaluation_end': assessed[-1].date, 'candidate_set_hash': 'fixture-candidates',
               'selected_key': 'baseline-kama', 'note': '回顾性比较，非独立样本外。',
               'rows': [{'key': 'baseline-kama', 'name': '原策略 KAMA', 'code': code,
                         'params': params, 'metrics': report['metrics']}]}
    with database.begin() as db:
        strategy = Strategy(name='Original', code=code, params=params, status='approved')
        db.add(strategy)
        db.flush()
        payload = {'spec': {'kind': 'backtest', 'asset_id': 'fixture', 'strategy_id': strategy.id,
                           'mode': 'historical', 'start': bars[0].date, 'end': bars[-1].date,
                           'initial': 100000, 'fee_bps': 5, 'slippage_bps': 5, 'max_drawdown': .15,
                           'client_key': 'source-experiment'},
                   'strategy': {'id': strategy.id, 'name': strategy.name, 'code': code, 'params': params},
                   'asset': {'id': 'fixture', 'symbol': 'HK.TEST', 'currency': 'HKD', 'lot': 1},
                   'risk': {'max_weight': .3}, 'frozen_data': frozen, 'providers': []}
        job = Job(kind='backtest', status='succeeded', payload=payload, result={'data_hash': frozen['hash']})
        db.add(job)
        db.flush()
        source_id, strategy_id = job.id, strategy.id
    artifact = {'summary': summary, 'reports': {'baseline-kama': report},
                'source_job_id': source_id, 'frozen_data': frozen}
    path = tmp_path / 'research/test/comparison.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(artifact))
    def replay(replayed, baseline, settings, start, end, max_drawdown):
        assert replayed == bars and baseline['code'] == code and settings == options
        assert [start, end] == [assessed[0].date, assessed[-1].date]
        assert max_drawdown == .15
        return deepcopy(summary), {'baseline-kama': deepcopy(report)}
    monkeypatch.setattr(strategy_study_archive, 'evaluate', replay)
    return {'source_id': source_id, 'strategy_id': strategy_id, 'path': path,
            'artifact': artifact, 'report': report, 'warmup': warmup}


def test_archive_is_idempotent_review_only_and_preserves_source(database, local_study):
    with database.begin() as db:
        source = deepcopy(db.get(Job, local_study['source_id']).payload)
        risk = deepcopy(db.get(Setting, 'risk').value)
        first = archive_study(db, 'research/test/comparison.json', 'archive-local-study')
        second = archive_study(db, 'research/test/comparison.json', 'archive-local-study')
        assert first.id == second.id
        assert first.status == 'succeeded' and first.kind == 'backtest'
        candidate = db.get(Strategy, first.payload['strategy']['id'])
        assert candidate.status == 'candidate' and candidate.parent_id == local_study['strategy_id']
        assert candidate.code == source['strategy']['code']
        assert first.result['strategy_study']['improved_over_baseline'] is False
        assert '未发现优于' in first.result['strategy_study']['conclusion']
        assert first.payload['spec']['model_ids'] == [] and first.payload['providers'] == []
        assert first.payload['spec']['start'] == '2025-01-01'
        assert first.payload['warmup_data']['hash'] == candidate.provenance['warmup_hash']
        assert first.result['metrics'] == local_study['report']['metrics']
        assert first.payload['risk'] == source['risk']
        assert db.get(Job, local_study['source_id']).payload == source
        assert db.get(Strategy, local_study['strategy_id']).status == 'approved'
        assert db.get(Setting, 'risk').value == risk
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'strategy_study.archived')) == 1


def test_archive_idempotency_rejects_changed_content(database, local_study):
    with database.begin() as db:
        archive_study(db, 'research/test/comparison.json', 'archive-local-study')
    local_study['path'].write_text(local_study['path'].read_text() + '\n')
    with pytest.raises(ValueError, match='幂等键'):
        with database.begin() as db:
            archive_study(db, 'research/test/comparison.json', 'archive-local-study')


@pytest.mark.parametrize('failure', ['report', 'summary', 'source', 'conditions', 'snapshot'])
def test_archive_rejects_unverifiable_evidence_without_rows(database, local_study, failure):
    with database() as db:
        strategies_before = db.scalar(select(func.count()).select_from(Strategy))
        jobs_before = db.scalar(select(func.count()).select_from(Job))
    artifact = deepcopy(local_study['artifact'])
    if failure == 'report':
        artifact['reports']['baseline-kama']['metrics']['total_return'] = 9
    elif failure == 'summary':
        artifact['summary']['note'] = 'incorrect claims'
    elif failure == 'source':
        artifact['frozen_data']['source']['name'] = 'another source'
    elif failure == 'conditions':
        artifact['summary']['conditions']['max_weight'] = 1
    else:
        from app.providers import data_path
        data_path(artifact['frozen_data']['path']).unlink()
    local_study['path'].write_text(json.dumps(artifact))
    with pytest.raises(ValueError):
        with database.begin() as db:
            archive_study(db, 'research/test/comparison.json', 'archive-local-study')
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Strategy)) == strategies_before
        assert db.scalar(select(func.count()).select_from(Job)) == jobs_before


def test_archive_replay_and_derived_strategy_retain_warmup_offline(database, local_study, monkeypatch):
    from app import runner
    monkeypatch.setattr(runner, 'load_bars', lambda *args: pytest.fail('must stay offline'))
    monkeypatch.setattr(runner, 'load_news', lambda *args: pytest.fail('must stay offline'))
    with database.begin() as db:
        job = archive_study(db, 'research/test/comparison.json', 'archive-local-study')
        payload = deepcopy(job.payload)
        candidate_id = payload['strategy']['id']
        derived = derive_strategy(db, job.id, StrategyRevisionInput(
            name='Reproduced', code=payload['strategy']['code'], params=payload['strategy']['params'],
            client_key='derived-with-warmup'))
        derived_payload = deepcopy(derived.payload)
        assert derived.payload['warmup_data'] == payload['warmup_data']
        assert derived.payload['derivation']['conditions']['warmup_days'] == len(local_study['warmup'])
        assert db.get(Strategy, candidate_id).status == 'candidate'
    replay = runner.run(payload)
    assert replay['metrics'] == local_study['report']['metrics']
    assert replay['trades'] == local_study['report']['trades']
    comparison = runner.run(derived_payload)['comparison']
    assert comparison['baseline_reproduced'] is True
    assert comparison['delta_metrics']['total_return'] == 0


@pytest.mark.parametrize('failure', ['missing', 'hash', 'source', 'date'])
def test_runner_refuses_invalid_study_warmup(database, local_study, monkeypatch, failure):
    from app import runner
    monkeypatch.setattr(runner, 'load_bars', lambda *args: pytest.fail('must stay offline'))
    with database.begin() as db:
        payload = deepcopy(archive_study(db, 'research/test/comparison.json', 'archive-local-study').payload)
    if failure == 'missing':
        payload.pop('warmup_data')
    elif failure == 'hash':
        payload['warmup_data']['hash'] = 'wrong'
    elif failure == 'source':
        payload['warmup_data']['source'] = {'name': 'changed'}
    else:
        from app.providers import data_path
        import pyarrow as pa
        import pyarrow.parquet as pq
        row = Bar(payload['spec']['start'], 100, 102, 99, 101, 10000)
        fingerprint = hashlib.sha256(json.dumps([asdict(row)], sort_keys=True).encode()).hexdigest()
        path = data_path('snapshots/overlap.parquet')
        pq.write_table(pa.Table.from_pylist([asdict(row)]), path)
        payload['warmup_data'].update(path='snapshots/overlap.parquet', hash=fingerprint)
        payload['study_archive']['warmup_hash'] = fingerprint
    with pytest.raises(ValueError):
        runner.run(payload)
