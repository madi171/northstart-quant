"""为旧报告补充经原始快照校验的行情，不重算或修改已保存结果。"""
import hashlib
import json
from dataclasses import asdict

from .contracts import Bar
from .providers import data_path, validate_bars


PRICE_FIELDS = ('open', 'high', 'low', 'close', 'volume')


def report_with_prices(report):
    if not report or not report.get('curve'):
        return report
    curve = report['curve']
    if all(all(key in point for key in PRICE_FIELDS) for point in curve):
        return report
    try:
        import pyarrow.parquet as pq
        if not report.get('snapshot') or not report.get('data_hash'):
            raise ValueError('缺少原始行情快照')
        path = data_path(report['snapshot'])
        if path.stat().st_size > 100_000_000:
            raise ValueError('行情快照超过100MB')
        bars = validate_bars([Bar(**item) for item in pq.read_table(path).to_pylist()])
        fingerprint = hashlib.sha256(json.dumps([asdict(bar) for bar in bars], sort_keys=True).encode()).hexdigest()
        if fingerprint != report['data_hash']:
            raise ValueError('行情快照校验失败')
        prices = {bar.date: asdict(bar) for bar in bars}
        if any(point['date'] not in prices for point in curve):
            raise ValueError('行情快照未覆盖报告日期')
        enriched = [{**point, **{key: prices[point['date']][key] for key in PRICE_FIELDS}} for point in curve]
        return {**report, 'curve': enriched}
    except Exception:
        return {**report, 'chart_warning': '原始行情快照缺失或未通过校验，无法展示每日单价；原收益与成交记录保持不变。'}
