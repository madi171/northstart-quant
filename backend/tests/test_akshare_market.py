"""AKShare 历史日线及 Parquet 缓存的离线回归测试。"""
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import ValidationError

from app.akshare_fetch import fetch, instrument, normalize
from app.akshare_market import AKShareMarket
from app.contracts import Bar
from app.market_cache import MarketCache, cache_lock
from app.providers import load_bars, validate_bars
from app.schemas import ProviderInput


def daily_rows(start='2024-01-01', end='2024-06-30', price=100):
    day, finish = date.fromisoformat(start), date.fromisoformat(end)
    rows = []
    while day <= finish:
        if day.weekday() < 5:
            rows.append(Bar(day.isoformat(), price, price + 5, price - 5, price + 1, 1000))
        day += timedelta(days=1)
    return rows


def source(fetched_at=None):
    return {'fetched_at': fetched_at or datetime.now(timezone.utc).isoformat(), 'warning': '历史日线。',
            'price_basis': 'adjusted', 'volume_unit': 'vendor_native'}


@pytest.mark.parametrize(('symbol', 'expected'), [
    ('GOOG', ('US', 'GOOG', 'stock')), ('US.QQQ', ('US', 'QQQ', 'stock')),
    ('RKLB', ('US', 'RKLB', 'stock')), ('TSLA', ('US', 'TSLA', 'stock')),
    ('VOO', ('US', 'VOO', 'stock')), ('00218', ('HK', '00218', 'stock')),
    ('HK.00700', ('HK', '00700', 'stock')), ('01810', ('HK', '01810', 'stock')),
    ('03032', ('HK', '03032', 'stock')), ('03690', ('HK', '03690', 'stock')),
    ('09618', ('HK', '09618', 'stock')), ('CN.510300', ('CN', '510300', 'etf')),
    ('CN.159915', ('CN', '159915', 'etf')), ('CN.000001', ('CN', '000001', 'stock')),
    ('600519', ('CN', '600519', 'stock')), ('CN.000300', ('CN', 'sh000300', 'index')),
    ('HK.HSI', ('HK', 'HSI', 'index')), ('US.SPX', ('US', '.INX', 'index')),
])
def test_instrument(symbol, expected):
    assert instrument(symbol) == expected


def test_normalize_dates_units_and_incomplete_day():
    frame = pd.DataFrame([{'日期': '2024-01-03', '开盘': 10, '最高': 12, '最低': 9,
                           '收盘': 11, '成交量': 20},
                          {'日期': '2024-01-02', '开盘': 10, '最高': 12, '最低': 9,
                           '收盘': 11, '成交量': 30}])
    rows = normalize(frame, 'CN', '2024-01-01', '2024-01-03',
                     current=datetime(2024, 1, 3, tzinfo=timezone.utc), volume_multiplier=100)
    assert rows == [Bar('2024-01-02', 10.0, 12.0, 9.0, 11.0, 3000)]


@pytest.mark.parametrize('change', [{'close': float('nan')}, {'volume': -1}, {'high': 1}])
def test_invalid_data_rejected(change):
    row = asdict(daily_rows()[0])
    row.update(change)
    with pytest.raises(ValueError, match='无效'):
        normalize(pd.DataFrame([row]), 'US', '2024-01-01', '2024-06-30')


@pytest.mark.parametrize(('changes', 'field', 'expected'), [
    ({'close': 445.39999}, 'close', 445.4),
    ({'close': 454.00001}, 'close', 454.0),
    ({'open': 445.39999}, 'open', 445.4),
    ({'open': 454.00001}, 'open', 454.0),
])
def test_sina_precision_repairs_are_explicit_and_audited(changes, field, expected):
    row = {'date': '2024-01-02', 'open': 445.6, 'high': 454.0,
           'low': 445.4, 'close': 445.4, 'volume': 16962334}
    row.update(changes)
    with pytest.raises(ValueError, match='无效'):
        normalize(pd.DataFrame([row]), 'HK', '2024-01-01', '2024-06-30')
    repairs = []
    bars = normalize(pd.DataFrame([row]), 'HK', '2024-01-01', '2024-06-30', precision_repairs=repairs)
    assert getattr(bars[0], field) == expected
    assert repairs == [{'date': row['date'], 'field': field, 'original': row[field], 'normalized': expected}]
    assert all(getattr(bars[0], key) == row[key] for key in ('open', 'high', 'low', 'close', 'volume') if key != field)


