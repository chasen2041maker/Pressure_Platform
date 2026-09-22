"""压测 WebSocket 消费者鉴权测试（DB-free）。

守护 consumers.PerfExecutionConsumer.connect() 的鉴权逻辑：
- 已登录用户 → 放行；
- 匿名用户携带有效且未过期分享令牌 (?token=) → 放行；
- 匿名用户无令牌 / 令牌无效 → 关闭连接（信息泄露防护）。

被测的 _authenticate / _lookup_by_token 在测试中均被 mock 或 DB-free 桩替代，
因此本测试不依赖远程数据库，可直接用 `python -m unittest` 运行。
"""
import os

import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
if not settings.configured:
    django.setup()

# 确保 channel layer 可解析（不连外部 Redis）；测试中每个 consumer 会再替换为 AsyncMock
settings.CHANNEL_LAYERS = {
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
}

from django.contrib.auth.models import AnonymousUser  # noqa: E402
from unittest import IsolatedAsyncioTestCase  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from apps.perf_testing.consumers import PerfExecutionConsumer  # noqa: E402


class _FakeAuthUser:
    is_authenticated = True


def _make_consumer(user=None, query_string=b'', token_lookup=None):
    scope = {
        'type': 'websocket',
        'url_route': {'kwargs': {'execution_id': '1'}},
        'user': user if user is not None else AnonymousUser(),
        'query_string': query_string,
        'channel_layer': 'default',
    }
    consumer = PerfExecutionConsumer(scope)
    consumer.scope = scope
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = 'test-channel'
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    if token_lookup is not None:
        consumer._lookup_by_token = AsyncMock(side_effect=token_lookup)
    return consumer


class TestPerfExecutionConsumerAuth(IsolatedAsyncioTestCase):

    async def test_anonymous_without_token_is_rejected(self):
        c = _make_consumer(user=AnonymousUser(), query_string=b'')
        await c.connect()
        c.close.assert_awaited_once()
        c.accept.assert_not_awaited()

    async def test_authenticated_user_is_accepted(self):
        c = _make_consumer(user=_FakeAuthUser())
        c._current_state = AsyncMock(return_value={'status': 'RUNNING'})
        await c.connect()
        c.accept.assert_awaited_once()
        c.channel_layer.group_add.assert_awaited_once()
        c.close.assert_not_awaited()

    async def test_anonymous_with_valid_share_token_is_accepted(self):
        fake_exec = object()
        c = _make_consumer(user=AnonymousUser(), query_string=b'token=abc123')
        c._lookup_by_token = AsyncMock(return_value=fake_exec)
        c._current_state = AsyncMock(return_value={'status': 'RUNNING'})
        await c.connect()
        c._lookup_by_token.assert_awaited_once_with('abc123')
        c.accept.assert_awaited_once()
        c.close.assert_not_awaited()

    async def test_anonymous_with_invalid_share_token_is_rejected(self):
        c = _make_consumer(user=AnonymousUser(), query_string=b'token=bad')
        c._lookup_by_token = AsyncMock(return_value=None)
        await c.connect()
        c._lookup_by_token.assert_awaited_once_with('bad')
        c.close.assert_awaited_once()
        c.accept.assert_not_awaited()
