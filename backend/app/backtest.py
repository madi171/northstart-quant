import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import asdict
from .strategy import parse, target
from .signal_diagnostics import summarize_signals
from .research_features import FeatureTimeline


def equity_returns(curve, initial):
    equities = [initial] + [point['equity'] for point in curve]
    return [current / previous - 1 for previous, current in zip(equities, equities[1:])]


def simulate(bars, code, params, initial=100000, lot=1, fee_bps=5, slippage_bps=5, max_weight=.3, warmup_bars=None,
             feature_records=None, rebalance_every=1, on_progress=None):
    """Close signal -> next bar open. Long-only, no borrowing, no same-day resell.

    Educational execution model; source calendar, corporate actions and market
    restrictions require certified production adapters before real execution.
    """
    if not bars or any(a.date >= b.date for a, b in zip(bars, bars[1:])):
        raise ValueError('评估行情不能为空且必须按日期严格升序')
    fn = parse(code)
    if type(rebalance_every) is not int or not 1 <= rebalance_every <= 250:
        raise ValueError('调仓间隔须为1至250个交易日')
    timeline = FeatureTimeline(feature_records or [])
    cash, shares, pending = float(initial), 0, 0.0
    curve, trades, closes = [], [], []
    benchmark_entry = None
    if warmup_bars:
        if any(bar.date >= bars[0].date for bar in warmup_bars) or any(a.date >= b.date for a, b in zip(warmup_bars, warmup_bars[1:])):
            raise ValueError('指标预热行情必须按日期有序且严格早于评估区间')
        closes = [bar.close for bar in warmup_bars]
        pending = min(max_weight, target(fn, closes, params, features=timeline.at(warmup_bars[-1])))
    peak, max_dd = initial, 0.0
    turnover = 0.0
    branches = Counter()
    raw_positive = capped_positive = 0
    execution = dict.fromkeys(('eligible_signal_days', 'zero_volume_days', 'below_lot_days', 'insufficient_cash_days'), 0)
    for index, bar in enumerate(bars):
        can_execute = bool(index or warmup_bars)
        rebalance = (index + len(warmup_bars or []) - 1) % rebalance_every == 0
        if can_execute and rebalance and pending > 0:
            execution['eligible_signal_days'] += 1
            if bar.volume <= 0:
                execution['zero_volume_days'] += 1
        if can_execute and bar.volume > 0:
            if benchmark_entry is None:
                benchmark_entry = {'date': bar.date, 'price': bar.open}
        if can_execute and rebalance and bar.volume > 0:
            opening_equity = cash + shares * bar.open
            desired = int((opening_equity * pending) / (bar.open * lot)) * lot
            if pending > 0 and desired == 0 and shares == 0:
                execution['below_lot_days'] += 1
            quantity = desired - shares
            if quantity:
                side = 'buy' if quantity > 0 else 'sell'
                price = bar.open * (1 + slippage_bps / 10000 * (1 if quantity > 0 else -1))
                if quantity > 0:
                    quantity = min(quantity, int(cash / (price * (1 + fee_bps / 10000) * lot)) * lot)
                    if quantity == 0:
                        execution['insufficient_cash_days'] += 1
                if quantity:
                    gross = abs(quantity) * price
                    fee = gross * fee_bps / 10000
                    cash -= quantity * price + fee
                    shares += quantity
                    turnover += gross
                    signal_date = bars[index - 1].date if index else warmup_bars[-1].date
                    trades.append({'date': bar.date, 'signal_date': signal_date, 'side': side,
                                   'quantity': abs(quantity), 'price': round(price, 6), 'fee': round(fee, 4)})
        equity = cash + shares * bar.close
        peak = max(peak, equity)
        drawdown = equity / peak - 1
        max_dd = min(max_dd, drawdown)
        curve.append({'date': bar.date, 'equity': round(equity, 4),
                      'benchmark': round(initial * bar.close / benchmark_entry['price'], 4) if benchmark_entry else float(initial), 'drawdown': drawdown,
                      'open': bar.open, 'high': bar.high, 'low': bar.low, 'close': bar.close,
                      'volume': bar.volume})
        closes.append(bar.close)
        trace = {}
        signal = target(fn, closes, params, trace=trace, features=timeline.at(bar, count=True))
        pending = min(max_weight, signal)
        raw_positive += signal > 0
        capped_positive += pending > 0
        if signal == 0:
            branches[trace['return_line']] += 1
        if on_progress and (index == len(bars) - 1 or (index + 1) % max(1, len(bars) // 100) == 0):
            on_progress(index + 1, len(bars), bar.date)
    returns = equity_returns(curve, initial)
    std = statistics.stdev(returns) if len(returns) > 1 else 0
    total_return = curve[-1]['equity'] / initial - 1
    annual = (1 + total_return) ** (252 / len(returns)) - 1
    sharpe = statistics.mean(returns) / std * math.sqrt(252) if std else 0
    metrics = {'total_return': total_return, 'annual_return': annual, 'max_drawdown': max_dd,
               'volatility': std * math.sqrt(252), 'sharpe': sharpe, 'trade_count': len(trades),
               'turnover': turnover / initial, 'benchmark_return': curve[-1]['benchmark'] / initial - 1}
    fingerprint = hashlib.sha256(json.dumps([asdict(b) for b in bars], sort_keys=True).encode()).hexdigest()
    return {'metrics': metrics, 'curve': curve, 'trades': trades, 'data_hash': fingerprint,
            'feature_snapshot': timeline.snapshot, 'feature_coverage': timeline.coverage, 'rebalance_every': rebalance_every,
            'initial_equity': float(initial), 'metrics_version': 2, 'benchmark_entry': benchmark_entry,
            'signal_diagnostics': summarize_signals(code, len(bars), branches, execution, raw_positive, capped_positive, pending, trades),
            'next_target': pending, 'last_close': bars[-1].close, 'last_date': bars[-1].date,
            'methodology': '指标口径v2：252日年化、无风险利率0；逐日收益含初始资金至首日收盘损益；收盘信号、下一交易日开盘撮合；'
                           '基准从首个允许执行且非零量的开盘满仓买入持有（忽略费用、手数）；收益含期末持仓收盘估值、不强制平仓；'
                           '回撤仅按收盘权益计算；成交笔数含买卖各边及再平衡；不含分红和公司行动。',
            'warnings': ['当前标的池可能存在幸存者偏差。',
                         '简化撮合未实现涨跌停、市场节假日校验及各市场真实费用；仅用于研究和模拟。',
                         '全日成交量仅作为开盘可交易状态的事后代理；未限制成交容量，不代表真实开盘流动性。',
                         '最大回撤仅为收盘回撤，不含盘中风险；历史回撤约束不是止损保证。']}
