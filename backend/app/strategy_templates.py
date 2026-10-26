"""Versioned research templates. No profitability or SOTA performance claim."""
from copy import deepcopy
import hashlib
import json
from .db import Strategy, audit
from .schemas import StrategyInput


def parameter(key, label, minimum, maximum, integer=False):
    return {'key': key, 'label': label, 'min': minimum, 'max': maximum, 'integer': integer}


COMMON_RISK = [parameter('weight', '最大目标仓位', .01, 1),
               parameter('vol_window', '波动率窗口', 5, 252, True),
               parameter('target_vol', '目标仓位年化波动', .01, .5),
               parameter('vol_floor', '年化波动下限', .005, .2)]
MOMENTUM_REFERENCE = {'title': 'Moskowitz, Ooi & Pedersen (2012) · Time Series Momentum',
                      'url': 'https://www.sciencedirect.com/science/article/pii/S0304405X11002613'}
KAMA_REFERENCE = {'title': 'StockCharts · Kaufman Adaptive Moving Average',
                  'url': 'https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/kaufmans-adaptive-moving-average-kama'}

TEMPLATES = [
    {
        'id': 'multi-horizon-momentum', 'version': 1, 'name': '多周期时序动量 · 风险缩放',
        'category': '动量 / 趋势', 'warmup': 253,
        'description': '三个回看周期投票，叠加长期均线过滤；以目标波动率动态控制做多仓位。',
        'limitations': '单标的、只做多的研究变体，并非论文跨资产多空组合复现；短区间可能没有交易。',
        'references': [MOMENTUM_REFERENCE],
        'params': {'short': 63, 'medium': 126, 'long': 252, 'trend': 200,
                   'weight': .3, 'vol_window': 20, 'target_vol': .08, 'vol_floor': .02},
        'parameters': [parameter('short', '短期动量窗口', 5, 252, True),
                       parameter('medium', '中期动量窗口', 10, 504, True),
                       parameter('long', '长期动量窗口', 20, 756, True),
                       parameter('trend', '长期均线窗口', 20, 504, True), *COMMON_RISK],
        'code': '''def target(history, params):
    # 回看周期须递增；N期动量和N个收益率都需要N+1个价格。
    if params["short"] >= params["medium"] or params["medium"] >= params["long"]:
        return 0.0
    if len(history) < max(params["long"] + 1, params["trend"], params["vol_window"] + 1):
        return 0.0
    # 均线包含当日收盘；趋势过滤不通过时保持空仓。
    price = history[-1]
    if price <= mean(history[-params["trend"]:]):
        return 0.0
    # 只对上涨与否投票，不按涨幅大小加权。
    votes = 0
    if price > history[-params["short"] - 1]:
        votes = votes + 1
    if price > history[-params["medium"] - 1]:
        votes = votes + 1
    if price > history[-params["long"] - 1]:
        votes = votes + 1
    # target_vol为年化波动预算而非收益目标；vol_floor防止分母过小。
    risk = max(realized_vol(history, params["vol_window"]), params["vol_floor"])
    # 返回账户目标权重，仍受平台仓位上限裁剪。
    return min(params["weight"], params["target_vol"] / risk) * votes / 3
'''
    },
    {
        'id': 'volatility-target-trend', 'version': 1, 'name': '波动率目标 EMA 趋势',
        'category': '趋势 / 风险管理', 'warmup': 100,
        'description': '快慢 EMA 确认上涨趋势；估计收盘收益波动，在高波动时主动缩小目标仓位。',
        'limitations': '目标波动率是持仓的近似风险预算，不是收益保证；单标的、无杠杆，平台仓位上限仍生效。',
        'references': [MOMENTUM_REFERENCE],
        'params': {'fast': 20, 'slow': 100, 'weight': .3, 'vol_window': 20,
                   'target_vol': .08, 'vol_floor': .02},
        'parameters': [parameter('fast', '快速 EMA 窗口', 2, 200, True),
                       parameter('slow', '慢速 EMA 窗口', 5, 756, True), *COMMON_RISK],
        'code': '''def target(history, params):
    # EMA周期须快小于慢；波动率需要窗口数+1根收盘价。
    if params["fast"] >= params["slow"]:
        return 0.0
    if len(history) < max(params["slow"], params["vol_window"] + 1):
        return 0.0
    # EMA沿传入历史递推，价格和快EMA都必须高于慢EMA。
    quick = ema(history, params["fast"])
    slow = ema(history, params["slow"])
    if quick <= slow or history[-1] <= slow:
        return 0.0
    # 年化波动预算/估计波动决定权重；weight是上限，不是乘数。
    risk = max(realized_vol(history, params["vol_window"]), params["vol_floor"])
    return min(params["weight"], params["target_vol"] / risk)
'''
    },
    {
        'id': 'adaptive-kama', 'version': 1, 'name': 'KAMA 自适应趋势过滤',
        'category': '自适应 / 趋势', 'warmup': 61,
        'description': '用效率比区分趋势与噪声，价格站上自适应均线后参与；波动率预算和近期高点回撤共同过滤。',
        'limitations': '近期高点回撤过滤不是基于实际持仓成本的止损单；KAMA 用首个窗口 SMA 初始化。',
        'references': [KAMA_REFERENCE],
        'params': {'er_window': 20, 'fast': 2, 'slow': 30, 'min_er': .25,
                   'peak_window': 60, 'max_pullback': .12, 'weight': .3,
                   'vol_window': 20, 'target_vol': .08, 'vol_floor': .02},
        'parameters': [parameter('er_window', '效率比窗口', 5, 252, True),
                       parameter('fast', '最快平滑周期', 1, 50, True),
                       parameter('slow', '最慢平滑周期', 10, 200, True),
                       parameter('min_er', '最低趋势效率比', .01, .95),
                       parameter('peak_window', '高点回看窗口', 10, 504, True),
                       parameter('max_pullback', '近期高点最大回撤', .01, .5), *COMMON_RISK],
        'code': '''def target(history, params):
    # fast/slow控制KAMA最快/最慢平滑，不是两条独立均线。
    if params["fast"] >= params["slow"]:
        return 0.0
    # 此前高点不含当日，因此peak_window也需要额外一根价格。
    if len(history) < max(params["er_window"] + 1, params["peak_window"] + 1, params["vol_window"] + 1):
        return 0.0
    # ER衡量路径效率，不表示上涨方向；还须检查价格站上KAMA。
    if efficiency_ratio(history, params["er_window"]) < params["min_er"]:
        return 0.0
    average = kama(history, params["er_window"], params["fast"], params["slow"])
    peak = max(history[-params["peak_window"] - 1:-1])
    # max_pullback=0.12表示相对此前高点回撤12%，不是持仓成本止损。
    if history[-1] <= average or history[-1] / peak - 1 < -params["max_pullback"]:
        return 0.0
    # 输出按年化波动缩放的目标仓位，仍受平台风控限制。
    risk = max(realized_vol(history, params["vol_window"]), params["vol_floor"])
    return min(params["weight"], params["target_vol"] / risk)
'''
    },
    {
        'id': 'risk-filtered-breakout', 'version': 1, 'name': '收盘通道突破 · 风险过滤',
        'category': '突破 / 趋势', 'warmup': 100,
        'description': '突破此前 N 日最高收盘价触发参与，跌破 M 日最低收盘价退出；叠加长期趋势与波动上限。',
        'limitations': '只使用收盘价，不是基于最高/最低价的标准 Donchian/海龟系统；不含 ATR 加仓与持仓成本止损。',
        'references': [],
        'params': {'entry': 55, 'exit': 20, 'trend': 100, 'max_vol': .6,
                   'weight': .3, 'vol_window': 20, 'target_vol': .08, 'vol_floor': .02},
        'parameters': [parameter('entry', '突破窗口', 10, 504, True),
                       parameter('exit', '退出窗口', 2, 252, True),
                       parameter('trend', '长期均线窗口', 20, 504, True),
                       parameter('max_vol', '年化波动禁入上限', .05, 2), *COMMON_RISK],
        'code': '''def target(history, params):
    # 退出窗口须短于突破窗口，预热后才计算信号。
    if params["exit"] >= params["entry"]:
        return 0.0
    if len(history) < max(params["entry"] + 1, params["trend"], params["vol_window"] + 1):
        return 0.0
    # 使用含当日的均线过滤；max_vol=0.6代表60%年化波动上限。
    if history[-1] <= mean(history[-params["trend"]:]):
        return 0.0
    volatility = realized_vol(history, params["vol_window"])
    if volatility > params["max_vol"]:
        return 0.0
    # 通道只用此前最高/最低收盘价，虚拟状态不读取账户持仓。
    # 外层过滤不会重置通道状态，恢复后可能无需新突破即可持仓。
    signal = channel_signal(history, params["entry"], params["exit"])
    return signal * min(params["weight"], params["target_vol"] / max(volatility, params["vol_floor"]))
'''
    },
    {
        'id': 'regime-adaptive-ensemble', 'version': 1, 'name': '趋势 / 震荡状态切换组合',
        'category': '组合信号 / 状态适应', 'warmup': 100,
        'description': '效率比高时跟随 EMA 趋势；低效率且长期趋势向上时，用 RSI 和前窗 Z-score 捕捉超卖反弹。',
        'limitations': '规则型状态切换研究策略，不是训练过的 HMM、深度学习或强化学习；超卖可能持续，收益未经样本外验证。',
        'references': [KAMA_REFERENCE],
        'params': {'fast': 20, 'slow': 100, 'er_window': 20, 'trend_er': .35,
                   'rsi_window': 14, 'rsi_entry': 35, 'z_window': 20, 'z_entry': 1.5,
                   'reversion_scale': .5, 'weight': .3, 'vol_window': 20,
                   'target_vol': .08, 'vol_floor': .02},
        'parameters': [parameter('fast', '快速 EMA 窗口', 2, 200, True),
                       parameter('slow', '慢速 EMA 窗口', 5, 756, True),
                       parameter('er_window', '效率比窗口', 5, 252, True),
                       parameter('trend_er', '趋势状态效率阈值', .05, .95),
                       parameter('rsi_window', 'RSI 周期', 2, 100, True),
                       parameter('rsi_entry', '超卖 RSI 阈值', 5, 50),
                       parameter('z_window', '前窗 Z-score 周期', 5, 252, True),
                       parameter('z_entry', '负 Z-score 入场阈值', .2, 5),
                       parameter('reversion_scale', '震荡状态风险系数', .05, 1), *COMMON_RISK],
        'code': '''def target(history, params):
    # 先保护周期关系和动态预热，N个收益或价差需要N+1个收盘价。
    if params["fast"] >= params["slow"]:
        return 0.0
    if len(history) < max(
        params["slow"], params["er_window"] + 1, params["rsi_window"] + 1,
        params["z_window"] + 1, params["vol_window"] + 1,
    ):
        return 0.0
    slow = ema(history, params["slow"])
    # 风险预算按年化波动缩放，weight是目标仓位上限。
    risk = max(realized_vol(history, params["vol_window"]), params["vol_floor"])
    weight = min(params["weight"], params["target_vol"] / risk)
    efficiency = efficiency_ratio(history, params["er_window"])
    # 高效率时仅执行趋势分支，不与震荡分支仓位叠加。
    if efficiency >= params["trend_er"]:
        if ema(history, params["fast"]) > slow and history[-1] > slow:
            return weight
        return 0.0
    # 低效率时仍需长期趋势向上；RSI用0-100刻度，Z阈值取负方向。
    if (
        history[-1] > slow
        and rsi(history, params["rsi_window"]) < params["rsi_entry"]
        and zscore(history, params["z_window"]) < -params["z_entry"]
    ):
        return weight * params["reversion_scale"]
    return 0.0
'''
    }
]


