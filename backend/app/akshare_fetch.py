"""AKShare 日线抓取子进程，隔离上游输出与无超时网络调用。"""
import contextlib
import json
import math
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .contracts import Bar


def instrument(symbol):
    import re
    symbol = symbol.upper()
    indices = {'CN.000300': ('CN', 'sh000300', 'index'),
               'HK.HSI': ('HK', 'HSI', 'index'), 'US.SPX': ('US', '.INX', 'index')}
    if symbol in indices:
        return indices[symbol]
    if re.fullmatch(r'(CN\.)?\d{6}', symbol):
        code = symbol.removeprefix('CN.')
        return 'CN', code, 'etf' if code.startswith(('5', '15', '16', '18')) else 'stock'
    if re.fullmatch(r'(HK\.)?\d{5}', symbol):
        return 'HK', symbol.removeprefix('HK.'), 'stock'
    code = symbol.removeprefix('US.')
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,31}', code):
        raise ValueError('AKShare代码须为 CN.六位代码、HK.五位代码或 US.股票代码')
    return 'US', code, 'stock'


def normalize(frame, market, start, end, current=None, volume_multiplier=1, precision_repairs=None):
    if frame is None or frame.empty:
        raise ValueError('AKShare 未返回行情，请核验代码、上市日期与上游可用性')
    frame = frame.rename(columns={'日期': 'date', '开盘': 'open', '最高': 'high',
                                  '最低': 'low', '收盘': 'close', '成交量': 'volume'})
    required = {'date', 'open', 'high', 'low', 'close', 'volume'}
    if not required.issubset(frame.columns):
        raise ValueError('AKShare 响应缺少完整 OHLCV 字段')
    zone = {'CN': 'Asia/Shanghai', 'HK': 'Asia/Hong_Kong', 'US': 'America/New_York'}[market]
    today = (current or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone)).date().isoformat()
    rows = []
    for item in frame.to_dict('records'):
        trading_date = date.fromisoformat(str(item['date'])[:10]).isoformat()
        # 日线接口没有完成标记，保守排除交易所当地当日的数据。
        if not start <= trading_date <= end or trading_date >= today:
            continue
        values = [float(item[k]) for k in ('open', 'high', 'low', 'close', 'volume')]
        opening, high, low, close, volume = values
        volume *= volume_multiplier
        if (any(not math.isfinite(v) for v in values) or min(values[:4]) <= 0
                or not math.isfinite(volume) or volume < 0 or not volume.is_integer() or low > high):
            raise ValueError('AKShare 返回无效 OHLCV，拒绝缓存')
        if not low <= min(opening, close) <= max(opening, close) <= high:
            outside = [(field, value) for field, value in (('open', opening), ('close', close))
                       if not low <= value <= high]
            if precision_repairs is None or any(
                not math.isclose(value, min(high, max(low, value)), rel_tol=1e-7, abs_tol=0)
                or abs(value - min(high, max(low, value))) > 0.00002
                for _, value in outside
            ):
                raise ValueError('AKShare 返回无效 OHLCV，拒绝缓存')
            for field, value in outside:
                precision_repairs.append({'date': trading_date, 'field': field, 'original': value,
                                          'normalized': min(high, max(low, value))})
            opening, close = min(high, max(low, opening)), min(high, max(low, close))
        rows.append(Bar(trading_date, opening, high, low, close, int(volume)))
    rows.sort(key=lambda row: row.date)
    if len({row.date for row in rows}) != len(rows):
        raise ValueError('AKShare 返回重复交易日期，拒绝缓存')
    return rows


def sina_frame(ak, market, code, adjustment):
    import pandas as pd
    daily = ak.stock_hk_daily if market == 'HK' else ak.stock_us_daily
    if market == 'US' and adjustment == 'hfq':
        raise ValueError('新浪美股不支持后复权，不能用前复权或原价代替')
    frame = daily(symbol=code, adjust='')
    if not adjustment:
        return frame
    factors = daily(symbol=code, adjust=adjustment + '-factor')
    factor_key = adjustment + '_factor'
    if factors is None or factors.empty or not {'date', factor_key}.issubset(factors.columns):
        raise ValueError('新浪复权因子不可用，拒绝把未复权行情标记为复权行情')
    frame = frame.copy()
    factors = factors.copy()
    frame['date'] = pd.to_datetime(frame['date'])
    factors['date'] = pd.to_datetime(factors['date'])
    frame = pd.merge_asof(frame.sort_values('date'), factors.sort_values('date'), on='date')
    factor = pd.Series(pd.to_numeric(frame[factor_key], errors='raise'))
    if any(not math.isfinite(float(value)) or float(value) <= 0 for value in factor):
        raise ValueError('新浪复权因子未覆盖历史区间或无效')
    offset_key = 'adjust' if market == 'US' else 'cash' if adjustment == 'hfq' else None
    offset = pd.Series(pd.to_numeric(frame[offset_key], errors='raise')) if offset_key else pd.Series(0, index=frame.index)
    for column in ('open', 'high', 'low', 'close'):
        values = pd.Series(pd.to_numeric(frame[column], errors='raise'))
        frame[column] = values * factor + offset
    return frame


