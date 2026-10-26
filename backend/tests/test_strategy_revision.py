from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select

from app.backtest import simulate
from app.contracts import Bar
from app.db import Asset, Job, Setting, Strategy
from app.schemas import RunInput, StrategyRevisionInput
from app.services import enqueue
from app.strategy_revision import derive_strategy


@pytest.fixture
def experiment(database, tmp_path, monkeypatch):
    from app import providers
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    code = 'def target(history, params):\n return params["weight"]'
    bars = [Bar((date(2024, 1, 1) + timedelta(days=i)).isoformat(),
                100 + i, 103 + i, 99 + i, 102 + i, 10000) for i in range(80)]
    with database.begin() as db:
        original = Strategy(name='Original', code=code, params={'weight': .1}, status='approved')
        asset = Asset(symbol='US.TEST', name='Test', market='US', currency='USD', lot=2)
        db.add_all([original, asset])
        db.flush()
        spec = RunInput(strategy_id=original.id, asset_id=asset.id, start=bars[0].date,
                        end=bars[-1].date, initial=50000, fee_bps=7, slippage_bps=13,
                        mode='historical', client_key='original-experiment')
        job = enqueue(db, spec)
        result = simulate(bars, code, original.params, initial=spec.initial, lot=asset.lot,
                          fee_bps=spec.fee_bps, slippage_bps=spec.slippage_bps,
                          max_weight=job.payload['risk']['max_weight'])
        result.update(symbol=asset.symbol, currency=asset.currency, source={'name': 'fixture', 'mode': 'historical'},
                      data_mode='historical', strategy_id=original.id,
                      snapshot='snapshots/' + result['data_hash'] + '.parquet')
        path = tmp_path / result['snapshot']
        path.parent.mkdir()
        pq.write_table(pa.Table.from_pylist([asdict(bar) for bar in bars]), path)
        job.result, job.status = result, 'succeeded'
        job.payload = {**job.payload, 'frozen_data': {'path': result['snapshot'],
                       'hash': result['data_hash'], 'source': result['source']}}
        return {'id': job.id, 'strategy_id': original.id, 'asset_id': asset.id,
                'payload': deepcopy(job.payload), 'result': deepcopy(result), 'path': path}


def request(key='revision-request', weight=.2):
    return StrategyRevisionInput(name='Adjusted', code='def target(history, params):\n return params["weight"]',
                                 params={'weight': weight}, notes='reduce missed gains', client_key=key)


def test_revision_inherits_experiment_not_current_settings(database, experiment):
    with database.begin() as db:
        db.get(Strategy, experiment['strategy_id']).code = 'def target(history, params):\n return 0'
        db.get(Asset, experiment['asset_id']).enabled = False
        db.get(Setting, 'risk').value = {**db.get(Setting, 'risk').value, 'max_weight': .01}
        job = derive_strategy(db, experiment['id'], request())
        assert job.kind == 'backtest' and job.status == 'queued'
        assert job.payload['risk'] == experiment['payload']['risk']
        assert job.payload['asset'] == experiment['payload']['asset']
        assert job.payload['frozen_data'] == experiment['payload']['frozen_data']
        assert job.payload['baseline_strategy'] == experiment['payload']['strategy']
        for key in ('mode', 'start', 'end', 'initial', 'fee_bps', 'slippage_bps'):
            assert job.payload['spec'][key] == experiment['payload']['spec'][key]
        assert job.payload['providers'] == [] and job.payload['spec']['model_ids'] == []
        candidate = db.get(Strategy, job.payload['strategy']['id'])
        assert candidate.status == 'candidate' and candidate.parent_id == experiment['strategy_id']
        assert candidate.provenance['baseline_job_id'] == experiment['id']
        assert db.get(Job, experiment['id']).result == experiment['result']
        assert db.get(Job, experiment['id']).payload == experiment['payload']


