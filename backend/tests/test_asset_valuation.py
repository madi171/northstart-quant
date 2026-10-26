from datetime import datetime, timedelta, timezone
from dataclasses import asdict

import pyarrow as pa
import pyarrow.parquet as pq

from app import providers
from app.asset_valuation import asset_valuations
from app.contracts import Bar
from app.market_cache import MarketCache, bars_hash


CURRENT = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
ASSET = {'id':'a', 'market':'US', 'symbol':'US.TEST', 'holding_quantity':10}
PROVIDER = {'enabled':True, 'kind':'market', 'driver':'akshare', 'name':'raw fixture', 'config':{}}


def test_raw_cache_valuation_excludes_unfinished_bars_and_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    identity = {'provider':'akshare', 'market':'US', 'code':'TEST', 'kind':'stock', 'adjust':'none', 'interval':'1d'}
    directory = tmp_path / 'cache/akshare'
    directory.mkdir(parents=True)
    rows = [Bar((CURRENT.date()-timedelta(days=50-i)).isoformat(),100,100,100,100,1000) for i in range(51)]
    rows[-1] = Bar('2026-10-22',1000,1000,1000,1000,1000)
    cache = MarketCache(directory, identity)
    cache.write(rows, {'version':1, 'identity':identity, 'start':rows[0].date, 'end':rows[-1].date,
                       'data_hash':bars_hash(rows), 'source':{'fetched_at':CURRENT.isoformat()}})
    result = asset_valuations([ASSET], [PROVIDER], CURRENT)[0]
    assert result['holding_value'] == 1000
    assert result['quote_date'] == '2026-10-21'
    assert result['price'] == 100


def test_missing_corrupt_and_adjusted_sources_do_not_invent_valuations(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    for source in [PROVIDER, {**PROVIDER,'driver':'demo'}, {**PROVIDER,'config':{'adjust':'qfq'}},
                   {**PROVIDER,'enabled':False}, {**PROVIDER,'driver':'yahoo'}]:
        result = asset_valuations([ASSET, {**ASSET,'id':'zero','holding_quantity':0}], [source], CURRENT)
        assert result[0]['holding_value'] is None
        assert 'price' not in result[0]
        assert result[1]['holding_value'] == 0


def test_raw_parquet_is_read_only_and_bad_file_does_not_break_other_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    directory = tmp_path / 'imports'
    directory.mkdir()
    rows = [Bar((CURRENT.date()-timedelta(days=50-i)).isoformat(),5,5,5,5,100) for i in range(50)]
    pq.write_table(pa.Table.from_pylist([asdict(row) for row in rows]), directory / 'US.TEST.parquet')
    source = {**PROVIDER, 'driver':'parquet', 'config':{'price_basis':'raw'}}
    assets = [ASSET, {**ASSET,'id':'missing','symbol':'US.MISSING'}]
    result = asset_valuations(assets,[source],CURRENT)
    assert result[0]['holding_value'] == 50
    assert result[1]['holding_value'] is None
    (directory / 'US.MISSING.parquet').write_bytes(b'invalid parquet')
    assert asset_valuations(assets,[source],CURRENT)[1]['holding_value'] is None
