# -*- coding: utf-8 -*-
"""数据库连接参数解析的回归测试。"""

import pytest
from sqlalchemy.engine import make_url

from app.db import database_url


@pytest.mark.parametrize('password', ['plain-password', 'demo@0726', 'demo@:/?#%+密码'])
def test_postgres_password_special_characters(password: str) -> None:
    """独立传递的密码不会被解析为主机或其他 URL 字段。"""
    url = database_url({'POSTGRES_HOST': 'db', 'POSTGRES_PASSWORD': password})

    assert url.drivername == 'postgresql+psycopg'
    assert url.username == 'quant'
    assert url.password == password
    assert url.host == 'db'
    assert url.port == 5432
    assert url.database == 'quant'
    assert make_url(url.render_as_string(hide_password=False)) == url


def test_explicit_database_url_takes_priority() -> None:
    """保留测试和现有部署的 DATABASE_URL 优先级。"""
    url = database_url({'DATABASE_URL': 'sqlite://', 'POSTGRES_HOST': 'db'})

    assert url.get_backend_name() == 'sqlite'


def test_default_database_url() -> None:
    """未配置 PostgreSQL 时继续使用本地 SQLite。"""
    url = database_url({})

    assert url.get_backend_name() == 'sqlite'
    assert url.database == 'data/quant.db'


def test_custom_postgres_parameters() -> None:
    """支持独立设置用户、端口和数据库名。"""
    url = database_url(
        {
            'POSTGRES_HOST': 'database',
            'POSTGRES_PORT': '55432',
            'POSTGRES_USER': 'test-user',
            'POSTGRES_PASSWORD': 'test-password',
            'POSTGRES_DB': 'test-db',
        },
    )

    assert url.host == 'database'
    assert url.port == 55432
    assert url.username == 'test-user'
    assert url.database == 'test-db'
