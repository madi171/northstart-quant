"""只读本地未复权行情估值，不触发采集或使用模拟账户。"""
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .akshare_fetch import instrument
from .market_cache import MarketCache
from .providers import data_path, validate_bars


def asset_valuations(assets, providers, current=None):
    current = current or datetime.now(timezone.utc)
    result = []
    for asset in assets:
        quotes = []
        zone = {'CN': 'Asia/Shanghai', 'HK': 'Asia/Hong_Kong', 'US': 'America/New_York'}[asset['market']]
        cutoff = current.astimezone(ZoneInfo(zone)).date().isoformat()
        for provider in providers:
            if not provider['enabled'] or provider['kind'] != 'market':
                continue
            config = provider['config']
            try:
                if provider['driver'] == 'akshare' and config.get('adjust', 'none') == 'none' and config.get('price_basis', 'raw') == 'raw':
                    market, code, kind = instrument(asset['symbol'])
                    identity = {'provider': 'akshare', 'market': market, 'code': code, 'kind': kind,
                                'adjust': 'none', 'interval': '1d'}
                    cache = MarketCache(data_path(config.get('directory', 'cache/akshare')), identity)
                    rows, _, _ = cache.read(validate_bars)
                elif provider['driver'] == 'parquet' and config.get('price_basis') == 'raw':
                    import pyarrow.parquet as pq
                    if not re.fullmatch(r'[A-Za-z0-9_.-]+', asset['symbol']):
                        continue
                    path = data_path(config.get('directory', 'imports') + '/' + asset['symbol'] + '.parquet')
                    if path.stat().st_size > 100_000_000:
                        continue
                    from .contracts import Bar
                    rows = validate_bars([Bar(**row) for row in pq.read_table(path).to_pylist()])
                else:
                    continue
                completed = [row for row in rows if row.date < cutoff and math.isfinite(row.close) and row.close > 0]
                if completed:
                    bar = max(completed, key=lambda row: row.date)
                    quotes.append({'price': bar.close, 'quote_date': bar.date, 'source': provider['name']})
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                continue
        quote = max(quotes, key=lambda row: row['quote_date']) if quotes else {}
        quantity = asset.get('holding_quantity', 0)
        result.append({'id': asset['id'], **quote,
                       'holding_value': quantity * quote['price'] if quote else 0 if quantity == 0 else None})
    return result
