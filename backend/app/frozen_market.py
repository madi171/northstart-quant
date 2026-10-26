import hashlib
import json
from dataclasses import asdict

from .contracts import Bar
from .providers import data_path, validate_bars


def read_frozen_bars(frozen):
    import pyarrow.parquet as pq
    if not isinstance(frozen, dict) or not all(frozen.get(key) for key in ('path', 'hash', 'source')):
        raise ValueError('缺少完整冻结行情，拒绝重新抓取或使用演示数据')
    try:
        path = data_path(frozen['path'])
        if path.stat().st_size > 100_000_000:
            raise ValueError('冻结行情文件超过100MB')
        bars = validate_bars([Bar(**row) for row in pq.read_table(path).to_pylist()])
    except Exception as exc:
        raise ValueError('冻结行情快照不可用，无法复测；请检查原快照文件') from exc
    fingerprint = hashlib.sha256(json.dumps([asdict(bar) for bar in bars], sort_keys=True).encode()).hexdigest()
    if fingerprint != frozen['hash']:
        raise ValueError('冻结行情快照校验失败，拒绝回放')
    return bars


def freeze_bars(bars, source):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from uuid import uuid4
    rows = [asdict(bar) for bar in validate_bars(bars)]
    fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    path = data_path(f'snapshots/{fingerprint}.parquet')
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
        try:
            pq.write_table(pa.Table.from_pylist(rows), temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    frozen = {'path': str(path.relative_to(data_path(''))), 'hash': fingerprint, 'source': source}
    read_frozen_bars(frozen)
    return frozen


def read_warmup_bars(payload, bars):
    frozen = payload.get('warmup_data')
    archive = payload.get('study_archive')
    if archive and (not frozen or frozen.get('hash') != archive.get('warmup_hash')):
        raise ValueError('研究归档缺少一致的预热快照，拒绝无预热复测')
    if not frozen:
        return []
    warmup = read_frozen_bars(frozen)
    if frozen['source'] != payload.get('frozen_data', {}).get('source') or warmup[-1].date >= bars[0].date:
        raise ValueError('预热行情来源或日期与评估行情不一致')
    return warmup
