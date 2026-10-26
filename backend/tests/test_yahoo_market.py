# -*- coding: utf-8 -*-
"""外部历史行情适配与模式隔离测试。"""

from datetime import datetime
from datetime import timezone

import httpx
import pytest
from pydantic import ValidationError

from app.providers import load_bars
from app.schemas import ProviderInput
from app.yahoo_market import YahooMarket
from app.yahoo_market import yahoo_symbol


@pytest.mark.parametrize(
    ('symbol', 'expected'),
    [('GOOG', 'GOOG'), ('US.QQQ', 'QQQ'), ('00700', '0700.HK'), ('HK.03032', '3032.HK'),
     ('CN.510300', '510300.SS'), ('CN.000001', '000001.SZ'), ('HK.HSI', '^HSI'), ('US.SPX', '^GSPC')],
)
def test_symbol_mapping(symbol: str, expected: str) -> None:
    """保留市场信息并转换港股前导零。"""
    assert yahoo_symbol(symbol) == expected


def test_unified_adjustment_and_date_range() -> None:
    """OHLC 同比例复权，不返回区间外数据。"""
    stamp = int(datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc).timestamp())
    body = {'chart': {'error': None, 'result': [{
        'meta': {'exchangeTimezoneName': 'America/New_York', 'currency': 'USD'},
        'timestamp': [stamp, stamp + 86400],
        'indicators': {'quote': [{'open': [100, 100], 'high': [110, 110], 'low': [90, 90],
                                  'close': [100, 100], 'volume': [1000, 1000]}],
                       'adjclose': [{'adjclose': [50, 50]}]},
    }]}}
    driver = YahooMarket({})
    rows = driver.parse(body, 'GOOG', '2024-01-02', '2024-01-02')
    assert len(rows) == 1
    assert (rows[0].open, rows[0].high, rows[0].low, rows[0].close) == (50, 55, 45, 50)
    assert driver.metadata['price_basis'] == 'adjusted'


def test_reject_live_mode() -> None:
    """历史复权源不能作为实时行情。"""
    with pytest.raises(ValidationError, match='historical'):
        ProviderInput(name='Yahoo', kind='market', driver='yahoo', config={'mode': 'live'})
    provider = {'id': 'test', 'name': 'Yahoo', 'driver': 'yahoo', 'kind': 'market',
                'config': {'mode': 'historical'}, 'enabled': True, 'priority': 10}
    with pytest.raises(ValueError, match='没有启用且匹配'):
        load_bars([provider], 'live', 'GOOG', '2024-01-01', '2024-06-01')


def test_rate_limit_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 429 直接失败，不退回合成行情。"""
    transport = httpx.MockTransport(lambda request: httpx.Response(429))
    original_client = httpx.Client

    def mock_client(**kwargs) -> httpx.Client:
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, 'Client', mock_client)
    with pytest.raises(ValueError, match='HTTP 429'):
        YahooMarket({}).bars('GOOG', '2024-01-01', '2024-06-01')
