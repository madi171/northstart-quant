import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.auth import login, require_auth, session_ttl_seconds


class LongSessionTest(unittest.TestCase):
    def test_default_session_lasts_thirty_days_and_keeps_signature(self):
        with patch('app.auth.time.time', return_value=1000000), patch.dict('os.environ', {'SESSION_TTL_DAYS': '30'}):
            result = login('test-only-password-123', 'long-session-test')
            self.assertEqual(result['expires_in'], 30 * 86400)
            with patch('app.auth.time.time', return_value=1000000 + 29 * 86400):
                require_auth('Bearer ' + result['token'])
            with patch('app.auth.time.time', return_value=1000000 + 30 * 86400):
                with self.assertRaises(HTTPException):
                    require_auth('Bearer ' + result['token'])

    def test_ttl_is_bounded_and_configurable(self):
        with patch.dict('os.environ', {'SESSION_TTL_DAYS': '90'}):
            self.assertEqual(session_ttl_seconds(), 90 * 86400)
        for invalid in ('0', '91', 'not-a-number'):
            with self.subTest(invalid=invalid), patch.dict('os.environ', {'SESSION_TTL_DAYS': invalid}):
                with self.assertRaises(RuntimeError):
                    session_ttl_seconds()


if __name__ == '__main__':
    unittest.main()
