import pytest
from app.schemas import ProviderInput, RunInput
from app.auth import login, require_auth
from fastapi import HTTPException


def test_plaintext_provider_credentials_rejected():
    with pytest.raises(ValueError, match='明文密钥'):
        ProviderInput(name='test', kind='model', driver='openai_compatible', config={'api_key':'secret'})


def test_live_model_requires_environment_reference():
    with pytest.raises(ValueError):
        ProviderInput(name='test', kind='model', driver='openai_compatible', config={'model':'x','base_url':'http://localhost','key_env':'PATH'})


def test_historical_data_cannot_generate_online_orders():
    with pytest.raises(ValueError, match='历史回放'):
        RunInput(kind='signal', mode='historical', strategy_id='s', asset_id='a', start='2024-01-01', end='2024-12-31', client_key='test-history')


def test_auth_signed_token_cannot_be_modified():
    result = login('test-only-password-123', 'test-client')
    require_auth('Bearer ' + result['token'])
    with pytest.raises(HTTPException):
        require_auth('Bearer ' + result['token'][:-1] + ('0' if result['token'][-1] != '0' else '1'))
