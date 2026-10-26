"""AKShare 历史行情驱动；有效 Parquet 缓存优先，过期完整刷新。"""
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .akshare_fetch import instrument
from .contracts import Bar
from .market_cache import MarketCache


class AKShareMarket:
    def __init__(self, config):
        if config.get('mode', 'historical') != 'historical':
            raise ValueError('AKShare 仅支持 historical，请选择历史数据回放')
        self.adjust = config.get('adjust', 'none')
        if self.adjust not in {'qfq', 'hfq', 'none'}:
            raise ValueError('AKShare adjust 须为 qfq、hfq 或 none')
        expected_basis = 'raw' if self.adjust == 'none' else 'adjusted'
        if config.get('price_basis', expected_basis) != expected_basis:
            raise ValueError('AKShare price_basis 与 adjust 不一致')
        self.hours = int(config.get('cache_hours', '24'))
        if not 0 <= self.hours <= 8760:
            raise ValueError('cache_hours 须为 0–8760 小时，0 表示每次刷新')
        if config.get('offline', 'false') not in {'true', 'false'}:
            raise ValueError('offline 须为 true 或 false 字符串')
        self.offline = config.get('offline', 'false') == 'true'
        self.directory = config.get('directory', 'cache/akshare')
        self.metadata = {}

    def download(self, symbol, start, end):
        from .providers import validate_bars
        market, _, kind = instrument(symbol)
        upstreams = ['eastmoney', 'sina'] if market in {'HK', 'US'} and kind != 'index' else ['eastmoney']
        failures = []
        for upstream in upstreams:
            try:
                result = subprocess.run(
                    [sys.executable, '-m', 'app.akshare_fetch'],
                    input=json.dumps({'symbol': symbol, 'start': start, 'end': end,
                                      'adjust': self.adjust, 'upstream': upstream}),
                    capture_output=True, text=True, timeout=40,
                )
                if len(result.stdout) > 16_000_000:
                    raise ValueError('AKShare 响应超过16MB')
                try:
                    response = json.loads(result.stdout)
                except (ValueError, TypeError) as exc:
                    raise ValueError('抓取进程未返回有效数据，请检查依赖或资源限制') from exc
                if result.returncode != 0 or response.get('error'):
                    raise ValueError(response.get('error', '抓取进程失败'))
                rows = validate_bars([Bar(**row) for row in response['rows']])
                return rows, {**response['metadata'], 'upstream_failures': failures}
            except subprocess.TimeoutExpired:
                failures.append({'upstream': upstream, 'error': '上游请求超过40秒，已终止'})
            except Exception as exc:
                failures.append({'upstream': upstream, 'error': str(exc)[:200]})
        raise ValueError('AKShare 上游均不可用：' + json.dumps(failures, ensure_ascii=False))

    def bars(self, symbol, start, end):
        from .providers import data_path, validate_bars
        market, code, kind = instrument(symbol)
        zone = {'CN': 'Asia/Shanghai', 'HK': 'Asia/Hong_Kong', 'US': 'America/New_York'}[market]
        last_complete = datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date() - timedelta(days=1)
        begin = date.fromisoformat(start)
        finish = min(date.fromisoformat(end), last_complete)
        if begin >= finish:
            raise ValueError('AKShare 请求区间需包含足够的已完成历史日线')
        identity = {'provider': 'akshare', 'market': market, 'code': code, 'kind': kind,
                    'adjust': 'none' if kind == 'index' else self.adjust, 'interval': '1d'}
        cache = MarketCache(data_path(self.directory), identity, self.hours, self.offline)
        rows, self.metadata = cache.get(start, finish.isoformat(),
                                        lambda first, last: self.download(symbol, first, last), validate_bars)
        self.metadata['requested_end'] = end
        return rows
