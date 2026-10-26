import base64
import hashlib
import hmac
import json
import os
import time
from collections import defaultdict, deque
from fastapi import Header, HTTPException

attempts = defaultdict(deque)


def config():
    password = os.getenv('APP_PASSWORD', '')
    secret = os.getenv('SESSION_SECRET', '')
    if len(password) < 12 or len(secret) < 32 or password.startswith('CHANGE_') or secret.startswith('CHANGE_'):
        raise RuntimeError('必须设置 APP_PASSWORD（至少12字符）与 SESSION_SECRET（至少32字符），禁止使用模板占位值')
    return password, secret


def session_ttl_seconds():
    value = os.getenv('SESSION_TTL_DAYS', '30')
    try:
        days = int(value)
    except ValueError:
        raise RuntimeError('SESSION_TTL_DAYS 须为1至90之间的整数') from None
    if not 1 <= days <= 90:
        raise RuntimeError('SESSION_TTL_DAYS 须为1至90之间的整数')
    return days * 86400


def login(password, client):
    expected, secret = config()
    current = time.time()
    queue = attempts[client]
    while queue and queue[0] < current - 60:
        queue.popleft()
    if len(queue) >= 5:
        raise HTTPException(429, '尝试过于频繁，请一分钟后重试')
    queue.append(current)
    if not hmac.compare_digest(password.encode(), expected.encode()):
        raise HTTPException(401, '密码不正确')
    ttl = session_ttl_seconds()
    body = base64.urlsafe_b64encode(json.dumps({'exp': int(current) + ttl}).encode()).decode()
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {'token': body + '.' + signature, 'expires_in': ttl}


def require_auth(authorization: str = Header(default='')):
    _, secret = config()
    try:
        scheme, token = authorization.split(' ', 1)
        body, signature = token.split('.', 1)
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if scheme != 'Bearer' or not hmac.compare_digest(expected, signature):
            raise ValueError()
        if json.loads(base64.urlsafe_b64decode(body))['exp'] <= time.time():
            raise ValueError()
    except Exception:
        raise HTTPException(401, '登录已过期，请重新登录')
