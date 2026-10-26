from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app import providers


@pytest.mark.parametrize('symbol,expected', [('HK.00700', ['2024-06-30']),
                                             ('CN.600519', ['2024-06-30']),
                                             ('US.SPY', [])])
def test_parquet_uses_exchange_local_date_and_excludes_today(monkeypatch, tmp_path, symbol, expected):
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    folder = tmp_path / 'imports'
    folder.mkdir()
    rows = [{'date': day, 'open': 100, 'high': 110, 'low': 90, 'close': 105, 'volume': 100}
            for day in ['2024-06-30', '2024-07-01', '2024-07-02']]
    pq.write_table(pa.Table.from_pylist(rows), folder / (symbol + '.parquet'))
    market = providers.ParquetMarket({})
    result = market.bars(symbol, '2024-06-01', '2024-07-02', current=datetime(2024, 7, 1, 1, tzinfo=timezone.utc))
    assert [row.date for row in result] == expected
    assert market.metadata['completed_before'] == ('2024-06-30' if symbol.startswith('US.') else '2024-07-01')
    assert '无完成标记' in market.metadata['warning']


def test_parquet_unknown_market_uses_conservative_date_and_requested_range(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, 'DATA_ROOT', tmp_path)
    folder = tmp_path / 'imports'
    folder.mkdir()
    rows = [{'date': day, 'open': 100, 'high': 110, 'low': 90, 'close': 105, 'volume': 100}
            for day in ['2024-06-28', '2024-06-29', '2024-06-30', '2024-07-01']]
    pq.write_table(pa.Table.from_pylist(rows), folder / 'UNKNOWN_1.parquet')
    market = providers.ParquetMarket({})
    result = market.bars('UNKNOWN_1', '2024-06-29', '2024-07-01', current=datetime(2024, 7, 1, 1, tzinfo=timezone.utc))
    assert [row.date for row in result] == ['2024-06-29']
    assert market.metadata['completed_before'] == '2024-06-30'
