"""Finite, retrospective price-only strategy comparisons; never places orders."""
from copy import deepcopy
import hashlib
import json
import random
import statistics

from .backtest import equity_returns, simulate
from .schemas import StrategyInput
from .strategy_templates import list_templates


NOTE = ('有限候选的回顾性比较，不代表全局最优或未来收益。2025及2026行情均已被查看，'
        '分段不是独立样本外验证；全区间最高收益存在选择偏差。'
        '只使用收盘价特征，未复现外部ADX/ATR、持仓锁定或满仓hold引擎。')
EMA_CODE = '''def target(history, params):
    if params["fast"] >= params["slow"]:
        return 0.0
    if len(history) < max(params["slow"], params["trend"], params["vol_window"] + 1):
        return 0.0
    if ema(history, params["fast"]) <= ema(history, params["slow"]) or history[-1] <= mean(history[-params["trend"]:]):
        return 0.0
    if params["risk_scaled"]:
        return min(params["weight"], params["target_vol"] / max(realized_vol(history, params["vol_window"]), params["vol_floor"]))
    return params["weight"]
'''


def candidates(baseline):
    templates = {row['id']: row for row in list_templates()}
    rows = [{'key': 'baseline-kama', 'name': '原策略 KAMA',
             'code': baseline['code'], 'params': deepcopy(baseline['params'])}]
    for threshold in (.15, .35):
        rows.append({'key': f'kama-er-{threshold}', 'name': f'KAMA · ER{threshold}',
                     'code': templates['adaptive-kama']['code'],
                     'params': {**templates['adaptive-kama']['params'], 'min_er': threshold}})
    for fast, slow in ((10, 30), (20, 60), (50, 200), (40, 180), (60, 220)):
        rows.append({'key': f'ema-{fast}-{slow}', 'name': f'EMA{fast}/{slow} + SMA200',
                     'code': EMA_CODE,
                     'params': {'fast': fast, 'slow': slow, 'trend': 200, 'weight': .3,
                                'risk_scaled': 0, 'vol_window': 20, 'target_vol': .08, 'vol_floor': .02}})
    scaled = deepcopy(next(row for row in rows if row['key'] == 'ema-50-200'))
    scaled.update(key='ema-50-200-vol', name='EMA50/200 + SMA200 · 波动率仓位')
    scaled['params']['risk_scaled'] = 1
    rows.append(scaled)
    for entry, exit in ((20, 10), (55, 20)):
        rows.append({'key': f'channel-{entry}-{exit}', 'name': f'收盘通道{entry}/{exit} + SMA200',
                     'code': '''def target(history, params):
    if len(history) < max(params["entry"] + 1, params["trend"]):
        return 0.0
    if history[-1] <= mean(history[-params["trend"]:]):
        return 0.0
    return params["weight"] * channel_signal(history, params["entry"], params["exit"])
''', 'params': {'entry': entry, 'exit': exit, 'trend': 200, 'weight': .3}})
    for identifier in ('volatility-target-trend', 'risk-filtered-breakout', 'regime-adaptive-ensemble'):
        template = templates[identifier]
        rows.append({'key': identifier, 'name': template['name'], 'code': template['code'],
                     'params': deepcopy(template['params'])})
    rows.append({'key': 'constant-weight', 'name': '30%恒定权重基准（每日再平衡，非买入持有）',
                 'code': 'def target(history, params):\n    return params["weight"]\n', 'params': {'weight': .3}})
    for row in rows:
        StrategyInput(name=row['name'], code=row['code'], params=row['params'])
        row['fingerprint'] = hashlib.sha256(json.dumps(
            {key: row[key] for key in ('code', 'params')}, sort_keys=True).encode()).hexdigest()
    return rows


def passes(metrics, max_drawdown):
    return metrics['trade_count'] > 0 and metrics['max_drawdown'] >= -max_drawdown


def select(rows, field, max_drawdown):
    qualified = [row for row in rows if row['key'] != 'constant-weight' and passes(row[field], max_drawdown)]
    return max(qualified, key=lambda row: (row[field]['total_return'], row[field]['sharpe'])) if qualified else None


def pnl_attribution(report, initial):
    shares, flow, episodes = 0, 0.0, []
    for trade in report['trades']:
        quantity = trade['quantity'] if trade['side'] == 'buy' else -trade['quantity']
        if not shares and quantity > 0:
            entry = trade['date']
        shares += quantity
        flow -= quantity * trade['price'] + trade['fee']
        if shares == 0:
            episodes.append({'entry': entry, 'exit': trade['date'], 'pnl': round(flow, 4), 'closed': True})
            flow = 0.0
    if shares:
        episodes.append({'entry': entry, 'exit': report['last_date'],
                         'pnl': round(flow + shares * report['last_close'], 4), 'closed': False})
    largest = max((row['pnl'] for row in episodes), default=0)
    return {'episodes': episodes, 'closed_episodes': sum(row['closed'] for row in episodes),
            'execution_count': len(report['trades']),
            'return_excluding_largest_profit': report['metrics']['total_return'] - max(0, largest) / initial,
            'note': '成交笔数为买卖及再平衡边数；持仓段按从空仓到清仓统计。剔除最大盈利为事后归因，不是可交易收益。'}


