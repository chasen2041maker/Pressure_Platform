"""JMeter 引擎（可选）：包装 `jmeter -n -t plan.jmx -l result.jtl`，解析 jtl 归一化指标。

设计取舍（方案 §2 / 设计文档）：
- 不把 JMeter 当作"被压测对象"，而是复用其运行时作为「中大型、多协议」场景的备选引擎。
- JMeter 非必装依赖；未安装时 engine_status 如实上报，前端置灰不可选。
- 归一化结果复用 services.metrics 的 MetricsCollector/Histogram，保证 summary/request_stats
  字段与内置引擎完全一致，上层（reporter/WS）零感知。

两种执行模式（由 snapshot['script_ref']['mode'] 决定）：
- 缺省 / 'scenario'：由平台步骤现场生成 .jmx（jmx_builder.build_jmx）
- 'script'：直接执行用户上传的 .jmx（PerfDataFile 中 file_type=JMX 的文件）
  jmx_path 必须由 views.resolve_script_ref 从 PerfDataFile 反查得到并校验落在
  MEDIA_ROOT 内，本模块不做来源校验，只做 XML 良构校验。

环境依赖说明（重要，决定 is_available() 的返回值）：
- 需要 java 与 jmeter 同时可达；jmeter 通过 PATH 查找，或用环境变量 JMETER_BIN
  指定绝对路径（可指向 apache-jmeter-x.y/bin/jmeter 或 jmeter.bat）。
- 仅装 java 是不够的：本项目开发机即为「只有 JRE、无 JMeter 发行包」的状态，
  此时 is_available() 返回 False，真实端到端压测无法在本地跑通；引擎生命周期
  （prepare/run/stop/collect + jtl 解析）由 tests/test_jmeter_engine.py 里的
  fake-jmeter 桩程序覆盖，不依赖真实 JMeter。
- 部署到装有 JMeter 的服务器后无需改代码，engine_status 会自动转为可用。
- 自检命令：python manage.py check_perf_engines
- 部署指引：docs/perf-testing-engine-deploy.md

临时文件约束（重要）：.jmx / .jtl / HTML 报告目录以及 JVM 临时文件一律写入
work_dir（默认项目内 perf_workspace，位于 F 盘），绝不落到系统 TMP（C 盘）。
子进程环境通过 engines.workspace.non_c_env 把 TMP/TEMP/TMPDIR 与
java.io.tmpdir 都指向 work_dir，详见 workspace.py。
"""
import csv
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from .base import BaseEngine, EngineError
from .jmx_builder import build_jmx
from .workspace import non_c_env, unique_run_path
from ..services.metrics import Histogram, MetricsCollector


def is_available():
    """JMeter 是否可用（java/jmeter 在 PATH 或环境变量 JMETER_BIN）。"""
    return _resolve_bin() is not None


def get_version():
    """尽力获取 jmeter 版本；不可用返回空字符串。

    JMeter 5.6.3 的 `jmeter --version` 输出为 ASCII banner + 一行 `#_# | 5.6.3`，
    并夹带 log4j2 的 WARN/SLF4J 噪声。此处优先解析版本号，避免把 banner 当版本。
    """
    cmd = _resolve_bin()
    if not cmd:
        return ''
    try:
        out = subprocess.run(cmd + ['--version'], capture_output=True, text=True, timeout=10)
        text = (out.stdout or '') + '\n' + (out.stderr or '')
        # 1) JMeter 5.x 显式版本行: "#_# | 5.6.3"
        m = re.search(r'#_#\s*\|\s*([\d.]+)', text)
        if m:
            return f'Apache JMeter v{m.group(1)}'
        # 2) 兼容其它形式: "Apache JMeter vX.Y.Z" 或 "Apache JMeter X.Y.Z"
        m = re.search(r'Apache\s+JMeter[^\d]*([\d.]+)', text)
        if m:
            return f'Apache JMeter v{m.group(1)}'
        # 3) 通用兜底: 5.6.3 的 banner 把版本嵌在末行末尾 (... _| \_\ 5.6.3)
        m = re.search(r'(\d+\.\d+\.\d+)', text)
        if m:
            return f'Apache JMeter v{m.group(1)}'
        return ''
    except Exception:  # noqa: BLE001
        return ''


def _resolve_bin():
    raw = os.environ.get('JMETER_BIN') or 'jmeter'
    if isinstance(raw, (list, tuple)):
        candidates = list(raw)
    else:
        candidates = [raw]
    for c in candidates:
        if os.path.isabs(c) and os.path.exists(c):
            return candidates
        if shutil.which(c):
            return candidates
    return None


