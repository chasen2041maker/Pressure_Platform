# Linux amd64 单机 HTTP 部署草稿

**此公开版部署包未构建、未启动、未部署。** 本仓库已包含 `testhub/` 完整应用源码、运行 profile、依赖锁和部署工具。请从仓库根目录构建，并为自己的实例创建独立数据目录。`K6_EXPECTED_SHA256` 必须填写本次审查的 Linux k6 二进制 SHA256；`doctor` 核对该摘要，并在无网络容器中验证 SSE 扩展。这里不依赖公司 CI、固定内网地址或历史发布包。静态检查和本地构建结果不能替代实际部署验收。

## 构建前必须确认

`django-simpleui==2025.6.24` 官方仅发布源码包。本包仅为此固定版本增加隔离构建阶段：按官方 SHA256 校验源码；以哈希锁定的 pip、setuptools、wheel、packaging 构建工具生成 wheel；构建步骤使用 BuildKit `RUN --network=none`、`--no-index --no-deps --no-build-isolation`。最终镜像仅接收此 wheel 与构建回执，先用 `--no-index --no-deps` 安装该 wheel 的确切本地路径，再执行原完整 runtime 锁安装，不使用升级或强制重装参数；已满足版本的 simpleui 保留本地产物来源。原 runtime 锁和所有版本保持不变，仍执行 `--only-binary=:all:` 与 `pip check`。其他包没有源码构建例外。需要支持 `RUN --network=none` 的 BuildKit；目前仅通过静态测试，尚未实际构建或安装。

- Linux amd64、rootful 本地 Docker；不支持本轮未验证的 rootless/userns-remap。SQLite 放本机文件系统，不放 NFS。安装包/Compose plugin 在服务器环境读取后确定，本草稿不执行安装。
- 后台 UID/GID 为 `0:0`；用同一身份执行 `host.py prepare`，数据目录保持 0700、密钥0600。runner 镜像的默认 USER 必须为空/root/0，因为其私有配置0600、目录和二进制0700；不能仅检查后台UID。Docker socket 权限等同宿主管理能力，只给此后台。
- `PRESSURE_PLATFORM_ROOT` 是专用绝对路径，无符号链接、逗号、CR/LF；挂载不含 `noexec`，宿主与后台容器使用相同绝对路径。当前 Docker `--mount` 直接拼接源路径，不能用含逗号路径。
- 后台/runner 共享指定外部 bridge。显式 `K6_RUNNER=DOCKER`；CLI 路径固定 `/usr/local/bin/docker`。NATIVE 模式未提供原生 VU 采集；本包只用 HTTP 轮询，不依赖跨进程内存 Channels。
- 最终 runner 镜像必须有 `/bin/sh` 和 `/bin/busybox wget`，与固定 k6 二进制/入口协议一起验证。应用升级后重新冻结镜像ID、源码manifest、k6 SHA及版本，不能混用旧后台/新worker。

## 依赖和构建（交付门禁通过后由 root 执行）

