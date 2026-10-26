from sqlalchemy import select
from .db import Base, engine, Session, Strategy, Provider, Setting, Account
from .migrations import migrate_asset_holdings
from .schemas import RiskInput, ScheduleInput
from .strategy import DEFAULT_CODE
from .model_presets import install_model_presets


def initialize():
    Base.metadata.create_all(engine)
    migrate_asset_holdings(engine)
    with Session.begin() as db:
        if not db.scalar(select(Strategy).limit(1)):
            db.add(Strategy(name='双均线趋势 · 初始版本', code=DEFAULT_CODE,
                            params={'fast': 10, 'slow': 30, 'weight': .15}, status='candidate', notes='示例策略，需完成回测后人工批准。'))
        if not db.scalar(select(Provider).limit(1)):
            db.add_all([Provider(name='可复现演示行情', kind='market', driver='demo', priority=100),
                        Provider(name='演示研究器（非大模型）', kind='model', driver='demo_model', priority=100),
                        Provider(name='演示政策消息', kind='news', driver='demo_news', priority=100)])
        if not db.scalar(select(Provider).where(Provider.driver == 'akshare').limit(1)):
            db.add(Provider(name='AKShare · 本地 Parquet 缓存', kind='market', driver='akshare', priority=5,
                            config={'directory': 'cache/akshare', 'mode': 'historical',
                                    'price_basis': 'raw', 'adjust': 'none',
                                    'cache_hours': '24', 'offline': 'false'}))
        install_model_presets(db)
        if not db.get(Setting, 'risk'):
            db.add(Setting(key='risk', value=RiskInput().model_dump()))
        if not db.get(Setting, 'schedule'):
            db.add(Setting(key='schedule', value=ScheduleInput().model_dump()))
        for mode in ['demo', 'live']:
            for currency in ['CNY', 'HKD', 'USD']:
                key = f'paper-{mode}-{currency}'
                if not db.get(Account, key):
                    db.add(Account(id=key, currency=currency, cash=100000, initial=100000, positions={}, curve=[]))
