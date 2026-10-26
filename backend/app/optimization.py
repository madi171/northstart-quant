from copy import deepcopy
import hashlib
import json
import time
from dataclasses import asdict
import ast
from difflib import SequenceMatcher, unified_diff
from datetime import datetime, timezone

from sqlalchemy import select
from .backtest import simulate
from .db import Job, Strategy, audit
from .frozen_market import read_frozen_bars
from .providers import MODEL_DRIVERS, validate_model_config
from .schemas import ProposalExplanation, StrategyInput
from .services import snapshot
from .research_features import STYLES, PRICE_FEATURES, FeatureTimeline, freeze_features


NOTE = ('收益目标仅用于训练/调优验证区间，不是收益保证。验证区间被反复使用，属于调优数据；'
        '最后20%测试区间仅对最终选定版本评估一次，不反馈模型或用于选优。'
        '训练和测试均从现金开始，使用此前价格预热；无杠杆、不改变费用/滑点/仓位上限，不自动批准或下单。'
        '历史行情及模型预训练可能产生过拟合或知识泄漏，最终测试也不能代替真正未来数据验证。')


def enqueue_optimization(db, spec):
    original = spec.model_dump(mode='json')
    existing = db.scalar(select(Job).where(Job.dedup == spec.client_key))
    if existing:
        if existing.payload.get('optimization_request') != original:
            raise ValueError('幂等键已用于不同的优化请求')
        return existing
    payload = snapshot(db, spec)
    enabled = {provider['id']: provider for provider in payload['providers'] if provider['kind'] == 'model' and provider['enabled']}
    selected = [enabled[identifier] for identifier in spec.model_ids]
    for provider in selected:
        if provider['driver'] == 'openai_compatible':
            validate_model_config(provider['config'])
    if spec.mode != 'demo' and any(provider['driver'] == 'demo_model' for provider in selected):
        raise ValueError('历史行情自动优化必须选择真实LLM，演示研究器仅可用于演示环境')
    if spec.baseline_job_id:
        baseline = db.get(Job, spec.baseline_job_id)
        if not baseline or baseline.status != 'succeeded' or baseline.kind not in ('backtest', 'research'):
            raise ValueError('请选择已完成的历史回测或研究主策略实验')
        previous, result = deepcopy(baseline.payload), baseline.result
        if previous['strategy']['id'] != spec.strategy_id or previous['asset']['id'] != spec.asset_id:
            raise ValueError('基准实验的策略或标的与选择不一致')
        if previous['spec']['mode'] != spec.mode:
            raise ValueError('基准实验与数据环境不一致')
        frozen = previous.get('frozen_data') or {'path':result.get('snapshot'), 'hash':result.get('data_hash'), 'source':result.get('source')}
        if frozen.get('hash') != result.get('data_hash') or frozen.get('source') != result.get('source') or frozen.get('path') != result.get('snapshot'):
            raise ValueError('原实验快照元数据不一致')
        bars = read_frozen_bars(frozen)
        if [bar.date for bar in bars] != [point['date'] for point in result.get('curve', [])] or any(not original['start'] <= bar.date <= original['end'] for bar in bars):
            raise ValueError('原实验快照与回测日期不一致')
        if result.get('symbol') != previous['asset']['symbol'] or result.get('currency') != previous['asset']['currency'] or result.get('data_mode') != spec.mode:
            raise ValueError('原实验标的、币种或行情模式不一致')
        for key in ('start', 'end', 'initial', 'fee_bps', 'slippage_bps'):
            if original[key] != previous['spec'][key]:
                raise ValueError('从实验优化时必须沿用原始日期、初始资金、费用和滑点')
        previous_features = previous.get('feature_snapshot', freeze_features(previous['spec'].get('feature_records', [])))
        if spec.feature_records and payload['feature_snapshot']['hash'] != previous_features['hash']:
            raise ValueError('从实验优化时必须沿用原始外部特征快照')
        if len(bars) < 150:
            raise ValueError('自动优化至少需要150个交易日，以保留独立测试区间')
        payload.update(asset=previous['asset'], strategy=previous['strategy'], risk=previous['risk'], frozen_data=deepcopy(frozen),
                       feature_snapshot=deepcopy(previous_features))
        for key in ('warmup_data', 'study_archive'):
            if key in previous:
                payload[key] = deepcopy(previous[key])
    payload['optimization_request'] = original
    row = Job(kind='optimization', payload=payload, dedup=spec.client_key)
    db.add(row)
    db.flush()
    audit(db, 'optimization.enqueued', job_id=row.id, target=spec.target_return, max_rounds=spec.max_rounds)
    return row


