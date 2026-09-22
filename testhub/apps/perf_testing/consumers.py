"""压测执行实时推送。

沿用 app_automation 的消费者范式：一个执行一个 group，
子进程通过 executor.push_update() 往 group 里发消息。
"""
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class PerfExecutionConsumer(AsyncJsonWebsocketConsumer):
    """订阅单次压测执行的实时指标。

    连接后主动回推一次当前状态，避免前端在"刚好错过上一条推送"时白屏；
    后续增量由 group_send 驱动。channels 不可用时前端自行降级为轮询
    /realtime/ 接口。
    """

    async def connect(self):
        try:
            if not await self._authenticate():
                await self.close()
                return
            self.execution_id = self.scope['url_route']['kwargs']['execution_id']
            self.group_name = f'perf_execution_{self.execution_id}'
            await self.channel_layer.group_add(self.group_name, self.channel_name)
            await self.accept()
            await self.send_json(await self._current_state())
        except Exception as exc:  # noqa: BLE001
            logger.error('压测 WebSocket 连接失败: %s', exc)
            await self.close()

    # ------------------------------------------------------------------ #
    async def _authenticate(self):
        """鉴权：已登录用户，或携带有效且未过期分享令牌的匿名用户，方可订阅。

        防止未授权客户端订阅任意 execution_id 的实时指标（信息泄露）。
        分享令牌校验与 HTTP 侧 ShareTokenAuthentication 语义保持一致。
        """
        user = self.scope.get('user')
        if user is not None and getattr(user, 'is_authenticated', False):
            from channels.db import database_sync_to_async
            from .models import PerfExecution
            from .services.environments import accessible_projects
            @database_sync_to_async
            def allowed():
                return PerfExecution.objects.filter(pk=self.scope['url_route']['kwargs']['execution_id'],
                    project__in=accessible_projects(user)).exists()
            if await allowed():
                return True
        token = self._extract_token()
        if not token:
            return False
        execution = await self._lookup_by_token(token)
        return execution is not None and str(execution.pk) == str(self.scope['url_route']['kwargs']['execution_id'])

    def _extract_token(self):
        qs = self.scope.get('query_string', b'')
        if isinstance(qs, bytes):
            qs = qs.decode('utf-8')
        from urllib.parse import parse_qs
        return parse_qs(qs).get('token', [None])[0]

    async def _lookup_by_token(self, token):
        from channels.db import database_sync_to_async
        from django.utils import timezone

        from .models import PerfExecution

        try:
            execution = await database_sync_to_async(PerfExecution.objects.get)(
                share_token=token)
        except PerfExecution.DoesNotExist:
            return None
        if execution.share_expires_at and execution.share_expires_at < timezone.now():
            return None
        return execution

    async def disconnect(self, close_code):
        try:
            if hasattr(self, 'group_name'):
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception as exc:  # noqa: BLE001
            logger.error('压测 WebSocket 断开处理失败: %s', exc)

    async def receive_json(self, content, **kwargs):
        """前端可主动发 {"action": "ping"} 保活。"""
        if (content or {}).get('action') == 'ping':
            await self.send_json({'type': 'pong'})

    async def execution_update(self, event):
        try:
            await self.send_json(event)
        except Exception as exc:  # noqa: BLE001
            logger.error('压测 WebSocket 推送失败: %s', exc)

    # ------------------------------------------------------------------ #
    async def _current_state(self):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def _load():
            from .models import PerfExecution
            execution = PerfExecution.objects.filter(id=self.execution_id).first()
            if not execution:
                return {'type': 'execution_update', 'status': 'NOT_FOUND'}
            return {
                'type': 'execution_update',
                'status': execution.status,
                'progress': execution.progress,
                'sla_result': execution.sla_result,
                'summary': execution.summary or {},
                'snapshot': True,
            }

        return await _load()
