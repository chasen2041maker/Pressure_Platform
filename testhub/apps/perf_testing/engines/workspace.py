"""非 C 盘运行时工作目录管理（硬约束：禁止任何临时文件写入 C 盘）。

背景
----
压测引擎（JMeter / Locust）运行期会产生大量临时产物：
- JMeter 的 .jmx 脚本、.jtl 结果、HTML 报告目录，以及 JVM 自身的临时文件
  （由 ``java.io.tmpdir`` 决定）；
- Locust 生成的 locustfile 与 ``--csv`` 产物。

此前代码使用标准库 ``tempfile``，默认落到系统 TMP（Windows 下为
``C:\\Users\\<user>\\AppData\\Local\\Temp``），违反了「不占用 C 盘」的硬性要求。

本模块统一把所有运行期临时文件重定向到项目内的 ``perf_workspace`` 目录
（位于 F 盘项目根，即 ``PROJECT_ROOT/perf_workspace``），并通过 :func:`non_c_env`
为子进程注入 ``TMP`` / ``TEMP`` / ``TMPDIR`` 以及 ``_JAVA_OPTIONS=-Djava.io.tmpdir=...``，
把 Java 自身的临时目录也指向同一非 C 目录。

所有函数均为纯标准库实现，不依赖 Django，便于 DB-free 单测直接 import。
"""
import os
import shutil
import time
import uuid

# PROJECT_ROOT = .../testhub_platform
# workspace.py -> engines -> perf_testing -> apps -> PROJECT_ROOT
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
WORKSPACE_ROOT = os.path.join(_PROJECT_ROOT, 'perf_workspace')


def project_root():
    """返回 Django 项目根目录绝对路径（F 盘）。"""
    return _PROJECT_ROOT


def workspace_root():
    """返回 perf_workspace 绝对路径（F 盘项目内，非 C 盘）。"""
    return WORKSPACE_ROOT


def ensure_workspace():
    """确保 perf_workspace 目录存在，返回其路径。"""
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    return WORKSPACE_ROOT


def _stamp():
    return time.strftime('%Y%m%d_%H%M%S') + '_' + str(os.getpid()) + '_' + uuid.uuid4().hex[:8]


def unique_run_path(prefix='run'):
    """返回 perf_workspace 下的唯一运行目录路径（**不**创建目录）。

    由调用方在真正需要写入时再 ``os.makedirs``，避免实例化即产生空目录。
    """
    return os.path.join(WORKSPACE_ROOT, '{}_{}'.format(prefix, _stamp()))


def make_run_dir(prefix='run'):
    """在 perf_workspace 下创建唯一运行目录（非 C 盘），返回绝对路径。"""
    path = unique_run_path(prefix)
    os.makedirs(path, exist_ok=True)
    return path


def make_run_file(prefix='file', suffix='', run_dir=None):
    """在 run_dir（缺省新建）下生成唯一文件路径（非 C 盘）。"""
    if run_dir is None:
        run_dir = make_run_dir(prefix='tmp')
    os.makedirs(run_dir, exist_ok=True)
    name = '{}_{}{}'.format(prefix, time.strftime('%H%M%S'), uuid.uuid4().hex[:8], suffix)
    return os.path.join(run_dir, name)


def _infer_jmeter_home():
    """从已有环境变量反推 JMETER_HOME（不写死路径，保证 Linux 部署可移植）。

    仅在环境已显式给出时才返回，绝不猜测一个不存在的路径。
    """
    home = os.environ.get('JMETER_HOME')
    if home and os.path.isdir(home):
        return home
    bin_path = os.environ.get('JMETER_BIN') or ''
    if isinstance(bin_path, (list, tuple)):
        bin_path = bin_path[0] if bin_path else ''
    if bin_path and os.path.isabs(bin_path):
        # .../bin/jmeter(.bat) -> ...
        parent = os.path.dirname(os.path.dirname(os.path.abspath(bin_path)))
        if os.path.isdir(parent):
            return parent
    return None


def non_c_env(work_dir=None, extra=None):
    """构造子进程环境字典：继承当前环境，并把临时目录重定向到非 C 目录。

    - ``TMP`` / ``TEMP`` / ``TMPDIR`` 指向 work_dir（缺省 perf_workspace）。
    - ``java.io.tmpdir`` 通过 ``_JAVA_OPTIONS=-Djava.io.tmpdir=...`` 注入，
      使 JMeter 启动的 JVM 也不在 C 盘写临时文件。
    - 若能从环境反推 ``JMETER_HOME`` / ``JAVA_HOME`` 则一并显式写入，
      规避 Git Bash / MINGW 下 ``jmeter.bat`` 猜错 ``JAVA_HOME`` 的问题。
    """
    env = dict(os.environ)
    target = work_dir or ensure_workspace()
    os.makedirs(target, exist_ok=True)
    env['TMP'] = target
    env['TEMP'] = target
    env['TMPDIR'] = target  # 防御 Unix 工具
    java_opts = env.get('_JAVA_OPTIONS', '')
    inject = '-Djava.io.tmpdir={}'.format(target)
    if inject not in java_opts:
        env['_JAVA_OPTIONS'] = (java_opts + ' ' + inject).strip()
    jm_home = _infer_jmeter_home()
    if jm_home:
        env['JMETER_HOME'] = jm_home
    java_home = os.environ.get('JAVA_HOME')
    if java_home:
        env['JAVA_HOME'] = java_home
    if extra:
        env.update(extra)
    return env


def cleanup_old_runs(max_age_hours=24, prefix=None):
    """清理 perf_workspace 下超期的运行目录，避免无限增长。

    仅删除由本模块 create 出来的、修改时间早于阈值的目录，不动其它文件，
    安全可重复调用。返回删除的目录数量。
    """
    if not os.path.isdir(WORKSPACE_ROOT):
        return 0
    deadline = time.time() - max_age_hours * 3600
    removed = 0
    for name in os.listdir(WORKSPACE_ROOT):
        if prefix and not name.startswith(prefix):
            continue
        path = os.path.join(WORKSPACE_ROOT, name)
        if not os.path.isdir(path):
            continue
        try:
            if os.path.getmtime(path) < deadline:
                shutil.rmtree(path)
                removed += 1
        except OSError:
            continue
    return removed
