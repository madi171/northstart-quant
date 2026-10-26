from copy import deepcopy
import hashlib
import json

from sqlalchemy import select
from .db import Job, Strategy, audit, serialize
from .frozen_market import read_frozen_bars, read_warmup_bars
from .schemas import RunInput, StrategyInput


def derive_strategy(db, baseline_id, request):
    fingerprint = hashlib.sha256(json.dumps({'baseline_job_id': baseline_id, **request.model_dump(exclude={'client_key'})}, sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(Job).where(Job.dedup == request.client_key))
    if existing:
        if existing.payload.get('derivation', {}).get('request_hash') != fingerprint:
            raise ValueError('幂等键已被不同策略调整请求使用')
        return existing
    baseline = db.get(Job, baseline_id)
    if not baseline or baseline.status != 'succeeded' or baseline.kind not in ('backtest', 'research'):
        raise ValueError('只能基于已完成的回测或研究主策略报告调整策略')
    payload, result = deepcopy(baseline.payload), baseline.result
    if not result.get('curve') or not result.get('metrics') or not payload.get('strategy'):
        raise ValueError('原实验缺少完整策略和回测报告')
    original = payload['strategy']
    validated = StrategyInput(name=request.name, code=request.code, params=request.params,
                              notes=request.notes, parent_id=original['id'])
    if not db.get(Strategy, original['id']):
        raise ValueError('原策略版本不存在')
    frozen = payload.get('frozen_data') or {'path': result.get('snapshot'), 'hash': result.get('data_hash'), 'source': result.get('source')}
    if frozen.get('hash') != result.get('data_hash') or frozen.get('source') != result.get('source'):
        raise ValueError('原实验行情来源或指纹不一致')
    if result.get('snapshot') and frozen.get('path') != result['snapshot']:
        raise ValueError('原实验冻结快照路径不一致')
    bars = read_frozen_bars(frozen)
    warmup = read_warmup_bars(payload, bars)
    spec = payload['spec']
    if [bar.date for bar in bars] != [point['date'] for point in result['curve']] or any(not spec['start'] <= bar.date <= spec['end'] for bar in bars):
        raise ValueError('原实验日期与冻结行情不一致')
    asset, risk = payload['asset'], payload['risk']
    if result.get('symbol') != asset['symbol'] or result.get('currency') != asset['currency'] or not 0 < risk['max_weight'] <= 1:
        raise ValueError('原实验标的、币种或仓位条件不完整')
    conditions = {key: spec[key] for key in ('mode', 'start', 'end', 'initial', 'fee_bps', 'slippage_bps')}
    conditions.update(symbol=asset['symbol'], currency=asset['currency'], lot=asset['lot'], max_weight=risk['max_weight'])
    if warmup:
        conditions.update(warmup_hash=payload['warmup_data']['hash'], warmup_start=warmup[0].date,
                          warmup_end=warmup[-1].date, warmup_days=len(warmup))
    derivation = {'baseline_job_id': baseline_id, 'original_strategy_id': original['id'],
                  'baseline_strategy_name': original['name'], 'baseline_metrics': deepcopy(result['metrics']),
                  'data_hash': frozen['hash'], 'conditions': conditions, 'request_hash': fingerprint,
                  'note': '复用原行情与实验条件；原策略在当前引擎下重放后对比。该区间已用于策略调整，不属于独立样本外验证。'}
    strategy = Strategy(**validated.model_dump(), status='candidate',
                        provenance={'type': 'experiment_revision', **deepcopy(derivation),
                                    'feature_snapshot':deepcopy(payload.get('feature_snapshot', {})),
                                    'rebalance_every':payload.get('rebalance_every', 1)})
    db.add(strategy)
    db.flush()
    payload['spec'] = RunInput(**{**spec, 'kind': 'backtest', 'strategy_id': strategy.id,
                                  'model_ids': [], 'client_key': request.client_key}).model_dump(mode='json')
    payload['strategy'] = serialize(strategy)
    payload['providers'] = []
    payload['frozen_data'] = deepcopy(frozen)
    payload['derivation'] = derivation
    payload['baseline_strategy'] = deepcopy(original)
    job = Job(kind='backtest', payload=payload, dedup=request.client_key)
    db.add(job)
    db.flush()
    audit(db, 'strategy.derived', strategy_id=strategy.id, baseline_job_id=baseline_id, job_id=job.id)
    audit(db, 'job.enqueued', job_id=job.id, kind=job.kind)
    return job


def compare_revision(payload, result, bars, options):
    from .backtest import simulate
    derivation = payload['derivation']
    if result['data_hash'] != derivation['data_hash']:
        raise ValueError('策略复测数据与原实验不一致')
    original = payload['baseline_strategy']
    replay = simulate(bars, original['code'], original['params'], **options)
    baseline_metrics = replay['metrics']
    return {'baseline_job_id': derivation['baseline_job_id'],
            'baseline_strategy_name': derivation['baseline_strategy_name'],
            'baseline_metrics': baseline_metrics, 'candidate_metrics': result['metrics'],
            'delta_metrics': {key: result['metrics'][key] - value for key, value in baseline_metrics.items()},
            'baseline_curve': replay['curve'], 'original_metrics': derivation['baseline_metrics'],
            'baseline_reproduced': baseline_metrics == derivation['baseline_metrics'],
            'conditions': derivation['conditions'], 'note': derivation['note']}
