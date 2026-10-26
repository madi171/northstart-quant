# -*- coding: utf-8 -*-
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, create_engine, Float, Integer, JSON, String, UniqueConstraint
from sqlalchemy.engine import make_url, URL
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid4().hex


class Base(DeclarativeBase):
    pass


def database_url(environ: Mapping[str, str] | None = None) -> URL:
    """构造数据库连接地址，保留密码中的特殊字符。"""
    config = os.environ if environ is None else environ
    if config.get('DATABASE_URL'):
        return make_url(config['DATABASE_URL'])
    if config.get('POSTGRES_HOST'):
        return URL.create(
            drivername='postgresql+psycopg',
            username=config.get('POSTGRES_USER', 'quant'),
            password=config['POSTGRES_PASSWORD'],
            host=config['POSTGRES_HOST'],
            port=int(config.get('POSTGRES_PORT', '5432')),
            database=config.get('POSTGRES_DB', 'quant'),
        )
    return make_url('sqlite:///data/quant.db')


url = database_url()
engine = create_engine(
    url,
    **(
        {'connect_args': {'check_same_thread': False, 'timeout': 30}}
        if url.get_backend_name() == 'sqlite'
        else {'pool_pre_ping': True}
    ),
)
Session = sessionmaker(engine, expire_on_commit=False)


class Asset(Base):
    __tablename__ = 'assets'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    symbol: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    market: Mapped[str] = mapped_column(String)
    currency: Mapped[str] = mapped_column(String)
    lot: Mapped[int] = mapped_column(Integer, default=1)
    holding_quantity: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)


class Strategy(Base):
    __tablename__ = 'strategies'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    code: Mapped[str] = mapped_column(String)
    params: Mapped[dict] = mapped_column(JSON)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default='candidate')
    notes: Mapped[str] = mapped_column(String, default='')
    created_at: Mapped[str] = mapped_column(String, default=now)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class InvestmentSignal(Base):
    __tablename__ = 'investment_signals'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String)
    asset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String)
    sentiment: Mapped[str | None] = mapped_column(String, nullable=True)
    feature_name: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(String, default='')
    observed_at: Mapped[str] = mapped_column(String)
    available_at: Mapped[str] = mapped_column(String)
    note: Mapped[str] = mapped_column(String, default='')
    created_at: Mapped[str] = mapped_column(String, default=now)


class Provider(Base):
    __tablename__ = 'providers'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    driver: Mapped[str] = mapped_column(String)
    priority: Mapped[int] = mapped_column(Integer, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    dedup: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='queued')
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String, default='')
    created_at: Mapped[str] = mapped_column(String, default=now)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    heartbeat: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Account(Base):
    __tablename__ = 'accounts'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    currency: Mapped[str] = mapped_column(String)
    cash: Mapped[float] = mapped_column(Float)
    initial: Mapped[float] = mapped_column(Float)
    positions: Mapped[dict] = mapped_column(JSON, default=dict)
    curve: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = 'orders'
    __table_args__ = (UniqueConstraint('intent_key'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    intent_key: Mapped[str] = mapped_column(String)
    account_id: Mapped[str] = mapped_column(String)
    asset_id: Mapped[str] = mapped_column(String)
    strategy_id: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String, default='pending')
    environment: Mapped[str] = mapped_column(String, default='paper')
    data_mode: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=now)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    at: Mapped[str] = mapped_column(String, default=now)
    action: Mapped[str] = mapped_column(String)
    details: Mapped[dict] = mapped_column(JSON)


def serialize(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def audit(db, action, **details):
    db.add(Audit(action=action, details=details))