@pytest.mark.parametrize('changes', [
    {'close': 445.3999}, {'open': 454.001}, {'low': 455},
    {'close': 0}, {'close': -1}, {'close': float('nan')}, {'high': float('inf')},
    {'volume': -1}, {'volume': 1.5}, {'volume': float('nan')},
    {'open': 1, 'high': 1.2, 'low': 1, 'close': .999999},
    {'open': 100000, 'high': 100001, 'low': 100000, 'close': 99999.999},
])
def test_sina_precision_repairs_do_not_accept_invalid_data(changes):
    row = {'date': '2024-01-02', 'open': 445.6, 'high': 454.0,
           'low': 445.4, 'close': 445.4, 'volume': 16962334}
    row.update(changes)
    with pytest.raises(ValueError, match='无效'):
        normalize(pd.DataFrame([row]), 'HK', '2024-01-01', '2024-06-30', precision_repairs=[])


def test_valid_sina_prices_are_not_rounded_or_marked_repaired():
    row = {'date': '2024-01-02', 'open': 445.612345, 'high': 454.012345,
           'low': 445.312345, 'close': 446.412345, 'volume': 16962334}
    repairs = []
    bars = normalize(pd.DataFrame([row]), 'HK', '2024-01-01', '2024-06-30', precision_repairs=repairs)
    assert bars == [Bar(**row)]
    assert repairs == []


def test_duplicate_dates_rejected():
    row = asdict(daily_rows()[0])
    with pytest.raises(ValueError, match='重复'):
        normalize(pd.DataFrame([row, row]), 'HK', '2024-01-01', '2024-06-30')


@pytest.mark.parametrize(('symbol', 'endpoint'), [
    ('CN.600519', 'stock_zh_a_hist'), ('CN.510300', 'fund_etf_hist_em'),
    ('HK.03032', 'stock_hk_hist'), ('QQQ', 'stock_us_hist'),
    ('CN.000300', 'stock_zh_index_daily_em'), ('HK.HSI', 'stock_hk_index_daily_sina'),
    ('US.SPX', 'index_us_stock_sina'),
])
def test_endpoint_routing(symbol, endpoint):
    calls = []
    def get_frame(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([asdict(row) for row in daily_rows()])
    ak = SimpleNamespace(**{endpoint: get_frame},
                         stock_us_spot_em=lambda: pd.DataFrame({'代码': ['105.QQQ']}))
    rows, meta = fetch(symbol, '2024-01-01', '2024-06-30', 'qfq', ak)
    assert meta['endpoint'] == endpoint
    assert len(rows) > 40
    if symbol == 'QQQ':
        assert calls[0]['symbol'] == '105.QQQ'
    if meta['market'] == 'CN' and not symbol == 'CN.000300':
        assert rows[0].volume == 100000
    if symbol in {'HK.HSI', 'CN.000300', 'US.SPX'}:
        assert meta['price_basis'] == 'raw'


def test_us_catalog_not_guessed():
    ak = SimpleNamespace(stock_us_spot_em=lambda: pd.DataFrame({'代码': ['105.MSFT']}))
    with pytest.raises(ValueError, match='不猜测'):
        fetch('QQQ', '2024-01-01', '2024-06-30', 'qfq', ak)


def test_cache_hit_and_subrange(tmp_path):
    cache = MarketCache(tmp_path, {'symbol': 'GOOG', 'adjust': 'qfq'})
    calls = []
    def loader(start, end):
        calls.append((start, end))
        return daily_rows(start, end), source()
    rows, info = cache.get('2024-01-01', '2024-06-30', loader, validate_bars)
    assert info['cache_status'] == 'miss'
    selected, info = cache.get('2024-02-01', '2024-05-01', loader, validate_bars)
    assert info['cache_status'] == 'hit'
    assert len(calls) == 1
    assert selected == [row for row in rows if '2024-02-01' <= row.date <= '2024-05-01']
    assert cache.path.exists()
    assert info['cache_hash']


def test_refresh_replaces_whole_adjusted_history(tmp_path):
    identity = {'symbol': 'GOOG', 'adjust': 'qfq'}
    cache = MarketCache(tmp_path, identity, hours=0)
    cache.get('2024-01-01', '2024-05-31', lambda a, b: (daily_rows(a, b), source()), validate_bars)
    calls = []
    def revised(start, end):
        calls.append((start, end))
        return daily_rows(start, end, price=50), source()
    _, info = cache.get('2024-03-01', '2024-06-30', revised, validate_bars)
    assert calls == [('2024-01-01', '2024-06-30')]
    assert info['cache_status'] == 'refreshed'
    assert all(row.open == 50 for row in cache.read(validate_bars)[0])
    assert MarketCache(tmp_path, {**identity, 'adjust': 'none'}).path != cache.path


def test_expired_cache_requires_explicit_offline(tmp_path):
    identity = {'symbol': '00700', 'adjust': 'qfq'}
    cache = MarketCache(tmp_path, identity)
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    cache.get('2024-01-01', '2024-06-30', lambda a, b: (daily_rows(a, b), source(old)), validate_bars)
    def failed(*args):
        raise ValueError('network unavailable')
    with pytest.raises(ValueError, match='未使用过期缓存或演示行情'):
        cache.get('2024-01-01', '2024-06-30', failed, validate_bars)
    offline = MarketCache(tmp_path, identity, offline=True)
    rows, info = offline.get('2024-01-01', '2024-06-30', failed, validate_bars)
    assert rows and info['cache_status'] == 'offline' and info['cache_stale']
    with pytest.raises(ValueError, match='未覆盖'):
        offline.get('2023-01-01', '2024-06-30', failed, validate_bars)


def test_invalid_refresh_preserves_previous_file(tmp_path):
    cache = MarketCache(tmp_path, {'symbol': 'GOOG'}, hours=0)
    cache.get('2024-01-01', '2024-06-30', lambda a, b: (daily_rows(a, b), source()), validate_bars)
    before = cache.path.read_bytes()
    with pytest.raises(ValueError, match='抓取失败'):
        cache.get('2024-01-01', '2024-06-30', lambda a, b: ([], source()), validate_bars)
    assert cache.path.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))