def test_revision_idempotency_includes_code_params_and_baseline(database, experiment):
    with database.begin() as db:
        count = db.scalar(select(func.count()).select_from(Strategy))
        first = derive_strategy(db, experiment['id'], request())
        second = derive_strategy(db, experiment['id'], request())
        assert first.id == second.id
        assert db.scalar(select(func.count()).select_from(Strategy)) == count + 1
    with pytest.raises(ValueError, match='幂等键'):
        with database.begin() as db:
            derive_strategy(db, experiment['id'], request(weight=.05))
    with pytest.raises(ValueError, match='幂等键'):
        with database.begin() as db:
            derive_strategy(db, 'other-baseline', request())


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'hash', 'traversal', 'date', 'source', 'snapshot_path'])
def test_bad_snapshot_refuses_revision_without_orphan(database, experiment, failure):
    with database.begin() as db:
        job = db.get(Job, experiment['id'])
        payload = deepcopy(job.payload)
        result = deepcopy(job.result)
        if failure == 'missing':
            experiment['path'].unlink()
        elif failure == 'corrupt':
            experiment['path'].write_bytes(b'bad parquet')
        elif failure == 'hash':
            payload['frozen_data']['hash'] = 'bad'
            result['data_hash'] = 'bad'
        elif failure == 'traversal':
            payload['frozen_data']['path'] = result['snapshot'] = '../../outside.parquet'
        elif failure == 'date':
            result['curve'][0]['date'] = '2020-01-01'
        elif failure == 'source':
            payload['frozen_data']['source'] = {'name': 'different'}
        else:
            payload['frozen_data']['path'] = 'another.parquet'
        job.payload, job.result = payload, result
        strategies = db.scalar(select(func.count()).select_from(Strategy))
        jobs = db.scalar(select(func.count()).select_from(Job))
    with pytest.raises(ValueError):
        with database.begin() as db:
            derive_strategy(db, experiment['id'], request())
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Strategy)) == strategies
        assert db.scalar(select(func.count()).select_from(Job)) == jobs


def test_legacy_snapshot_metadata_fallback(database, experiment):
    with database.begin() as db:
        original = db.get(Job, experiment['id'])
        original.payload = {key: value for key, value in original.payload.items() if key != 'frozen_data'}
        job = derive_strategy(db, original.id, request())
        assert job.payload['frozen_data'] == experiment['payload']['frozen_data']


@pytest.mark.parametrize('kind,status', [('signal','succeeded'), ('backtest','failed'), ('backtest','running')])
def test_only_completed_experiments_can_be_revised(database, experiment, kind, status):
    with database.begin() as db:
        original = db.get(Job, experiment['id'])
        original.kind, original.status = kind, status
    with pytest.raises(ValueError, match='已完成'):
        with database.begin() as db:
            derive_strategy(db, experiment['id'], request())


def test_invalid_code_and_empty_params_do_not_default(database, experiment):
    with pytest.raises(ValueError):
        with database.begin() as db:
            derive_strategy(db, experiment['id'], request().model_copy(update={'code':'import os'}))
    with database.begin() as db:
        body = request().model_copy(update={'params': {}, 'code':'def target(history, params):\n return 0'})
        job = derive_strategy(db, experiment['id'], body)
        assert job.payload['strategy']['params'] == {}


def test_same_engine_comparison_is_offline_and_requires_approval(database, experiment, monkeypatch):
    from app import runner
    from app.main import approve_strategy
    def forbidden(*args):
        pytest.fail('revision must never fetch market/news/model data')
    monkeypatch.setattr(runner, 'load_bars', forbidden)
    monkeypatch.setattr(runner, 'load_news', forbidden)
    with database.begin() as db:
        job = derive_strategy(db, experiment['id'], request())
        job_id, strategy_id, payload = job.id, job.payload['strategy']['id'], deepcopy(job.payload)
    with pytest.raises(ValueError, match='批准前'):
        approve_strategy(strategy_id)
    result = runner.run(payload)
    comparison = result['comparison']
    assert result['data_hash'] == experiment['result']['data_hash']
    assert comparison['baseline_metrics'] == experiment['result']['metrics']
    assert comparison['baseline_curve'] == experiment['result']['curve']
    assert comparison['baseline_reproduced'] is True
    assert comparison['candidate_metrics'] == result['metrics']
    assert comparison['conditions']['fee_bps'] == 7
    assert comparison['conditions']['max_weight'] == experiment['payload']['risk']['max_weight']
    assert comparison['delta_metrics']['total_return'] == result['metrics']['total_return'] - experiment['result']['metrics']['total_return']
    with database.begin() as db:
        job = db.get(Job, job_id)
        job.result, job.status = result, 'succeeded'
        assert db.get(Strategy, strategy_id).status == 'candidate'
    approved = approve_strategy(strategy_id)
    assert approved['status'] == 'approved'
    with database() as db:
        assert db.get(Strategy, experiment['strategy_id']).status == 'approved'
        assert db.get(Job, experiment['id']).result == experiment['result']


