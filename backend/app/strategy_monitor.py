# -*- coding: utf-8 -*-
"""Read-only operational snapshots. Shared paper accounts are not strategy ledgers."""

from datetime import datetime
import math

from sqlalchemy import or_, select

from .db import Account, Asset, Job, Order, Setting, Strategy, now


def finite(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def job_summary(job):
    return {key: getattr(job, key) for key in
            ('id', 'status', 'created_at', 'started_at', 'finished_at', 'heartbeat', 'error')}


def order_summary(order):
    details = order.details or {}
    return {'id': order.id, 'status': order.status, 'side': order.side, 'quantity': order.quantity,
            'price': finite(order.price), 'created_at': order.created_at,
            'quote_date': details.get('quote_date'),
            'fill_price': finite(details.get('fill_price')), 'fee': finite(details.get('fee')),
            'filled_at': details.get('filled_at')}


def strategy_monitor(db):
    stamp = now()
    risk_row, schedule_row = db.get(Setting, 'risk'), db.get(Setting, 'schedule')
    risk = (risk_row.value or {}) if risk_row else {}
    schedule = (schedule_row.value or {}) if schedule_row else {}
    assets = {row.id: row for row in db.scalars(select(Asset))}
    accounts = {row.id: row for row in db.execute(select(Account.id, Account.positions))}
    pending_ids = select(Order.strategy_id).where(Order.status == 'pending', Order.environment == 'paper')
    strategies = db.scalars(select(Strategy).where(
        (Strategy.status == 'approved') | Strategy.id.in_(pending_ids)
    ).order_by(Strategy.created_at.desc(), Strategy.id)).all()
    entries = {}
    groups = {}
    for strategy in strategies:
        provenance = strategy.provenance or {}
        blockers = []
        if strategy.status != 'approved':
            blockers.append('批准已撤销：待确认订单不能成交，不会自动平仓')
        if risk.get('halted'):
            blockers.append('紧急停止已启用')
        if (provenance.get('feature_snapshot') or {}).get('records') or provenance.get('rebalance_every', 1) != 1:
            blockers.append('外部特征或非每日调仓：当前仅支持离线回测')
        scheduled = bool(schedule.get('daily_enabled') and schedule.get('strategy_id') == strategy.id)
        entries[strategy.id] = {'id': strategy.id, 'name': strategy.name, 'status': strategy.status,
                                'scheduled': scheduled, 'schedule_mode': schedule.get('mode') if scheduled else None,
                                'schedule_asset_id': schedule.get('asset_id') if scheduled else None,
                                'blockers': blockers, 'runs': []}

    def group(strategy_id, mode, asset_id, account_id=None):
        asset = assets.get(asset_id)
        currency = asset.currency if asset else None
        account_id = account_id or (f'paper-{mode}-{currency}' if currency and mode in ('demo', 'live') else None)
        key = (strategy_id, mode, asset_id, account_id)
        if key not in groups:
            account = accounts.get(account_id)
            position = (account.positions or {}).get(asset_id) if account and asset else None
            groups[key] = {
                'mode': mode, 'environment': 'paper', 'asset_id': asset_id,
                'symbol': asset.symbol if asset else None, 'asset_name': asset.name if asset else None,
                'currency': currency, 'asset_enabled': asset.enabled if asset else None,
                'account_id': account_id,
                'shared_position': {'quantity': position.get('quantity', 0), 'price': finite(position.get('price')),
                                    'quote_date': position.get('quote_date')} if position is not None else None,
                'job_counts': {}, 'latest_attempt': None, 'latest_signal': None,
                'order_counts': {}, 'latest_order': None, 'latest_fill': None,
            }
            entries[strategy_id]['runs'].append(groups[key])
        return groups[key]

    ids = list(entries)
    # Only operational signal jobs belonging to monitored strategies are included.
    jobs = db.execute(select(
        Job.id, Job.status, Job.created_at, Job.started_at, Job.finished_at, Job.heartbeat, Job.error,
        Job.payload['spec'].label('spec'), Job.payload['schedule'].label('schedule'),
        Job.payload['strategy']['id'].as_string().label('strategy_id'),
        Job.payload['asset']['id'].as_string().label('asset_id'),
        Job.result['last_date'].as_string().label('quote_date'),
        Job.result['next_target'].label('target_weight'), Job.result['last_close'].label('price'),
        Job.result['source']['name'].as_string().label('source_name'),
        Job.result['intent']['message'].as_string().label('message'),
    ).where(Job.kind == 'signal', or_(
        Job.payload['strategy']['id'].as_string().in_(ids),
        Job.payload['spec']['strategy_id'].as_string().in_(ids),
        Job.payload['schedule']['strategy_id'].as_string().in_(ids),
    )).order_by(Job.created_at.desc(), Job.id.desc())) if ids else []
    for job in jobs:
        spec = job.spec or job.schedule or {}
        strategy_id = job.strategy_id or spec.get('strategy_id')
        if strategy_id not in entries:
            continue
        asset_id = job.asset_id or spec.get('asset_id')
        row = group(strategy_id, spec.get('mode'), asset_id)
        row['job_counts'][job.status] = row['job_counts'].get(job.status, 0) + 1
        if row['latest_attempt'] is None:
            row['latest_attempt'] = job_summary(job)
        if job.status == 'succeeded' and row['latest_signal'] is None:
            quote_date = job.quote_date
            try:
                age = (datetime.fromisoformat(stamp).date() - datetime.fromisoformat(quote_date).date()).days
                stale = age > 4 or age < 0
            except (TypeError, ValueError):
                stale = True
            row['latest_signal'] = {
                'job_id': job.id, 'finished_at': job.finished_at,
                'target_weight': finite(job.target_weight), 'quote_date': quote_date,
                'price': finite(job.price), 'quote_stale': stale,
                'source_name': job.source_name, 'message': job.message,
            }

    if ids:
        orders = db.scalars(select(Order).where(Order.strategy_id.in_(ids), Order.environment == 'paper')
                            .order_by(Order.created_at.desc(), Order.id.desc()))
        for order in orders.yield_per(100):
            row = group(order.strategy_id, order.data_mode, order.asset_id, order.account_id)
            row['order_counts'][order.status] = row['order_counts'].get(order.status, 0) + 1
            if row['latest_order'] is None:
                row['latest_order'] = order_summary(order)
            if order.status == 'filled':
                summary = order_summary(order)
                if row['latest_fill'] is None or (summary['filled_at'] or summary['created_at']) > (
                        row['latest_fill']['filled_at'] or row['latest_fill']['created_at']):
                    row['latest_fill'] = summary
    for entry in entries.values():
        if entry['scheduled']:
            group(entry['id'], entry['schedule_mode'], entry['schedule_asset_id'])
    return {'server_time': stamp, 'real_trading': False, 'strategies': list(entries.values()),
            'note': '仅虚拟盘运行记录；live是行情环境，不是券商实盘。共享账户持仓不能归属单个策略，暂无策略独立收益账本。'}