def eligible(metrics, spec):
    return (metrics['trade_count'] >= spec['min_trades'] and metrics['max_drawdown'] >= -spec['max_drawdown']
            and metrics['turnover'] <= spec.get('max_turnover', 50))


def score(metrics, spec):
    return (eligible(metrics, spec), metrics[spec['target_metric']], metrics['sharpe'])


def describe_change(parent, candidate, spec):
    before, after = parent['code'].splitlines(), candidate.code.splitlines()
    changed = sum(max(i2-i1, j2-j1) for tag, i1, i2, j1, j2 in SequenceMatcher(None, before, after).get_opcodes() if tag != 'equal')
    params = {key: {'before': parent['params'].get(key), 'after': candidate.params.get(key)}
              for key in sorted(parent['params'].keys() | candidate.params.keys())
              if parent['params'].get(key) != candidate.params.get(key)}
    change = {'changed_lines': changed, 'param_changes': params,
              'diff': '\n'.join(unified_diff(before, after, fromfile='incumbent', tofile='candidate', lineterm=''))}
    return change


def diagnose(training, evaluation, previous, spec):
    metrics = evaluation['metrics']
    reasons = []
    if metrics['trade_count'] < spec['min_trades']:
        reasons.append('成交不足，检查预热、触发条件、手数与资金')
    if metrics['max_drawdown'] < -spec['max_drawdown']:
        reasons.append('验证回撤超限，优先缩小暴露或增加风险过滤')
    if metrics['turnover'] > spec.get('max_turnover', 50):
        reasons.append('换手超限，降低调仓频率或增加信号阈值')
    if training['metrics']['total_return'] - metrics['total_return'] > .1:
        reasons.append('训练与验证收益差异较大，检查行情状态与过拟合；区间长度不同，不能直接归因')
    delta = {key: metrics[key] - previous[key] for key in metrics}
    return {'summary': '；'.join(reasons) or '风险与成交约束满足，按固定验证指标比较',
            'flags': reasons, 'delta_metrics': delta,
            'next_hypothesis': reasons[0] if reasons else '保持其余条件，单独检验窗口或阈值的局部敏感性'}


