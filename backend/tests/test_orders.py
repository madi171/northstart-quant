from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import select
from app.db import Asset, Strategy, Account, Order, Setting
from app.services import confirm_order, enqueue
from app.schemas import RunInput


def seed(Session):
    with Session.begin() as db:
        strategy = db.scalar(select(Strategy))
        strategy.status = 'approved'
        asset = Asset(symbol='US.TEST', name='Test', market='US', currency='USD', lot=1)
        db.add(asset)
        db.flush()
        row = Order(intent_key='once', account_id='paper-demo-USD', asset_id=asset.id,
                    strategy_id=strategy.id, side='buy', quantity=10, price=100, data_mode='demo',
                    details={'quote_date': datetime.now(timezone.utc).date().isoformat(), 'slippage_bps': 5, 'fee_bps': 5, 'account_revision': 0})
        db.add(row)
        db.flush()
        return row.id, asset.id, strategy.id


def test_repeated_confirmation_never_duplicates_fill(database):
    order_id, asset_id, _ = seed(database)
    with database.begin() as db:
        confirm_order(db, order_id)
    with database.begin() as db:
        cash = db.get(Account, 'paper-demo-USD').cash
        confirm_order(db, order_id)
        assert db.get(Account, 'paper-demo-USD').cash == cash
        assert db.get(Account, 'paper-demo-USD').positions[asset_id]['quantity'] == 10


def test_halt_blocks_fill_and_rolls_back_intermediate_state(database):
    order_id, _, _ = seed(database)
    with database.begin() as db:
        db.get(Setting, 'risk').value = {**db.get(Setting, 'risk').value, 'halted': True}
    with pytest.raises(ValueError, match='紧急停止'):
        with database.begin() as db:
            confirm_order(db, order_id)
    with database() as db:
        assert db.get(Order, order_id).status == 'pending'
        assert db.get(Account, 'paper-demo-USD').cash == 100000


def test_revoked_strategy_cannot_trade(database):
    order_id, _, strategy_id = seed(database)
    with database.begin() as db:
        db.get(Strategy, strategy_id).status = 'candidate'
    with pytest.raises(ValueError, match='不再获批'):
        with database.begin() as db:
            confirm_order(db, order_id)


def test_expired_confirmation_rejected(database):
    order_id, _, _ = seed(database)
    with database.begin() as db:
        db.get(Order, order_id).created_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match='过期'):
        with database.begin() as db:
            confirm_order(db, order_id)


def test_account_revision_prevents_stale_confirmation(database):
    order_id, _, _ = seed(database)
    with database.begin() as db:
        db.get(Account, 'paper-demo-USD').revision = 1
    with pytest.raises(ValueError, match='账户已变化'):
        with database.begin() as db:
            confirm_order(db, order_id)


def test_job_creation_is_idempotent_and_rejects_key_reuse(database):
    _, asset_id, strategy_id = seed(database)
    spec = RunInput(strategy_id=strategy_id, asset_id=asset_id, start='2024-01-01', end='2024-12-31', client_key='same-request')
    with database.begin() as db:
        first = enqueue(db, spec)
        second = enqueue(db, spec)
        assert first.id == second.id
    with pytest.raises(ValueError, match='幂等键'):
        with database.begin() as db:
            enqueue(db, spec.model_copy(update={'initial': 200000}))
