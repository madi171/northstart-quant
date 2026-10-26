# -*- coding: utf-8 -*-
"""Read-only monitoring coverage for approved strategy versions."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import login
from app.db import Account, Asset, Audit, Base, Job, Order, Setting, Strategy
from app.main import app
from app.strategy_monitor import strategy_monitor


class StrategyMonitorTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(self.engine)()
        self.db.add_all([
            Strategy(id='one', name='已批准一', code='unused', params={}, status='approved'),
            Strategy(id='two', name='已批准二', code='unused', params={}, status='approved'),
            Strategy(id='revoked', name='已撤销', code='unused', params={}, status='candidate'),
            Strategy(id='hidden', name='仅候选', code='unused', params={}, status='candidate'),
            Asset(id='asset', name='测试ETF', symbol='US.TEST', market='US', currency='USD'),
            Account(id='paper-live-USD', currency='USD', initial=100000, cash=90000,
                    positions={'asset': {'quantity': 100, 'price': 100, 'quote_date': '2026-10-23'}}),
            Setting(key='risk', value={'halted': False}),
            Setting(key='schedule', value={'daily_enabled': True, 'strategy_id': 'two',
                                          'asset_id': 'asset', 'mode': 'live'}),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def job(self, id='job', strategy='one', mode='live', **kwargs):
        row = Job(id=id, kind='signal', payload={'spec': {'mode': mode, 'strategy_id': strategy, 'asset_id': 'asset'},
                                               'strategy': {'id': strategy}}, **kwargs)
        self.db.add(row)
        return row

    def order(self, id='order', strategy='one', **kwargs):
        row = Order(id=id, intent_key=id, strategy_id=strategy, asset_id='asset',
                    account_id='paper-live-USD', data_mode='live', side='buy', quantity=10, price=100, **kwargs)
        self.db.add(row)
        return row

    def test_all_approved_and_scheduled_without_fabricated_execution(self):
        result = strategy_monitor(self.db)
        entries = {row['id']: row for row in result['strategies']}
        self.assertEqual(set(entries), {'one', 'two'})
        self.assertFalse(result['real_trading'])
        self.assertEqual(entries['one']['runs'], [])
        self.assertTrue(entries['two']['scheduled'])
        run = entries['two']['runs'][0]
        self.assertIsNone(run['latest_attempt'])
        self.assertIsNone(run['latest_signal'])
        self.assertEqual(run['shared_position']['quantity'], 100)
        self.assertNotIn('metrics', run)

    def test_latest_failure_is_not_hidden_by_older_success_and_zero_target(self):
        today = datetime.now(timezone.utc).date().isoformat()
        self.job('success', status='succeeded', created_at='2026-01-01T00:00:00+00:00',
                 result={'next_target': 0, 'last_date': today, 'last_close': 100,
                         'source': {'name': 'local-parquet'}, 'metrics': {'total_return': 8},
                         'intent': {'order_id': 'other', 'message': '已有订单'}})
        self.job('failure', status='failed', created_at='2026-02-01T00:00:00+00:00', error='行情失败')
        self.order('other', strategy='two', status='pending')
        self.db.commit()
        run = next(row for row in strategy_monitor(self.db)['strategies'] if row['id'] == 'one')['runs'][0]
        self.assertEqual(run['latest_attempt']['id'], 'failure')
        self.assertEqual(run['latest_attempt']['error'], '行情失败')
        self.assertEqual(run['latest_signal']['target_weight'], 0)
        self.assertFalse(run['latest_signal']['quote_stale'])
        self.assertEqual(run['latest_signal']['source_name'], 'local-parquet')
        self.assertEqual(run['shared_position']['quantity'], 100)
        self.assertEqual(run['order_counts'], {})
        self.assertNotIn('metrics', run['latest_signal'])

    def test_full_history_not_limited_by_state_jobs_or_orders(self):
        for index in range(105):
            self.job(f'job{index}', status='succeeded', result={})
            self.order(f'order{index}', status='filled', details={'fill_price': 101, 'fee': 1})
        self.db.commit()
        before = {model.__name__: self.db.scalar(select(func.count()).select_from(model))
                  for model in (Job, Order, Strategy, Account, Audit)}
        result = strategy_monitor(self.db)
        run = next(row for row in result['strategies'] if row['id'] == 'one')['runs'][0]
        self.assertEqual(run['job_counts']['succeeded'], 105)
        self.assertEqual(run['order_counts']['filled'], 105)
        self.assertIsNone(run['latest_signal']['target_weight'])
        self.assertTrue(run['latest_signal']['quote_stale'])
        self.assertEqual(run['latest_fill']['fill_price'], 101)
        after = {model.__name__: self.db.scalar(select(func.count()).select_from(model))
                 for model in (Job, Order, Strategy, Account, Audit)}
        self.assertEqual(before, after)
        self.assertFalse(self.db.new or self.db.dirty or self.db.deleted)

    def test_modes_and_revoked_pending_are_separate(self):
        self.job('demo', mode='demo', status='queued')
        self.job('live', mode='live', status='running')
        self.order('pending-revoked', strategy='revoked', status='pending')
        self.db.commit()
        rows = {row['id']: row for row in strategy_monitor(self.db)['strategies']}
        self.assertIn('revoked', rows)
        self.assertTrue(rows['revoked']['blockers'])
        self.assertEqual({run['mode'] for run in rows['one']['runs']}, {'demo', 'live'})
        self.assertNotIn('hidden', rows)

    def test_ignores_non_paper_orders_and_handles_missing_feature_snapshot(self):
        self.db.get(Strategy, 'one').provenance = {'feature_snapshot': None}
        self.order('real-order', strategy='one', status='pending', environment='broker')
        self.db.commit()
        entries = {row['id']: row for row in strategy_monitor(self.db)['strategies']}
        self.assertEqual(set(entries), {'one', 'two'})
        self.assertEqual(entries['one']['runs'], [])

    def test_filters_unrelated_jobs_without_losing_spec_only_history(self):
        self.job('excluded', strategy='hidden', status='failed')
        self.db.add(Job(id='spec-only', kind='signal', status='queued',
                        payload={'spec': {'strategy_id': 'one', 'asset_id': 'asset', 'mode': 'demo'}}))
        self.db.commit()
        rows = {row['id']: row for row in strategy_monitor(self.db)['strategies']}
        self.assertNotIn('hidden', rows)
        self.assertEqual(rows['one']['runs'][0]['job_counts'], {'queued': 1})
        self.assertEqual(rows['one']['runs'][0]['latest_attempt']['id'], 'spec-only')

    def test_order_quote_date_and_full_strategy_isolation(self):
        self.job('demo', mode='demo', status='succeeded', result={'next_target': 0.25})
        self.order('pending-one', status='pending', details={'quote_date': '2026-10-23'})
        self.order('filled-two', strategy='two', status='filled', details={
            'quote_date': '2026-10-22', 'filled_at': '2026-10-24T00:00:00+00:00',
            'fill_price': 101, 'fee': 2,
        })
        self.db.commit()
        rows = {row['id']: row for row in strategy_monitor(self.db)['strategies']}
        first = rows['one']['runs']
        self.assertEqual({run['mode'] for run in first}, {'demo', 'live'})
        self.assertEqual(next(run for run in first if run['mode'] == 'demo')['order_counts'], {})
        self.assertEqual(next(run for run in first if run['mode'] == 'live')['latest_order']['quote_date'],
                         '2026-10-23')
        second = rows['two']['runs'][0]
        self.assertEqual(second['order_counts'], {'filled': 1})
        self.assertEqual(second['latest_fill']['quote_date'], '2026-10-22')
        self.assertEqual(second['latest_fill']['fee'], 2)

    def test_empty_database_has_no_runs_or_writes(self):
        for model in (Job, Order, Strategy, Asset, Account, Setting):
            self.db.query(model).delete()
        self.db.commit()
        self.assertEqual(strategy_monitor(self.db)['strategies'], [])
        self.assertFalse(self.db.new or self.db.dirty or self.db.deleted)

    def test_schedule_failures_and_offline_only_blockers(self):
        strategy = self.db.get(Strategy, 'two')
        strategy.provenance = {'feature_snapshot': {'records': [{'name': 'test'}]}}
        self.db.get(Setting, 'risk').value = {'halted': True}
        self.db.add(Job(id='schedule-failed', kind='signal', status='failed',
                        payload={'schedule': {'strategy_id': 'two', 'asset_id': 'asset', 'mode': 'live'}},
                        error='在线运行被拒绝'))
        self.db.commit()
        row = next(row for row in strategy_monitor(self.db)['strategies'] if row['id'] == 'two')
        self.assertEqual(len(row['blockers']), 2)
        self.assertEqual(row['runs'][0]['latest_attempt']['id'], 'schedule-failed')
    def test_authenticated_get_returns_every_approved_strategy_without_writes(self):
        self.db.close()
        self.engine.dispose()
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.engine = engine
        self.db = sessionmaker(engine)()
        self.db.add_all([
            Strategy(id='alpha', name='Alpha', code='private code', params={}, status='approved'),
            Strategy(id='beta', name='Beta', code='private code', params={}, status='approved'),
            Strategy(id='candidate', name='Candidate', code='private code', params={}, status='candidate'),
        ])
        self.db.commit()
        with patch('app.main.Session', sessionmaker(engine)):
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/strategy-monitor').status_code, 401)
                token = login('test-only-password-123', 'monitor-test')['token']
                headers = {'Authorization': f'Bearer {token}'}
                result = client.get('/api/strategy-monitor', headers=headers)
                self.assertEqual(result.status_code, 200)
                self.assertEqual({row['id'] for row in result.json()['strategies']}, {'alpha', 'beta'})
                self.assertTrue(all(row['runs'] == [] for row in result.json()['strategies']))
                self.assertNotIn('private code', result.text)
                self.assertEqual(result.headers['Cache-Control'], 'no-store')
                self.assertEqual(client.post('/api/strategy-monitor', headers=headers).status_code, 405)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Audit)), 0)
        self.assertFalse(self.db.new or self.db.dirty or self.db.deleted)


if __name__ == '__main__':
    unittest.main()
