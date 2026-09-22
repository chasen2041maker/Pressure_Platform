"""压测引擎工厂。

三个引擎的可用性由「环境」而非「代码」决定，engine_status() 是唯一真相源：
前端据此置灰下拉项，executor.preflight 据此拦截执行。三处结论必然一致。

| 引擎    | 运行期依赖                        | 依赖是否已声明                    |
|---------|-----------------------------------|-----------------------------------|
| BUILTIN | 无（asyncio + httpx，随项目安装）  | 是，恒可用                        |
| LOCUST  | pip 包 locust                     | 否——未写入 req_light.txt，需显式安装 |
| JMETER  | java + JMeter 发行包              | java 已在基础镜像；JMeter 发行包需自行安装 |

自检：python manage.py check_perf_engines
部署：docs/perf-testing-engine-deploy.md
"""
from .base import BaseEngine, EngineError, build_load_profile
from .builtin import BuiltinEngine, debug_run
from .locust_engine import LocustEngine, get_version as locust_version, is_available as locust_available
from .jmeter_engine import (
    JmeterEngine,
    debug_run as jmeter_debug_run,
    get_version as jmeter_version,
    is_available as jmeter_available,
)
from .k6_engine import (
    K6Engine, get_version as k6_version, is_available as k6_available,
    validate_snapshot as validate_k6_snapshot,
)
import threading
import time

ENGINES = {
    'BUILTIN': BuiltinEngine,
    'LOCUST': LocustEngine,
    'JMETER': JmeterEngine,
    'K6': K6Engine,
}


def get_engine_class(name):
    engine_class = ENGINES.get((name or 'BUILTIN').upper())
    if not engine_class:
        raise EngineError(f'未知的压测引擎：{name}')
    return engine_class


#: engine_status 结果缓存（TTL）。前端打开压测页面/轮询都会打 /engines/status/，
#: 而 jmeter --version 要拉起 JVM 子进程，单次耗时数秒，不缓存会把整个
#: 页面交互拖卡。短 TTL 既保证性能，又能让运维临时安装引擎后很快生效。
_STATUS_TTL_SECONDS = 15
_status_cache = {'ts': 0.0, 'data': None}
_status_lock = threading.Lock()


def engine_status(force=False):
    """引擎健康检查，供 /engines/status/ 与 check_perf_engines 命令使用。

    结果带 {_STATUS_TTL_SECONDS} 秒 TTL 缓存：jmeter --version 要启动 JVM，
    每次现场探测会让接口耗时数秒；force=True 绕过缓存（自检命令用）。
    """
    now = time.monotonic()
    with _status_lock:
        if not force and _status_cache['data'] is not None \
                and now - _status_cache['ts'] < _STATUS_TTL_SECONDS:
            return _status_cache['data']

    from ..services.k6_capabilities import build_capabilities

    try:
        k6_ready, k6_identity = k6_available(), k6_version() or 'unknown'
    except (EngineError, OSError, ValueError):
        k6_ready, k6_identity = False, 'unknown'
    capabilities = build_capabilities(available=k6_ready, version=k6_identity)
    data = [
        {
            'name': 'K6',
            'label': 'k6',
            'available': k6_ready and capabilities['available'],
            'version': k6_identity,
            'capabilities': capabilities,
            'description': '本地 k6：固定并发、每用户独立账号；一次运行一个任务',
        },
        {
            'name': 'BUILTIN',
            'label': '内置引擎 (asyncio + httpx)',
            'available': True,
            'version': 'built-in',
            'description': '零依赖，适合中小并发（建议 ≤ 1000 并发 / 3000 RPS）',
        },
        {
            'name': 'LOCUST',
            'label': 'Locust',
            'available': locust_available(),
            'version': locust_version(),
            'description': '需 pip install locust（未包含在 req_light.txt 中，'
                           '镜像默认不带），适合更大并发与后续分布式扩展',
        },
        {
            'name': 'JMETER',
            'label': 'JMeter',
            'available': jmeter_available(),
            'version': jmeter_version() or 'unknown',
            'description': '需 java + jmeter（环境变量 JMETER_BIN 可指定），协议覆盖广、可复用既有 .jmx 资产',
        },
    ]

    with _status_lock:
        _status_cache['ts'] = time.monotonic()
        _status_cache['data'] = data
    return data


__all__ = [
    'BaseEngine', 'EngineError', 'BuiltinEngine', 'LocustEngine', 'JmeterEngine',
    'build_load_profile', 'debug_run', 'jmeter_debug_run', 'get_engine_class', 'engine_status',
    'locust_available', 'locust_version', 'jmeter_available', 'jmeter_version',
    'K6Engine', 'k6_available', 'k6_version', 'validate_k6_snapshot',
]