@pytest.mark.parametrize('failure', ['metadata', 'file', 'hash'])
def test_runner_revalidates_before_replay(database, experiment, monkeypatch, failure):
    from app import runner
    monkeypatch.setattr(runner, 'load_bars', lambda *args: pytest.fail('no fallback allowed'))
    with database.begin() as db:
        payload = deepcopy(derive_strategy(db, experiment['id'], request()).payload)
    if failure == 'metadata':
        payload['frozen_data'] = {}
    elif failure == 'file':
        experiment['path'].unlink()
    else:
        payload['frozen_data']['hash'] = 'changed'
    with pytest.raises(ValueError):
        runner.run(payload)


def test_saved_metrics_drift_uses_same_engine_baseline(database, experiment):
    from app.runner import run
    with database.begin() as db:
        original = db.get(Job, experiment['id'])
        changed = deepcopy(original.result)
        changed['metrics']['sharpe'] = -99
        original.result = changed
        payload = deepcopy(derive_strategy(db, original.id, request()).payload)
    comparison = run(payload)['comparison']
    assert comparison['baseline_reproduced'] is False
    assert comparison['original_metrics']['sharpe'] == -99
    assert comparison['baseline_metrics']['sharpe'] == experiment['result']['metrics']['sharpe']


def test_api_response_and_request_allow_only_strategy_changes(database, experiment):
    from app.main import revise_strategy
    response = revise_strategy(experiment['id'], request())
    assert response['baseline_job_id'] == experiment['id']
    assert response['status'] == 'queued'
    with database() as db:
        assert db.get(Strategy, response['strategy_id']).status == 'candidate'
    with pytest.raises(ValueError):
        StrategyRevisionInput(**{**request().model_dump(), 'status': 'approved'})
    with pytest.raises(ValueError):
        StrategyRevisionInput(**{**request().model_dump(), 'fee_bps': 0})


def test_worker_persists_comparison_without_orders_or_autoapproval(database, experiment, monkeypatch):
    from app.db import Order
    from app import worker
    monkeypatch.setenv('DATA_DIR', str(experiment['path'].parent.parent))
    with database.begin() as db:
        job = derive_strategy(db, experiment['id'], request())
        job_id, strategy_id = job.id, job.payload['strategy']['id']
    claimed = worker.claim()
    assert claimed[0] == job_id
    worker.execute(*claimed)
    with database() as db:
        finished = db.get(Job, job_id)
        assert finished.status == 'succeeded', finished.error
        assert finished.result['comparison']['baseline_job_id'] == experiment['id']
        assert finished.result['comparison']['baseline_reproduced'] is True
        assert finished.result['comparison']['candidate_metrics'] == finished.result['metrics']
        assert db.get(Strategy, strategy_id).status == 'candidate'
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.get(Job, experiment['id']).result == experiment['result']


def test_research_derivation_targets_original_not_model_candidate(database, experiment):
    with database.begin() as db:
        original = db.get(Job, experiment['id'])
        original.kind = 'research'
        original.payload = {**original.payload, 'spec': {**original.payload['spec'], 'kind': 'research', 'model_ids': ['unused-model']}}
        original.result = {**original.result, 'research': {'candidate_id': 'other-candidate'}}
        job = derive_strategy(db, original.id, request())
        assert job.kind == 'backtest'
        assert job.payload['strategy']['parent_id'] == experiment['strategy_id']
        assert job.payload['baseline_strategy']['id'] == experiment['strategy_id']
        assert job.payload['spec']['model_ids'] == []


def test_next_revision_tracks_immediate_baseline(database, experiment):
    from app.runner import run
    with database.begin() as db:
        first = derive_strategy(db, experiment['id'], request())
        result = run(first.payload)
        first.result, first.status = result, 'succeeded'
        first.payload = {**first.payload, 'frozen_data': {'path': result['snapshot'], 'hash': result['data_hash'], 'source': result['source']}}
        second = derive_strategy(db, first.id, request(key='second-revision', weight=.15))
        assert second.payload['derivation']['baseline_job_id'] == first.id
        assert second.payload['baseline_strategy']['id'] == first.payload['strategy']['id']
        assert second.payload['strategy']['parent_id'] == first.payload['strategy']['id']
        assert second.payload['derivation']['baseline_metrics'] == first.result['metrics']

