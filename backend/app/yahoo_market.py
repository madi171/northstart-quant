# -*- coding: utf-8 -*-
"""Yahoo Finance 历史日线适配器，不提供实时交易行情。"""

import math
import re
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from .contracts import Bar


INDEX_SYMBOLS = {'CN.000300': '000300.SS', 'HK.HSI': '^HSI', 'US.SPX': '^GSPC'}


def yahoo_symbol(symbol: str) -> str:
    """将系统代码转换为供应商代码，保留市场语义。"""
    symbol = symbol.upper()
    if symbol in INDEX_SYMBOLS:
        return INDEX_SYMBOLS[symbol]
    if symbol.startswith('US.'):
        symbol = symbol[3:]
    elif symbol.startswith('HK.') or re.fullmatch(r'\d{5}', symbol):
        code = symbol[3:] if symbol.startswith('HK.') else symbol
        if not re.fullmatch(r'\d{5}', code):
            raise ValueError('港股代码须为五位数字')
        return f'{int(code):04d}.HK'
    elif symbol.startswith('CN.'):
        code = symbol[3:]
        if not re.fullmatch(r'\d{6}', code):
            raise ValueError('A股代码须为六位数字')
        if code.startswith(('4', '8', '92')):
            raise ValueError('当前 Yahoo 适配器不支持北交所代码')
        return code + ('.SS' if code.startswith(('5', '6', '9')) else '.SZ')
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,31}', symbol):
        raise ValueError('请使用 US.股票代码、HK.五位代码或 CN.六位代码')
    return symbol


class YahooMarket:
    """从固定 HTTPS 地址抓取已完成历史日线，统一复权 OHLC。"""

    def __init__(self, config: dict[str, str]) -> None:
        if config.get('mode', 'historical') != 'historical':
            raise ValueError('Yahoo 仅支持 historical，请选择历史数据回放')
        if config.get('price_basis', 'adjusted') != 'adjusted':
            raise ValueError('Yahoo 历史源须使用 adjusted 复权口径')
        self.metadata: dict[str, object] = {}

    def bars(self, symbol: str, start: str, end: str) -> list[Bar]:
        """获取指定闭区间的历史行情，不自动重试限流请求。"""
        vendor_symbol = yahoo_symbol(symbol)
        begin = datetime.combine(date.fromisoformat(start) - timedelta(days=1), time(), timezone.utc)
        finish = datetime.combine(date.fromisoformat(end) + timedelta(days=2), time(), timezone.utc)
        url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + quote(vendor_symbol, safe='')
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                with client.stream(
                    'GET',
                    url,
                    params={'period1': int(begin.timestamp()), 'period2': int(finish.timestamp()), 'interval': '1d'},
                    headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
                ) as response:
                    if response.status_code == 429:
                        raise ValueError('Yahoo HTTP 429：出口 IP 被限流；稍后再试或改用 Parquet 历史快照')
                    if response.status_code != 200:
                        raise ValueError(f'Yahoo 行情返回 HTTP {response.status_code}')
                    chunks = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 8_000_000:
                            raise ValueError('Yahoo 响应超过 8MB')
                        chunks.append(chunk)
                    body = httpx.Response(200, content=b''.join(chunks)).json()
        except httpx.HTTPError as exc:
            raise ValueError('Yahoo 网络连接失败或超时；请检查服务器 HTTPS 出口') from exc
        try:
            return self.parse(body, vendor_symbol, start, end)
        except (KeyError, IndexError, TypeError, OverflowError) as exc:
            raise ValueError('Yahoo 响应缺少完整 OHLCV 或复权价格') from exc

    def parse(self, body: dict, vendor_symbol: str, start: str, end: str) -> list[Bar]:
        """解析交易所当地日期，忽略缺失及尚未完成的日线。"""
        chart = body['chart']
        if chart.get('error') or not chart.get('result'):
            raise ValueError(f'Yahoo 没有 {vendor_symbol} 的历史行情')
        result = chart['result'][0]
        metadata = result['meta']
        zone = ZoneInfo(metadata['exchangeTimezoneName'])
        quotes = result['indicators']['quote'][0]
        adjusted = result['indicators']['adjclose'][0]['adjclose']
        current = datetime.now(timezone.utc)
        rows = []
        for index, timestamp in enumerate(result.get('timestamp') or []):
            trading_date = datetime.fromtimestamp(timestamp, zone).date().isoformat()
            if not start <= trading_date <= end or timestamp + 86400 > current.timestamp():
                continue
            values = [quotes[key][index] for key in ('open', 'high', 'low', 'close', 'volume')]
            if any(value is None for value in values) or adjusted[index] is None:
                continue
            opening, high, low, close, volume = map(float, values)
            factor = float(adjusted[index]) / close if close > 0 else 0
            if not math.isfinite(factor) or factor <= 0 or not math.isfinite(volume) or volume < 0:
                raise ValueError('Yahoo 返回无效复权系数或成交量')
            rows.append(Bar(trading_date, opening * factor, high * factor, low * factor, close * factor, int(volume)))
        self.metadata = {
            'vendor_symbol': vendor_symbol,
            'currency': metadata.get('currency'),
            'exchange_timezone': metadata['exchangeTimezoneName'],
            'interval': '1d',
            'price_basis': 'adjusted',
            'fetched_at': current.isoformat(),
            'requested_start': start,
            'requested_end': end,
            'warning': '公开历史数据可能事后修订；OHLC按复权收盘比例调整，不是实时成交报价或严格时点数据。',
        }
        return rows
