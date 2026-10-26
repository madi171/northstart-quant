from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update
from .db import Asset, Strategy, Provider, Job, Setting, Account, Order, audit, now, serialize
from .research_features import freeze_features


def snapshot(db, spec):
    asset, strategy = db.get(Asset, spec.asset_id), db.get(Strategy, spec.strategy_id)
    if not asset or not asset.enabled:
        raise ValueError('标的不存在或已停用')
    if not strategy:
        raise ValueError('策略不存在')
    if spec.kind == 'signal' and strategy.status != 'approved':
        raise ValueError('只有已批准策略可生成在线交易意图')
    provenance = strategy.provenance or {}
    records = [row.model_dump(mode='json') for row in spec.feature_records]
    inherited = provenance.get('feature_snapshot', {}).get('records', [])
    if spec.kind == 'signal' and (records or inherited or provenance.get('rebalance_every', 1) != 1):
        raise ValueError('多源特征或定期调仓策略目前仅支持离线回测；在线运行需接入新鲜特征与调仓日历')
    records = records or inherited
    if any(row['symbol'] not in (asset.symbol, '*') for row in records):
        raise ValueError('特征标的与实验不一致；全市场宏观特征使用 *')
    providers = [serialize(p) for p in db.scalars(select(Provider)).all()]
    if spec.kind == 'research' and not spec.model_ids:
        raise ValueError('请选择至少一个研究模型')
    model_ids = {p['id'] for p in providers if p['kind'] == 'model' and p['enabled']}
    if set(spec.model_ids) - model_ids:
        raise ValueError('研究模型不存在或已停用')
    return {'spec': spec.model_dump(mode='json'), 'asset': serialize(asset), 'strategy': serialize(strategy),
            'feature_snapshot': freeze_features(records), 'rebalance_every': provenance.get('rebalance_every', 1),
            'providers': providers, 'risk': dict(db.get(Setting, 'risk').value)}


def enqueue(db, spec, dedup=None):
    key = dedup or spec.client_key
    existing = db.scalar(select(Job).where(Job.dedup == key))
    if existing:
        if existing.payload['spec'] != spec.model_dump(mode='json'):
            raise ValueError('幂等键已被不同请求使用')
        return existing
    row = Job(kind=spec.kind, payload=snapshot(db, spec), dedup=key)
    db.add(row)
    db.flush()
    audit(db, 'job.enqueued', job_id=row.id, kind=row.kind)
    return row


def propose_order(db, job, result):
    spec, asset, strategy = [job.payload[k] for k in ('spec', 'asset', 'strategy')]
    if db.get(Strategy, strategy['id']).status != 'approved':
        raise ValueError('策略批准已撤销')
    risk = db.scalar(select(Setting).where(Setting.key == 'risk').with_for_update()).value
    if risk['halted']:
        raise ValueError('紧急停止中')
    if (datetime.now(timezone.utc).date() - datetime.fromisoformat(result['last_date']).date()).days > 4:
        raise ValueError('行情已过期，不能生成在线意图')
    account = db.scalar(select(Account).where(Account.id == f"paper-{spec['mode']}-{asset['currency']}").with_for_update())
    price = result['last_close']
    positions = dict(account.positions)
    held = positions.get(asset['id'], {}).get('quantity', 0)
    # Revalue this position only. Other symbols retain explicit quote timestamps.
    equity = account.cash + sum(p['quantity'] * (price if aid == asset['id'] else p['price']) for aid, p in positions.items())
    if held:
        positions[asset['id']] = {**positions[asset['id']], 'price': price, 'quote_date': result['last_date']}
        if positions != account.positions:
            stamp = now()
            curve = list(account.curve)
            if not any(p['date'][:10] == stamp[:10] for p in curve):
                prior_equity = account.cash + sum(p['quantity'] * p['price'] for p in account.positions.values())
                curve.append({'date': stamp, 'equity': prior_equity})
            updated = db.execute(update(Account).where(Account.id == account.id, Account.revision == account.revision).values(
                positions=positions, revision=account.revision + 1,
                curve=(curve + [{'date': stamp, 'equity': equity}])[-2000:]),
                execution_options={'synchronize_session': False})
            if updated.rowcount != 1:
                raise ValueError('账户在估值期间发生变化，请重新生成信号')
            db.refresh(account)
    weight = min(result['next_target'], risk['max_weight'])
    desired = int(equity * weight / (price * asset['lot'])) * asset['lot']
    delta = desired - held
    if not delta:
        return {'message': '目标仓位与当前持仓一致，无需生成订单'}
    existing = db.scalar(select(Order).where(Order.asset_id == asset['id'], Order.account_id == account.id, Order.status == 'pending'))
    if existing:
        return {'message': '该标的已有待确认订单', 'order_id': existing.id}
    order = Order(intent_key=job.id, account_id=account.id, asset_id=asset['id'], strategy_id=strategy['id'],
                  side='buy' if delta > 0 else 'sell', quantity=abs(delta), price=price, data_mode=spec['mode'],
                  details={'quote_date': result['last_date'], 'fee_bps': spec['fee_bps'], 'slippage_bps': spec['slippage_bps'],
                           'symbol': asset['symbol'], 'source': result['source'], 'account_revision': account.revision,
                           'note': '人工确认按此演示报价成交，不是下一开盘价实盘撮合。'})
    db.add(order)
    db.flush()
    return {'order_id': order.id, 'message': '模拟订单等待人工确认'}


