# -*- coding: utf-8 -*-
"""Read-only paper portfolio valuation; no broker calls or account mutations."""
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.auth import login
from app.db import Account, Asset, Audit, Base, Provider
from app.main import app
from app.paper_portfolio import account_snapshot, paper_portfolio


TODAY = date(2026, 10, 26)
STAMP = '2026-10-26T12:00:00+00:00'


def account(positions=None, cash=850, initial=1000):
    return SimpleNamespace(id='paper-live-USD', currency='USD', cash=cash, initial=initial,
                           positions=positions if positions is not None else {
                               'first': {'quantity': 1, 'price': 100, 'quote_date': TODAY.isoformat(), 'symbol': 'US.A'},
                               'second': {'quantity': 1, 'price': 50, 'quote_date': TODAY.isoformat(), 'symbol': 'US.B'},
                           })


class PaperPortfolioTest(unittest.TestCase):
    def test_return_uses_all_positions_and_cash(self):
        assets = {'first': SimpleNamespace(symbol='US.A', currency='USD'),
                  'second': SimpleNamespace(symbol='US.B', currency='USD')}
        snapshot = account_snapshot(account(), assets, {'first': {'price': 110, 'quote_date': TODAY.isoformat(), 'source': 'cache'},
                                                        'second': {'price': 60, 'quote_date': TODAY.isoformat(), 'source': 'cache'}}, TODAY, STAMP)
        self.assertTrue(snapshot['complete'])
        self.assertEqual(snapshot['equity'], 1020)
        self.assertAlmostEqual(snapshot['return_rate'], .02)
        self.assertEqual(len(snapshot['positions']), 2)
        self.assertEqual(snapshot['positions'][0]['source'], 'cache')

    def test_missing_stale_or_future_quote_hides_total_return(self):
        for bad in ({'price': 100, 'quote_date': '2026-10-20', 'source': 'old'},
                    {'price': 100, 'quote_date': '2026-10-27', 'source': 'future'}, None):
            positions = {'first': {'quantity': 1, 'price': 100, 'quote_date': '2026-10-19'}}
            quotes = {'first': bad} if bad is not None else {}
            row = account_snapshot(account(positions), {}, quotes, TODAY, STAMP)
            self.assertIsNone(row['equity'])
            self.assertIsNone(row['return_rate'])
            self.assertFalse(row['positions'][0]['fresh'])

    def test_account_record_is_not_used_to_replace_stale_or_missing_market_quote(self):
        positions = {'first': {'quantity': 2, 'price': 111, 'quote_date': TODAY.isoformat()}}
        row = account_snapshot(account(positions, cash=700), {},
                               {'first': {'price': 101, 'quote_date': '2026-10-20', 'source': 'cache'}}, TODAY, STAMP)
        self.assertIsNone(row['positions'][0]['price'])
        self.assertFalse(row['positions'][0]['fresh'])
        self.assertIsNone(row['equity'])
        empty = account_snapshot(account({'first': {'quantity': 0}}, cash=1000), {}, {}, TODAY, STAMP)
        self.assertEqual(empty['return_rate'], 0)
        self.assertEqual(empty['positions'], [])

    def test_invalid_or_corrupt_position_hides_return(self):
        for quantity in (float('nan'), -1, None):
            row = account_snapshot(account({'first': {'quantity': quantity}}), {}, {}, TODAY, STAMP)
            self.assertIsNone(row['return_rate'])
            self.assertFalse(row['complete'])
        row = account_snapshot(account({'first': None}), {}, {}, TODAY, STAMP)
        self.assertIsNone(row['equity'])

    def test_readonly_live_mode_keeps_environment_separate_without_market_download(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with sessionmaker(engine)() as db:
            db.add_all([
                Asset(id='a', symbol='US.A', name='A', market='US', currency='USD', lot=1),
                Account(id='paper-live-USD', currency='USD', cash=900, initial=1000,
                        positions={'a': {'quantity': 1, 'price': 100, 'quote_date': TODAY.isoformat(), 'symbol': 'US.A'}}),
                Account(id='paper-demo-USD', currency='USD', cash=1000, initial=1000, positions={}),
                Provider(id='market', name='local', kind='market', driver='parquet', config={
                    'mode': 'live', 'price_basis': 'raw', 'directory': 'imports'}),
            ])
            db.commit()
            with patch('app.paper_portfolio.now', return_value=STAMP), patch('app.paper_portfolio.asset_valuations', return_value=[
                {'id': 'a', 'price': 110, 'quote_date': TODAY.isoformat(), 'source': 'local'}]) as valuation:
                result = paper_portfolio(db, 'live', 'USD')
            self.assertEqual(result['equity'], 1010)
            self.assertAlmostEqual(result['return_rate'], .01)
            self.assertFalse(result['real_trading'])
            valuation.assert_called_once()
            self.assertEqual(paper_portfolio(db, 'demo', 'USD')['positions'], [])
            self.assertEqual(db.get(Account, 'paper-live-USD').cash, 900)
            self.assertFalse(db.new or db.dirty or db.deleted)
        engine.dispose()

    def test_demo_estimation_excludes_today_and_does_not_use_live_quotes(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with sessionmaker(engine)() as db:
            db.add_all([
                Asset(id='demo-a', symbol='US.A', name='A', market='US', currency='USD', lot=1),
                Account(id='paper-demo-USD', currency='USD', cash=900, initial=1000,
                        positions={'demo-a': {'quantity': 1, 'price': 200, 'quote_date': '2026-10-26'}}),
                Provider(id='live', name='live-local', kind='market', driver='parquet',
                         config={'mode': 'live', 'price_basis': 'raw'}),
            ])
            db.commit()
            with patch('app.paper_portfolio.now', return_value=STAMP), \
                 patch('app.paper_portfolio.DemoMarket') as demo, \
                 patch('app.paper_portfolio.asset_valuations') as live:
                demo.return_value.bars.return_value = [SimpleNamespace(date='2026-10-23', close=108)]
                result = paper_portfolio(db, 'demo', 'USD')
            self.assertEqual(result['equity'], 1008)
            self.assertEqual(result['positions'][0]['source'], '可复现合成日线')
            self.assertEqual(demo.return_value.bars.call_args.args[-1], '2026-10-25')
            live.assert_not_called()
        engine.dispose()

    def test_authenticated_readonly_route_filters_invalid_inputs(self):
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        with sessionmaker(engine)() as db:
            db.add(Account(id='paper-live-USD', currency='USD', cash=1000, initial=1000,
                           positions={}, curve=[{'date': STAMP, 'equity': 999999}]))
            db.commit()
            with patch('app.main.Session', sessionmaker(engine)):
                with TestClient(app) as client:
                    url = '/api/paper-portfolio?mode=live&currency=USD'
                    self.assertEqual(client.get(url).status_code, 401)
                    token = login('test-only-password-123', 'paper-test')['token']
                    headers = {'Authorization': f'Bearer {token}'}
                    result = client.get(url, headers=headers)
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()['equity'], 1000)
                    self.assertEqual(result.json()['return_rate'], 0)
                    self.assertNotIn('curve', result.text)
                    self.assertEqual(result.headers['cache-control'], 'no-store')
                    self.assertEqual(client.get('/api/paper-portfolio?mode=historical&currency=USD', headers=headers).status_code, 400)
                    self.assertEqual(client.post(url, headers=headers).status_code, 405)
            self.assertEqual(db.scalar(select(func.count()).select_from(Audit)), 0)
            self.assertEqual(db.get(Account, 'paper-live-USD').cash, 1000)
            self.assertFalse(db.new or db.dirty or db.deleted)
        engine.dispose()

    def test_invalid_mode_and_currency_cannot_access_account(self):
        with self.assertRaisesRegex(ValueError, '请选择'):
            paper_portfolio(None, 'historical', 'USD')
        with self.assertRaisesRegex(ValueError, '请选择'):
            paper_portfolio(None, 'live', 'EUR')


if __name__ == '__main__':
    unittest.main()
