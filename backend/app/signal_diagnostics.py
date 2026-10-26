"""Explain actual interpreter paths and execution barriers without changing signals."""
import ast


TEMPLATE_REASONS = {
    'adaptive-kama': [
        ('invalid_periods', '快速周期不小于慢速周期'),
        ('warmup', '历史长度不足，仍在指标预热'),
        ('low_efficiency', '效率比低于最低趋势阈值'),
        ('trend_or_pullback', '价格不高于 KAMA，或近期高点回撤超限'),
        ('entry', '通过入场过滤'),
    ],
    'regime-adaptive-ensemble': [
        ('invalid_periods', '快速周期不小于慢速周期'),
        ('warmup', '历史长度不足，仍在指标预热'),
        ('trend_entry', '趋势状态满足 EMA 入场条件'),
        ('trend_filtered', '趋势状态未满足价格与快慢 EMA 条件'),
        ('reversion_entry', '震荡状态满足反弹入场条件'),
        ('reversion_filtered', '震荡状态的长期趋势、RSI 超卖和 Z-score 条件未同时满足'),
    ],
}


def return_reasons(code):
    from .strategy_templates import list_templates
    tree = ast.parse(code)
    nodes = sorted((node for node in ast.walk(tree) if isinstance(node, ast.Return)), key=lambda node: node.lineno)
    structure = ast.dump(tree, include_attributes=False)
    template = next((item for item in list_templates()
                     if ast.dump(ast.parse(item['code']), include_attributes=False) == structure), None)
    labels = TEMPLATE_REASONS.get(template['id'] if template else '')
    if labels and len(labels) == len(nodes):
        return {node.lineno: label for node, label in zip(nodes, labels)}
    return {node.lineno: (f'return_{node.lineno}', f'第 {node.lineno} 行返回分支') for node in nodes}


def summarize_signals(code, days, branches, execution, raw_positive, capped_positive, last_target, trades):
    labels = return_reasons(code)
    reasons = [{'key': labels[line][0], 'label': labels[line][1], 'line': line, 'days': count}
               for line, count in sorted(branches.items())]
    warmup = sum(reason['days'] for reason in reasons if reason['key'] == 'warmup')
    if trades:
        status, summary = 'traded', '已产生回测撮合成交。'
    elif not raw_positive:
        status = 'no_signal'
        summary = ('全区间仍在预热，尚未产生入场信号。' if warmup == days else
                   f'未产生正仓位信号；{warmup} 根日线处于预热，其余交易日未通过入场过滤。' if warmup else
                   '策略在整个区间都返回零仓位，没有入场信号；请查看实际返回分支。')
    elif not capped_positive:
        status, summary = 'risk_blocked', '策略有正仓位信号，但全部被实验仓位上限裁剪为零。'
    elif not execution['eligible_signal_days']:
        status, summary = 'last_day_only', '正仓位信号仅出现在最后一根日线，区间内没有下一交易日可撮合。'
    else:
        status = 'execution_blocked'
        summary = ('策略有正仓位信号，但未成交；请检查下方成交量、整手数量和可用资金阻断统计。')
    return {'status': status, 'summary': summary, 'signal_days': days,
            'warmup_days': warmup if any(value[0] == 'warmup' for value in labels.values()) else None,
            'raw_positive_days': raw_positive, 'positive_target_days': capped_positive,
            'zero_target_days': days - capped_positive, 'last_target': last_target,
            'zero_return_branches': reasons, **dict(execution),
            'note': '分支天数按实际执行到的零仓位 return 统计，互斥而非各条件独立计数；收盘信号在下一交易日开盘撮合。无成交不等于收益验证成功。'}


def report_with_signal_diagnostics(report, payload):
    if not report or report.get('signal_diagnostics') or report.get('metrics', {}).get('trade_count') != 0:
        return report
    try:
        from .backtest import simulate
        from .frozen_market import read_frozen_bars
        frozen = payload.get('frozen_data') or {'path': report.get('snapshot'), 'hash': report.get('data_hash'), 'source': report.get('source')}
        if frozen.get('hash') != report.get('data_hash') or frozen.get('source') != report.get('source'):
            raise ValueError('冻结元数据不一致')
        if report.get('snapshot') and frozen.get('path') != report['snapshot']:
            raise ValueError('快照路径不一致')
        bars = read_frozen_bars(frozen)
        spec, strategy, asset, risk = (payload[key] for key in ('spec', 'strategy', 'asset', 'risk'))
        if [bar.date for bar in bars] != [point['date'] for point in report['curve']] or any(not spec['start'] <= bar.date <= spec['end'] for bar in bars):
            raise ValueError('日期不一致')
        replay = simulate(bars, strategy['code'], strategy['params'],
                          feature_records=payload.get('feature_snapshot', {}).get('records', spec.get('feature_records', [])),
                          rebalance_every=payload.get('rebalance_every', 1), initial=spec['initial'], lot=asset['lot'],
                          fee_bps=spec['fee_bps'], slippage_bps=spec['slippage_bps'], max_weight=risk['max_weight'])
        if replay['metrics'] != report['metrics'] or replay['trades'] != report.get('trades'):
            raise ValueError('旧实验不能在当前引擎完全复现')
        if [(point['date'], point['equity']) for point in replay['curve']] != [(point['date'], point['equity']) for point in report['curve']]:
            raise ValueError('旧实验曲线不能复现')
        return {**report, 'signal_diagnostics': {**replay['signal_diagnostics'], 'replayed': True}}
    except Exception:
        return {**report, 'diagnostic_warning': '原快照或实验条件不完整，或当前引擎未能复现旧结果，无法可靠补充无成交原因；原报告保持不变。'}