def test_offline_cache_rejects_corruption(tmp_path):
    cache = MarketCache(tmp_path, {'symbol': 'GOOG'})
    cache.get('2024-01-01', '2024-06-30', lambda a, b: (daily_rows(a, b), source()), validate_bars)
    cache.path.write_bytes(b'invalid parquet')
    cache.offline = True
    with pytest.raises(ValueError, match='离线缓存缺失'):
        cache.get('2024-01-01', '2024-06-30', None, validate_bars)


def test_hash_mismatch_rejected(tmp_path):
    cache = MarketCache(tmp_path, {'symbol': 'GOOG'})
    cache.get('2024-01-01', '2024-06-30', lambda a, b: (daily_rows(a, b), source()), validate_bars)
    rows, metadata, _ = cache.read(validate_bars)
    metadata['data_hash'] = 'incorrect'
    cache.write(rows, metadata)
    with pytest.raises(ValueError, match='校验失败'):
        cache.read(validate_bars)


def test_lock_timeout(tmp_path):
    path = tmp_path / 'symbol.lock'
    with cache_lock(path):
        with pytest.raises(ValueError, match='等待锁超时'):
            with cache_lock(path, timeout=0):
                pass


@pytest.mark.parametrize('config', [
    {'mode': 'live'}, {'adjust': 'invalid'}, {'cache_hours': '-1'},
    {'cache_hours': 'invalid'}, {'offline': 'yes'}, {'directory': '../../outside'},
    {'adjust': 'none', 'price_basis': 'adjusted'},
])
def test_provider_config_validation(config):
    with pytest.raises(ValidationError):
        ProviderInput(name='AKShare', driver='akshare', kind='market', config=config)


def test_pipeline_cache_and_mode_isolation(tmp_path, monkeypatch):
    from app import providers
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(AKShareMarket, 'download', lambda self, symbol, a, b: (daily_rows(a, b), source()))
    provider = {'id': 'test', 'name': 'AKShare', 'driver': 'akshare', 'kind': 'market',
                'config': {'mode': 'historical'}, 'enabled': True, 'priority': 5}
    rows, info = load_bars([provider], 'historical', 'QQQ', '2024-01-01', '2024-06-30')
    assert info['driver'] == 'akshare' and info['cache_status'] == 'miss'
    _, info = load_bars([provider], 'historical', 'QQQ', '2024-01-01', '2024-06-30')
    assert info['cache_status'] == 'hit'
    with pytest.raises(ValueError, match='没有启用且匹配'):
        load_bars([provider], 'live', 'QQQ', '2024-01-01', '2024-06-30')
    def failed(*args):
        raise ValueError('offline')
    monkeypatch.setattr(AKShareMarket, 'download', failed)
    with pytest.raises(ValueError, match='不会降级到演示数据'):
        load_bars([provider, {'id': 'demo', 'name': 'demo', 'driver': 'demo', 'kind': 'market',
                             'config': {}, 'enabled': True, 'priority': 100}],
                  'historical', 'TSLA', '2024-01-01', '2024-06-30')


