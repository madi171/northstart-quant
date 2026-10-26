"""带跨进程锁、完整刷新与原子写入的历史日线 Parquet 缓存。"""
import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .contracts import Bar


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 100_000_000


def bars_hash(rows):
    canonical = [{'date': row.date, 'open': float(row.open), 'high': float(row.high),
                  'low': float(row.low), 'close': float(row.close), 'volume': int(row.volume)} for row in rows]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False).encode()).hexdigest()


@contextmanager
def cache_lock(path, timeout=30):
    import fcntl
    with path.open('a') as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('行情缓存正在刷新，等待锁超时，请稍后重试')
                time.sleep(.1)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class MarketCache:
    def __init__(self, directory, identity, hours=24, offline=False):
        self.directory = Path(directory)
        self.identity = identity
        self.hours = hours
        self.offline = offline
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        self.path = self.directory / (key + '.parquet')

    def read(self, validate):
        import pyarrow.parquet as pq
        if self.path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('行情缓存超过100MB')
        table = pq.read_table(self.path)
        metadata = json.loads(table.schema.metadata[b'northstar_market'])
        if metadata['version'] != SCHEMA_VERSION or metadata['identity'] != self.identity:
            raise ValueError('行情缓存版本或复权口径不匹配')
        rows = validate([Bar(**item) for item in table.to_pylist()])
        if metadata['data_hash'] != bars_hash(rows):
            raise ValueError('行情缓存校验失败')
        if any(not metadata['start'] <= row.date <= metadata['end'] for row in rows):
            raise ValueError('行情缓存日期超出覆盖区间')
        fetched = datetime.fromisoformat(metadata['source']['fetched_at'])
        if fetched.tzinfo is None:
            raise ValueError('行情缓存抓取时间无时区')
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        if age < -60:
            raise ValueError('行情缓存抓取时间在未来')
        return rows, metadata, age

    def write(self, rows, metadata):
        import pyarrow as pa
        import pyarrow.parquet as pq
        schema = pa.schema([('date', pa.string()), ('open', pa.float64()), ('high', pa.float64()),
                            ('low', pa.float64()), ('close', pa.float64()), ('volume', pa.int64())])
        schema = schema.with_metadata({b'northstar_market': json.dumps(metadata, ensure_ascii=False).encode()})
        table = pa.Table.from_pylist([asdict(row) for row in rows], schema=schema)
        temporary = self.path.with_suffix('.' + uuid4().hex + '.tmp')
        try:
            pq.write_table(table, temporary, compression='zstd')
            if temporary.stat().st_size > MAX_FILE_BYTES:
                raise ValueError('行情缓存超过100MB')
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, start, end, loader, validate):
        self.directory.mkdir(parents=True, exist_ok=True)
        with cache_lock(self.path.with_suffix('.lock')):
            cached = None
            cache_error = None
            try:
                cached = self.read(validate)
            except FileNotFoundError:
                pass
            except Exception as exc:
                cache_error = str(exc)
            if cached:
                rows, metadata, age = cached
                covered = metadata['start'] <= start and end <= metadata['end']
                fresh = self.hours > 0 and age <= self.hours * 3600
                if covered and (fresh or self.offline):
                    selected = validate([row for row in rows if start <= row.date <= end])
                    source = {**metadata['source'], 'cache_status': 'offline' if self.offline else 'hit',
                              'cache_age_seconds': round(max(0, age)), 'cache_stale': not fresh,
                              'cache_hash': metadata['data_hash'], 'requested_start': start, 'requested_end': end}
                    if self.offline:
                        source['warning'] += '当前为离线缓存回放，不代表最新行情。'
                    return selected, source
            if self.offline:
                detail = f'：{cache_error}' if cache_error else ''
                raise ValueError('离线缓存缺失或未覆盖请求区间' + detail)
            fetch_start, fetch_end = start, end
            if cached:
                fetch_start, fetch_end = min(start, cached[1]['start']), max(end, cached[1]['end'])
            # 复权基准可能变动，重新抓取整个覆盖区间而不拼接旧价格。
            try:
                rows, source = loader(fetch_start, fetch_end)
                rows = validate(rows)
                if any(not fetch_start <= row.date <= fetch_end for row in rows):
                    raise ValueError('抓取行情日期超出请求区间')
                selected = validate([row for row in rows if start <= row.date <= end])
            except Exception as exc:
                raise ValueError('AKShare 抓取失败；未使用过期缓存或演示行情。' + str(exc)[:300]) from exc
            metadata = {'version': SCHEMA_VERSION, 'identity': self.identity, 'start': fetch_start,
                        'end': fetch_end, 'data_hash': bars_hash(rows), 'source': source}
            self.write(rows, metadata)
            return selected, {**source, 'cache_status': 'refreshed' if cached or cache_error else 'miss',
                              'cache_stale': False, 'cache_age_seconds': 0,
                              'cache_hash': metadata['data_hash'], 'requested_start': start, 'requested_end': end}
