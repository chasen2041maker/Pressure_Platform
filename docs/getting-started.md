# 本地启动指南

[文档中心](README.md) / 本地启动

本指南使用独立 SQLite 开发配置，不导入已有数据库或业务账号。先启动管理页面，再单独准备实际执行请求的运行器。**这不是生产部署指南，也没有“几条命令即完成容量验收”的承诺。**

## 1. 确认环境

需要 Python 3.12+、Node.js 22.12+、npm 和 Git。Windows 示例使用 PowerShell；Linux/macOS 使用对应 shell。

```sh
python --version
node --version
npm --version
git --version
```

Python 命令应指向满足要求的解释器；有多个版本时，使用该解释器创建虚拟环境。依赖安装以仓库配置和锁文件为准，不通过随意升级版本绕过安装失败。

## 2. 获取完整源码

```sh
git clone --recurse-submodules https://github.com/chasen2041maker/Pressure_Platform.git
cd Pressure_Platform
```

已有克隆执行：

```sh
git submodule update --init --recursive
```

`vendor/k6` 是官方引擎的固定源码子模块。仓库不包含预编译 k6；自定义有界 SSE 扩展见 [运行时说明](../deployment/runtime/bounded-sse/README.md)。

## 3. 创建并激活 Python 环境

以下命令从仓库根目录运行：

```sh
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

Linux/macOS：

```sh
source .venv/bin/activate
```

激活后安装依赖：

```sh
python -m pip install -r deployment/requirements-local.txt
```

## 4. 初始化独立密钥

仍在仓库根目录运行。已有有效密钥会保留，不会重新生成：

```sh
python -c "import sys; from pathlib import Path; sys.path.insert(0, 'deployment/linux-docker'); from runtime_tools import ensure_secret; ensure_secret(Path('runtime/private').resolve())"
```

默认密钥位置为 `runtime/private/django-secret.txt`，运行数据写入仓库根 `runtime/`。不要提交密钥、数据库、账号池、报告或其他运行数据。

> 使用 `PRESSURE_PLATFORM_ROOT` 指向其他独立目录时，需要把上面命令的 `runtime/private` 路径换成该目录下的 `runtime/private`，并让后端与 worker 使用一致的配置。已有数据库但原密钥丢失时，应恢复原密钥，不要重新生成后继续使用旧数据库。

## 5. 初始化数据库并启动后端

```sh
cd testhub
python manage.py migrate --settings=backend.pressure_settings
python manage.py createsuperuser --settings=backend.pressure_settings
python manage.py runserver 127.0.0.1:8000 --settings=backend.pressure_settings
```

使用自己创建的管理员账号。本地开发配置只供本机使用，不应通过更换监听地址直接作为公网部署方案。

## 6. 启动前端

另开终端，从仓库根目录运行：

```sh
cd testhub/frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 58101
```

打开 `http://127.0.0.1:58101`。后端地址为 `http://127.0.0.1:8000`；前后端终端都需要保持运行。

## 7. 准备第一次执行

先在页面创建项目、环境和测试账号，准备自己拥有或已获授权的目标接口。随后按 [k6 适配说明](../testhub/docs/k6-adapter.md)配置运行器，并核对 [能力矩阵](../testhub/docs/k6-capability-matrix.md) 与 [固定运行时](../deployment/runtime/bounded-sse/README.md)。

`K6_RUNNER` 区分 `NATIVE` 与 `DOCKER`；原生二进制通过 `K6_BIN` 指定或从 PATH 查找。这里只说明入口，不用任意最新版二进制替代固定版本。修改 runner 环境后，应重启管理服务，使新 worker 继承配置，再创建新任务。

**推荐顺序：准备接口 → 绑定环境和账号 → 单轮验证 → 小规模运行 → 查看报告与执行完整性。** 不要把第一次启动成功当成大并发测试的准备完成。

## 常见卡点

| 现象 | 先检查 |
| --- | --- |
| 依赖安装或构建失败 | Python / Node 版本、当前虚拟环境、锁文件要求及完整错误；不要删除锁文件掩盖问题 |
| 后端提示密钥或数据库问题 | 是否在正确根目录初始化、`PRESSURE_PLATFORM_ROOT` 是否一致、是否使用原密钥 |
| 页面可打开，但登录或 API 失败 | 后端终端是否仍在运行、迁移是否完成、是否创建了管理员 |
| 配置可保存，调试或运行不可用 | runner 是否存在、版本核对是否通过、请求是否属于已接入能力子集 |
| Docker 内无法访问目标 | 容器视角的地址与专用网络；容器的 `127.0.0.1` 不是宿主目标地址 |
| 原生 VU 没有数据 | 检查运行模式与采集支持；缺失观测不能解释为零负载或成功采集 |

Linux 部署请阅读 [独立部署草稿](../deployment/linux-docker/README.md)。当前公开版镜像与实际部署未验收，不能把本地开发步骤直接当成上线流程。
