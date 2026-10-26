# -*- coding: utf-8 -*-
"""首页固定指数的只读延迟报价，与研究及交易行情完全隔离。"""

from copy import deepcopy
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import math
import re
from threading import Lock
import time
from zoneinfo import ZoneInfo

import httpx


INDEX_QUOTES = (
    ('CN.000300', '沪深300', 'sh000300', '000300', 'Asia/Shanghai'),
    ('HK.HSI', '恒生指数', 'hkHSI', 'HSI', 'Asia/Hong_Kong'),
    ('US.SPX', '标普500', 'usINX', '.INX', 'America/New_York'),
)
QUOTE_URL = 'https://qt.gtimg.cn/q=' + ','.join(row[2] for row in INDEX_QUOTES)
CACHE_SECONDS = 30
_CACHE_LOCK = Lock()
_cached_rows: list[dict] = []
_cache_deadline = 0.0


def parse_quote(text: str, index: tuple, now: datetime) -> dict:
    """校验供应商代码、点位和交易所当地报价时间，不将抓取时间当报价时间。"""
    symbol, name, vendor_symbol, vendor_code, zone = index
    match = re.search(rf'(?:^|\n)v_{re.escape(vendor_symbol)}="([^"\r\n]*)";', text)
    if match is None:
        raise ValueError('行情源未返回该指数')
    fields = match.group(1).split('~')
    if len(fields) < 33 or fields[2] != vendor_code:
        raise ValueError('指数报价代码或字段不匹配')
    try:
        price = float(fields[3])
        previous_close = float(fields[4])
    except ValueError as exc:
        raise ValueError('指数点位格式无效') from exc
    if not all(math.isfinite(value) and value > 0 for value in (price, previous_close)):
        raise ValueError('指数点位或昨收无效')
    stamp = fields[30]
    if re.fullmatch(r'\d{14}', stamp):
        date_format = '%Y%m%d%H%M%S'
    elif re.fullmatch(r'\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}', stamp):
        date_format = '%Y/%m/%d %H:%M:%S'
    elif re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', stamp):
        date_format = '%Y-%m-%d %H:%M:%S'
    else:
        raise ValueError('指数报价时间格式无效')
    try:
        quoted_at = datetime.strptime(stamp, date_format).replace(tzinfo=ZoneInfo(zone))
    except ValueError as exc:
        raise ValueError('指数报价时间无效') from exc
    if quoted_at > now + timedelta(minutes=5):
        raise ValueError('指数报价时间超前，请核验数据源')
    stale = now - quoted_at > timedelta(days=4)
    return {
        'symbol': symbol,
        'name': name,
        'price': price,
        'change': price / previous_close - 1,
        'date': quoted_at.date().isoformat(),
        'quoted_at': quoted_at.isoformat(),
        'fetched_at': now.isoformat(),
        'source': '腾讯财经公开指数报价（可能延迟）',
        'synthetic': False,
        'quote_kind': 'delayed',
        'stale': stale,
        'warning': '报价已超过4个自然日，请核验市场休市或上游更新状态。' if stale else '',
    }


def index_quotes() -> list[dict]:
    """短缓存合并并发请求；失败保留上次报价并明确标旧，绝不退回合成行情。"""
    global _cached_rows
    global _cache_deadline

    with _CACHE_LOCK:
        if _cached_rows and time.monotonic() < _cache_deadline:
            return deepcopy(_cached_rows)
        now = datetime.now(timezone.utc)
        fetch_error = ''
        text = ''
        try:
            with httpx.Client(timeout=8.0, follow_redirects=False) as client:
                response = client.get(QUOTE_URL, headers={'User-Agent': 'Northstar-MarketObserver/1.0'})
                response.raise_for_status()
                if len(response.content) > 100000:
                    raise ValueError('指数报价响应过大')
                text = response.content.decode('gb18030')
        except (httpx.HTTPError, UnicodeError, ValueError):
            fetch_error = '公开指数行情源暂不可用，请稍后刷新'
        previous = {row['symbol']: row for row in _cached_rows}
        rows = []
        for index in INDEX_QUOTES:
            symbol, name = index[:2]
            try:
                if fetch_error:
                    raise ValueError(fetch_error)
                row = parse_quote(text, index, now)
            except ValueError as exc:
                row = deepcopy(previous.get(symbol, {'symbol': symbol, 'name': name, 'synthetic': False}))
                row.update(error=str(exc), stale=True, attempted_at=now.isoformat())
                if 'price' in row:
                    row['warning'] = '更新失败，显示上次成功报价；不是当前报价。'
            rows.append(row)
        _cached_rows = rows
        _cache_deadline = time.monotonic() + CACHE_SECONDS
        return deepcopy(rows)