`requirements-linux.lock.txt` 保留原 local lock 中的版本，仅删除 Windows `pywin32==312`、增加 `gunicorn==26.2.0`。Gunicorn 版本/发行包SHA已从 [PyPI 官方版本元数据](https://pypi.org/pypi/gunicorn/26.2.0/json) 核实并记入 `dependency-sources.json`。这些版本在 Linux 的 wheel 可得性、相互兼容性与镜像构建仍待验证；不称 Linux 安装已通过。

基础镜像参数不设猜测值。操作者必须先选并冻结 Linux amd64 Python 3.12、Node、nginx、Docker CLI 镜像的实际 digest；Node 要满足当前 package-lock 的引擎要求。后台只复制 Docker CLI，不在容器内启动 daemon。Python 用 `--no-deps` 仅安装锁内条目，再 `pip check`，缺 Linux 特有依赖时明确失败，不自动引入浮动版本。构建依赖版本不允许临时放宽；`pip check`、`npm ci` 或构建失败应回到候选修正后重审。

在**已组装的干净发布根目录**执行下列命令前，先导出四个已核实的基础镜像引用；此处不提供虚构 digest：

```sh
docker build --platform linux/amd64 --build-arg PYTHON_IMAGE --build-arg DOCKER_CLI_IMAGE -f deployment/linux-docker/Dockerfile.backend -t testhub-intranet-backend:RELEASE .
docker build --platform linux/amd64 --build-arg NODE_IMAGE --build-arg NGINX_IMAGE -f deployment/linux-docker/Dockerfile.web -t testhub-intranet-web:RELEASE .
```

把 RELEASE 替换为本次唯一发布标识，并保存实际镜像ID。`.dockerignore` 排除环境文件、私有数据、数据库、依赖树和产物，不打包本机凭据。上述构建默认需要依赖源；离线服务器使用事先构建并经审查的 backend/web/runner 镜像 `docker save` 包，核归档SHA后 `docker load`。Compose 仅引用本地镜像且 `pull_policy: never`，不会在启动时自动构建/拉取。

## 初始化和启动

1. 准备独立发布目录与持久根目录。Linux host 工具要求 Python ≥3.12；若宿主未提供，先准备这一依赖，不能直接运行 Windows venv。用交付的真实二进制SHA执行 `python3 deployment/linux-docker/host.py prepare --root /srv/pressure-platform --k6 /path/to/delivery/k6-linux --sha256 ACTUAL_SHA`。
2. 复制 `.env.example` 为服务器受限的 `.env`（不入源码），填写内网访问IP/域名、origin、资源限制、已加载的实际 backend/web/runner 镜像ID。它不存密码/密钥。资源限制需符合 runner 当前 CPU≤8、内存≤2GiB 的配置校验，不能把这些限额当作压测容量证明。
3. 先检查 daemon 的 rootful/userns 配置、runner 镜像默认USER、持久路径与挂载选项，创建专用 bridge，并与 `K6_DOCKER_NETWORK` 一致。检查没有其他实例占用该 runtime。以下从 `deployment/linux-docker` 执行：

```sh
docker compose --env-file .env config --quiet
docker compose --env-file .env run --rm backend init
docker compose --env-file .env run --rm backend admin
docker compose --env-file .env run --rm backend doctor
docker compose --env-file .env up -d
docker compose --env-file .env ps
```

`init` 只迁移并首次生成密钥；`admin` 交互隐藏输入密码，已有管理员保持原样。`doctor` 核对固定 host runtime 路径和已批准二进制 SHA，并用既有 runner 的 `--network=none` 容器加载 SSE 模块，验证 API 版本和导出；不打开流或发业务请求，不能代替真实目标或容量验收。候选暂存、冷备原子升级及回滚约束见 [CI 维护说明](../ci/README.md#固定-sse-运行时升级)。数据库存在但密钥丢失时必须恢复原密钥，不能重置初始化。

只有 web 绑定选定内网IP/端口；后台8000和临时runner端口均不发布。web 容器没有 socket/runtime mount；`/media/`、`/runtime/`、`/private/` 返回404，报告/原始文件走鉴权API。默认是直接 HTTP；当前 nginx/profile 未验收外部 TLS 终止，不能只改 origin 为 https 就认为 TLS 已配置。

## 停止、备份、恢复与验收

- **先在平台停止活动任务并确认数据库活动数为0、所属runner已退出，再执行 `docker compose --env-file .env stop`。** execution worker 为独立会话，Gunicorn TERM 不会自动替它完整收尾；不要把 Compose stop/强杀当作成功停止压测。异常退出后需复核 worker/runner 清理和数据库状态，不能手工把失败记录改成成功。
- 冷备入口：`python3 host.py backup --root /srv/pressure-platform --archive /separate/backup/unique.tar.gz --project pressure-intranet`。它检查服务已停/活动数0，并连同SQLite、原密钥、二进制、账号/产物备份；保留SHA及对应镜像清单。不得使用 `down -v` 删除数据。
- 恢复入口：`python3 host.py restore --new-root /srv/pressure-platform-restore --archive /separate/backup/unique.tar.gz --sha256 BACKUP_SHA`。只恢复到不存在的新根，更新同绝对路径与项目名后验证；不覆盖原目录试验。
- 实际验收仍待做：nginx `-t`/Compose校验、镜像构建、空目录迁移、中文登录与重登录、项目/账号持久化、确定性HTTP fixed/manual-stop、原生VU/SQL/原始文件对账、未登录下载拒绝、服务/宿主重启、隔离恢复。匿名API返回401/403的health仅是存活/鉴权检查，不能代替这些步骤。
- 本轮静态检查：`python -B -m unittest discover -s deployment/linux-docker -p 'test_*.py'`，不执行 Docker/HTTP；原7项工具测试及新包合同测试分别保留日志。当前包保留一份SQLite、Gunicorn单worker/4线程、一次一个压测执行；4线程不代表4个任务。
