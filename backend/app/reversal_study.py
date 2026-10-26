"""Fixed, retrospective 2026 mean-reversion study; no orders or external data."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

from .backtest import simulate
from .providers import data_path
from .schemas import StrategyInput
from .strategy_study import bootstrap_sharpe, pnl_attribution

STUDY_TYPE = 'reversal-2026-v1'
MIN_EPISODES = 5
NOTE = ('2026年已被反复查看并用于本次选优，全部结果都是回顾性拟合，不是独立样本外证据。'
        '固定18个新增反转配置及原KAMA，不因结果扩展搜索；历史净盈利不保证未来盈利。'
        '只做多、收盘信号下一日开盘撮合、逐日目标仓位再平衡；不含真实港股全部费用、分红和公司行动。')
RSI_CODE = '''def target(history, params):
    if len(history) < params["period"] + 1:
        return 0.0
    if rsi(history, params["period"]) < params["entry"]:
        return params["weight"]
    return 0.0
'''
ZSCORE_CODE = '''def target(history, params):
    if len(history) < params["period"] + 1:
        return 0.0
    if zscore(history, params["period"]) < -params["entry"]:
        return params["weight"]
    return 0.0
'''
DROP_CODE = '''def target(history, params):
    if len(history) < params["period"] + 1:
        return 0.0
    if history[-1] / history[-params["period"] - 1] - 1 <= -params["drop"]:
        return params["weight"]
    return 0.0
'''
REBOUND_CODE = '''def target(history, params):
    if len(history) < params["period"] + 2:
        return 0.0
    if rsi(history[:-1], params["period"]) < params["entry"] and history[-1] > history[-2]:
        return params["weight"]
    return 0.0
'''
GUARDED_CODE = '''def target(history, params):
    if len(history) < params["period"] + 1:
        return 0.0
    if history[-1] / history[-21] - 1 < -params["max_drop"] or realized_vol(history, 20) > params["max_vol"]:
        return 0.0
    if zscore(history, params["period"]) < -params["entry"]:
        return params["weight"]
    return 0.0
'''


def candidates(baseline):
    rows = [{'key': 'baseline-kama', 'name': '原策略 KAMA', 'family': 'baseline',
             'code': baseline['code'], 'params': deepcopy(baseline['params'])}]
    for period, thresholds in ((2, (10, 20, 30)), (5, (20, 30, 40))):
        for entry in thresholds:
            rows.append({'key': f'rsi-{period}-{entry}', 'name': f'RSI{period} < {entry} 超卖',
                         'family': f'rsi-{period}', 'code': RSI_CODE,
                         'params': {'period': period, 'entry': entry, 'weight': .3}})
    for period in (10, 20):
        for entry in (1.0, 1.5):
            rows.append({'key': f'zscore-{period}-{entry}', 'name': f'Z-score{period} < -{entry}',
                         'family': f'zscore-{period}', 'code': ZSCORE_CODE,
                         'params': {'period': period, 'entry': entry, 'weight': .3}})
    for period, thresholds in ((1, (.02, .03)), (3, (.03, .05))):
        for drop in thresholds:
            rows.append({'key': f'drop-{period}-{drop}', 'name': f'{period}日跌幅 ≥ {drop:.0%} 反转',
                         'family': f'drop-{period}', 'code': DROP_CODE,
                         'params': {'period': period, 'drop': drop, 'weight': .3}})
    for entry in (20, 30):
        rows.append({'key': f'rebound-2-{entry}', 'name': f'昨日RSI2 < {entry} + 今日回升',
                     'family': 'rebound-2', 'code': REBOUND_CODE,
                     'params': {'period': 2, 'entry': entry, 'weight': .3}})
    for entry in (1.0, 1.5):
        rows.append({'key': f'guarded-20-{entry}', 'name': f'Z-score20 < -{entry} · 下跌保护',
                     'family': 'guarded-20', 'code': GUARDED_CODE,
                     'params': {'period': 20, 'entry': entry, 'weight': .3,
                                'max_drop': .15, 'max_vol': .6}})
    for row in rows:
        StrategyInput(name=row['name'], code=row['code'], params=row['params'])
        row['fingerprint'] = fingerprint({key: row[key] for key in ('code', 'params')})
    return rows


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def conditions(options):
    return {key: options[key] for key in ('initial', 'lot', 'fee_bps', 'slippage_bps', 'max_weight')}


def study_plan(baseline, options, data_hash, start, end):
    return {'study_type': STUDY_TYPE, 'candidates': candidates(baseline), 'conditions': conditions(options),
            'data_hash': data_hash, 'start': start, 'end': end, 'max_drawdown': .15,
            'min_closed_episodes': MIN_EPISODES,
            'selection_rule': '2026净收益及双倍成本收益均大于0、至少5个已清仓回合、最大回撤不超过15%；合格者按2026净收益排序。'}


def register_plan(baseline, options, data_hash, start, end, path):
    plan = study_plan(baseline, options, data_hash, start, end)
    target = data_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if target.exists() and json.loads(target.read_text()) != plan:
        raise ValueError('研究计划已冻结，禁止按结果更换候选或筛选规则')
    if not target.exists():
        with target.open('x') as output:
            output.write(encoded)
    return plan


def qualifies(row, max_drawdown=.15):
    return (row['metrics']['total_return'] > 0 and row['metrics']['max_drawdown'] >= -max_drawdown
            and row['attribution']['closed_episodes'] >= MIN_EPISODES
            and row['doubled_cost_metrics']['total_return'] > 0)


def evaluate(bars, baseline, options, start, end, max_drawdown=.15):
    if not '2026-01-01' <= start < end <= '2026-12-31' or max_drawdown != .15:
        raise ValueError('本研究固定评估2026年且回撤约束为15%')
    definitions = candidates(baseline)
    assessed = [bar for bar in bars if start <= bar.date <= end]
    warmup = [bar for bar in bars if bar.date < start]
    previous = [bar for bar in bars if '2025-01-01' <= bar.date < '2026-01-01']
    before_previous = [bar for bar in bars if bar.date < '2025-01-01']
    if len(assessed) < 100 or len(previous) < 100 or len(before_previous) < 253:
        raise ValueError('研究需要2025、2026各至少100根日线及此前253根预热')
    rows, reports = [], {}
    for candidate in definitions:
        def backtest(part, prior, **overrides):
            return simulate(part, candidate['code'], candidate['params'], warmup_bars=prior,
                            **{**options, **overrides})
        current = backtest(assessed, warmup)
        old = backtest(previous, before_previous)
        reports[candidate['key']] = current
        attribution = pnl_attribution(current, options['initial'])
        row = {**candidate, 'metrics': current['metrics'], 'year_2026': current['metrics'],
               'year_2025': old['metrics'], 'attribution': attribution,
               'closed_episodes_2025': pnl_attribution(old, options['initial'])['closed_episodes'],
               'full_period_metrics': backtest(previous + assessed, before_previous)['metrics'],
               'doubled_cost_metrics': backtest(assessed, warmup, fee_bps=options['fee_bps'] * 2,
                                               slippage_bps=options['slippage_bps'] * 2)['metrics']}
        row['eligible'] = qualifies(row, max_drawdown)
        rows.append(row)
    qualified = [row for row in rows if row['eligible']]
    winner = max(qualified, key=lambda row: row['metrics']['total_return']) if qualified else None
    old_qualified = [row for row in rows if row['year_2025']['total_return'] > 0
                     and row['year_2025']['max_drawdown'] >= -max_drawdown
                     and row['closed_episodes_2025'] >= MIN_EPISODES]
    old_winner = max(old_qualified, key=lambda row: row['year_2025']['total_return']) if old_qualified else None
    baseline_row = rows[0]
    summary = {'version': 1, 'study_type': STUDY_TYPE, 'note': NOTE,
               'candidate_count': len(rows), 'new_candidate_count': len(rows) - 1,
               'objective': 'year_2026_total_return', 'max_drawdown': max_drawdown,
               'min_closed_episodes': MIN_EPISODES,
               'selection_rule': '2026年净收益、双倍成本净收益均为正，至少5个已清仓回合，回撤≤15%；再按2026年净收益排序。',
               'evaluation_start': assessed[0].date, 'evaluation_end': assessed[-1].date,
               'warmup_start': warmup[0].date, 'warmup_end': warmup[-1].date, 'warmup_days': len(warmup),
               'candidate_set_hash': fingerprint(definitions), 'conditions': conditions(options),
               'data_hash': fingerprint([asdict(bar) for bar in assessed]),
               'rows': sorted(rows, key=lambda row: row['metrics']['total_return'], reverse=True),
               'selected_key': winner['key'] if winner else None,
               'selected_using_2025_only': old_winner['key'] if old_winner else None,
               'positive_candidate_count': sum(row['metrics']['total_return'] > 0 for row in rows),
               'qualified_candidate_count': len(qualified),
               'improved_over_baseline': bool(winner and winner['metrics']['total_return'] > baseline_row['metrics']['total_return'])}
    if winner:
        report = reports[winner['key']]
        def sensitivity(**overrides):
            return simulate(assessed, winner['code'], winner['params'], warmup_bars=warmup,
                            **{**options, **overrides})
        summary.update(attribution=winner['attribution'], bootstrap_sharpe=bootstrap_sharpe(report),
                       doubled_cost_metrics=winner['doubled_cost_metrics'])
        summary['lot_sensitivity'] = [
            {'label': '原本金、100股一手', 'initial': options['initial'], 'lot': 100,
             'metrics': sensitivity(lot=100)['metrics']},
            {'label': '假设25万本金、100股一手（不是原条件）', 'initial': 250000, 'lot': 100,
             'metrics': sensitivity(initial=250000, lot=100)['metrics']}]
        summary['higher_cost_metrics'] = sensitivity(fee_bps=20, slippage_bps=10)['metrics']
        summary['neighbor_rows'] = [
            {'key': row['key'], 'name': row['name'], 'metrics': row['metrics'], 'eligible': row['eligible']}
            for row in rows if row['family'] == winner['family']]
        summary['segments'] = []
        for begin, finish in (('2026-01-01', '2026-03-31'), ('2026-04-01', '2026-06-30'),
                              ('2026-07-01', '2026-09-30'), ('2026-10-01', end)):
            part = [bar for bar in assessed if begin <= bar.date <= finish]
            if len(part) < 5:
                continue
            prior = [bar for bar in bars if bar.date < part[0].date]
            summary['segments'].append({'start': part[0].date, 'end': part[-1].date,
                'metrics': simulate(part, winner['code'], winner['params'], warmup_bars=prior, **options)['metrics']})
    return summary, reports