def fetch(symbol, start, end, adjust, ak=None, upstream='eastmoney'):
    if ak is None:
        import akshare as ak
    market, code, kind = instrument(symbol)
    dates = {'start_date': start.replace('-', ''), 'end_date': end.replace('-', '')}
    adjustment = '' if adjust == 'none' or kind == 'index' else adjust
    vendor_symbol = code
    volume_multiplier = 1
    volume_unit = 'vendor_native'
    if kind == 'index':
        if market == 'CN':
            frame = ak.stock_zh_index_daily_em(symbol=code, **dates)
        elif market == 'HK':
            frame = ak.stock_hk_index_daily_sina(symbol=code)
        else:
            frame = ak.index_us_stock_sina(symbol=code)
        endpoint = {'CN': 'stock_zh_index_daily_em', 'HK': 'stock_hk_index_daily_sina',
                    'US': 'index_us_stock_sina'}[market]
    elif upstream == 'sina' and market in {'HK', 'US'}:
        endpoint = 'stock_hk_daily' if market == 'HK' else 'stock_us_daily'
        frame = sina_frame(ak, market, code, adjustment)
    elif market == 'CN':
        endpoint = 'fund_etf_hist_em' if kind == 'etf' else 'stock_zh_a_hist'
        frame = getattr(ak, endpoint)(symbol=code, period='daily', adjust=adjustment, **dates)
        volume_multiplier, volume_unit = 100, 'shares_or_units'
    elif market == 'HK':
        endpoint = 'stock_hk_hist'
        frame = ak.stock_hk_hist(symbol=code, period='daily', adjust=adjustment, **dates)
    else:
        endpoint = 'stock_us_hist'
        catalog = ak.stock_us_spot_em()
        if catalog is None or '代码' not in catalog.columns:
            raise ValueError('AKShare 美股代码目录不可用，无法确认交易所代码')
        matches = [str(value) for value in catalog['代码']
                   if str(value).partition('.')[2].upper() == code]
        if len(matches) != 1:
            raise ValueError(f'AKShare 美股目录无法唯一定位 {code}，不猜测交易所代码')
        vendor_symbol = matches[0]
        frame = ak.stock_us_hist(symbol=vendor_symbol, period='daily', adjust=adjustment, **dates)
    precision_repairs = []
    rows = normalize(frame, market, start, end, volume_multiplier=volume_multiplier,
                     precision_repairs=precision_repairs if upstream == 'sina' and kind != 'index' else None)
    warning = ('公开历史行情可能被事后修订；前后复权不是严格时点数据。'
               '仅使用交易所当地昨日及之前日线，不提供盘中或当日收盘报价。')
    if kind != 'index' and not adjustment:
        warning += '当前是不复权原价，回测未处理分红与拆股，收益不可视为总回报。'
    if volume_unit == 'vendor_native':
        warning += '成交量保留上游单位，尚未核验股/手，勿用于跨市场成交量比较。'
    if precision_repairs:
        warning += f'新浪日线有{len(precision_repairs)}个开/收盘字段存在极小边界精度偏差，已归一到高低价边界；原值与修正见 precision_repairs，未删除交易日。'
    return rows, {'vendor_symbol': vendor_symbol, 'endpoint': endpoint, 'market': market,
                  'currency': {'CN': 'CNY', 'HK': 'HKD', 'US': 'USD'}[market],
                  'interval': '1d', 'adjust': 'none' if kind == 'index' else adjust,
                  'price_basis': 'raw' if not adjustment else 'adjusted',
                  'volume_unit': volume_unit, 'fetched_at': datetime.now(timezone.utc).isoformat(),
                  'precision_repairs': precision_repairs,
                  'last_date': rows[-1].date if rows else None, 'warning': warning}


if __name__ == '__main__':
    from dataclasses import asdict
    try:
        request = json.load(sys.stdin)
        with contextlib.redirect_stdout(sys.stderr):
            rows, metadata = fetch(**request)
        print(json.dumps({'rows': [asdict(row) for row in rows], 'metadata': metadata}, allow_nan=False))
    except Exception as exc:
        print(json.dumps({'error': f'{type(exc).__name__}: {str(exc)[:300]}'}, ensure_ascii=False))
        sys.exit(1)
