# -*- coding: utf-8 -*-
"""标的独立持仓记录及旧数据库升级的回归测试。"""

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import Account
from app.main import create_asset
from app.main import update_asset
from app.migrations import migrate_asset_holdings
from app.schemas import AssetInput


def test_holding_updates_do_not_change_accounts(database) -> None:
    """持仓数量可以修改和清零，不改变模拟账户或交易单位。"""
    with database() as session:
        account = session.get(Account, 'paper-demo-USD')
        before = (account.cash, account.positions, account.revision)
    asset = create_asset(AssetInput(symbol='GOOG', name='谷歌-C', market='US', holding_quantity=25))
    assert asset['holding_quantity'] == 25
    payload = {**asset, 'holding_quantity': 26}
    assert update_asset(asset['id'], AssetInput(**payload))['holding_quantity'] == 26
    del payload['holding_quantity']
    assert update_asset(asset['id'], AssetInput(**payload))['holding_quantity'] == 26
    payload['holding_quantity'] = 0
    assert update_asset(asset['id'], AssetInput(**payload))['holding_quantity'] == 0
    with database() as session:
        account = session.get(Account, 'paper-demo-USD')
        assert (account.cash, account.positions, account.revision) == before
    assert asset['lot'] == 1


@pytest.mark.parametrize('quantity', [-1, 1.5, 2147483648, True])
def test_invalid_holding_quantity(quantity: object) -> None:
    """拒绝负数、非整数和越界数量。"""
    with pytest.raises(ValidationError):
        AssetInput(symbol='GOOG', name='谷歌-C', market='US', holding_quantity=quantity)


def test_migration_preserves_existing_assets() -> None:
    """旧表重复升级不会丢失数据，也不会重置已录入数量。"""
    engine: Engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE assets (id TEXT PRIMARY KEY, symbol TEXT NOT NULL)'))
            connection.execute(text("INSERT INTO assets VALUES ('test', 'GOOG')"))
        migrate_asset_holdings(engine)
        with engine.begin() as connection:
            assert connection.execute(text('SELECT holding_quantity FROM assets')).scalar_one() == 0
            connection.execute(text('UPDATE assets SET holding_quantity = 25'))
        migrate_asset_holdings(engine)
        with engine.connect() as connection:
            assert connection.execute(text('SELECT symbol, holding_quantity FROM assets')).one() == ('GOOG', 25)
    finally:
        engine.dispose()
