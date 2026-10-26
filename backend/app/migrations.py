# -*- coding: utf-8 -*-
"""为已有标的表补齐独立持仓数量字段。"""

from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.engine import Engine


def migrate_asset_holdings(engine: Engine) -> None:
    """幂等补齐字段，保留已有标的数据。

    PostgreSQL 使用事务锁，避免 API 与 worker 同时执行迁移。
    """
    with engine.begin() as connection:
        if connection.dialect.name == 'postgresql':
            connection.execute(text('SELECT pg_advisory_xact_lock(741038261)'))
        columns = inspect(connection).get_columns('assets')
        if not any(column['name'] == 'holding_quantity' for column in columns):
            connection.execute(
                text('ALTER TABLE assets ADD COLUMN holding_quantity INTEGER NOT NULL DEFAULT 0'),
            )
    return None