def list_templates():
    return deepcopy(TEMPLATES)


def validate_template(template_id, body):
    template = next((item for item in TEMPLATES if item['id'] == template_id), None)
    if not template:
        raise ValueError('策略模板不存在')
    if body.parent_id:
        raise ValueError('模板创建不能指定父策略；已有版本请使用派生流程')
    if set(body.params) != set(template['params']):
        raise ValueError('请提供模板要求的完整参数对象')
    for rule in template['parameters']:
        value = body.params[rule['key']]
        if type(value) not in (int, float) or not rule['min'] <= value <= rule['max'] or (rule['integer'] and value != int(value)):
            raise ValueError(f"参数 {rule['key']} 须位于 {rule['min']} 到 {rule['max']}" + (' 且为整数' if rule['integer'] else ''))
        if rule['integer']:
            body.params[rule['key']] = int(value)
    if 'fast' in body.params and body.params['fast'] >= body.params['slow']:
        raise ValueError('快速周期必须小于慢速周期')
    if 'entry' in body.params and body.params['exit'] >= body.params['entry']:
        raise ValueError('退出周期必须小于突破周期')
    if 'short' in body.params and not body.params['short'] < body.params['medium'] < body.params['long']:
        raise ValueError('动量周期必须满足短期 < 中期 < 长期')
    validated = StrategyInput(**body.model_dump())
    return template, validated


def instantiate_template(db, template_id, body):
    template, validated = validate_template(template_id, body)
    request_hash = hashlib.sha256(json.dumps({'template_id': template_id, 'version': template['version'],
                                              **validated.model_dump()}, sort_keys=True).encode()).hexdigest()
    strategy_id = hashlib.sha256(('strategy-template:' + body.client_key).encode()).hexdigest()[:32]
    existing = db.get(Strategy, strategy_id)
    if existing:
        if existing.provenance.get('request_hash') != request_hash:
            raise ValueError('幂等键已用于其他模板或参数')
        return existing
    row = Strategy(id=strategy_id, **validated.model_dump(), status='candidate',
                   provenance={'type': 'research_template', 'template_id': template_id,
                               'template_version': template['version'], 'request_hash': request_hash,
                               'customized_code': validated.code != template['code'],
                               'references': deepcopy(template['references']),
                               'limitations': template['limitations']})
    db.add(row)
    db.flush()
    audit(db, 'strategy.template_created', strategy_id=row.id, template_id=template_id)
    return row
