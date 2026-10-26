"""Explicit operator-driven CSV import. Does not fetch external data."""
import argparse
import csv
import json
import re
from datetime import datetime, timezone
from .contracts import Bar
from .providers import data_path, validate_bars


def main():
    parser = argparse.ArgumentParser(description='导入已授权获取的原始 OHLCV CSV 到 Parquet')
    parser.add_argument('--csv', required=True)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--source', required=True, help='实际供应商名称或原始出处')
    parser.add_argument('--directory', default='imports')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}', args.symbol):
        parser.error('symbol只允许字母、数字、点、下划线和短横线')
    from pathlib import Path
    with Path(args.csv).open(newline='', encoding='utf-8-sig') as f:
        raw = list(csv.DictReader(f))
    rows = validate_bars([Bar(r['date'], float(r['open']), float(r['high']), float(r['low']), float(r['close']), int(r['volume'])) for r in raw])
    import pyarrow as pa
    import pyarrow.parquet as pq
    from dataclasses import asdict
    from uuid import uuid4
    destination = data_path(args.directory + '/' + args.symbol + '.parquet')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.' + uuid4().hex + '.tmp')
    pq.write_table(pa.Table.from_pylist([asdict(b) for b in rows]), temporary)
    temporary.replace(destination)
    destination.with_suffix('.meta.json').write_text(json.dumps({'source':args.source, 'imported_at':datetime.now(timezone.utc).isoformat(),
        'symbol':args.symbol,'rows':len(rows), 'price_basis':'raw', 'calendar':'provided_rows'}, ensure_ascii=False, indent=2))
    print(f'已导入 {len(rows)} 行：{destination}')


if __name__ == '__main__':
    main()