def bootstrap_sharpe(report, samples=500, block=10):
    if not report.get('initial_equity'):
        return {'samples': samples, 'block_days': block, 'interval': None, 'note': '报告缺少初始资金，无法核验首日收益；未计算Bootstrap。'}
    returns = equity_returns(report['curve'], report['initial_equity'])
    if len(returns) < max(2, block) or statistics.stdev(returns) == 0:
        return {'samples': samples, 'block_days': block, 'interval': None, 'note': '收益序列不足或无波动。'}
    rng, scores = random.Random(20261015), []
    for _ in range(samples):
        sampled = []
        while len(sampled) < len(returns):
            start = rng.randrange(len(returns))
            sampled.extend(returns[(start + offset) % len(returns)] for offset in range(block))
        sampled = sampled[:len(returns)]
        deviation = statistics.stdev(sampled)
        scores.append(statistics.mean(sampled) / deviation * 252 ** .5 if deviation else 0)
    scores.sort()
    return {'samples': samples, 'block_days': block,
            'interval': [scores[int(samples * .025)], scores[min(samples - 1, int(samples * .975))]],
            'note': '固定种子循环区块Bootstrap，描述性95%区间；未校正多重选优偏差，不是显著性或未来收益保证。'}


def evaluate(bars, baseline, options, start, end, max_drawdown=.15):
    definitions = candidates(baseline)
    evaluated = [bar for bar in bars if start <= bar.date <= end]
    warmup = [bar for bar in bars if bar.date < start]
    training = [bar for bar in evaluated if bar.date < '2026-01-01']
    later = [bar for bar in evaluated if bar.date >= '2026-01-01']
    if len(evaluated) < 150 or len(warmup) < 253 or len(training) < 40 or len(later) < 40:
        raise ValueError('比较需至少253根此前预热行情，及2025/2026各40根评估行情')
    rows, reports = [], {}
    for candidate in definitions:
        def backtest(part, prior, **overrides):
            return simulate(part, candidate['code'], candidate['params'],
                            warmup_bars=prior, **{**options, **overrides})
        full = backtest(evaluated, warmup)
        reports[candidate['key']] = full
        rows.append({**candidate, 'metrics': full['metrics'],
                     'year_2025': backtest(training, warmup)['metrics'],
                     'year_2026': backtest(later, warmup + training)['metrics'],
                     'eligible': passes(full['metrics'], max_drawdown)})
    winner = select(rows, 'metrics', max_drawdown)
    train_winner = select(rows, 'year_2025', max_drawdown)
    summary = {'version': 1, 'note': NOTE, 'candidate_count': len(rows), 'objective': 'total_return',
               'selection_rule': '非被动基准、至少1条成交记录、回撤不超过约束，然后累计收益最高；不使用年化收益替代。',
               'max_drawdown': max_drawdown, 'evaluation_start': evaluated[0].date,
               'evaluation_end': evaluated[-1].date, 'warmup_start': warmup[0].date,
               'warmup_end': warmup[-1].date, 'warmup_days': len(warmup),
               'candidate_set_hash': hashlib.sha256(json.dumps(definitions, sort_keys=True).encode()).hexdigest(),
               'rows': sorted(rows, key=lambda row: row['metrics']['total_return'], reverse=True),
               'selected_key': winner['key'] if winner else None,
               'selected_using_2025_only': train_winner['key'] if train_winner else None,
               'conditions': deepcopy(options), 'trade_count_note': '成交边数，包含再平衡，不是完整买卖回合数。'}
    if winner:
        selected_report = reports[winner['key']]
        summary['attribution'] = pnl_attribution(selected_report, options['initial'])
        summary['bootstrap_sharpe'] = bootstrap_sharpe(selected_report)
        summary['doubled_cost_metrics'] = simulate(
            evaluated, winner['code'], winner['params'], warmup_bars=warmup,
            **{**options, 'fee_bps': options['fee_bps'] * 2, 'slippage_bps': options['slippage_bps'] * 2})['metrics']
        summary['segments'] = []
        for begin, finish in (('2025-01-01', '2025-06-30'), ('2025-07-01', '2025-12-31'),
                              ('2026-01-01', '2026-06-30'), ('2026-07-01', end)):
            part = [bar for bar in evaluated if begin <= bar.date <= finish]
            if len(part) < 20:
                continue
            prior = [bar for bar in bars if bar.date < part[0].date]
            selected = simulate(part, winner['code'], winner['params'], warmup_bars=prior, **options)
            original = simulate(part, baseline['code'], baseline['params'], warmup_bars=prior, **options)
            summary['segments'].append({'start': part[0].date, 'end': part[-1].date,
                                        'selected_metrics': selected['metrics'], 'baseline_metrics': original['metrics']})
    return summary, reports
