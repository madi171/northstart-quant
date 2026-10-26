import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import login
from app.db import Asset, Audit, Base, InvestmentSignal, Job, Order
from app.main import app


class InvestmentSignalsTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(self.engine)
        with self.session.begin() as db:
            db.add(Asset(id='asset', symbol='US.TEST', name='测试标的', market='US', currency='USD'))
        self.patcher = patch('app.main.Session', self.session)
        self.patcher.start()
        self.client = TestClient(app)
        self.client.__enter__()
        token = login('test-only-password-123', 'signal-test')['token']
        self.headers = {'Authorization': f'Bearer {token}'}
        self.news = {'kind': 'news', 'title': '经营数据发布', 'asset_id': 'asset', 'sentiment': 'neutral',
                     'source': '企业公告', 'source_url': 'https://example.com/report',
                     'observed_at': '2025-01-02T12:00:00+08:00',
                     'available_at': '2025-01-02T12:30:00+08:00', 'note': '仅标注，未验证方向'}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.patcher.stop()
        self.engine.dispose()

    def test_auth_crud_and_no_orders_or_jobs(self):
        self.assertEqual(self.client.get('/api/investment-signals').status_code, 401)
        result = self.client.post('/api/investment-signals', json=self.news, headers=self.headers)
        self.assertEqual(result.status_code, 201, result.text)
        row = result.json()
        self.assertEqual(row['sentiment'], 'neutral')
        self.assertEqual(self.client.get('/api/investment-signals', headers=self.headers).json()[0]['id'], row['id'])
        self.assertEqual(self.client.get('/api/investment-signals/features', headers=self.headers).json()['records'], [])
        self.assertEqual(self.client.delete('/api/investment-signals/' + row['id'], headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get('/api/investment-signals', headers=self.headers).json(), [])
        with self.session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Audit)), 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(InvestmentSignal)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Job)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Order)), 0)

    def test_feature_export_with_source_and_point_in_time(self):
        feature = {**self.news, 'kind': 'feature', 'title': '营收增速', 'sentiment': None,
                   'feature_name': 'fundamental.revenue_growth', 'category': 'fundamental',
                   'value': 0.17, 'max_age_days': 180}
        result = self.client.post('/api/investment-signals', json=feature, headers=self.headers)
        self.assertEqual(result.status_code, 201, result.text)
        record = self.client.get('/api/investment-signals/features', headers=self.headers).json()['records'][0]
        self.assertEqual(record['name'], 'fundamental.revenue_growth')
        self.assertEqual(record['symbol'], 'US.TEST')
        self.assertEqual(record['value'], 0.17)
        self.assertIn('04:00', result.json()['observed_at'])

    def test_invalid_sentiment_feature_future_and_missing_asset_rejected(self):
        cases = [
            {**self.news, 'sentiment': 'bullish'},
            {**self.news, 'asset_id': 'missing'},
            {**self.news, 'available_at': '2024-01-01T00:00:00+08:00'},
            {**self.news, 'available_at': '2099-01-01T00:00:00+08:00'},
            {**self.news, 'source_url': 'javascript:alert(1)'},
            {**self.news, 'value': 1},
            {**self.news, 'kind': 'feature', 'sentiment': None, 'feature_name': 'price.close',
             'category': 'other', 'value': 1, 'max_age_days': 30},
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertGreaterEqual(self.client.post('/api/investment-signals', json=body, headers=self.headers).status_code, 400)
        with self.session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(InvestmentSignal)), 0)

    def test_import_rejects_duplicate_feature_version_atomically(self):
        feature = {**self.news, 'kind': 'feature', 'sentiment': None, 'feature_name': 'fundamental.sales_growth',
                   'category': 'fundamental', 'value': 0.2, 'max_age_days': 120}
        response = self.client.post('/api/investment-signals/import', json={'records': [feature, feature]}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        with self.session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(InvestmentSignal)), 0)

    def test_atomic_import(self):
        bad = {**self.news, 'asset_id': 'missing', 'title': '无效标的'}
        response = self.client.post('/api/investment-signals/import', json={'records': [self.news, bad]}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/investment-signals', headers=self.headers).json(), [])
        response = self.client.post('/api/investment-signals/import', json={'records': [self.news]}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['count'], 1)


if __name__ == '__main__':
    unittest.main()
