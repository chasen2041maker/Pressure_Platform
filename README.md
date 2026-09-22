# Pressure Platform

基于 **TestHub、Django、Vue 3 和 k6** 的中文性能测试管理平台，支持从接口配置、账号绑定到运行分析和报告导出的完整流程。

这是由 [chasen2041maker](https://github.com/chasen2041maker) 维护的个人项目。项目基于开源 TestHub 扩展，保留上游来源和许可证，详见 [UPSTREAM.md](UPSTREAM.md) 与 [LICENSE](LICENSE)。

## 主要功能

- 项目、OpenAPI / Swagger 接口库导入与更新。
- 持久环境、独立账号池、变量提取与前置依赖。
- 接口配置准备、单轮验证及已验证配置复用。
- k6 固定并发、固定轮数和按时长运行，支持停止及有界收尾。
- HTTP、有限 JSON WebSocket 会话和有界 SSE 场景。
- 逐接口统计、错误分析、原生 VU 观测及 HTML / JSON / CSV 报告。
- 执行完整性与数据恢复记录；失败、未完成和未采集分别展示。

原 TestHub 的其他测试管理模块源码也保留在 `testhub/` 中。

## 获取完整源码

```sh
git clone --recurse-submodules https://github.com/chasen2041maker/Pressure_Platform.git
cd Pressure_Platform
```

已有克隆可运行 `git submodule update --init --recursive`。`vendor/k6` 是官方引擎的固定版本；自定义有界 SSE 扩展在 `deployment/runtime/bounded-sse/`，不包含预编译引擎。

## 目录

| 目录 | 内容 |
| --- | --- |
| `testhub/frontend/` | Vue 3 管理页面 |
| `testhub/apps/perf_testing/` | 压测 API、配置、执行引擎适配、统计和测试 |
| `testhub/backend/` | Django 设置与轻量压测运行配置 |
| `deployment/linux-docker/` | 独立 Linux Docker 部署工具和配置模板 |
| `deployment/runtime/bounded-sse/` | 固定版本 k6 的有界 SSE 扩展及构建工具 |
| `vendor/k6/` | 官方 k6 源码子模块 |

## 本地开发

运行环境：Python 3.12+、Node.js 22.12+。下列命令使用独立 SQLite 开发配置，不需要导入任何已有账号或数据库。

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r deployment/requirements-local.txt
```

在仓库根目录初始化本地密钥文件；已有有效密钥会保留，不会重新生成：

```sh
python -c "import sys; from pathlib import Path; sys.path.insert(0, 'deployment/linux-docker'); from runtime_tools import ensure_secret; ensure_secret(Path('runtime/private').resolve())"
```

然后在 `testhub/` 中执行：

```sh
python manage.py migrate --settings=backend.pressure_settings
python manage.py createsuperuser --settings=backend.pressure_settings
python manage.py runserver 127.0.0.1:8000 --settings=backend.pressure_settings
```

密钥位于 `runtime/private/django-secret.txt`，不要提交到 Git。运行数据默认写入仓库根 `runtime/`；使用 `PRESSURE_PLATFORM_ROOT` 指向其他独立目录时，也须在该目录的 `runtime/private/` 初始化密钥。开发配置仅供本机使用；部署请使用下方独立部署工具。

另开终端：

```sh
cd testhub/frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 58101
```

浏览器打开 `http://127.0.0.1:58101`，使用自己创建的管理员账号。首次使用需要创建项目、环境及测试账号，仓库不预置业务账号或目标地址。

真正发压前还需配置 k6 运行器。SSE 扩展的固定 Go / k6 版本与构建方法见 [引擎说明](deployment/runtime/bounded-sse/README.md)；HTTP / WebSocket / SSE 的实际支持边界见 [能力矩阵](testhub/docs/k6-capability-matrix.md)。

## 独立部署

参见 [Linux Docker 部署说明](deployment/linux-docker/README.md)，从 `.env.example` 填写自己的镜像、地址、资源限制和数据目录。该流程需要 Docker，并明确配置能够到达测试目标的独立网络。仓库不包含任何公司的 GitLab CI、Kubernetes 配置、内网账号或部署数据。

## 验证

```sh
# 从 testhub/ 运行维护套件；此入口自动创建并清理独立测试目录
python ../deployment/test-runner/run_catalog_tests.py

# 从 testhub/frontend/ 运行
node --test src/views/performance-testing/*.test.mjs
npm run build
```

本次公开副本的验证结果及完整套件中的已知问题见 [VALIDATION.md](VALIDATION.md)。协议集成测试可能需要本机 k6、Docker或其他依赖；未满足条件的跳过项不算通过。仓库中的 OpenAPI 大样本由程序合成，不包含真实公司接口规范。原生 VU 是离散观测，P95/P99是累计直方图估算；功能验证、报告核对和目标服务容量验收是不同结论。