def _to_int(val, default=0):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _feed(collector, row, counted=True):
    """把一行 jtl（dict）喂入 MetricsCollector。"""
    label = row.get('label') or 'unknown'
    elapsed = _to_int(row.get('elapsed'))
    ok = str(row.get('success') or '').lower() == 'true'
    recv = _to_int(row.get('bytes'))
    sent = _to_int(row.get('sentBytes'))
    code = row.get('responseCode') or ''
    err_type = None
    err_msg = ''
    if not ok:
        err_type = f'HTTP_{code}' if code else 'FAILED'
        err_msg = row.get('failureMessage') or ''
    url = row.get('URL') or ''
    collector.record(label, elapsed, ok, sent=sent, recv=recv,
                     error_type=err_type, error_message=err_msg, url=url, counted=counted)


def parse_jtl(jtl_path, duration_seconds=None, stop_reason=''):
    """解析 jtl（CSV）为归一化结果字典。

    返回结构：{summary, request_stats, samples, raw_rows, duration, stop_reason}
    与 builtin.collect() 的返回契约一致。
    """
    collector = MetricsCollector()
    rows = []
    with open(jtl_path, newline='', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for r in reader:
            if not r.get('label'):
                continue
            _feed(collector, r)
            rows.append(r)

    if duration_seconds is None:
        ts = [int(r['timeStamp']) for r in rows if str(r.get('timeStamp') or '').isdigit()]
        duration_seconds = (max(ts) - min(ts)) / 1000.0 if len(ts) > 1 else 1.0
    duration_seconds = duration_seconds or 1.0

    return {
        'summary': collector.build_summary(duration_seconds),
        'request_stats': collector.build_request_stats(duration_seconds),
        'samples': rows[:200],
        'raw_rows': len(rows),
        'duration': round(duration_seconds, 2),
        'stop_reason': stop_reason,
    }


class JmeterEngine(BaseEngine):
    """基于 JMeter（headless）的压测引擎。"""

    name = 'jmeter'

    def __init__(self, snapshot, on_sample=None, on_log=None, raw_csv_path=None,
                 jmeter_bin=None, work_dir=None):
        super().__init__(snapshot, on_sample=on_sample, on_log=on_log)
        self.raw_csv_path = raw_csv_path
        self.jmeter_bin = jmeter_bin or os.environ.get('JMETER_BIN') or 'jmeter'
        # work_dir：所有运行期临时文件（.jmx/.jtl/HTML 报告目录）的落盘位置。
        # 默认落在项目内的 perf_workspace（F 盘），严禁落到系统 TMP（C 盘）。
        # 不在此处创建目录，待 prepare() 真正写入时再 makedirs。
        self.work_dir = work_dir or unique_run_path('jmeter')
        self.load_config = snapshot.get('load_config') or {}
        self.runtime = snapshot.get('runtime_config') or {}
        self.variables = snapshot.get('variables') or []
        self.steps = snapshot.get('steps') or []
        self.script_ref = snapshot.get('script_ref') or {}

        self._sample_interval = max(int(self.runtime.get('sample_interval') or 1), 1)
        self.jmx_path = None
        self.jtl_path = None
        self.report_dir = None
        self.jmeter_cmd = None
        self._proc = None
        self._start_ts = None
        self._duration = 0.0
        self._stop_reason = ''
        self._seen = 0
        self._global_total = 0
        self._active_users = 0
        # 窗口累计（用于实时采样）
        self._win_hist = Histogram()
        self._win_total = 0
        self._win_failed = 0

    # ------------------------------------------------------------------ #
    def _resolve_instance_bin(self):
        raw = self.jmeter_bin
        if isinstance(raw, (list, tuple)):
            candidates = list(raw)
        else:
            candidates = [raw]
        for c in candidates:
            if os.path.isabs(c) and os.path.exists(c):
                return candidates
            if shutil.which(c):
                return candidates
        return None

    def prepare(self):
        cmd = self._resolve_instance_bin()
        if cmd is None:
            raise EngineError('JMeter 未安装或不可达（需 java/jmeter，或设置环境变量 JMETER_BIN）')
        self.jmeter_cmd = list(cmd)

        # 所有临时产物写入 work_dir（F 盘项目内 perf_workspace），不碰系统 TMP。
        os.makedirs(self.work_dir, exist_ok=True)

        # 模式一：上传脚本；模式二：场景生成
        mode = (self.script_ref or {}).get('mode')
        if mode == 'script':
            jmx_path = self.script_ref.get('jmx_path')
            if not jmx_path or not os.path.exists(jmx_path):
                raise EngineError('JMeter 脚本模式缺少有效的 .jmx 文件')
            self._validate_jmx(jmx_path)
            self.jmx_path = jmx_path
        else:
            jmx_text = build_jmx({
                'load_config': self.load_config,
                'variables': self.variables,
                'steps': self.steps,
            })
            self.jmx_path = os.path.join(self.work_dir, 'plan.jmx')
            with open(self.jmx_path, 'w', encoding='utf-8') as f:
                f.write(jmx_text)
            self._validate_jmx(self.jmx_path)

        self.jtl_path = os.path.join(self.work_dir, 'result.jtl')
        self.report_dir = os.path.join(self.work_dir, 'report')
        os.makedirs(self.report_dir, exist_ok=True)
        return True

    def _validate_jmx(self, path):
        try:
            ET.parse(path)
        except ET.ParseError as exc:
            raise EngineError(f'.jmx 不是合法的 XML：{exc}')

    def run(self):
        if not self.jmx_path:
            self.prepare()
        self._start_ts = time.time()
        self._seen = 0
        self._global_total = 0
        self._active_users = 0
        self._win_reset()

        cmd = self.jmeter_cmd + [
            '-n', '-t', self.jmx_path,
            '-l', self.jtl_path,
            '-e', '-o', self.report_dir,
        ]
        self.log(f'启动 JMeter: {" ".join(cmd)}')
        # 子进程环境：把 TMP/TEMP/java.io.tmpdir 都重定向到 work_dir（非 C 盘），
        # 并显式写入 JMETER_HOME/JAVA_HOME，避免 JVM/ jmeter.bat 在 C 盘写临时文件。
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=non_c_env(self.work_dir),
        )
        self._proc = proc

        try:
            while not self._stopping:
                new_rows = self._read_new_rows()
                if new_rows:
                    self._ingest(new_rows)
                    self._emit_sample()
                    self._win_reset()
                if proc.poll() is not None:
                    break
                time.sleep(self._sample_interval)
        finally:
            if self._stopping and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            else:
                proc.wait()
            # 收尾：确保 jtl 末尾数据被采集
            new_rows = self._read_new_rows()
            if new_rows:
                self._ingest(new_rows)
                self._emit_sample()
                self._win_reset()
            self._duration = time.time() - self._start_ts if self._start_ts else 0.0

    def _read_new_rows(self):
        if not self.jtl_path or not os.path.exists(self.jtl_path):
            return []
        with open(self.jtl_path, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            rows = [r for r in reader if r.get('label')]
        new_rows = rows[self._seen:]
        self._seen = len(rows)
        return new_rows

    def _ingest(self, new_rows):
        for r in new_rows:
            self._global_total += 1
            self._win_total += 1
            elapsed = _to_int(r.get('elapsed'))
            self._win_hist.add(elapsed)
            ok = str(r.get('success') or '').lower() == 'true'
            if not ok:
                self._win_failed += 1
            try:
                self._active_users = max(self._active_users, _to_int(r.get('allThreads')))
            except (TypeError, ValueError):
                pass

    def _win_reset(self):
        self._win_hist = Histogram()
        self._win_total = 0
        self._win_failed = 0

    def _emit_sample(self):
        sample = {
            'ts_offset': int(time.time() - self._start_ts) if self._start_ts else 0,
            'active_users': self._active_users,
            'tps': round(self._win_total / self._sample_interval, 2),
            'avg_rt': self._win_hist.avg,
            'p90_rt': self._win_hist.percentile(90),
            'p95_rt': self._win_hist.percentile(95),
            'p99_rt': self._win_hist.percentile(99),
            'error_rate': round(self._win_failed / self._win_total * 100, 2) if self._win_total else 0.0,
            'total_requests': self._global_total,
            'cpu_percent': 0.0,
            'memory_mb': 0.0,
        }
        self.on_sample(sample)

    def stop(self, graceful=True):
        self._stopping = True
        if not self._stop_reason:
            self._stop_reason = '收到停止指令'
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def collect(self):
        if not self.jtl_path or not os.path.exists(self.jtl_path):
            return {
                'summary': {}, 'request_stats': [], 'samples': [],
                'raw_rows': 0, 'duration': 0, 'stop_reason': self._stop_reason,
            }
        return parse_jtl(self.jtl_path, duration_seconds=self._duration, stop_reason=self._stop_reason)


def debug_run(snapshot, max_steps=50):
    """JMeter 调试：生成并校验 .jmx，不实际发起压测。

    返回结构与内置引擎调试对齐（steps 带 success 字段），便于前端调试弹窗复用同一套渲染。
    """
    try:
        jmx_text = build_jmx({
            'load_config': snapshot.get('load_config') or {},
            'variables': snapshot.get('variables') or [],
            'steps': snapshot.get('steps') or [],
        })
        ET.fromstring(jmx_text)  # 校验 XML 良构
        steps = []
        for s in (snapshot.get('steps') or [])[:max_steps]:
            steps.append({'name': s.get('name'), 'ok': True, 'success': True, 'elapsed_ms': 0})
        return {'passed': True, 'jmx_valid': True, 'engine': 'JMETER', 'steps': steps}
    except Exception as exc:  # noqa: BLE001
        return {'passed': False, 'jmx_valid': False, 'engine': 'JMETER',
                'error': str(exc), 'steps': []}
