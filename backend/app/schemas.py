import re
from datetime import date, datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from .strategy import parse, target
from .research_features import FeatureObservation, freeze_features


class InvestmentSignalInput(BaseModel):
    model_config = {'extra': 'forbid', 'str_strip_whitespace': True}
    kind: Literal['news', 'feature']
    asset_id: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    sentiment: Literal['positive', 'negative', 'neutral'] | None = None
    feature_name: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_.]{0,63}$')
    category: Literal['fundamental', 'macro', 'orderbook', 'announcement', 'other'] | None = None
    value: float | None = Field(default=None, allow_inf_nan=False, ge=-1e12, le=1e12)
    max_age_days: int | None = Field(default=None, ge=1, le=730)
    source: str = Field(min_length=1, max_length=200)
    source_url: str = Field(default='', max_length=1000)
    observed_at: datetime
    available_at: datetime
    note: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def validate_signal(self):
        if self.observed_at.tzinfo is None or self.available_at.tzinfo is None:
            raise ValueError('观测与首次可用时间必须包含时区')
        if self.observed_at > self.available_at or self.available_at > datetime.now(timezone.utc):
            raise ValueError('首次可用时间不得早于观测时间，也不能晚于当前时间')
        if self.source_url and not re.fullmatch(r'https?://[^\s/]+(?:/[^\s]*)?', self.source_url):
            raise ValueError('资讯链接仅支持有效的 HTTP 或 HTTPS 地址')
        if self.kind == 'news':
            if self.sentiment is None or any(value is not None for value in (self.feature_name, self.category, self.value, self.max_age_days)):
                raise ValueError('资讯须选择利好、利空或中性，不可填入数值特征')
        elif self.sentiment is not None or not self.feature_name or self.category is None or self.value is None or self.max_age_days is None:
            raise ValueError('数值特征需填写名称、类别、数值和有效期，不能同时标注资讯情绪')
        self.observed_at = self.observed_at.astimezone(timezone.utc)
        self.available_at = self.available_at.astimezone(timezone.utc)
        return self


class InvestmentSignalBatch(BaseModel):
    model_config = {'extra': 'forbid'}
    records: list[InvestmentSignalInput] = Field(min_length=1, max_length=100)


class AssetInput(BaseModel):
    symbol: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,32}$')
    name: str = Field(min_length=1, max_length=80)
    market: Literal['CN', 'HK', 'US']
    lot: int = Field(default=1, ge=1, le=100000)
    holding_quantity: int = Field(default=0, ge=0, le=2147483647, strict=True)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list, max_length=20)


class AssetBatch(BaseModel):
    assets: list[AssetInput] = Field(min_length=1, max_length=100)


class StrategyInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    code: str = Field(max_length=12000)
    params: dict[str, float | int] = Field(default_factory=lambda: {'fast': 10, 'slow': 30, 'weight': .15})
    parent_id: str | None = None
    notes: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def validate_strategy(self):
        import math
        if len(self.params) > 30 or any(not math.isfinite(v) or abs(v) > 1e6 for v in self.params.values()):
            raise ValueError('策略参数数量或数值越界')
        parse(self.code)
        for size in [0, 20, 100]:
            target(self.code, [100.0 + i for i in range(size)], self.params)
        return self


class ProposalExplanation(BaseModel):
    model_config = {'str_strip_whitespace': True}
    name: str = Field(default='', max_length=80, strict=True)
    strategy_logic: str = Field(default='', max_length=2000, strict=True)
    change_summary: str = Field(default='', max_length=2000, strict=True)
    naming_reason: str = Field(default='', max_length=1000, strict=True)


class StrategyTemplateInput(StrategyInput):
    model_config = {'extra': 'forbid'}
    client_key: str = Field(min_length=8, max_length=100)