def run_optimization(payload, bars, base_report, options, progress=None):
    spec, strategy = payload['spec'], payload['strategy']
    if len(bars) < 150:
        raise ValueError('自动优化至少需要150个交易日，以保留独立测试区间')
    split, final = int(len(bars) * .6), int(len(bars) * .8)
    train, validation, test = bars[:split], bars[split:final], bars[final:]
    style = STYLES[spec.get('trading_style', 'balanced')]
    features = payload.get('feature_snapshot', freeze_features(spec.get('feature_records', [])))
    if freeze_features(features['records'])['hash'] != features['hash']:
        raise ValueError('外部特征快照指纹不一致')
    # Selection rules and execution options stay fixed throughout this experiment.
    options = {**options, 'feature_records': features['records'], 'rebalance_every': style['rebalance_every']}
    warmup = options.pop('warmup_bars', None) or []
    cutoff = datetime.fromisoformat(validation[-1].date).replace(tzinfo=timezone.utc)
    visible_records = [row for row in features['records'] if datetime.fromisoformat(row['available_at']) < cutoff]
    visible_snapshot = freeze_features(visible_records)
    catalog = {name: {'name': name, 'category': 'price', 'source': 'frozen OHLCV'} for name in PRICE_FEATURES}
    for row in visible_records:
        catalog[row['name']] = {key: row[key] for key in ('name', 'category', 'source', 'max_age_days')}
    timeline = FeatureTimeline(visible_records)
    feature_preview = [{'date': bar.date, 'values': timeline.at(bar)} for bar in bars[:final]][-30:]
    protocol = {'version': 'autoresearch-v1', 'split': 'chronological 60/20/20; cash reset with past-only warmup',
                'selection': 'eligibility first, then validation target, then Sharpe; strict lexicographic improvement only',
                'execution': {key: value for key, value in options.items() if key != 'feature_records'},
                'risk': {key: spec.get(key, 50) for key in ('max_drawdown', 'min_trades', 'max_turnover')},
                'objective': {key: spec[key] for key in ('target_metric', 'target_return')},
                'tuning_data_hash': hashlib.sha256(json.dumps([asdict(bar) for bar in bars[:final]], sort_keys=True).encode()).hexdigest(),
                'tuning_feature_hash': visible_snapshot['hash'], 'feature_policy': features['policy']}
    if warmup:
        protocol['warmup'] = {'days':len(warmup), 'start':warmup[0].date, 'end':warmup[-1].date,
                              'hash':hashlib.sha256(json.dumps([asdict(bar) for bar in warmup], sort_keys=True).encode()).hexdigest()}
    protocol_hash = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    models = [next(provider for provider in payload['providers'] if provider['id'] == identifier and provider['enabled']) for identifier in spec['model_ids']]
    training = simulate(train, strategy['code'], strategy['params'], warmup_bars=warmup, **options)
    validated = simulate(validation, strategy['code'], strategy['params'], warmup_bars=warmup+train, **options)
    baseline = {'name':strategy['name'], 'code':strategy['code'], 'params':deepcopy(strategy['params']),
                'training_metrics':training['metrics'], 'validation_metrics':validated['metrics'], 'round':0}
    best, best_report = deepcopy(baseline), validated
    opt = {'target_metric':spec['target_metric'], 'target_return':spec['target_return'], 'max_rounds':spec['max_rounds'],
           'max_drawdown':spec['max_drawdown'], 'min_trades':spec['min_trades'], 'max_output_tokens':spec['max_output_tokens'],
           'time_budget_seconds':spec['time_budget_seconds'], 'rounds':[], 'baseline':baseline,
           'best_round':0, 'best_validation_metrics':validated['metrics'], 'target_reached':False,
           'stop_reason':'running', 'phase':'准备迭代', 'note':NOTE,
           'trading_style':spec.get('trading_style', 'balanced'), 'style':style,
           'evaluation_protocol':protocol, 'protocol_hash':protocol_hash,
           'feature_hash':features['hash'], 'feature_catalog':list(catalog.values()),
           'change_budget':{'max_changed_lines':spec.get('max_changed_lines',12), 'max_param_changes':spec.get('max_param_changes',3)},
           'full_period_note':'优化尚未完成，当前主图为原策略全区间基线，并非本轮候选；每轮成绩请看训练/调优验证指标。',
           'split':{'train_start':train[0].date, 'train_end':train[-1].date, 'validation_start':validation[0].date,
                    'validation_end':validation[-1].date, 'test_start':test[0].date, 'test_end':test[-1].date,
                    'train_days':len(train), 'validation_days':len(validation), 'test_days':len(test)}}
    report = {**base_report, 'optimization':opt}
    began = time.monotonic()
    def publish():
        if progress:
            progress(report)
    publish()
    if eligible(validated['metrics'], spec) and validated['metrics'][spec['target_metric']] >= spec['target_return']:
        opt.update(target_reached=True, stop_reason='baseline_target_reached')
    else:
        for index in range(spec['max_rounds']):
            remaining = spec['time_budget_seconds'] - (time.monotonic() - began) - 15
            if remaining < 10:
                opt['stop_reason'] = 'time_budget'
                if opt['rounds']:
                    opt['rounds'][-1]['next_action'] = 'final_test'
                break
            provider = models[index % len(models)]
            opt.update(phase=f'第 {index + 1} 轮：等待 {provider["name"]} 生成候选', active_round=index+1)
            publish()
            context = {'strategy':{key:best[key] for key in ('name','code','params')},
                       'hypothesis':spec.get('hypothesis', ''), 'trading_style':style,
                       'evaluation_protocol':protocol, 'change_budget':opt['change_budget'],
                       'feature_catalog':list(catalog.values()), 'feature_preview':feature_preview,
                       'feature_usage':'feature("name", explicit_missing_default); price.* is known at signal close. External releases are delayed until a later UTC date and expire. Never use future/revised values early.',
                       'training_metrics':best['training_metrics'], 'validation_metrics':best['validation_metrics'],
                       'signal_diagnostics':{key:best_report['signal_diagnostics'][key] for key in ('status','summary','raw_positive_days','zero_return_branches')},
                       'objective':{key:spec[key] for key in ('target_metric','target_return','max_drawdown','min_trades')},
                       'constraints':protocol['execution'], 'training_end':train[-1].date, 'validation_end':validation[-1].date,
                       'prices':[asdict(bar) for bar in bars[:final][-180:]],
                       'attempts':[{key:row[key] for key in ('round','status','error','training_metrics','validation_metrics','code','params','notes','signal_diagnostics','hypothesis','decision','diagnosis','next_action') if key in row} for row in opt['rounds'][-6:]],
                       'instruction':'State one falsifiable hypothesis, then make a minimal code/parameter change. Improve validation return subject to fixed risk, turnover and trade constraints. Do not hardcode dates/prices or overfit. No test feedback will be given. Return code, params, reason, hypothesis, expected_effect, name, strategy_logic, change_summary, naming_reason as JSON. Explain in Chinese what changed and why. Name describes the full candidate logic and modification, not promised returns.'}
            context_hash = hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
            row = {'round':index+1, 'provider_id':provider['id'], 'provider_name':provider['name'], 'status':'invalid',
                   'context_hash':context_hash, 'prompt_version':'optimization-v3', 'protocol_hash':protocol_hash,
                   'parent_round':best['round'], 'decision':'rollback', 'decision_reason':'候选尚未通过评估'}
            try:
                config = {**provider['config'], 'max_output_tokens':str(spec['max_output_tokens']),
                          'timeout_seconds':str(min(int(provider['config'].get('timeout_seconds', '90')), max(10,int(remaining))))}
                proposal = MODEL_DRIVERS[provider['driver']](config).propose(context)
                row['hypothesis'] = str(proposal.get('hypothesis') or proposal.get('reason') or spec.get('hypothesis') or '检验局部修改是否提高固定约束下的验证收益')[:1000]
                row['expected_effect'] = str(proposal.get('expected_effect', ''))[:1000]
                row['usage'] = {key:value for key,value in proposal.get('usage',{}).items() if isinstance(value,(int,float,dict))}
                if isinstance(proposal.get('code'), str):
                    row['code'] = proposal['code'][:12000]
                explanation = ProposalExplanation.model_validate({field:proposal[field] for field in ProposalExplanation.model_fields if field in proposal})
                row.update(explanation.model_dump(), naming_source='demo' if provider['driver']=='demo_model' else 'model' if explanation.name else 'fallback')
                row['name'] = explanation.name or f'{strategy["name"][:40]} · AI 第{index+1}轮'
                row['notes'] = str(proposal.get('reason',''))[:2000]
                candidate = StrategyInput(name=row['name'], code=proposal['code'], params=proposal['params'],
                                          parent_id=strategy['id'], notes=row['notes'])
                row.update(code=candidate.code, params=candidate.params)
                row['change'] = describe_change(best, candidate, spec)
                if row['change']['changed_lines'] > spec.get('max_changed_lines', 12) or len(row['change']['param_changes']) > spec.get('max_param_changes', 3):
                    raise ValueError('策略修改超过本轮最小变更预算')
                referenced = {node.args[0].value for node in ast.walk(ast.parse(candidate.code))
                              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'feature'}
                if referenced - catalog.keys():
                    raise ValueError('策略引用了未提供的特征字段')
                opt['phase'] = f'第 {index+1} 轮：自动回测'
                publish()
                train_result = simulate(train, candidate.code, candidate.params, warmup_bars=warmup, **options)
                evaluation = simulate(validation, candidate.code, candidate.params, warmup_bars=warmup+train, **options)
                row.update(**candidate.model_dump(), status='evaluated', training_metrics=train_result['metrics'],
                           validation_metrics=evaluation['metrics'], signal_diagnostics=evaluation['signal_diagnostics'],
                           usage={key:value for key,value in proposal.get('usage',{}).items() if isinstance(value,(int,float,dict))},
                           eligible=eligible(evaluation['metrics'],spec))
                row['diagnosis'] = diagnose(train_result, evaluation, best['validation_metrics'], spec)
                changed = row['change']['changed_lines'] or row['change']['param_changes']
                if changed and eligible(evaluation['metrics'],spec) and score(evaluation['metrics'],spec) > score(best['validation_metrics'],spec):
                    row.update(decision='keep', decision_reason=('验证指标严格改善且满足回撤、换手与成交约束'
                               if eligible(best['validation_metrics'], spec) else '满足固定约束，替换此前未满足约束的版本'))
                    best, best_report = row, evaluation
                    opt.update(best_round=index+1,best_validation_metrics=evaluation['metrics'])
                else:
                    row['decision_reason'] = ('代码与参数未发生变化' if not changed else
                                              '未满足固定风险、换手或成交约束' if not row['eligible'] else '验证指标未严格优于当前保留版本')
            except Exception as exc:
                from pydantic import ValidationError
                if isinstance(exc, ValidationError):
                    details = exc.errors(include_input=False, include_url=False)
                    row['error'] = ('策略校验失败：' + '; '.join(str(item['msg']) for item in details[:3]))[:250]
                elif isinstance(exc, ValueError) and str(exc).startswith(('模型','策略','指标','目标仓位','数值','Python','安全策略','仅允许','不支持','算术','计算预算','KAMA','波动率','Z-score','通道')):
                    row['error'] = str(exc)[:250]
                else:
                    row['error'] = '候选生成、校验或回测失败；本轮已计入预算，未采用此候选。'
                if row['error'] in ('模型服务返回 HTTP 401', '模型服务返回 HTTP 403', '模型服务返回 HTTP 429', '模型密钥未配置', '模型地址必须是部署者允许的 HTTPS 地址'):
                    opt['stop_reason'] = 'provider_access_denied'
                row['decision_reason'] = row['error']
                row['diagnosis'] = {'summary':row['error'], 'next_hypothesis':'修正候选合法性并缩小修改范围，保持评估条件'}
            opt['rounds'].append(row)
            reached = eligible(best['validation_metrics'],spec) and best['validation_metrics'][spec['target_metric']] >= spec['target_return']
            row['incumbent_round'] = best['round']
            row['next_action'] = ('stop_provider' if opt['stop_reason'] == 'provider_access_denied' else
                                  'final_test' if reached or index+1 == spec['max_rounds'] else
                                  'refine_kept' if row['decision'] == 'keep' else 'retry_from_incumbent')
            opt.update(target_reached=reached, phase=f'已完成 {index+1} 轮')
            publish()
            if opt['stop_reason'] == 'provider_access_denied':
                break
            if reached:
                opt['stop_reason'] = 'target_reached'
                break
        else:
            opt['stop_reason'] = 'max_rounds'
    if opt['stop_reason'] == 'running':
        opt['stop_reason'] = 'max_rounds'
    opt.update(phase='最终独立测试（不反馈模型）', selected={key:best[key] for key in ('name','code','params','round','strategy_logic','change_summary','naming_reason','naming_source','notes') if key in best})
    publish()
    tested = simulate(test,best['code'],best['params'],warmup_bars=warmup+bars[:final],**options)
    test_baseline = simulate(test,strategy['code'],strategy['params'],warmup_bars=warmup+bars[:final],**options)
    full = simulate(bars,best['code'],best['params'],warmup_bars=warmup,**options)
    opt.update(test_metrics=tested['metrics'],test_baseline_metrics=test_baseline['metrics'],
               test_signal_diagnostics=tested['signal_diagnostics'], test_eligible=eligible(tested['metrics'],spec),
               test_passed=eligible(tested['metrics'],spec) and tested['metrics'][spec['target_metric']] >= spec['target_return'],
               phase='已完成', duration_seconds=round(time.monotonic()-began,1),
               full_period_note='主图为最终选定策略的全区间回测，包含调优数据，不是独立样本外成绩。独立测试指标请看下方。')
    opt['experiment_log'] = {'format':'northstar-autoresearch-v1', 'protocol_hash':protocol_hash,
                             'data_hash':base_report['data_hash'], 'feature_hash':features['hash'],
                             'selected_round':best['round'], 'round_count':len(opt['rounds'])}
    result = {**base_report, **full, 'optimization':opt, 'strategy_id':strategy['id'] if best['round']==0 else None}
    report.clear()
    report.update(result)
    publish()
    return result


