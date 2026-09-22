"""MCP Server 装配：FastMCP 实例 + ASGI 桥接。

挂载策略（不影响现有流量的最小侵入方案）：
- FastMCP streamable_http 子应用路由在根路径 ``/``
- ``McpAsgiBridge`` 仅拦截精确路径 ``/api/mcp``（含尾斜杠），改写
  scope path 为 ``/`` 后转发给子应用；其余请求一律不触碰
- 会话管理器懒启动：Daphne 触发 lifespan 时走正规流程；未触发时
  首个 MCP 请求兜底启动（两种部署形态都可用）
- 鉴权中间件在协议端点前置拦截（未认证直接 401）
"""
import asyncio
import contextvars
import functools
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

#: 协议端点精确路径（asgi.py 分流与鉴权中间件共用）
MCP_PATHS = ('/api/mcp', '/api/mcp/')

_mcp_instance = None
_starlette_app = None

#: 同步工具的执行线程池：mcp SDK 会把同步工具直接在事件循环线程里调用，
#: 而工具内含 Django ORM 调用（@async_unsafe），会抛 SynchronousOnlyOperation
#: 导致工具执行失败（表现为「未认证」错误且无调用日志），必须投递到线程执行
_TOOL_THREADS = ThreadPoolExecutor(max_workers=8, thread_name_prefix='mcp-tool')


