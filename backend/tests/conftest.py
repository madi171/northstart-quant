import os
import pytest
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['APP_PASSWORD'] = 'test-only-password-123'
os.environ['SESSION_SECRET'] = 'test-only-signing-secret-123456789012345'


@pytest.fixture
def database():
    from app.db import Base, engine, Session
    from app.bootstrap import initialize
    Base.metadata.drop_all(engine)
    initialize()
    yield Session
    Base.metadata.drop_all(engine)
