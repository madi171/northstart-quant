"""Compute-only subprocess entrypoint. The parent persists approved results."""
import hashlib
import json
import sys
from dataclasses import asdict
from .backtest import simulate
from .providers import load_bars, load_news, MODEL_DRIVERS, data_path
from .frozen_market import read_frozen_bars, read_warmup_bars
from .strategy_revision import compare_revision
from .schemas import ProposalExplanation, StrategyInput
from .research_features import STYLES, freeze_features


def run(payload, progress=None):
    spec, asset, strategy, providers, risk = [payload[k] for k in ['spec', 'asset', 'strategy', 'providers', 'risk']]
    features = payload.get('feature_snapshot')
    if features and freeze_features(features['records'])['hash'] != features['hash']:
        raise ValueError('外部特征快照指纹不一致')
    records = (features or {}).get('records', spec.get('feature_records', []))
    if any(row['symbol'] not in (asset['symbol'], '*') for row in records):
        raise ValueError('特征标的与实验不一致')
    if payload.get('derivation') and not payload.get('frozen_data'):
        raise ValueError('策略复测缺少冻结行情，拒绝重新获取数据')
    if payload.get('frozen_data'):
        frozen = payload['frozen_data']
        bars = read_frozen_bars(frozen)
        source = frozen['source']
    else:
        bars, source = load_bars(providers, spec['mode'], asset['symbol'], spec['start'], spec['end'])
    options = dict(initial=spec['initial'], lot=asset['lot'], fee_bps=spec['fee_bps'], slippage_bps=spec['slippage_bps'], max_weight=risk['max_weight'])
    options['feature_records'] = records
    options['rebalance_every'] = (STYLES[spec.get('trading_style', 'balanced')]['rebalance_every']
                                  if spec['kind'] == 'optimization' else payload.get('rebalance_every', 1))
    warmup = read_warmup_bars(payload, bars)
    if warmup:
        options['warmup_bars'] = warmup
    if spec['kind'] == 'backtest' and progress:
        progress({'phase': 'simulating', 'completed': 0, 'total': len(bars), 'date': None})
        options['on_progress'] = lambda completed, total, day: progress({
            'phase': 'simulating', 'completed': completed, 'total': total, 'date': day})
    result = simulate(bars, strategy['code'], strategy['params'], **options)
    options.pop('on_progress', None)
    if spec['kind'] == 'backtest' and progress:
        progress({'phase': 'finalizing', 'completed': len(bars), 'total': len(bars), 'date': bars[-1].date})
    result.update(source=source, currency=asset['currency'], symbol=asset['symbol'], strategy_id=strategy['id'], data_mode=spec['mode'])
    if source.get('warning'):
        result['warnings'].append(source['warning'])
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = data_path(f"snapshots/{result['data_hash']}.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Unique temporary file then atomic replacement, safe across independent workers.
        from uuid import uuid4
        temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
        pq.write_table(pa.Table.from_pylist([asdict(b) for b in bars]), temporary)
        temporary.replace(path)
    result['snapshot'] = str(path.relative_to(data_path('')))
    if payload.get('derivation'):
        result['comparison'] = compare_revision(payload, result, bars, options)
    if spec['kind'] == 'optimization':
        from .optimization import run_optimization
        return run_optimization(payload, bars, result, options, progress=progress)
    if spec['kind'] != 'research':
        return result
    # The last 30% is never passed to models or used for candidate selection.
    split = int(len(bars) * .7)
    if split < 40 or len(bars) - split < 20:
        raise ValueError('研究至少需要约90个交易日，包含独立样本外区间')
    train, holdout = bars[:split], bars[split:]
    train_report = simulate(train, strategy['code'], strategy['params'], **options)
    news = load_news(providers, train[-1].date + 'T23:59:59+00:00')
    if spec['mode'] != 'demo':
        news['items'] = [item for item in news['items'] if not item.get('synthetic')]
    context = {'strategy': {k: strategy[k] for k in ('code', 'params', 'name')},
               'training_metrics': train_report['metrics'], 'training_end': train[-1].date,
               'prices': [asdict(b) for b in train[-90:]], 'news': news['items'][:15],
               'instruction': 'Generate one candidate. An unseen holdout will be evaluated independently.'}
    candidates, failures = [], []
    selected = [p for p in providers if p['id'] in spec['model_ids'] and p['enabled']]
    context_hash = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    for provider in selected[:4]:
        try:
            candidate = MODEL_DRIVERS[provider['driver']](provider['config']).propose(context)
            explanation = ProposalExplanation.model_validate({field:candidate[field] for field in ProposalExplanation.model_fields if field in candidate})
            validated = StrategyInput(name=explanation.name or f"{strategy['name'][:40]} · {provider['name'][:20]}", code=candidate['code'], params=candidate['params'],
                                      parent_id=strategy['id'], notes=str(candidate.get('reason', ''))[:2000])
            from .optimization import describe_change
            training = simulate(train, validated.code, validated.params, **options)
            candidates.append({**explanation.model_dump(), **validated.model_dump(), 'provider_id': provider['id'], 'provider_name': provider['name'],
                               'naming_source':'demo' if provider['driver']=='demo_model' else 'model' if explanation.name else 'fallback',
                               'change':describe_change(strategy, validated, spec),
                               'training_metrics': training['metrics'], 'usage': candidate.get('usage', {}),
                               'context_hash': context_hash, 'prompt_version': 'research-v2'})
        except Exception as exc:
            failures.append({'provider': provider['name'], 'error': str(exc)[:250]})
    if not candidates:
        raise ValueError('所有研究模型均失败：' + json.dumps(failures, ensure_ascii=False))
    # Rank by training only. OOS is a report for human review, never selection input.
    best = max(candidates, key=lambda c: c['training_metrics']['sharpe'])
    holdout_options = {**options, 'warmup_bars': (warmup or []) + train}
    baseline = simulate(holdout, strategy['code'], strategy['params'], **holdout_options)
    evaluated = simulate(holdout, best['code'], best['params'], **holdout_options)
    # Each segment starts in cash and only uses preceding bars for indicators.
    segments = []
    for i in range(3):
        start = int(len(bars) * (.4 + i * .2))
        chunk = bars[start:int(len(bars) * (.6 + i * .2))]
        if chunk:
            segment_options = {**options, 'warmup_bars': (warmup or []) + bars[:start]}
            segments.append({'start': chunk[0].date, 'end': chunk[-1].date,
                             'warmup_days': len(segment_options['warmup_bars']),
                             'metrics': simulate(chunk, best['code'], best['params'], **segment_options)['metrics']})
    result['research'] = {'candidates': candidates, 'selected': best, 'failures': failures,
                          'holdout_baseline': baseline['metrics'], 'holdout_candidate': evaluated['metrics'],
                          'holdout_start': holdout[0].date, 'holdout_warmup_days': len(holdout_options['warmup_bars']), 'segments': segments,
                          'news_count': len(news['items']), 'news_failures': news['failures'],
                          'note': '按训练集夏普排序选一个候选；最后30%仅在本次任务内隔离评估；各段从现金开始，仅用此前行情预热指标。'
                                  '分段与训练区间可能重叠，是回顾诊断，不是重新拟合的walk-forward；看过留出结果后再调参会失去独立性。需人工审核。'}
    return result


if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        def progress(result):
            if len(sys.argv) > 1:
                from pathlib import Path
                path = Path(sys.argv[1])
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
                temporary.replace(path)
        print(json.dumps({'ok': True, 'result': run(payload, progress=progress)}, ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:1000]}, ensure_ascii=False))
        sys.exit(1)