def _async_safe_tool(fn):
    """把同步工具包装为事件循环安全的协程函数。

    - 有运行中事件循环（Daphne 生产路径）：投递到线程池执行，
      复制 contextvars 保证 contextvar 可见；阻塞抛 RuntimeError 快速失败
    - 无事件循环（单元测试等同步直调）：直接执行，行为不变
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        ctx = contextvars.copy_context()
        future = _TOOL_THREADS.submit(ctx.run, functools.partial(fn, *args, **kwargs))
        try:
            return await asyncio.wrap_future(future)
        except RuntimeError:
            # 事件循环已关闭等异常场景：同步阻塞等待结果，避免丢失异常
            return future.result()

    return wrapper


def _transport_security():
    """构造 DNS Rebinding 防护的 Host/Origin 白名单。

    SDK 默认防护开启且白名单为空 → 非 localhost 的 Host 一律 421，
    局域网/域名部署必挂。策略：
    - ALLOWED_HOSTS 含 '*'：Django 层已接受任意 Host，SDK 层再拦无意义，
      关闭防护（端点本身仍有 API-Key/JWT 前置鉴权）
    - 具体主机：派生白名单（含通配端口），支持 MCP_ALLOWED_HOSTS 追加
    """
    from django.conf import settings
    from mcp.server.transport_security import TransportSecuritySettings

    allowed_hosts = getattr(settings, 'ALLOWED_HOSTS', [])
    if '*' in allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    hosts = {'localhost', '127.0.0.1', '[::1]'}
    for h in allowed_hosts:
        if h:
            hosts.add(h.rstrip('/').split('//')[-1])
    extra = getattr(settings, 'MCP_ALLOWED_HOSTS', '') or ''
    hosts.update(h.strip() for h in extra.split(',') if h.strip())
    allowed = sorted(hosts) + [f'{h}:*' for h in sorted(hosts)]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed,
        # 非浏览器客户端不带 Origin；带了则需与 Host 同源，沿用同一白名单
        allowed_origins=allowed,
    )


def get_mcp():
    """构建（单例）FastMCP 实例并注册全部工具。"""
    global _mcp_instance
    if _mcp_instance is None:
        from mcp.server.fastmcp import FastMCP
        from .registry import get_tool_meta
        from .tools import ALL_TOOLS

        # stateless_http：每请求独立 transport，无需跨进程会话粘滞，
        # 对 Daphne 多 worker 与反向代理部署最稳；JSON-RPC 语义不变。
        # transport_security：Host 白名单，不配会被 SDK 默认防护拒为 421
        mcp = FastMCP(
            'TestHub',
            instructions=(
                'TestHub AI 测试平台 MCP Server。读工具（list_*/get_*/search_*）即时返回；'
                '危险工具需先调用 preview_* 获取 confirm_token，再调用对应 confirm_* 确认执行。'
                '若平台开启了人工审批模式，confirm_* 会返回 awaiting_approval，'
                '此时请用同一 confirm_token 轮询 get_approval_status 直至 approved/rejected/expired。'
            ),
            streamable_http_path='/',
            stateless_http=True,
            transport_security=_transport_security(),
        )
        for tool in ALL_TOOLS:
            # 从注册表读取 MCP 规范 annotations（title/readOnlyHint 等），
            # 使客户端能感知工具行为；未登记的工具降级为无注解注册。
            # 工具为同步函数（内含 ORM），经 _async_safe_tool 包装为协程，
            # 避免 SDK 在事件循环线程直调导致 SynchronousOnlyOperation
            meta = get_tool_meta(tool.__name__)
            mcp.tool(annotations=meta.to_mcp_annotations() if meta else None)(
                _async_safe_tool(tool))
        _mcp_instance = mcp
    return _mcp_instance


def get_starlette_app():
    """获取（单例）streamable-http Starlette 子应用。"""
    global _starlette_app
    if _starlette_app is None:
        _starlette_app = get_mcp().streamable_http_app()
    return _starlette_app


class McpAsgiBridge:
    """将 MCP 子应用挂到精确路径 /api/mcp 的 ASGI 桥。

    - 会话管理器懒启动（进程内仅一次）
    - 转发前改写 scope path 为子应用根路径
    - 启动失败不影响 Django 主流量（抛错仅发生在 MCP 请求上）
    """

    def __init__(self):
        self._started = False
        self._lock = asyncio.Lock()

    async def _ensure_started(self):
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            mcp = get_mcp()
            get_starlette_app()  # 首次调用会创建 session_manager
            session_manager = getattr(mcp, 'session_manager', None)
            if session_manager is not None and hasattr(session_manager, '__aenter__'):
                # 旧版 SDK：session_manager 本身是异步上下文管理器
                await session_manager.__aenter__()
            elif session_manager is not None and hasattr(session_manager, 'run'):
                # 新版 SDK（mcp>=1.25）：生命周期由 run() 管理。正常部署下
                # 由 Starlette lifespan 触发，但本桥直接转发 scope 绕过了
                # lifespan，故需自行在后台启动并等待 _task_group 就绪，
                # 否则 handle_request 会抛 "Task group is not initialized"
                await self._start_run_lifecycle(session_manager)
            self._started = True
            logger.info('MCP 会话管理器已就绪')

    @staticmethod
    async def _start_run_lifecycle(session_manager):
        """后台进入 session_manager.run() 上下文（常驻至进程退出）。

        run() 内部先设置 _task_group 再 yield，轮询该属性即可确认就绪。
        """

        async def _hold_run():
            try:
                async with session_manager.run():
                    while True:
                        await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception('MCP session_manager.run() 异常退出')

        asyncio.create_task(_hold_run())
        for _ in range(100):  # 最多等 5s
            if session_manager._task_group is not None:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError('MCP session_manager 启动超时（_task_group 未就绪）')

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            return

        from asgiref.sync import sync_to_async
        from .auth import McpAuthMiddleware, headers_from_scope, authenticate_headers

        # 前置鉴权：子应用路径即将被改写为 '/'，McpAuthMiddleware 按原始
        # 路径匹配会失效，故在此直接按请求头校验（未认证 401，与中间件语义一致）；
        # 未认证请求不启动会话管理器，避免白耗资源。
        # 注意：鉴权内含 ORM 查询，协程内直接同步调用会抛 SynchronousOnlyOperation，
        # 必须经 sync_to_async 放到线程里执行
        headers = headers_from_scope(scope)
        user = await sync_to_async(authenticate_headers)(headers)
        if user is None:
            await McpAuthMiddleware._send_401(send)
            return

        await self._ensure_started()

        app = get_starlette_app()

        # 精确路径转发：子应用路由在 '/'
        sub_scope = dict(scope)
        sub_scope['path'] = '/'
        sub_scope['raw_path'] = b'/'
        sub_scope['root_path'] = scope.get('root_path', '') + '/api/mcp'
        await app(sub_scope, receive, send)


#: asgi.py 引用的全局桥实例（import 时不初始化 MCP，避免拖慢启动）
mcp_bridge = McpAsgiBridge()
