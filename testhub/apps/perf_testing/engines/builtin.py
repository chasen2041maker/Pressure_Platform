"""内置压测引擎：asyncio + httpx。

选型理由：httpx 已是平台既有依赖（api_testing 在用），零新增依赖即可获得
连接池复用与 HTTP/2 能力；asyncio 的协程模型让「一个虚拟用户 = 一个协程」
的心智映射非常直接，且单进程可承载数千并发。

已知边界（见方案 §8.1）：CPython GIL 下单进程约 2000-3000 RPS 封顶，
因此引擎同时采集压力机自身 CPU/内存，超阈值时在报告中标注数据可信度存疑。
"""
import asyncio
import csv
import gzip
import json
import logging
import os
import random
import re
import time

import httpx

from ..services.metrics import MetricsCollector
from ..services.variables import VariableContext
from .base import BaseEngine, EngineError, build_load_profile, build_proxy_kwargs

# httpx / httpcore 默认对每个请求打一条 INFO 日志，压测下每秒上千条会把
# 磁盘和 GIL 都吃满，直接反噬压测结果，这里统一压到 WARNING。
for _noisy in ('httpx', 'httpcore', 'hpack'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def _parse_form_body(raw_body):
    """把 FORM 步骤的 body 文本解成 dict（兼容 JSON 对象与 k=v&k=v 两种历史格式）。"""
    raw_body = raw_body or ''
    if not raw_body.strip():
        return {}
    try:
        parsed = json.loads(raw_body)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass
    return dict(pair.split('=', 1) for pair in raw_body.split('&') if '=' in pair)


def _build_multipart_files(step_files, cache):
    """把快照里的文件字段转成 httpx files 参数（带字节缓存）。

    压测下每次迭代都重读磁盘会被 IO 拖慢且结果失真，文件内容在首次
    读入后缓存于 cache（path → bytes）；文件丢失时记录错误并跳过该字段。
    返回 (files_list, error_message)。
    """
    files = []
    error = ''
    for item in step_files or []:
        path = item.get('path') or ''
        if not path:
            continue
        content = cache.get(path)
        if content is None:
            try:
                with open(path, 'rb') as fh:
                    content = fh.read()
                cache[path] = content
            except OSError as exc:
                error = f'读取上传文件失败：{path}（{exc}）'
                continue
        files.append((
            item.get('field') or 'file',
            (item.get('filename') or os.path.basename(path),
             content,
             item.get('content_type') or 'application/octet-stream'),
        ))
    return files, error


class BuiltinEngine(BaseEngine):
    """基于 asyncio + httpx 的压测引擎。"""

    name = 'BUILTIN'

    #: 并发调整周期（秒）——比采样周期更密，保证阶梯曲线平滑
    ADJUST_INTERVAL = 0.5
    #: 优雅停止时等待在途请求的最长时间（秒）
    GRACEFUL_WAIT = 10

    def __init__(self, snapshot, on_sample=None, on_log=None, raw_csv_path=None):
        super().__init__(snapshot, on_sample, on_log)
        self.raw_csv_path = raw_csv_path

        self.load_config = snapshot.get('load_config') or {}
        self.runtime = snapshot.get('runtime_config') or {}
        self.env = snapshot.get('env_config') or {}
        self.steps = [s for s in (snapshot.get('steps') or []) if s.get('enabled', True)]
        self.setup_steps = [s for s in self.steps if s.get('is_setup')]
        self.main_steps = [s for s in self.steps if not s.get('is_setup')]
        self.variables = snapshot.get('variables') or []
        self.csv_data = snapshot.get('csv_data') or {}

        self.collector = MetricsCollector()
        self.target_at, self.planned_duration = build_load_profile(self.load_config)

        self._loop = None
        self._users = []
        self._user_seq = 0
        self._active_users = 0
        self._start_ts = None
        self._request_count = 0
        self._max_requests = int(self.load_config.get('max_requests') or 0)
        self._raw_writer = None
        self._raw_fh = None
        self._raw_rows = 0
        self._raw_sample_rate = 1.0
        self._client = None
        self._stop_reason = ''
        self._proc = None
        # multipart 文件字节缓存（path → bytes），避免每次迭代重复读盘
        self._file_bytes_cache = {}

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def prepare(self):
        if not self.main_steps:
            raise EngineError('场景没有可执行的业务步骤（前置步骤不计入压测）')

        model = self.load_config.get('model', 'CONCURRENCY')
        if model == 'RPS':
            if int(self.load_config.get('target_rps') or 0) <= 0:
                raise EngineError('RPS 模式下 target_rps 必须大于 0')
        elif model == 'RAMPING':
            if not (self.load_config.get('stages') or []):
                raise EngineError('阶梯加压模式下必须配置至少一个阶段')
        if self.planned_duration <= 0:
            raise EngineError('压测时长必须大于 0')

        base_url = (self.env.get('base_url') or '').strip()
        for step in self.main_steps + self.setup_steps:
            url = step.get('url') or ''
            if not url.startswith(('http://', 'https://')) and not base_url:
                raise EngineError(f'步骤「{step.get("name")}」使用相对路径，但未配置环境基址 base_url')
            # multipart 文件提前验可读：在 prepare 阶段失败比压测中逐迭代报错更早暴露
            for item in (step.get('files') or []):
                path = item.get('path') or ''
                if path and not os.path.exists(path):
                    raise EngineError(f'步骤「{step.get("name")}」的上传文件不存在：{path}')

        try:
            import psutil
            self._proc = psutil.Process(os.getpid())
            self._proc.cpu_percent(interval=None)  # 首次调用用于建立基线
        except Exception:  # noqa: BLE001 - 自监控不可用不影响压测
            self._proc = None

        self._open_raw_writer()
        self.log(f'引擎准备完成：{len(self.main_steps)} 个业务步骤，'
                 f'{len(self.setup_steps)} 个前置步骤，计划时长 {int(self.planned_duration)}s')

    def run(self):
        try:
            asyncio.run(self._run_async())
        except KeyboardInterrupt:
            self._stopping = True
        finally:
            self._close_raw_writer()

    def stop(self, graceful=True):
        self._stopping = True
        if not self._stop_reason:
            self._stop_reason = '收到停止指令'

    def collect(self):
        duration = (time.time() - self._start_ts) if self._start_ts else 0
        self.collector.max_concurrency = max(self.collector.max_concurrency, self._active_users)
        return {
            'summary': self.collector.build_summary(duration),
            'request_stats': self.collector.build_request_stats(duration),
            'duration': round(duration, 2),
            'stop_reason': self._stop_reason,
            'raw_rows': self._raw_rows,
        }

    # ------------------------------------------------------------------ #
    # 原始明细：只落 gz CSV，绝不入库（方案 §8.2）
    # ------------------------------------------------------------------ #
    def _open_raw_writer(self):
        if not self.raw_csv_path:
            return
        try:
            os.makedirs(os.path.dirname(self.raw_csv_path), exist_ok=True)
            self._raw_fh = gzip.open(self.raw_csv_path, 'wt', encoding='utf-8', newline='')
            self._raw_writer = csv.writer(self._raw_fh)
            self._raw_writer.writerow(
                ['timestamp_ms', 'elapsed_ms', 'step', 'method', 'url',
                 'status_code', 'success', 'sent_bytes', 'recv_bytes', 'error'])
        except Exception as exc:  # noqa: BLE001
            self.log(f'原始明细文件创建失败，将跳过明细记录：{exc}', 'WARNING')
            self._raw_writer = None

    def _write_raw(self, row):
        if not self._raw_writer:
            return
        # 自适应采样：超过 20 万行后按比例抽样，避免磁盘与 IO 失控
        if self._raw_rows >= 200000:
            self._raw_sample_rate = 0.1
            if random.random() > self._raw_sample_rate:
                return
        try:
            self._raw_writer.writerow(row)
            self._raw_rows += 1
        except Exception:  # noqa: BLE001
            pass

    def _close_raw_writer(self):
        if self._raw_fh:
            try:
                self._raw_fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._raw_fh = None
            self._raw_writer = None

    # ------------------------------------------------------------------ #
    # 异步主流程
    # ------------------------------------------------------------------ #
    async def _run_async(self):
        model = self.load_config.get('model', 'CONCURRENCY')
        timeout = float(self.runtime.get('timeout') or 30)
        keep_alive = bool(self.runtime.get('keep_alive', True))
        follow_redirects = bool(self.runtime.get('follow_redirects', False))
        verify_ssl = bool(self.env.get('verify_ssl', False))

        peak = self._estimate_peak_concurrency()
        limits = httpx.Limits(
            max_connections=max(peak * 2, 20),
            max_keepalive_connections=max(peak, 10) if keep_alive else 0,
        )

        self._start_ts = time.time()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=limits,
            verify=verify_ssl,
            follow_redirects=follow_redirects,
            headers=self._global_headers(),
            **build_proxy_kwargs(self.runtime),
        ) as client:
            self._client = client
            sampler = asyncio.create_task(self._sample_loop())
            try:
                if model == 'RPS':
                    await self._drive_rps()
                else:
                    await self._drive_concurrency()
            finally:
                self._stopping = True
                sampler.cancel()
                try:
                    await sampler
                except asyncio.CancelledError:
                    pass
                await self._drain_users()

    def _estimate_peak_concurrency(self):
        model = self.load_config.get('model', 'CONCURRENCY')
        if model == 'CONCURRENCY':
            return max(int(self.load_config.get('concurrency') or 1), 1)
        if model == 'RAMPING':
            return max([int(s.get('target') or 0) for s in (self.load_config.get('stages') or [])] or [1])
        if model == 'SPIKE':
            return max(int(self.load_config.get('spike_concurrency') or 1), 1)
        if model == 'RPS':
            cap = int(self.load_config.get('max_concurrency') or 0)
            return cap if cap > 0 else max(int(self.load_config.get('target_rps') or 10), 1)
        return 10

    def _global_headers(self):
        headers = {'User-Agent': 'TestHub-PerfEngine/1.0'}
        for k, v in (self.env.get('headers') or {}).items():
            if k:
                headers[str(k)] = str(v)
        return headers

    async def _drive_concurrency(self):
        """并发曲线驱动：按 target_at(t) 动态增减虚拟用户协程。"""
        while not self._stopping:
            elapsed = time.time() - self._start_ts
            if elapsed >= self.planned_duration:
                self._stop_reason = self._stop_reason or '达到计划时长'
                break
            if self._max_requests and self._request_count >= self._max_requests:
                self._stop_reason = '达到最大请求数'
                break

            target = max(int(self.target_at(elapsed)), 0)
            self._adjust_users(target)
            await asyncio.sleep(self.ADJUST_INTERVAL)

        self._stopping = True

    async def _drive_rps(self):
        """固定吞吐驱动：按目标速率投放一次性请求任务，并发上限保护防雪崩。"""
        target_rps = max(int(self.load_config.get('target_rps') or 1), 1)
        max_conc = int(self.load_config.get('max_concurrency') or 0) or target_rps * 5
        interval = 1.0 / target_rps
        inflight = set()
        next_fire = time.time()

        while not self._stopping:
            elapsed = time.time() - self._start_ts
            if elapsed >= self.planned_duration:
                self._stop_reason = self._stop_reason or '达到计划时长'
                break
            if self._max_requests and self._request_count >= self._max_requests:
                self._stop_reason = '达到最大请求数'
                break

            inflight = {t for t in inflight if not t.done()}
            self._active_users = len(inflight)
            self.collector.max_concurrency = max(self.collector.max_concurrency, self._active_users)

            if len(inflight) < max_conc:
                self._user_seq += 1
                task = asyncio.create_task(self._one_shot_iteration(self._user_seq))
                inflight.add(task)

            next_fire += interval
            sleep_for = next_fire - time.time()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                # 已落后于目标速率，立即继续并重置基准，避免无限追赶堆积
                next_fire = time.time()
                await asyncio.sleep(0)

        self._stopping = True
        if inflight:
            await asyncio.wait(inflight, timeout=self.GRACEFUL_WAIT)

    def _adjust_users(self, target):
        """增减虚拟用户协程使其数量逼近 target。"""
        self._users = [t for t in self._users if not t.done()]
        current = len(self._users)

        if target > current:
            for _ in range(target - current):
                self._user_seq += 1
                self._users.append(asyncio.create_task(self._virtual_user(self._user_seq)))
        elif target < current:
            for task in self._users[target:]:
                task.cancel()
            self._users = self._users[:target]

        self._active_users = len(self._users)
        self.collector.max_concurrency = max(self.collector.max_concurrency, self._active_users)

    async def _drain_users(self):
        """停止阶段：等待在途请求收尾，超时则强制取消。"""
        pending = [t for t in self._users if not t.done()]
        if not pending:
            return
        done, still = await asyncio.wait(pending, timeout=self.GRACEFUL_WAIT)
        for task in still:
            task.cancel()
        if still:
            await asyncio.gather(*still, return_exceptions=True)

    # ------------------------------------------------------------------ #
    # 虚拟用户
    # ------------------------------------------------------------------ #
    async def _virtual_user(self, user_index):
        """一个虚拟用户：前置步骤跑一次，之后循环执行业务步骤。"""
        ctx = VariableContext(
            self.variables, user_index=user_index, csv_data=self.csv_data,
            base_url=self.env.get('base_url'))

        try:
            for step in self.setup_steps:
                if self._stopping:
                    return
                await self._execute_step(step, ctx, counted=False)

            while not self._stopping:
                if self._max_requests and self._request_count >= self._max_requests:
                    return
                ctx.refresh()
                if ctx.csv_exhausted:
                    self.log(f'虚拟用户 {user_index} 的 CSV 数据已耗尽，停止该用户', 'WARNING')
                    return
                for step in self.main_steps:
                    if self._stopping:
                        return
                    await self._execute_step(step, ctx, counted=True)
                    await self._think(step)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单用户异常不应中断整体压测
            self.log(f'虚拟用户 {user_index} 异常退出：{exc}', 'ERROR')

    async def _one_shot_iteration(self, user_index):
        """RPS 模式下的一次性迭代（跑完一轮业务步骤即结束）。"""
        ctx = VariableContext(
            self.variables, user_index=user_index, csv_data=self.csv_data,
            base_url=self.env.get('base_url'))
        try:
            for step in self.setup_steps:
                if self._stopping:
                    return
                await self._execute_step(step, ctx, counted=False)
            for step in self.main_steps:
                if self._stopping:
                    return
                await self._execute_step(step, ctx, counted=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    async def _think(self, step):
        think = step.get('think_time') or {}
        ttype = (think.get('type') or '').upper()
        if ttype == 'FIXED':
            delay = float(think.get('min') or 0)
        elif ttype == 'RANDOM':
            lo = float(think.get('min') or 0)
            hi = float(think.get('max') or 0)
            delay = random.uniform(min(lo, hi), max(lo, hi))
        else:
            delay = 0
        if delay > 0:
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------ #
    # 单步执行
    # ------------------------------------------------------------------ #
    async def _execute_step(self, step, ctx, counted=True):
        name = step.get('name') or 'unnamed'
        method = (step.get('method') or 'GET').upper()
        url = self._build_url(step.get('url') or '', ctx)
        # 头值必须 strip：变量渲染为空时会留下 "Bearer " 这种带尾空格的值，
        # httpx 会直接抛 LocalProtocolError，报错信息完全看不出是变量没取到。
        headers = {str(k): str(v).strip()
                   for k, v in (ctx.render_dict(step.get('headers') or {})).items() if k}
        params = {str(k): str(v) for k, v in (ctx.render_dict(step.get('params') or {})).items() if k}

        kwargs = {'headers': headers}
        if params:
            kwargs['params'] = params

        body_type = (step.get('body_type') or 'NONE').upper()
        raw_body = ctx.render(step.get('body') or '')
        step_files = step.get('files') or []
        sent_bytes = 0
        if body_type == 'FORM' and step_files:
            # multipart/form-data：文本字段 + 文件字段共同编码（httpx 在
            # data + files 同时存在时自动走 multipart），文件字节带缓存复用
            form_files, file_err = _build_multipart_files(step_files, self._file_bytes_cache)
            if file_err:
                self.log(file_err, 'WARNING')
            kwargs['data'] = {str(k): ctx.render(str(v)) for k, v in _parse_form_body(raw_body).items()}
            kwargs['files'] = form_files
            sent_bytes = sum(len(f[1][1]) for f in form_files) + len(raw_body.encode('utf-8'))
        elif body_type == 'JSON' and raw_body.strip():
            try:
                kwargs['json'] = json.loads(raw_body)
            except (ValueError, TypeError):
                kwargs['content'] = raw_body.encode('utf-8')
                headers.setdefault('Content-Type', 'application/json')
            sent_bytes = len(raw_body.encode('utf-8'))
        elif body_type == 'FORM' and raw_body.strip():
            try:
                kwargs['data'] = json.loads(raw_body)
            except (ValueError, TypeError):
                kwargs['data'] = dict(
                    pair.split('=', 1) for pair in raw_body.split('&') if '=' in pair)
            sent_bytes = len(raw_body.encode('utf-8'))
        elif body_type in ('RAW', 'XML') and raw_body:
            kwargs['content'] = raw_body.encode('utf-8')
            if body_type == 'XML':
                headers.setdefault('Content-Type', 'application/xml')
            sent_bytes = len(raw_body.encode('utf-8'))

        started = time.perf_counter()
        status_code = 0
        ok = False
        error_type = None
        error_message = ''
        recv_bytes = 0
        response = None

        try:
            response = await self._client.request(method, url, **kwargs)
            status_code = response.status_code
            recv_bytes = len(response.content or b'')
            ok = 200 <= status_code < 400
            if not ok:
                error_type = f'HTTP {status_code}'
                error_message = (response.text or '')[:200]
        except httpx.TimeoutException as exc:
            error_type = 'Timeout'
            error_message = str(exc)[:200]
        except httpx.ConnectError as exc:
            error_type = 'ConnectError'
            error_message = str(exc)[:200]
        except httpx.HTTPError as exc:
            error_type = type(exc).__name__
            error_message = str(exc)[:200]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            error_type = type(exc).__name__
            error_message = str(exc)[:200]

        elapsed_ms = (time.perf_counter() - started) * 1000

        # 断言（仅在拿到响应时执行）
        if ok and response is not None:
            passed, fail_msg = self._run_assertions(step.get('assertions') or [], response, elapsed_ms)
            if not passed:
                ok = False
                error_type = 'AssertionFailed'
                error_message = fail_msg[:200]

        # 关联提取
        if response is not None:
            self._run_extractors(step.get('extractors') or [], response, ctx)

        if counted:
            self._request_count += 1

        self.collector.record(
            name, elapsed_ms, ok, sent=sent_bytes, recv=recv_bytes,
            error_type=error_type, error_message=error_message,
            method=method, url=step.get('url') or '', counted=counted,
        )
        self._write_raw([
            int(time.time() * 1000), round(elapsed_ms, 2), name, method, url,
            status_code, int(ok), sent_bytes, recv_bytes, error_message,
        ])

    def _build_url(self, url, ctx):
        url = ctx.render(url)
        # 正常绝对 URL
        if url.startswith(('http://', 'https://')):
            return url
        # 兼容从接口测试导入时出现的 /{{baseUrl}}/login → /http://host/login 数据错误
        stripped = url.lstrip('/')
        if stripped.startswith(('http://', 'https://')):
            return stripped
        base = (self.env.get('base_url') or '').rstrip('/')
        path = url if url.startswith('/') else f'/{url}'
        return f'{base}{path}'

    # ------------------------------------------------------------------ #
    # 断言与提取
    # ------------------------------------------------------------------ #
    @staticmethod
    def _run_assertions(assertions, response, elapsed_ms):
        for item in assertions or []:
            atype = (item.get('type') or '').upper().replace('JSONPATH', 'JSON_PATH')
            expected = item.get('expected')
            try:
                if atype == 'STATUS_CODE':
                    if int(response.status_code) != int(expected):
                        return False, f'状态码期望 {expected}，实际 {response.status_code}'
                elif atype == 'CONTAINS':
                    if str(expected) not in (response.text or ''):
                        return False, f'响应未包含「{expected}」'
                elif atype == 'NOT_CONTAINS':
                    if str(expected) in (response.text or ''):
                        return False, f'响应不应包含「{expected}」'
                elif atype == 'RESPONSE_TIME':
                    if elapsed_ms > float(expected):
                        return False, f'响应时间 {round(elapsed_ms, 1)}ms 超过 {expected}ms'
                elif atype == 'REGEX':
                    if not re.search(str(expected), response.text or ''):
                        return False, f'响应不匹配正则「{expected}」'
                elif atype == 'JSON_PATH':
                    actual = _extract_jsonpath(response, item.get('expr') or item.get('json_path') or '')
                    if str(actual) != str(expected):
                        return False, f'JSONPath 期望 {expected}，实际 {actual}'
                elif atype == 'HEADER':
                    actual = response.headers.get(item.get('header_name') or '')
                    if str(actual) != str(expected):
                        return False, f'响应头期望 {expected}，实际 {actual}'
            except Exception as exc:  # noqa: BLE001
                return False, f'断言执行异常：{exc}'
        return True, ''

    @staticmethod
    def _run_extractors(extractors, response, ctx):
        """执行提取规则，写入 ctx 并返回本次提取的 {name: value}。

        返回值仅含本调用实际写入的变量，便于调试展示区分「本步骤提取」与
        「上下文既有变量」（环境基址注入 / 场景变量 / 前序步骤提取），
        避免把环境配置误读为从响应里提取的硬编码值。
        """
        extracted = {}
        for item in extractors or []:
            name = item.get('name')
            if not name:
                continue
            # 兼容两种写法：接口层规范化后是 JSON_PATH，早期数据可能是 JSONPATH
            etype = (item.get('type') or 'JSON_PATH').upper().replace('JSONPATH', 'JSON_PATH')
            expr = item.get('expr') or item.get('json_path') or ''
            value = ''
            try:
                if etype == 'JSON_PATH':
                    value = _extract_jsonpath(response, expr)
                elif etype == 'REGEX':
                    match = re.search(expr, response.text or '')
                    if match:
                        value = match.group(1) if match.groups() else match.group(0)
                elif etype == 'HEADER':
                    value = response.headers.get(expr, '')
            except Exception:  # noqa: BLE001 - 提取失败置空，不中断压测
                value = ''
            value = value if value is not None else ''
            ctx.set(name, value)
            extracted[name] = value
        return extracted

    # ------------------------------------------------------------------ #
    # 采样循环
    # ------------------------------------------------------------------ #
    async def _sample_loop(self):
        interval = max(int(self.runtime.get('sample_interval') or 1), 1)
        next_offset = interval
        try:
            while not self._stopping:
                await asyncio.sleep(interval)
                elapsed = time.time() - self._start_ts
                sample = self.collector.take_window(interval)
                sample['ts_offset'] = int(round(elapsed))
                sample['active_users'] = self._active_users
                cpu, mem = self._probe_self()
                sample['cpu_percent'] = cpu
                sample['memory_mb'] = mem
                sample['steps'] = self.collector.step_snapshot(elapsed)
                try:
                    self.on_sample(sample)
                except Exception as exc:  # noqa: BLE001 - 采样回调失败不影响压测
                    self.log(f'采样回调异常：{exc}', 'WARNING')
                next_offset += interval
        except asyncio.CancelledError:
            raise

    def _probe_self(self):
        """压力机自监控：CPU 过高说明客户端已饱和，数据可信度存疑。"""
        if not self._proc:
            return 0.0, 0.0
        try:
            cpu = self._proc.cpu_percent(interval=None)
            mem = self._proc.memory_info().rss / 1024 / 1024
            return round(cpu, 1), round(mem, 1)
        except Exception:  # noqa: BLE001
            return 0.0, 0.0


def _extract_jsonpath(response, expr):
    """JSONPath 提取，复用平台既有的 jsonpath-ng 依赖。"""
    if not expr:
        return ''
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return ''
    try:
        from jsonpath_ng.ext import parse as jsonpath_parse
        matches = jsonpath_parse(expr).find(data)
        if not matches:
            return ''
        value = matches[0].value
        return value
    except Exception:  # noqa: BLE001
        return ''


async def debug_run(snapshot, max_steps=50):
    """单次调试：1 并发跑 1 轮，返回每步的请求/响应/耗时/断言结果。

    与 api_testing 的单接口调试体验保持一致，不落 Execution。
    """
    env = snapshot.get('env_config') or {}
    runtime = snapshot.get('runtime_config') or {}
    steps = [s for s in (snapshot.get('steps') or []) if s.get('enabled', True)][:max_steps]
    ctx = VariableContext(snapshot.get('variables') or [], user_index=0,
                          csv_data=snapshot.get('csv_data') or {},
                          base_url=(snapshot.get('env_config') or {}).get('base_url'))

    results = []
    headers_global = {'User-Agent': 'TestHub-PerfEngine/1.0'}
    for k, v in (env.get('headers') or {}).items():
        if k:
            headers_global[str(k)] = str(v)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(float(runtime.get('timeout') or 30)),
        verify=bool(env.get('verify_ssl', False)),
        follow_redirects=bool(runtime.get('follow_redirects', False)),
        headers=headers_global,
        **build_proxy_kwargs(runtime),
    ) as client:
        file_cache = {}
        for step in steps:
            name = step.get('name') or 'unnamed'
            method = (step.get('method') or 'GET').upper()
            raw_url = ctx.render(step.get('url') or '')
            if raw_url.startswith(('http://', 'https://')):
                url = raw_url
            else:
                stripped = raw_url.lstrip('/')
                if stripped.startswith(('http://', 'https://')):
                    url = stripped
                else:
                    base = (env.get('base_url') or '').rstrip('/')
                    url = f"{base}{raw_url if raw_url.startswith('/') else '/' + raw_url}"

            headers = {str(k): str(v).strip()
                       for k, v in ctx.render_dict(step.get('headers') or {}).items() if k}
            params = {str(k): str(v) for k, v in ctx.render_dict(step.get('params') or {}).items() if k}
            body_type = (step.get('body_type') or 'NONE').upper()
            raw_body = ctx.render(step.get('body') or '')
            step_files = step.get('files') or []

            kwargs = {'headers': headers}
            if params:
                kwargs['params'] = params
            file_err = ''
            if body_type == 'FORM' and step_files:
                # multipart/form-data：与正式压测（_execute_step）同一构造逻辑
                form_files, file_err = _build_multipart_files(step_files, file_cache)
                kwargs['data'] = {str(k): ctx.render(str(v))
                                  for k, v in _parse_form_body(raw_body).items()}
                kwargs['files'] = form_files
            elif body_type == 'JSON' and raw_body.strip():
                try:
                    kwargs['json'] = json.loads(raw_body)
                except (ValueError, TypeError):
                    kwargs['content'] = raw_body.encode('utf-8')
            elif body_type == 'FORM' and raw_body.strip():
                try:
                    kwargs['data'] = json.loads(raw_body)
                except (ValueError, TypeError):
                    kwargs['data'] = dict(p.split('=', 1) for p in raw_body.split('&') if '=' in p)
            elif body_type in ('RAW', 'XML') and raw_body:
                kwargs['content'] = raw_body.encode('utf-8')

            started = time.perf_counter()
            entry = {
                'name': name, 'method': method, 'url': url,
                'request_headers': headers, 'request_body': raw_body,
                'is_setup': bool(step.get('is_setup')),
                'files': [f['filename'] for f in step_files],
            }
            try:
                response = await client.request(method, url, **kwargs)
                elapsed = (time.perf_counter() - started) * 1000
                passed, fail_msg = BuiltinEngine._run_assertions(
                    step.get('assertions') or [], response, elapsed)
                extracted_now = BuiltinEngine._run_extractors(
                    step.get('extractors') or [], response, ctx)
                body_text = response.text or ''
                entry.update({
                    'success': (200 <= response.status_code < 400) and passed,
                    'status_code': response.status_code,
                    'elapsed_ms': round(elapsed, 2),
                    'response_headers': dict(response.headers),
                    'response_body': body_text[:20000],
                    'response_size': len(response.content or b''),
                    'assertion_passed': passed,
                    'assertion_message': fail_msg,
                    # extracted 仅含本步骤提取器写入的变量，避免环境注入/场景变量
                    # 被误读为从响应提取的硬编码值；全量上下文见 context_snapshot。
                    'extracted': extracted_now,
                    'context_snapshot': {k: v for k, v in ctx.values.items()},
                    'error': file_err if (file_err and passed) else ('' if passed else fail_msg),
                })
            except Exception as exc:  # noqa: BLE001 - 调试需要把异常回显给用户
                entry.update({
                    'success': False,
                    'status_code': 0,
                    'elapsed_ms': round((time.perf_counter() - started) * 1000, 2),
                    'response_headers': {},
                    'response_body': '',
                    'response_size': 0,
                    'assertion_passed': False,
                    'assertion_message': '',
                    'error': f'{type(exc).__name__}: {exc}',
                })
            results.append(entry)

    return results
