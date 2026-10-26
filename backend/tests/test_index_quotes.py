# -*- coding: utf-8 -*-
"""首页指数报价解析、缓存与失败隔离回归。"""

from datetime import datetime
from datetime import timezone

import httpx
import pytest

from app import index_quotes as quotes


def quote_line(index: tuple, stamp: str, price: str = '4100', previous: str = '4000') -> str:
    """构造固定供应商字段的离线样本。"""
    fields = ['0'] * 34
    fields[1] = index[1]
    fields[2] = index[3]
    fields[3] = price
    fields[4] = previous
    fields[30] = stamp
    return f'v_{index[2]}="' + '~'.join(fields) + '";'


@pytest.mark.parametrize(
    ('position', 'stamp', 'offset'),
    [(0, '20261026132939', '+08:00'), (1, '2026/10/26 13:14:42', '+08:00'),
     (2, '2026-10-23 16:38:21', '-04:00')],
)
def test_quote_mapping_and_timestamps(position: int, stamp: str, offset: str) -> None:
    """三市场报价保留当地时区，涨跌以点位与昨收计算。"""
    index = quotes.INDEX_QUOTES[position]
    row = quotes.parse_quote(quote_line(index, stamp), index, datetime(2026, 10, 26, 6, tzinfo=timezone.utc))
    assert row['symbol'] == index[0]
    assert row['quoted_at'].endswith(offset)
    assert row['change'] == pytest.approx(0.025)
    assert not row['synthetic']
    assert not row['stale']
    assert 'prices' not in row


@pytest.mark.parametrize('price', ['0', '-1', 'nan', 'inf', 'invalid'])
def test_reject_invalid_prices(price: str) -> None:
    """缺失或非有限点位不能显示为正常行情。"""
    index = quotes.INDEX_QUOTES[0]
    with pytest.raises(ValueError, match='点位'):
        quotes.parse_quote(quote_line(index, '20261026132939', price), index,
                           datetime(2026, 10, 26, 6, tzinfo=timezone.utc))


def test_missing_wrong_symbol_and_future_time() -> None:
    """拒绝错配代码及未来时间，老报价标记过期。"""
    index = quotes.INDEX_QUOTES[0]
    now = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match='未返回'):
        quotes.parse_quote('', index, now)
    with pytest.raises(ValueError, match='不匹配'):
        quotes.parse_quote(quote_line(index, '20261026132939').replace('~000300~', '~999999~'), index, now)
    with pytest.raises(ValueError, match='超前'):
        quotes.parse_quote(quote_line(index, '20261027132939'), index, now)
    row = quotes.parse_quote(quote_line(index, '20261019132939'), index, now)
    assert row['stale']


def test_cache_partial_failure_and_explicit_old_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """短缓存不重复抓取，单指数异常隔离，网络失败不伪造新报价时间。"""
    monkeypatch.setattr(quotes, '_cached_rows', [])
    monkeypatch.setattr(quotes, '_cache_deadline', 0)
    now = datetime.now(timezone.utc)
    text = '\n'.join(quote_line(index, now.astimezone(quotes.ZoneInfo(index[4])).strftime('%Y%m%d%H%M%S'))
                     for index in quotes.INDEX_QUOTES[:2])
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(503)
        return httpx.Response(200, content=text.encode('gb18030'))

    transport = httpx.MockTransport(respond)
    original_client = httpx.Client

    def client(**kwargs) -> httpx.Client:
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(quotes.httpx, 'Client', client)
    rows = quotes.index_quotes()
    assert len(rows) == 3
    assert rows[0]['price'] == 4100
    assert rows[1]['price'] == 4100
    assert 'error' in rows[2] and 'price' not in rows[2]
    rows[0]['price'] = 1
    cached = quotes.index_quotes()
    assert len(calls) == 1
    assert cached[0]['price'] == 4100
    monkeypatch.setattr(quotes, '_cache_deadline', 0)
    failed = quotes.index_quotes()
    assert len(calls) == 2
    assert failed[0]['stale'] and failed[0]['error']
    assert failed[0]['fetched_at'] == cached[0]['fetched_at']
    assert failed[0]['quoted_at'] == cached[0]['quoted_at']
    assert not failed[0]['synthetic']