def test_bootstrap_does_not_duplicate_or_reenable(database):
    from sqlalchemy import select
    from app.bootstrap import initialize
    from app.db import Provider
    with database.begin() as db:
        provider = db.scalar(select(Provider).where(Provider.driver == 'akshare'))
        assert provider.config['cache_hours'] == '24'
        provider.enabled = False
    initialize()
    with database() as db:
        providers = db.scalars(select(Provider).where(Provider.driver == 'akshare')).all()
        assert len(providers) == 1 and not providers[0].enabled


def test_sina_adjustment_uses_verified_factors():
    def daily(**kwargs):
        if kwargs['adjust'] == 'qfq-factor':
            return pd.DataFrame({'date': ['1970-01-01'], 'qfq_factor': [0.5], 'adjust': [-1]})
        return pd.DataFrame([asdict(row) for row in daily_rows()])
    rows, meta = fetch('QQQ', '2024-01-01', '2024-06-30', 'qfq',
                       SimpleNamespace(stock_us_daily=daily), upstream='sina')
    assert rows[0].open == 49 and rows[0].volume == 1000
    assert meta['price_basis'] == 'adjusted' and meta['endpoint'] == 'stock_us_daily'


def test_sina_cannot_silently_fallback_to_raw():
    ak = SimpleNamespace(stock_hk_daily=lambda **kwargs: pd.DataFrame())
    with pytest.raises(ValueError, match='复权因子不可用'):
        fetch('03032', '2024-01-01', '2024-06-30', 'qfq', ak, upstream='sina')


def test_sina_precision_repair_metadata_survives_fallback_and_cache(tmp_path, monkeypatch):
    import json
    import subprocess
    from app import providers
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    calls = []
    frame = pd.DataFrame([asdict(row) for row in daily_rows()]).astype({'close': float})
    frame.loc[3, 'close'] = 94.999999
    ak = SimpleNamespace(stock_hk_daily=lambda **kwargs: frame.copy())
    def run(*args, **kwargs):
        request = json.loads(kwargs['input'])
        calls.append(request['upstream'])
        if request['upstream'] == 'eastmoney':
            return SimpleNamespace(returncode=1, stdout=json.dumps({'error': 'invalid JSON'}))
        rows, metadata = fetch(ak=ak, **request)
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {'rows': [asdict(row) for row in rows], 'metadata': metadata}))
    monkeypatch.setattr(subprocess, 'run', run)
    driver = AKShareMarket({'adjust': 'none'})
    bars = driver.bars('00700', '2024-01-01', '2024-06-30')
    assert len(bars) == len(frame)
    assert bars[3].close == 95
    assert validate_bars(bars) == bars
    assert driver.metadata['price_basis'] == 'raw'
    assert driver.metadata['precision_repairs'] == [
        {'date': bars[3].date, 'field': 'close', 'original': 94.999999, 'normalized': 95}]
    assert '极小边界精度偏差' in driver.metadata['warning']
    assert '不复权原价' in driver.metadata['warning']
    assert driver.metadata['upstream_failures'][0]['upstream'] == 'eastmoney'
    assert driver.bars('00700', '2024-01-01', '2024-06-30') == bars
    assert calls == ['eastmoney', 'sina']
    assert driver.metadata['cache_status'] == 'hit'
    assert driver.metadata['precision_repairs'][0]['original'] == 94.999999


def test_download_falls_back_and_records_upstream(monkeypatch):
    import json
    import subprocess
    calls = []
    def run(*args, **kwargs):
        upstream = json.loads(kwargs['input'])['upstream']
        calls.append(upstream)
        if upstream == 'eastmoney':
            return SimpleNamespace(returncode=1, stdout=json.dumps({'error': 'invalid JSON'}))
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            {'rows': [asdict(row) for row in daily_rows()], 'metadata': source()}))
    monkeypatch.setattr(subprocess, 'run', run)
    rows, info = AKShareMarket({}).download('QQQ', '2024-01-01', '2024-06-30')
    assert rows and calls == ['eastmoney', 'sina']
    assert info['upstream_failures'][0]['upstream'] == 'eastmoney'

