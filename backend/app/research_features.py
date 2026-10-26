"""Immutable, point-in-time numeric inputs for daily research (no network access)."""
from datetime import datetime, timezone, timedelta
import hashlib
import json

from pydantic import BaseModel, Field, model_validator
from typing import Literal


STYLES = {
    'balanced': {'label': '均衡日线', 'rebalance_every': 1, 'guidance': 'Daily decisions; balance trend, risk and turnover.'},
    'long_trend': {'label': '长线趋势', 'rebalance_every': 20, 'guidance': 'Prefer 60–250 bar trends and slow fundamental/macro filters; rebalance every 20 bars.'},
    'swing': {'label': '短线波段', 'rebalance_every': 5, 'guidance': 'Prefer 5–30 bar momentum/reversal and volume filters; rebalance every 5 bars. Daily bars only, no intraday fills.'},
}
PRICE_FEATURES = ('price.open', 'price.high', 'price.low', 'price.close', 'price.volume')


class FeatureObservation(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(pattern=r'^[a-z][a-z0-9_.]{0,63}$')
    category: Literal['price', 'fundamental', 'macro', 'orderbook', 'announcement', 'other']
    symbol: str = Field(min_length=1, max_length=32)
    observed_at: datetime
    available_at: datetime
    value: float = Field(allow_inf_nan=False, ge=-1e12, le=1e12)
    source: str = Field(min_length=1, max_length=200)
    max_age_days: int = Field(default=120, ge=1, le=730)

    @model_validator(mode='after')
    def valid_times(self):
        if self.observed_at.tzinfo is None or self.available_at.tzinfo is None:
            raise ValueError('特征时间必须包含时区')
        if self.observed_at > self.available_at:
            raise ValueError('特征实际可用时间不得早于观测时间')
        if self.name in PRICE_FEATURES:
            raise ValueError('特征名称与内置价格字段冲突')
        self.observed_at = self.observed_at.astimezone(timezone.utc)
        self.available_at = self.available_at.astimezone(timezone.utc)
        return self


def freeze_features(records):
    if len(records) > 5000:
        raise ValueError('单次实验最多5000条外部特征记录')
    observations = [FeatureObservation(**row) for row in records]
    normalized = [{**row.model_dump(mode='json'),
                   'observed_at': row.observed_at.isoformat(timespec='microseconds'),
                   'available_at': row.available_at.isoformat(timespec='microseconds')} for row in observations]
    normalized.sort(key=lambda row: (row['name'], row['observed_at'], row['available_at']))
    keys, metadata = set(), {}
    for row in normalized:
        key = (row['name'], row['observed_at'], row['available_at'])
        if key in keys:
            raise ValueError('特征存在重复版本；同一观测和可用时间只能有一个值')
        keys.add(key)
        identity = (row['category'], row['symbol'], row['source'], row['max_age_days'])
        if row['name'] in metadata and metadata[row['name']] != identity:
            raise ValueError('同名特征必须使用一致的类别、标的、来源和有效期')
        metadata[row['name']] = identity
    if len(metadata) > 64:
        raise ValueError('单次实验最多64个外部特征')
    return {'records': normalized, 'hash': hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest(),
            'policy': 'available_at < trading-date 00:00 UTC; latest observed version; expire by available_at; missing requires explicit default'}


class FeatureTimeline:
    def __init__(self, records):
        self.snapshot = freeze_features(records)
        self.records = sorted(self.snapshot['records'], key=lambda row: row['available_at'])
        self.position, self.latest = 0, {}
        self.coverage = {row['name']: 0 for row in self.records}

    def at(self, bar, count=False):
        # Conservatively exclude same-day releases even when a market closes later.
        cutoff = datetime.fromisoformat(bar.date).replace(tzinfo=timezone.utc)
        while self.position < len(self.records):
            row = self.records[self.position]
            if datetime.fromisoformat(row['available_at']) >= cutoff:
                break
            previous = self.latest.get(row['name'])
            if previous is None or (row['observed_at'], row['available_at']) > (previous['observed_at'], previous['available_at']):
                self.latest[row['name']] = row
            self.position += 1
        values = {f'price.{name}': getattr(bar, name) for name in ('open', 'high', 'low', 'close', 'volume')}
        for name, row in self.latest.items():
            if cutoff - datetime.fromisoformat(row['available_at']) <= timedelta(days=row['max_age_days']):
                values[name] = row['value']
                if count:
                    self.coverage[name] += 1
        return values