def persist_candidates(db, job, result):
    opt = result.get('optimization', {})
    for row in opt.get('rounds', []):
        if row.get('status') != 'evaluated':
            continue
        identifier = hashlib.sha256(f'optimization:{job.id}:{row["round"]}'.encode()).hexdigest()[:32]
        if not db.get(Strategy,identifier):
            parent_round = row.get('parent_round', 0)
            parent_id = (hashlib.sha256(f'optimization:{job.id}:{parent_round}'.encode()).hexdigest()[:32]
                         if parent_round else job.payload['strategy']['id'])
            db.add(Strategy(id=identifier, name=row['name'], code=row['code'], params=row['params'], parent_id=parent_id,
                            notes=row['notes'], status='candidate', provenance={'type':'llm_optimization','job_id':job.id,'round':row['round'],
                            'provider_id':row['provider_id'],'data_hash':result['data_hash'],'context_hash':row['context_hash'],
                            'prompt_version':row['prompt_version'],'training_metrics':row['training_metrics'],
                            'validation_metrics':row['validation_metrics'],'usage':row['usage'], 'note':NOTE,
                            'hypothesis':row.get('hypothesis'), 'decision':row.get('decision'), 'change':row.get('change'),
                            **{key:row.get(key) for key in ('strategy_logic','change_summary','naming_reason','naming_source')},
                            'diagnosis':row.get('diagnosis'), 'protocol_hash':opt.get('protocol_hash'),
                            'feature_snapshot':result.get('feature_snapshot', freeze_features([])),
                            'rebalance_every':opt.get('style', {}).get('rebalance_every', 1)}))
            db.flush()
        row['candidate_id'] = identifier
    if opt.get('best_round'):
        selected = next((row for row in opt['rounds'] if row['round']==opt['best_round']),None)
        if selected and selected.get('candidate_id'):
            opt['candidate_id'] = selected['candidate_id']
            if opt.get('test_metrics'):
                result['strategy_id'] = selected['candidate_id']
                candidate = db.get(Strategy, selected['candidate_id'])
                candidate.provenance = {**candidate.provenance, 'test_metrics':opt['test_metrics'], 'test_passed':opt['test_passed']}
    return result
