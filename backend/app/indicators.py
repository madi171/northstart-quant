"""Bounded, causal indicators for the strategy interpreter; no IO or mutation."""
from collections import deque
import math
import statistics

MAX_POINTS = 8192
MAX_PERIOD = 2000


def _period(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value != int(value) or not 1 <= value <= MAX_PERIOD:
        raise ValueError('指标周期须为1到2000的整数')
    return int(value)


def _prices(values, minimum):
    if not isinstance(values, (list, tuple)) or not minimum <= len(values) <= MAX_POINTS:
        raise ValueError('指标历史长度不足或超过8192条')
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 1e12 for value in values):
        raise ValueError('指标只接受有限正价格')
    return values


def ema(values, period):
    period = _period(period)
    values = _prices(values, period)
    average = statistics.fmean(values[:period])
    alpha = 2.0 / (period + 1)
    for price in values[period:]:
        average += alpha * (price - average)
    return average


def realized_vol(values, period):
    period = _period(period)
    if period < 2:
        raise ValueError('波动率窗口至少为2')
    values = _prices(values, period + 1)
    window = values[-period - 1:]
    returns = [window[index] / window[index - 1] - 1 for index in range(1, len(window))]
    return statistics.stdev(returns) * math.sqrt(252)


def rsi(values, period):
    period = _period(period)
    values = _prices(values, period + 1)
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gain = math.fsum(max(change, 0) for change in changes[:period]) / period
    loss = math.fsum(max(-change, 0) for change in changes[:period]) / period
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
    if gain == 0 and loss == 0:
        return 50.0
    if loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + gain / loss)


def efficiency_ratio(values, period):
    period = _period(period)
    values = _prices(values, period + 1)
    window = values[-period - 1:]
    noise = math.fsum(abs(window[index] - window[index - 1]) for index in range(1, len(window)))
    return min(1.0, abs(window[-1] - window[0]) / noise) if noise else 0.0


def kama(values, period, fast, slow):
    period, fast, slow = _period(period), _period(fast), _period(slow)
    if fast >= slow:
        raise ValueError('KAMA快速周期须小于慢速周期')
    values = _prices(values, period + 1)
    adaptive = statistics.fmean(values[:period])
    quick, gradual = 2.0 / (fast + 1), 2.0 / (slow + 1)
    differences = [abs(values[index] - values[index - 1]) for index in range(1, len(values))]
    noise = math.fsum(differences[:period])
    for index in range(period, len(values)):
        if index > period:
            noise += differences[index - 1] - differences[index - period - 1]
        noise = max(0.0, noise)
        efficiency = min(1.0, abs(values[index] - values[index - period]) / noise) if noise else 0.0
        smoothing = (efficiency * (quick - gradual) + gradual) ** 2
        adaptive += smoothing * (values[index] - adaptive)
    return adaptive


def zscore(values, period):
    period = _period(period)
    if period < 2:
        raise ValueError('Z-score窗口至少为2')
    values = _prices(values, period + 1)
    previous = values[-period - 1:-1]
    deviation = statistics.stdev(previous)
    if not deviation:
        return 0.0
    return (values[-1] - statistics.fmean(previous)) / deviation


def channel_signal(values, entry, exit):
    entry, exit = _period(entry), _period(exit)
    if exit >= entry:
        raise ValueError('通道退出窗口须小于突破窗口')
    values = _prices(values, entry + 1)
    highs, lows = deque(), deque()
    holding = 0.0
    for index, price in enumerate(values):
        while highs and highs[0] < index - entry:
            highs.popleft()
        while lows and lows[0] < index - exit:
            lows.popleft()
        if index >= entry:
            if holding == 0 and price > values[highs[0]]:
                holding = 1.0
            elif holding and price < values[lows[0]]:
                holding = 0.0
        while highs and values[highs[-1]] <= price:
            highs.pop()
        while lows and values[lows[-1]] >= price:
            lows.pop()
        highs.append(index)
        lows.append(index)
    return holding


INDICATORS = {function.__name__: function for function in
              (ema, realized_vol, rsi, efficiency_ratio, kama, zscore, channel_signal)}
