"""压测引擎环境自检。

用途：部署后（或排查「引擎置灰」问题时）一条命令确认三个引擎的真实可用性，
而不是靠翻代码猜。输出与 /api/perf-testing/engines/status/ 完全同源，
前端看到的置灰状态就是这里的结论。

    python manage.py check_perf_engines
    python manage.py check_perf_engines --strict   # 有引擎不可用时以退出码 1 结束（可用于 CI 卡关）

典型结论与处置：
    BUILTIN 不可用 —— 不可能发生，若出现说明代码被破坏
    LOCUST  不可用 —— pip install locust（注意：locust 未写入 req_light.txt，
                      镜像默认不带，需显式安装或加进依赖清单后重建基础镜像）
    JMETER  不可用 —— 需要 java + jmeter 同时可达；基础镜像已带 openjdk-21-jre-headless，
                      缺的是 JMeter 发行包本身，见 docs/perf-testing-engine-deploy.md
"""
import os
import shutil

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = '检查内置/Locust/JMeter 三个压测引擎在当前环境的可用性'

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true',
                            help='存在不可用引擎时以退出码 1 结束')

    def handle(self, *args, **options):
        from apps.perf_testing.engines import engine_status, locust_version

        self.stdout.write(self.style.MIGRATE_HEADING('=== 压测引擎环境自检 ==='))

        # 先把底层依赖摊开，避免只看到 "available: False" 却不知道缺哪一环
        java = shutil.which('java')
        jmeter_bin_env = os.environ.get('JMETER_BIN') or ''
        jmeter_which = shutil.which('jmeter')
        self.stdout.write('底层依赖：')
        self.stdout.write(f'  java           : {java or "未找到（JMeter 必需）"}')
        self.stdout.write(f'  JMETER_BIN(env): {jmeter_bin_env or "未设置"}')
        self.stdout.write(f'  jmeter(PATH)   : {jmeter_which or "未找到"}')
        # 安全探测：用 importlib.metadata 读版本，绝不 `import locust` 本体
        # （locust 导入即触发 gevent monkey.patch_all，会破坏 Django 线程级 DB 连接，
        #  表现为命令结束时 DatabaseWrapper thread 错误；运行期 engine_status 同样避开直接 import）
        try:
            locust_ver = locust_version() or '未安装'
        except Exception:  # noqa: BLE001
            locust_ver = '未安装'
        self.stdout.write(f'  locust(python) : {locust_ver}')
        self.stdout.write('')

        unavailable = []
        self.stdout.write('引擎状态：')
        for item in engine_status(force=True):
            name = item.get('name')
            ok = bool(item.get('available'))
            tag = self.style.SUCCESS('可用') if ok else self.style.ERROR('不可用')
            version = item.get('version') or '-'
            self.stdout.write(f'  [{tag}] {name:<8} version={version}')
            self.stdout.write(f'           {item.get("description", "")}')
            if not ok:
                unavailable.append(name)

        self.stdout.write('')
        if unavailable:
            self.stdout.write(self.style.WARNING(
                f'不可用引擎：{", ".join(unavailable)}（前端会自动置灰，'
                f'选择该引擎的场景在 preflight 阶段会被拦截）'))
            self.stdout.write('处置指引见 docs/perf-testing-engine-deploy.md')
            if options.get('strict'):
                raise SystemExit(1)
        else:
            self.stdout.write(self.style.SUCCESS('全部引擎可用'))
