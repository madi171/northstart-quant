"""Archive a verified local study as a review-only strategy and backtest."""
from copy import deepcopy
import hashlib
import json

from sqlalchemy import select

from .db import Job, Strategy, audit, now, serialize
from .frozen_market import freeze_bars, read_frozen_bars
from .providers import data_path
from .schemas import RunInput, StrategyInput
from .strategy_study import evaluate


def archive_study(db, artifact_path, client_key):
    if not artifact_path.startswith('research/') or not artifact_path.endswith('/comparison.json'):
        raise ValueError('仅支持本地研究目录中的 comparison.json')
    path = data_path(artifact_path)
    if path.stat().st_size > 20_000_000:
        raise ValueError('研究文件超过20MB')
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    existing = db.scalar(select(Job).where(Job.dedup == client_key))
    if existing:
        prior = existing.payload.get('study_archive', {})
        if prior.get('artifact_hash') != fingerprint or prior.get('artifact_path') != artifact_path:
            raise ValueError('幂等键已被不同研究归档使用')
        return existing
    artifact = json.loads(raw)
    summary = artifact['summary']
    original = db.get(Job, artifact['source_job_id'])
    if not original or original.status != 'succeeded':
        raise ValueError('研究原任务不存在或未成功完成')
    payload = deepcopy(original.payload)
    frozen = payload.get('frozen_data')
    if frozen != artifact['frozen_data'] or frozen['hash'] != original.result.get('data_hash'):
        raise ValueError('研究与原任务的冻结行情不一致')
    bars = read_frozen_bars(frozen)
    spec, asset, risk = payload['spec'], payload['asset'], payload['risk']
    options = dict(initial=spec['initial'], lot=asset['lot'], fee_bps=spec['fee_bps'],
                   slippage_bps=spec['slippage_bps'], max_weight=risk['max_weight'])
    if summary['conditions'] != options or summary['max_drawdown'] != spec.get('max_drawdown', .15):
        raise ValueError('研究仓位、费用或回撤约束与原任务不一致')
    started = now()
    study_type = summary.get('study_type')
    if study_type == 'reversal-2026-v1':
        from .reversal_study import evaluate as evaluate_reversal, study_plan, fingerprint as plan_fingerprint
        plan_path = artifact_path.removesuffix('comparison.json') + 'plan.json'
        if artifact.get('plan_path') != plan_path:
            raise ValueError('反转研究计划路径不一致')
        plan = json.loads(data_path(plan_path).read_text())
        expected = study_plan(payload['strategy'], options, frozen['hash'],
                              summary['evaluation_start'], summary['evaluation_end'])
        if plan != expected or plan_fingerprint(plan) != artifact.get('plan_hash'):
            raise ValueError('反转研究冻结计划校验失败')
        evaluate_study = evaluate_reversal
    elif study_type is None:
        evaluate_study = evaluate
    else:
        raise ValueError('未知研究类型，拒绝归档')
    reproduced, reports = evaluate_study(bars, payload['strategy'], options, summary['evaluation_start'],
                                         summary['evaluation_end'], max_drawdown=summary['max_drawdown'])
    if reproduced != summary or reports != artifact['reports']:
        raise ValueError('研究重放与归档证据不一致，拒绝导入')
    key = summary['selected_key']
    if not key:
        raise ValueError('研究没有满足约束的策略')
    selected = next(row for row in summary['rows'] if row['key'] == key)
    baseline = next(row for row in summary['rows'] if row['key'] == 'baseline-kama')
    improved = selected['metrics']['total_return'] > baseline['metrics']['total_return']
    conclusion = ('发现历史累计收益更高的候选；仍待人工审核。' if improved else
                  '15候选中原KAMA累计收益最高，本次未发现优于原策略的方案；归档成功不代表优化成功。')
    if study_type == 'reversal-2026-v1':
        conclusion = (f'固定18个新增反转配置中，{selected["name"]}在2026年简化条件下净收益'
                      f'{selected["metrics"]["total_return"]:.2%}、'
                      f'{selected["attribution"]["closed_episodes"]}个完整回合；'
                      '按2026年事后选优，不是独立验证或实盘盈利承诺。'
                      f'原本金{options["initial"]:g}、100股一手敏感性净收益'
                      f'{summary["lot_sensitivity"][0]["metrics"]["total_return"]:.2%}、'
                      f'{summary["lot_sensitivity"][0]["metrics"]["trade_count"]}条成交记录。')
    start, end = summary['evaluation_start'], summary['evaluation_end']
    assessed = [bar for bar in bars if start <= bar.date <= end]
    warmup = [bar for bar in bars if bar.date < start]
    evaluation_data = freeze_bars(assessed, frozen['source'])
    warmup_data = freeze_bars(warmup, frozen['source'])
    provenance = {'type': 'finite_strategy_study', 'source_job_id': original.id,
                  'artifact_path': artifact_path, 'artifact_hash': fingerprint,
                  'candidate_set_hash': summary['candidate_set_hash'], 'selected_key': key,
                  'data_hash': evaluation_data['hash'], 'warmup_hash': warmup_data['hash'],
                  'improved_over_baseline': improved}
    if study_type:
        provenance.update(study_type=study_type, plan_hash=artifact['plan_hash'],
                          objective=summary['objective'])
    parent_id = payload['strategy']['id']
    if not db.get(Strategy, parent_id):
        raise ValueError('研究原策略版本不存在')
    label = '2026反转研究' if study_type else '15候选研究归档'
    validated = StrategyInput(name=f'腾讯 · {selected["name"]} · {label}',
                              code=selected['code'], params=selected['params'], parent_id=parent_id,
                              notes=conclusion + summary['note'])
    strategy = Strategy(**validated.model_dump(), status='candidate', provenance=deepcopy(provenance))
    db.add(strategy)
    db.flush()
    payload['spec'] = RunInput(**{**spec, 'kind': 'backtest', 'strategy_id': strategy.id,
                                  'start': start, 'end': end, 'model_ids': [],
                                  'client_key': client_key}).model_dump(mode='json')
    payload.update(strategy=serialize(strategy), providers=[], frozen_data=evaluation_data,
                   warmup_data=warmup_data, study_archive=provenance)
    for field in ('derivation', 'baseline_strategy'):
        payload.pop(field, None)
    result = deepcopy(reports[key])
    result.update(source=deepcopy(frozen['source']), currency=asset['currency'], symbol=asset['symbol'],
                  strategy_id=strategy.id, data_mode=spec['mode'], snapshot=evaluation_data['path'],
                  strategy_study={**deepcopy(summary), **provenance, 'conclusion': conclusion})
    result['warnings'].extend([conclusion, summary['note'],
        f'评估前预热{len(warmup)}根日线，另存冻结快照；按原实验每手{asset["lot"]}股和简化费率研究，并非真实港股完整交易规则。',
        '2025与2026分段各自从现金开始，保留此前信号预热，不等同于全区间连续持仓收益拆分。'])
    if study_type == 'reversal-2026-v1':
        result['warnings'].extend([
            '季度各自从现金开始，不能直接相加或连乘替代全年连续回测；四季度只截至已有行情。',
            'Bootstrap未校正本次及此前多轮选优偏差，即使区间不跨零也不是盈利显著性证明。',
            '尚无未参与选优的未来验证；请结合参数邻域结果评估敏感性。'])
        for neighbor in summary['neighbor_rows']:
            if neighbor['metrics']['total_return'] < 0:
                result['warnings'].append(f'邻近参数{neighbor["name"]}在2026年亏损，选中值存在参数敏感性。')
    if frozen['source'].get('warning'):
        result['warnings'].append(frozen['source']['warning'])
    job = Job(kind='backtest', status='succeeded', dedup=client_key, payload=payload, result=result,
              started_at=started, finished_at=now(), attempts=1)
    db.add(job)
    db.flush()
    audit(db, 'strategy_study.archived', job_id=job.id, strategy_id=strategy.id, **provenance)
    return job