class StrategyRevisionInput(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(min_length=1, max_length=80)
    code: str = Field(max_length=12000)
    params: dict[str, float | int]
    notes: str = Field(default='', max_length=2000)
    client_key: str = Field(min_length=8, max_length=100)


class RunInput(BaseModel):
    kind: Literal['backtest', 'research', 'signal'] = 'backtest'
    strategy_id: str
    asset_id: str
    mode: Literal['demo', 'historical', 'live'] = 'demo'
    start: date
    end: date
    initial: float = Field(default=100000, gt=0, le=1e10, allow_inf_nan=False)
    fee_bps: float = Field(default=5, ge=0, le=500, allow_inf_nan=False)
    slippage_bps: float = Field(default=5, ge=0, le=500, allow_inf_nan=False)
    model_ids: list[str] = Field(default_factory=list, max_length=4)
    client_key: str = Field(min_length=8, max_length=100)
    feature_records: list[FeatureObservation] = Field(default_factory=list, max_length=5000)

    @model_validator(mode='after')
    def dates_valid(self):
        if self.start >= self.end or self.start < date(2015, 1, 1) or self.end > datetime.now(timezone.utc).date() or (self.end - self.start).days > 1827:
            raise ValueError('区间需位于2015年至今日内，起点早于终点，最长5年')
        if len(set(self.model_ids)) != len(self.model_ids):
            raise ValueError('模型不可重复选择')
        if self.kind == 'signal' and self.mode == 'historical':
            raise ValueError('历史回放不能更新在线模拟账户')
        if self.kind == 'signal' and self.end != datetime.now(timezone.utc).date():
            raise ValueError('在线信号必须截至今日')
        freeze_features([row.model_dump(mode='json') for row in self.feature_records])
        return self


class OptimizationInput(RunInput):
    model_config = {'extra': 'forbid'}
    kind: Literal['optimization'] = 'optimization'
    mode: Literal['demo', 'historical'] = 'historical'
    model_ids: list[str] = Field(min_length=1, max_length=2)
    target_metric: Literal['total_return', 'annual_return'] = 'total_return'
    target_return: float = Field(default=.05, gt=0, le=5, allow_inf_nan=False)
    max_rounds: int = Field(default=5, ge=1, le=12)
    max_drawdown: float = Field(default=.15, gt=0, le=1, allow_inf_nan=False)
    min_trades: int = Field(default=1, ge=1, le=1000)
    max_output_tokens: int = Field(default=6000, ge=1000, le=16000)
    time_budget_seconds: int = Field(default=900, ge=120, le=1800)
    baseline_job_id: str | None = None
    trading_style: Literal['balanced', 'long_trend', 'swing'] = 'balanced'
    max_turnover: float = Field(default=50, gt=0, le=1000, allow_inf_nan=False)
    max_changed_lines: int = Field(default=12, ge=1, le=80)
    max_param_changes: int = Field(default=3, ge=1, le=10)
    hypothesis: str = Field(default='', max_length=1000)


class ProviderInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal['market', 'model', 'news']
    driver: Literal['demo', 'parquet', 'yahoo', 'akshare', 'demo_model', 'openai_compatible', 'demo_news', 'json_news']
    priority: int = Field(default=10, ge=0, le=1000)
    enabled: bool = True
    config: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def driver_valid(self):
        allowed = {
            'market': {'demo', 'parquet', 'yahoo', 'akshare'},
            'model': {'demo_model', 'openai_compatible'},
            'news': {'demo_news', 'json_news'},
        }
        if self.driver not in allowed[self.kind]:
            raise ValueError('驱动与类型不匹配')
        allowed_keys = {'demo': set(), 'parquet': {'directory', 'mode', 'price_basis'}, 'demo_model': set(),
                        'yahoo': {'mode', 'price_basis'},
                        'akshare': {'directory', 'mode', 'price_basis', 'adjust', 'cache_hours', 'offline'},
                        'openai_compatible': {'base_url', 'model', 'key_env', 'api_format', 'reasoning_effort', 'max_output_tokens', 'timeout_seconds'}, 'demo_news': set(), 'json_news': {'file'}}
        if set(self.config) - allowed_keys[self.driver]:
            raise ValueError('未知配置字段；禁止将明文密钥写入配置')
        if any(not isinstance(v, str) or len(v) > 500 for v in self.config.values()):
            raise ValueError('配置值须为短字符串')
        if self.driver == 'parquet' and self.config.get('mode', 'historical') not in {'historical', 'live'}:
            raise ValueError('Parquet数据模式须为 historical 或 live')
        if self.driver == 'openai_compatible' and (not self.config.get('model') or not self.config.get('base_url', '').startswith('https://') or not re.fullmatch(r'MODEL_KEY_[A-Z0-9_]+', self.config.get('key_env', ''))):
            raise ValueError('请配置HTTPS地址、模型名与 MODEL_KEY_ 开头的密钥环境变量名')
        if self.driver == 'openai_compatible':
            if self.config.get('api_format', 'chat_completions') not in {'chat_completions', 'responses'}:
                raise ValueError('模型协议须为 chat_completions 或 responses')
            if self.config.get('reasoning_effort', '') not in {'', 'low', 'medium', 'high', 'xhigh'}:
                raise ValueError('不支持的模型推理强度')
            for key, default, minimum, maximum in [('max_output_tokens', '2000', 1000, 16000), ('timeout_seconds', '45', 10, 120)]:
                value = self.config.get(key, default)
                if not value.isdigit() or not minimum <= int(value) <= maximum:
                    raise ValueError(f'模型配置 {key} 超过允许范围')
        if self.driver == 'yahoo':
            if self.config.get('mode', 'historical') != 'historical':
                raise ValueError('Yahoo仅支持 historical，请选择历史数据回放')
            if self.config.get('price_basis', 'adjusted') != 'adjusted':
                raise ValueError('Yahoo历史行情须使用 adjusted 复权口径')
        if self.driver == 'akshare':
            from .akshare_market import AKShareMarket
            from .providers import data_path
            AKShareMarket(self.config)
            if not self.config.get('directory', 'cache/akshare').strip():
                raise ValueError('请指定 DATA_DIR 内缓存目录')
            data_path(self.config.get('directory', 'cache/akshare'))
        if self.driver == 'json_news' and not self.config.get('file'):
            raise ValueError('请指定 DATA_DIR 内新闻JSON文件')
        return self


class RiskInput(BaseModel):
    halted: bool = False
    max_weight: float = Field(default=.3, gt=0, le=1, allow_inf_nan=False)
    max_order_value: float = Field(default=20000, gt=0, le=1e9, allow_inf_nan=False)
    max_total_weight: float = Field(default=.8, gt=0, le=1, allow_inf_nan=False)
    daily_loss_limit: float = Field(default=.03, gt=0, le=1, allow_inf_nan=False)
    approval_ttl_minutes: int = Field(default=15, ge=1, le=60)


class ScheduleInput(BaseModel):
    weekly_enabled: bool = False
    daily_enabled: bool = False
    asset_id: str = ''
    strategy_id: str = ''
    mode: Literal['demo', 'live'] = 'demo'
    model_ids: list[str] = Field(default_factory=list, max_length=4)
