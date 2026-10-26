"""只读模拟账户估值：按行情环境与币种隔离，不执行策略或更新账户。"""
from datetime import date, datetime, timedelta, timezone
import math

from sqlalchemy import select

from .asset_valuation import asset_valuations
from .db import Account, Asset, Provider, serialize, now
from .providers import DemoMarket


def valid_number(value, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (not positive or value > 0)


def completed_quote_date(value, today):
    try:
        day = date.fromisoformat(value)
        return day <= today and (today - day).days <= 4
    except (TypeError, ValueError):
        return False


def demo_quotes(assets, today):
    result = {}
    source = DemoMarket()
    start = (today - timedelta(days=100)).isoformat()
    end = (today - timedelta(days=1)).isoformat()
    for asset in assets:
        try:
            rows = source.bars(asset.symbol, start, end)
            if rows and valid_number(rows[-1].close, positive=True):
                result[asset.id] = {'price': rows[-1].close, 'quote_date': rows[-1].date,
                                    'source': '可复现合成日线'}
        except (TypeError, ValueError):
            continue
    return result


def account_snapshot(account, assets, quotes, today, stamp):
    positions = []
    total = account.cash if valid_number(account.cash) else None
    complete = total is not None
    for asset_id, position in (account.positions or {}).items():
        if not isinstance(position, dict):
            complete = False
            positions.append({'asset_id': asset_id, 'symbol': assets[asset_id].symbol if asset_id in assets else asset_id,
                              'quantity': None, 'price': None, 'quote_date': None, 'source': None,
                              'fresh': False, 'market_value': None})
            continue
        quantity = position.get('quantity')
        if quantity == 0:
            continue
        if not valid_number(quantity) or quantity < 0:
            complete = False
            positions.append({'asset_id': asset_id, 'symbol': assets[asset_id].symbol if asset_id in assets else asset_id,
                              'quantity': quantity if valid_number(quantity) else None, 'price': None,
                              'quote_date': position.get('quote_date'), 'source': None,
                              'fresh': False, 'market_value': None})
            continue
        asset = assets.get(asset_id)
        quote = quotes.get(asset_id) if asset and asset.currency == account.currency else None
        if quote and completed_quote_date(quote.get('quote_date'), today):
            price = quote.get('price')
            source = quote.get('source')
            quote_date = quote.get('quote_date')
        else:
            price, source, quote_date = None, None, quote.get('quote_date') if quote else None
        fresh = valid_number(price, positive=True)
        value = quantity * price if fresh else None
        if value is None or not valid_number(value):
            complete = False
        elif total is not None:
            total += value
        positions.append({'asset_id': asset_id, 'symbol': asset.symbol if asset else position.get('symbol') or asset_id,
                          'quantity': quantity, 'price': price if fresh else None,
                          'quote_date': quote_date, 'source': source,
                          'fresh': bool(fresh), 'market_value': value if value is not None and valid_number(value) else None})
    equity = total if complete and total is not None and valid_number(total) else None
    initial = account.initial if valid_number(account.initial, positive=True) else None
    rate = equity / initial - 1 if equity is not None and initial is not None else None
    if rate is not None and not valid_number(rate):
        equity, rate, complete = None, None, False
    return {'account_id': account.id, 'currency': account.currency, 'cash': account.cash if valid_number(account.cash) else None,
            'initial': initial, 'equity': equity, 'return_rate': rate,
            'complete': complete, 'positions': positions,
            'valuation_at': stamp,
            'note': '收益率=(共享模拟账户现金+全部有效持仓估值)/初始资金-1，包含模拟成交费用；不是单策略收益。'
                    '估值仅使用可复现的合成日线或已缓存的本地未复权完整日线，不使用账户旧成交价；'
                    '报价超过4自然日、缺失或未来日期时不展示总权益与收益率；并非交易所逐笔实时报价。'}


def paper_portfolio(db, mode, currency):
    if mode not in ('demo', 'live') or currency not in ('CNY', 'HKD', 'USD'):
        raise ValueError('请选择演示或真实行情虚拟盘，以及有效币种')
    account = db.execute(select(Account.id, Account.currency, Account.cash, Account.initial, Account.positions)
                         .where(Account.id == f'paper-{mode}-{currency}')).one_or_none()
    if not account:
        raise ValueError('虚拟盘账户不存在')
    stamp = now()
    today = datetime.fromisoformat(stamp).astimezone(timezone.utc).date()
    assets = {asset.id: asset for asset in db.scalars(select(Asset))}
    held_assets = [assets[asset_id] for asset_id, position in (account.positions or {}).items()
                   if asset_id in assets and isinstance(position, dict) and valid_number(position.get('quantity'))
                   and position['quantity'] > 0 and assets[asset_id].currency == currency]
    if mode == 'demo':
        quotes = demo_quotes(held_assets, today)
    else:
        providers = [serialize(row) for row in db.scalars(select(Provider).order_by(Provider.priority))
                     if (row.config or {}).get('mode', 'historical') == 'live']
        quotes = {row['id']: row for row in asset_valuations([serialize(asset) for asset in held_assets], providers,
                                                         datetime.fromisoformat(stamp)) if row.get('price') is not None}
    return account_snapshot(account, assets, quotes, today, stamp) | {'mode': mode, 'real_trading': False}