def confirm_order(db, order_id):
    row = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not row:
        raise ValueError('订单不存在')
    if row.status == 'filled':
        return row
    if row.status != 'pending' or row.environment != 'paper':
        raise ValueError('订单不可确认；真实交易尚未启用')
    count = db.execute(update(Order).where(Order.id == order_id, Order.status == 'pending').values(
        status='executing'), execution_options={'synchronize_session': False}).rowcount
    if count != 1:
        db.refresh(row)
        if row.status == 'filled':
            return row
        raise ValueError('订单状态已变化')
    risk = db.scalar(select(Setting).where(Setting.key == 'risk').with_for_update()).value
    if risk['halted']:
        raise ValueError('紧急停止中，禁止新增成交')
    if datetime.now(timezone.utc) - datetime.fromisoformat(row.created_at) > timedelta(minutes=risk['approval_ttl_minutes']):
        raise ValueError('确认已过期，请取消订单后重新生成')
    asset = db.get(Asset, row.asset_id)
    strategy = db.get(Strategy, row.strategy_id)
    if not asset or not asset.enabled or not strategy or strategy.status != 'approved':
        raise ValueError('标的停用或策略不再获批')
    account = db.scalar(select(Account).where(Account.id == row.account_id).with_for_update())
    if account.revision != row.details['account_revision']:
        raise ValueError('账户已变化，请取消并重新生成订单')
    if account.id != f'paper-{row.data_mode}-{asset.currency}':
        raise ValueError('环境或币种不一致')
    positions = {k: dict(v) for k, v in account.positions.items()}
    old = positions.get(asset.id, {'quantity': 0, 'price': row.price})
    today = datetime.now(timezone.utc).date().isoformat()
    if row.side == 'sell' and old.get('bought_date') == today:
        raise ValueError('保守模拟规则禁止同日买入后卖出')
    price = row.price * (1 + row.details['slippage_bps'] / 10000 * (1 if row.side == 'buy' else -1))
    gross = row.quantity * price
    fee = gross * row.details['fee_bps'] / 10000
    if gross > risk['max_order_value']:
        raise ValueError('超过单笔金额上限，请调整策略权重或风险设置后重新生成')
    if row.side == 'buy' and gross + fee > account.cash:
        raise ValueError('资金不足')
    if row.side == 'sell' and row.quantity > old['quantity']:
        raise ValueError('可卖持仓不足')
    quantity = old['quantity'] + row.quantity * (1 if row.side == 'buy' else -1)
    cash = account.cash + (-gross - fee if row.side == 'buy' else gross - fee)
    positions[asset.id] = {**old, 'quantity': quantity, 'price': row.price, 'symbol': asset.symbol,
                           'quote_date': row.details['quote_date'], 'bought_date': today if row.side == 'buy' else old.get('bought_date')}
    equity = cash + sum(p['quantity'] * p['price'] for p in positions.values())
    invested = sum(p['quantity'] * p['price'] for p in positions.values())
    if row.side == 'buy' and (quantity * row.price / equity > risk['max_weight'] + 1e-6 or invested / equity > risk['max_total_weight'] + 1e-6):
        raise ValueError('超过单标的或总仓位上限')
    day_points = [p for p in account.curve if p['date'][:10] == today]
    day_start = day_points[0]['equity'] if day_points else account.cash + sum(p['quantity'] * p['price'] for p in account.positions.values())
    if row.side == 'buy' and any(p['quantity'] > 0 and (datetime.now(timezone.utc).date() - datetime.fromisoformat(p['quote_date']).date()).days > 4 for p in account.positions.values()):
        raise ValueError('已有持仓报价过期，请先运行对应标的信号更新估值')
    if row.side == 'buy' and equity / day_start - 1 < -risk['daily_loss_limit']:
        raise ValueError('触发每日亏损阈值')
    changed = db.execute(update(Account).where(Account.id == account.id, Account.revision == account.revision).values(
        cash=cash, positions=positions, revision=account.revision + 1,
        curve=((account.curve or [{'date': now(), 'equity': day_start}]) + [{'date': now(), 'equity': equity}])[-2000:]), execution_options={'synchronize_session': False})
    if changed.rowcount != 1:
        raise ValueError('账户并发变更，请重新生成订单')
    row.status = 'filled'
    row.details = {**row.details, 'fill_price': price, 'fee': fee, 'filled_at': now()}
    audit(db, 'order.paper_filled', order_id=row.id, gross=gross, fee=fee)
    return row
