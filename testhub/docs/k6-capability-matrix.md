# k6 固定版本能力矩阵

矩阵版本：`2026-09-21.1`；更新：2026-09-21；候选适配器 `0.12.1`，本次变更尚未发布。

2026-09-21 补充：按时长模式到期停止接纳新的 WS 会话，已接纳会话可继续认证和有序命令，受原 `max_session_ms` 与全局收尾期限约束。全局收尾仍为请求超时加 5 秒，包含最多 1 秒真实关闭观察；连接/命令超时、断言、固定轮次硬截止、主动停止及后继 HTTP 截止均保留。回归见 `k6_websocket.test.mjs`、`k6_timed_drain.test.mjs`、`test_k6_websocket_integration`；无固定 k6 二进制或场景配置变更，旧报告不重算。以下带日期的记录保留其历史验收范围。

2026-09-20 补充：手工 K6 步骤已接入有限的 `k6/websockets` JSON 会话：首帧认证、有序命令、严格关联回执、心跳、有界关闭及独立指标。配置、原生及 Docker 隔离协议测试、页面与报告回归已通过；详见 [验收与发布边界](../../deployment/websocket-scenarios.md#验收与发布边界)。此证据不是线上平台已部署或容量验收。该子集不包含任意原生脚本、二进制/图片、自动重连、自动重发、命令级 SLA 或 SSE，不改变 HTTP 接口准备池的验证证明。

2026-09-18 补充（运行能力矩阵 `2026-09-18.1`、适配器 `0.7.5`）：按时长模式统一以 k6 场景起点计时，到期停止发送新请求，在途请求最多收尾至请求超时加 5 秒；固定轮数仍保持最长时限硬截止。当前收尾回归证据为 `test_k6_timed_drain.TimedDrainTests` 和 `deployment/test-runner/k6_timed_drain.test.mjs`。以下 2026-09-15 运行记录保留为历史证据，不能代表最新版本或业务服务的 1000 并发验收。

基线二进制：`2.2.1-0.20260915101307-0ae3c2989e5b`；源码：`0ae3c2989e5b7d24e5d18080aa72aa968ad5015f`；适配器：`0.12.1`。版本行仅接受 `k6` 或 Windows `k6.exe` 名称及固定版本，拒绝多行或附加文本；私密执行信封的 schema/adapter 整数版本仍为 `1`。

历史本地 Docker 基线镜像：`alpine:3.22`，ID `sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce`；Linux 二进制 SHA256：`b11bc3064bd0873a26b785f14d824a42c9b2a0e1c2a5870a2050f4d1dba2875b`。镜像标签可变，应以每次运行冻结的 ID 为准。

本候选新增 `bounded-execution`：连续步骤组、固定 VU 范围、尝试次数上限和最小间隔，入口 `steps[].execution_policy`，执行路径 `k6_script.js + k6_execution_policy.js`，报告区分真实调用与策略跳过。证据及发布边界见[专项执行](../../deployment/bounded-execution.md)。不实现随机权重或分布式配额。

## 状态合同

- **已验收**仅限该行的适配器子集和证据，不能扩称整个上游模块/所有方法已经可用。
- **待开发**表示入口未接入或缺少验收。源码里有此功能不等于平台已支持。
- **需要可选依赖**表示需要额外运行环境、扩展、基础设施或外部服务；当前仍待接入和验收。
- **该版本无此功能**表示固定版本未注册或明确移除。替代模块或扩展独立登记。
- **原生脚本入口尚未交付**。平台生成的 `k6_script.js` 不是任意 JS/TS 上传入口，JSON 参数透传也不构成完整支持。
- 未识别或变更版本使用独立 `verification.state=unverified/mismatch` 保持待核对，不沿用已验收状态，页面阻止调试/执行。
- 矩阵按注册点、选项和方法组索引覆盖范围，详细签名/子参数以固定源码为准；只接受第一节明确标明的子集。不存在“模块的全部方法已可用”的隐含承诺。

## 1. 平台常用能力及当前入口

此表对应能力 API `GET /api/perf-testing/engines/status/` 的 K6 `capabilities.items`。普通编辑器常显摘要，完整状态放在折叠详情；运行依赖或版本变化使入口禁用。

| ID | 官方名 / 固定版本出处 | 中文入口 | 配置路径 | 执行路径 | 报告路径 | 测试证据 | 表单状态 | 脚本状态 |
|---|---|---|---|---|---|---|---|---|
| debug | [per-vu-iterations (bounded debug)](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/per_vu_iterations.go) | 调试（1 用户 / 1 轮） | debug → 固定 1 VU / 1 轮 / 最长 60 秒 | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_debug | 已验收 | 待开发 |
| constant-vus | [constant-vus](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/constant_vus.go) | 固定并发 / 按时长 | load_config.concurrency,duration; iterations_per_vu=0 | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_engine.RealK6Tests.test_duration_cutoff_distinguishes_started_from_completed:93（历史时限验证，本块未重跑） | 已验收 | 待开发 |
| per-vu-iterations | [per-vu-iterations](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/per_vu_iterations.go) | 固定并发 / 每用户轮数 | load_config.concurrency,iterations_per_vu,duration | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_debug | 已验收 | 待开发 |
| shared-iterations | [shared-iterations](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/shared_iterations.go) | 共享总轮数（待开发） | 尚未接入；上游 scenarios.*.iterations | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| ramping-vus | [ramping-vus](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/ramping_vus.go) | 阶梯 / 尖峰负载（待开发） | 尚未接入；上游 scenarios.*.stages | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| constant-arrival-rate | [constant-arrival-rate](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/constant_arrival_rate.go) | 固定迭代到达率（待开发） | 尚未接入；上游 scenarios.*.rate,timeUnit | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| ramping-arrival-rate | [ramping-arrival-rate](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/ramping_arrival_rate.go) | 阶段迭代到达率（待开发） | 尚未接入；上游 scenarios.*.stages,timeUnit | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| externally-controlled | [externally-controlled](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor) | 外部动态 VU 执行器 | 该版本未注册 | 尚未接入 | 尚未接入 | 固定源码执行器注册清单：无此项 | 该版本无此功能 | 该版本无此功能 |
| http-basic | [k6/http.request (NONE / JSON)](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/js/modules/k6/http/request.go) | 顺序 HTTP 请求 | steps[].method,url,headers,params,body_type,body | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_engine.AdapterContractTests | 已验收 | 待开发 |
| csv-identity | [k6/data.SharedArray (adapter CSV identity)](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/data/data.go) | CSV 独立用户 | variables[].type=CSV,data_file_id,column | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_engine; test_k6_worker | 已验收 | 待开发 |
| assertions-basic | [Response.status / Response.json (adapter equality)](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/js/modules/k6/http/response.go) | 状态码 / 基础 JSONPath | steps[].assertions,extractors | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_engine.AdapterContractTests | 已验收 | 待开发 |
| per-vu-setup | [VU-local state (adapter login once per VU)](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/runner.go) | 每用户前置登录 | steps[].is_setup=true | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_engine; test_k6_debug | 已验收 | 待开发 |
| stop | [run / process termination](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/run.go) | 运行 / 停止 | scenarios/:id/execute; executions/:id/stop | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_stop_race | 已验收 | 待开发 |
| report | [console / adapter request events](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/console.go) | 每接口报告 / 原始 CSV | 执行监控 / 执行报告 | apps/perf_testing/engines/k6_engine.py + k6_script.js | 执行监控 / 执行报告 / 原始 CSV | test_k6_report; test_k6_engine | 已验收 | 待开发 |
| native-script | [JavaScript / TypeScript / lifecycle](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/bundle.go) | 原生脚本（待开发） | 尚未接入；不能通过 script_ref 或 JSON 参数启用 | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| multi-scenario | [options.scenarios / http.batch](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go) | 多场景 / 权重 / 并行（待开发） | 尚未接入 | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| native-metrics | [Counter / Gauge / Rate / Trend / thresholds](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/metrics/metrics.go) | 原生指标与阈值（待开发） | 尚未接入；平台断言不等于原生 checks/thresholds | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| http-advanced | [HTTP files / redirects / TLS / DNS](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go) | 高级 HTTP（待开发） | 尚未完整接入；已接入子集见矩阵 | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| proxy | [HTTP_PROXY / HTTPS_PROXY](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/runner.go#L204) | HTTP 代理 | runtime_config.proxy（Docker 禁止；原生待验收） | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| websocket | [k6/websockets（有界 JSON 关联会话）](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/websockets/websockets.go) | 手工 WebSocket JSON 会话 | steps[].protocol=WEBSOCKET、websocket_config；首帧认证与 id/ok 回执 | k6_engine.py + k6_script.js + k6_websocket.js | 会话/命令指标、诊断、原始 CSV | test_websocket_steps、test_k6_websocket_integration、test_k6_websocket_metrics、k6_websocket.test.mjs；本地隔离验收，未发布 | 已验收 | 待开发 |
| grpc | [k6/net/grpc](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/grpc/grpc.go) | gRPC（待开发） | 尚未接入 | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 待开发 | 待开发 |
| browser | [k6/browser](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/module.go) | 浏览器与混合压测 | 待接入 Chromium runner | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 需要可选依赖 | 需要可选依赖 |
| cloud | [Grafana Cloud k6](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/cloud.go) | Cloud 托管（可选外部服务） | 待接入外部账号与服务 | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 需要可选依赖 | 需要可选依赖 |
| extensions | [k6/x/* / output / secret-source extensions](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/ext/ext.go) | 社区扩展 | 待登记和验收各扩展及其版本 | 尚未接入 | 尚未接入 | 待开发；无运行验收 | 需要可选依赖 | 需要可选依赖 |

### 已接入子集的边界

当前只生成一个 business 场景：按时间 constant-vus，配置时间包含前置和认证，截止后仅收尾在途请求；每人固定轮次 per-vu-iterations，duration 为最大时限，gracefulStop 为 0s。每人轮数不是 shared-iterations。调试 1 VU / 1 轮 / 最长 60 秒，原场景不变。

HTTP 仅标准 GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS，顺序执行，NONE/JSON 正文、键值请求头/查询参数、常量/CSV变量、固定毫秒思考时间。状态码与基础 JSONPath 相等断言/提取仅支持 `$.field.child[数字]`；无通配符、过滤、正则、动态预期值。前置步骤在每个 VU 内执行一次，**不是**原生全局 setup()。CSV 每用户固定一行，多轮身份不变。

WebSocket 仅手工 K6 步骤，使用与环境同源的 WS(S) 地址；STATIC/BEARER 或 LOGIN/BEARER Token 只通过首 auth 帧传入。每次步骤建立新连接，认证后执行 1–64 条有限 JSON 命令，要求同 id 且布尔 ok=true，再执行命令级基础 JSONPath 规则。没有跨步骤连接复用或自动重连/重发。连接、认证、命令、心跳分列；会话耗时包含连接至关闭，命令 RTT 独立统计。混合场景全局速率是业务步骤完成速率，不能声称消息 QPS。详细边界与安全只读模板见 [客服 WebSocket 场景](../../deployment/websocket-scenarios.md)。

verify_ssl、keep_alive、timeout 已有传递路径，完整 TLS/连接参数和行为验收仍待后续块。原生 proxy 存在传递路径但未验收，页面暂不开放；Docker 禁止代理。

报告使用适配器请求事件、CSV与估算直方图，区分前置/业务和发起/完成/未完成。此路径不代表所有原生指标、checks/thresholds、handleSummary 或输出器已接入。probe-10 是固定轮数短测；constant-vus 的历史时限验证证据为 RealK6Tests.test_duration_cutoff_distinguishes_started_from_completed（历史测试日志第93行），本块不重跑真实负载。


## 2. 执行器子配置与运行控制

六个注册执行器已在第一节列齐。REST 状态端点仍存在，但不代表 externally-controlled 仍存在。
| ID | 官方名 / 固定版本出处 | 中文入口 | 配置路径 | 执行路径 | 报告路径 | 测试证据 | 表单状态 | 脚本状态 |
|---|---|---|---|---|---|---|---|---|
| LOAD-SCENARIO | [exec/startTime/env/tags/gracefulStop/options](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/base_config.go) | 原生脚本 / 高级配置（待开发） | scenarios.*；当前仅固定 business，其余自定义配置待开发 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| LOAD-ARRIVAL | [rate/startRate/timeUnit/preAllocatedVUs/maxVUs/stages](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/ramping_arrival_rate.go) | 原生脚本 / 高级配置（待开发） | 迭代到达率，不是精确HTTP RPS | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| LOAD-RAMP | [startVUs/stages/gracefulRampDown](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/executor/ramping_vus.go) | 原生脚本 / 高级配置（待开发） | 阶段负载和宽限退出 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| LOAD-SEGMENT | [executionSegment/executionSegmentSequence](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/execution_segment.go) | 原生脚本 / 高级配置（待开发） | 切片、身份与集中统计待开发 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CTRL-REST | [status/metrics/groups/setup/teardown](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/api/v1/routes.go) | 原生脚本 / 高级配置（待开发） | 原生 REST 控制尚未桥接；停止/暂停字段见 status_routes.go | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CTRL-LIVE-VUS | [live VU configuration updates](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/api/v1/status_routes.go) | 原生脚本 / 高级配置（待开发） | 当前明确拒绝 live VUs/VUsMax 更新 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| CTRL-LEGACY-CLI | [pause/resume/scale CLI](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/root.go) | 原生脚本 / 高级配置（待开发） | 当前根命令未注册旧控制命令 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |

## 3. Options 全部45个字段

此表按完整原生选项入口计状态；第一节有限映射不构成完整选项验收。json:"-" 的两项是 ConsoleOutput 和 LocalIPs。
| ID | 官方名 / 固定版本出处 | 中文入口 | 配置路径 | 执行路径 | 报告路径 | 测试证据 | 表单状态 | 脚本状态 |
|---|---|---|---|---|---|---|---|---|
| OPT-paused | [paused](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L232) | 暂停启动（未完整接入） | options.paused | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-vus | [vus](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L236) | 用户数（未完整接入） | options.vus；有限映射见第一节 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-duration | [duration](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L237) | 持续时间（未完整接入） | options.duration；有限映射见第一节 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-iterations | [iterations](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L238) | 总轮数（未完整接入） | options.iterations | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-stages | [stages](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L239) | 阶段（未完整接入） | options.stages | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-scenarios | [scenarios](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L247) | 多场景（未完整接入） | options.scenarios；有限映射见第一节 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-executionSegment | [executionSegment](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L248) | 切片（未完整接入） | options.executionSegment | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-executionSegmentSequence | [executionSegmentSequence](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L249) | 切片序列（未完整接入） | options.executionSegmentSequence | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-noSetup | [noSetup](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L252) | 跳过setup（未完整接入） | options.noSetup | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-setupTimeout | [setupTimeout](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L253) | setup超时（未完整接入） | options.setupTimeout | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-noTeardown | [noTeardown](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L254) | 跳过teardown（未完整接入） | options.noTeardown | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-teardownTimeout | [teardownTimeout](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L255) | teardown超时（未完整接入） | options.teardownTimeout | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-handleSummaryTimeout | [handleSummaryTimeout](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L256) | 摘要回调超时（未完整接入） | options.handleSummaryTimeout | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-rps | [rps](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L259) | HTTP速率上限（未完整接入） | options.rps | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-dns | [dns](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L262) | DNS配置（未完整接入） | options.dns | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-maxRedirects | [maxRedirects](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L265) | 重定向次数（未完整接入） | options.maxRedirects；当前固定0，不允许开启 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-userAgent | [userAgent](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L268) | User-Agent（未完整接入） | options.userAgent | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-batch | [batch](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L271) | 批量并行数（未完整接入） | options.batch | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-batchPerHost | [batchPerHost](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L272) | 每主机批量数（未完整接入） | options.batchPerHost | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-httpDebug | [httpDebug](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L275) | HTTP调试日志（未完整接入） | options.httpDebug | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-insecureSkipTLSVerify | [insecureSkipTLSVerify](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L278) | TLS校验（未完整接入） | options.insecureSkipTLSVerify；已有verify_ssl反向映射，完整TLS待验收 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-tlsCipherSuites | [tlsCipherSuites](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L281) | TLS密码套件（未完整接入） | options.tlsCipherSuites | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-tlsVersion | [tlsVersion](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L282) | TLS版本（未完整接入） | options.tlsVersion | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-tlsAuth | [tlsAuth](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L283) | 客户端证书（未完整接入） | options.tlsAuth | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-tlsAIAFetch | [tlsAIAFetch](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L289) | TLS AIA获取（未完整接入） | options.tlsAIAFetch | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-throw | [throw](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L292) | 请求异常（未完整接入） | options.throw | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-thresholds | [thresholds](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L297) | 原生阈值（未完整接入） | options.thresholds | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-blacklistIPs | [blacklistIPs](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L300) | IP黑名单（未完整接入） | options.blacklistIPs | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-blockHostnames | [blockHostnames](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L303) | 阻止主机（未完整接入） | options.blockHostnames | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-hosts | [hosts](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L306) | 主机映射（未完整接入） | options.hosts | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-noConnectionReuse | [noConnectionReuse](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L309) | 连接复用（未完整接入） | options.noConnectionReuse；已有keep_alive反向映射，完整连接行为待验收 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-noVUConnectionReuse | [noVUConnectionReuse](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L314) | 跨轮连接复用（未完整接入） | options.noVUConnectionReuse | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-minIterationDuration | [minIterationDuration](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L318) | 最短轮次时长（未完整接入） | options.minIterationDuration | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-cloud | [cloud](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L321) | Cloud配置（未完整接入） | options.cloud | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OPT-ext | [ext](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L325) | 扩展配置（未完整接入） | options.ext | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OPT-summaryTrendStats | [summaryTrendStats](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L328) | Trend摘要（未完整接入） | options.summaryTrendStats | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-summaryTimeUnit | [summaryTimeUnit](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L331) | 摘要时间单位（未完整接入） | options.summaryTimeUnit | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-systemTags | [systemTags](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L335) | 系统标签（未完整接入） | options.systemTags；当前适配器固定集合 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-tags | [tags](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L338) | 运行标签（未完整接入） | options.tags | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-metricSamplesBufferSize | [metricSamplesBufferSize](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L341) | 指标缓冲（未完整接入） | options.metricSamplesBufferSize | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-noCookiesReset | [noCookiesReset](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L344) | 跨轮Cookie（未完整接入） | options.noCookiesReset | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-discardResponseBodies | [discardResponseBodies](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L347) | 丢弃响应体（未完整接入） | options.discardResponseBodies | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-ConsoleOutput | [ConsoleOutput](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L350) | 控制台文件（未完整接入） | CLI / env ConsoleOutput | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-LocalIPs | [LocalIPs](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L353) | 本地源IP（未完整接入） | CLI / env LocalIPs | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPT-features | [features](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/options.go#L357) | 功能开关（未完整接入） | options.features | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |

## 4. 模块注册与方法组

固定注册表25项：15个稳定模块、3个experimental、1个deprecated别名、6个removed占位项。removedModule会抛错；目录中有文件不等于可import。
| ID | 官方名 / 固定版本出处 | 中文入口 | 配置路径 | 执行路径 | 报告路径 | 测试证据 | 表单状态 | 脚本状态 |
|---|---|---|---|---|---|---|---|---|
| MOD-k6 | [k6 [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L33) | 原生模块（未完整接入） | check/fail/group/randomSeed/sleep；仅固定思考时间sleep子集见第一节 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/browser | [k6/browser [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L34) | 原生模块（未完整接入） | browser/chromium/devices；对象方法见第6节 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| MOD-k6/crypto | [k6/crypto [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L35) | 原生模块（未完整接入） | createHash/createHMAC/hmac/md4/md5/randomBytes/ripemd160/sha1/sha256/sha384/sha512/sha512_224/sha512_256/hexEncode | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/crypto/x509 | [k6/crypto/x509 [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L36) | 原生模块（未完整接入） | parse/getAltNames/getIssuer/getSubject | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/data | [k6/data [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L37) | 原生模块（未完整接入） | SharedArray；仅CSV身份子集已验收 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/encoding | [k6/encoding [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L38) | 原生模块（未完整接入） | b64encode/b64decode | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/execution | [k6/execution [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L39) | 原生模块（未完整接入） | scenario/instance/test.abort/test.fail/test.options/vu及tags/metadata | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/html | [k6/html [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L40) | 原生模块（未完整接入） | parseHTML/Selection选择、遍历、属性、值与映射 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/http | [k6/http [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L41) | 原生模块（未完整接入） | request/get/post/put/patch/del/head/options/asyncRequest/batch/file/CookieJar/cookieJar/url/expectedStatuses/setResponseCallback；仅顺序子集已验收 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/net/grpc | [k6/net/grpc [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L42) | 原生模块（未完整接入） | Client.load/loadProtoset/connect/healthCheck/invoke/asyncInvoke/close；Stream消息、事件、结束；Status/HealthCheck常量 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/metrics | [k6/metrics [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L43) | 原生模块（未完整接入） | Counter/Gauge/Trend/Rate/add | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/secrets | [k6/secrets [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L44) | 原生模块（未完整接入） | default.get；秘密源单列 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/timers | [k6/timers [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L45) | 原生模块（未完整接入） | setTimeout/clearTimeout/setInterval/clearInterval | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/websockets | [k6/websockets [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L46) | 原生模块（未完整接入） | 完整 WebSocket/Blob、binaryType、缓冲仍未开放 | 有限 JSON 表单子集见 websocket 行 | 子集会话及命令指标见 websocket 行 | 子集已做本地隔离验收；完整模块无验收 | 待开发 | 待开发 |
| MOD-k6/ws | [k6/ws [available]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L47) | 原生模块（未完整接入） | connect；Socket.on/send/sendBinary/ping/setTimeout/setInterval/close | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/experimental/csv | [k6/experimental/csv [experimental]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L50) | 原生模块（未完整接入） | parse/Parser.next；不是平台CSV上传入口 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/experimental/fs | [k6/experimental/fs [experimental]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L51) | 原生模块（未完整接入） | open/File.read/seek/stat/SeekMode | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/experimental/streams | [k6/experimental/streams [experimental]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L52) | 原生模块（未完整接入） | ReadableStream/CountQueuingStrategy/ReadableStreamDefaultReader/WritableStream/WritableStreamDefaultWriter | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/experimental/websockets | [k6/experimental/websockets [deprecated]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L55) | 原生模块（未完整接入） | deprecated别名；使用k6/websockets | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| MOD-k6/experimental/redis | [k6/experimental/redis [removed]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L58) | 原生模块（未完整接入） | removed；替代k6/x/redis为可选扩展 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| MOD-k6/experimental/browser | [k6/experimental/browser [removed]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L64) | 原生模块（未完整接入） | removed；改用k6/browser | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| MOD-k6/experimental/grpc | [k6/experimental/grpc [removed]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L69) | 原生模块（未完整接入） | removed；改用k6/net/grpc | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| MOD-k6/experimental/timers | [k6/experimental/timers [removed]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L73) | 原生模块（未完整接入） | removed；改用全局timers | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| MOD-k6/experimental/tracing | [k6/experimental/tracing [removed]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L76) | 原生模块（未完整接入） | removed；改用纯JS工具包，单独固定依赖 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| MOD-k6/experimental/webcrypto | [k6/experimental/webcrypto [removed]](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/jsmodules.go#L80) | 原生模块（未完整接入） | removed；改用全局crypto | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |

## 5. 方法组索引、生命周期与全局API

这些源码索引定义未接入方法及子参数的完整维护范围；不把方法组中的所有方法冒称可用。
| ID | 官方名 / 固定版本出处 | 中文入口 | 配置路径 | 执行路径 | 报告路径 | 测试证据 | 表单状态 | 脚本状态 |
|---|---|---|---|---|---|---|---|---|
| METHOD-HTTP | [HTTP导出/常量/请求参数](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/js/modules/k6/http/http.go) | 原生脚本 / 高级配置（待开发） | http.go导出与常量；request.go中的headers/cookies/jar/redirects/tags/auth/timeout/responseType/compression、上传/二进制正文 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METHOD-RESPONSE | [Response与CookieJar](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/js/modules/k6/http/response.go) | 原生脚本 / 高级配置（待开发） | status/body/headers/cookies/timings/error/error_code/proto/remote_ip/remote_port/TLS/OCSP；json/html/submitForm/clickLink；cookiejar.go的cookiesForURL/set/clear/delete | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METHOD-HTML | [Selection methods](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/js/modules/k6/html/html.go) | 原生脚本 / 高级配置（待开发） | 选择、遍历、过滤、内容、属性、表单值、映射/切片；全部方法以此固定文件为索引 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METHOD-EXEC | [Execution info/control](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/execution/execution.go) | 原生脚本 / 高级配置（待开发） | scenario/instance/test/vu属性、test.abort/fail、VU tags/metadata | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METHOD-GRPC | [Client/Stream/params](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/grpc/client.go) | 原生脚本 / 高级配置（待开发） | grpc.go导出与常量；client.go调用/加载/protoset/reflection/healthCheck；stream.go流；params.go含metadata/tags/timeout/discardResponseMessage/TLS/authority/消息大小 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METHOD-WS | [Legacy WS socket](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/ws/ws.go) | 原生脚本 / 高级配置（待开发） | 连接/消息/事件/定时/关闭；headers/tags/jar/compression | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METHOD-WEBSOCKETS | [WebSocket/Blob](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/websockets/websockets.go) | 原生脚本 / 高级配置（待开发） | 构造、事件与消息；params.go和此目录对象方法 | 有限 JSON 表单子集见 websocket 行；不开放任意方法 | 子集报告见 websocket 行 | 子集已做本地隔离验收；完整方法组无验收 | 待开发 | 待开发 |
| METHOD-CRYPTO | [Crypto exports](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/crypto/crypto.go) | 原生脚本 / 高级配置（待开发） | 哈希/HMAC/随机字节及x509子包导出，输出编码与对象方法 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METHOD-EXPERIMENTAL | [CSV/FS/Streams](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/experimental) | 原生脚本 / 高级配置（待开发） | csv/module.go、fs/module.go、streams/module.go为导出与子对象方法索引 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SCRIPT-LIFE | [init/setup/VU exec/teardown/handleSummary](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/runner.go) | 原生脚本 / 高级配置（待开发） | 原生生命周期、返回数据、异常及超时；当前平台前置步骤不是全局setup | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SCRIPT-LANGUAGE | [JS/TS/ES modules/CommonJS](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/compiler/compiler.go) | 原生脚本 / 高级配置（待开发） | 编译转译；依赖导入和本地/远程包版本冻结尚未接入 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SCRIPT-GLOBAL | [open/__ENV/__VU/__ITER/console/import.meta.resolve](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/bundle.go) | 原生脚本 / 高级配置（待开发） | init open和require；__ITER见runner.go；日志方法见console.go；标准Sobek全局不等于Node/浏览器全局全集 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SCRIPT-COMPAT | [base/extended compatibility](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/bundle.go) | 原生脚本 / 高级配置（待开发） | extended的global为globalThis别名 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SCRIPT-ENCODING | [TextEncoder/TextDecoder](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/vendor/github.com/grafana/sobek-webapi-encoding/register.go) | 原生脚本 / 高级配置（待开发） | encode/encodeInto/decode及选项，以固定vendor实现为准 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SCRIPT-TIMERS | [global timers](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/timers/timers.go) | 原生脚本 / 高级配置（待开发） | setTimeout/clearTimeout/setInterval/clearInterval | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SCRIPT-WEBCRYPTO | [global crypto/SubtleCrypto/CryptoKey](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/webcrypto/module.go) | 原生脚本 / 高级配置（待开发） | getRandomValues/randomUUID；subtle decrypt/deriveBits/deriveKey/digest/encrypt/exportKey/generateKey/importKey/sign/unwrapKey/verify/wrapKey | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |

## 6. 浏览器对象方法组与混合负载

需要Chromium或受支持CDP浏览器、资源配额、产物权限及独立验收。固定mapping文件只用作范围索引。
| ID | 官方名 / 固定版本出处 | 中文入口 | 配置路径 | 执行路径 | 报告路径 | 测试证据 | 表单状态 | 脚本状态 |
|---|---|---|---|---|---|---|---|---|
| BROWSER-browser_context | [browser_context](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/browser_context_mapping.go) | 浏览器/混合压测（待接入） | addCookies, addInitScript, browser, clearCookies, clearPermissions, close, cookies, grantPermissions 等；完整导出组见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-browser | [browser](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/browser_mapping.go) | 浏览器/混合压测（待接入） | context, closeContext, isConnected, newContext, userAgent, version, newPage | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-chromium | [chromium](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/chromium_mapping.go) | 浏览器/混合压测（待接入） | connectOverCDP | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-console_message | [console_message](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/console_message_mapping.go) | 浏览器/混合压测（待接入） | args, page, text, type | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-element_handle | [element_handle](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/element_handle_mapping.go) | 浏览器/混合压测（待接入） | boundingBox, check, click, contentFrame, dblclick, dispatchEvent, fill, focus 等；完整导出组见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-frame_locator | [frame_locator](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/frame_locator_mapping.go) | 浏览器/混合压测（待接入） | getByAltText, getByLabel, getByPlaceholder, getByRole, getByTestId, getByText, getByTitle, locator 等；完整导出组见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-frame | [frame](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/frame_mapping.go) | 浏览器/混合压测（待接入） | check, childFrames, click, content, dblclick, dispatchEvent, evaluate, evaluateHandle 等；完整导出组见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-js_handle | [js_handle](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/js_handle_mapping.go) | 浏览器/混合压测（待接入） | asElement, dispose, evaluate, evaluateHandle, getProperties, jsonValue | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-keyboard | [keyboard](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/keyboard_mapping.go) | 浏览器/混合压测（待接入） | down, up, press, type, insertText | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-locator | [locator](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/locator_mapping.go) | 浏览器/混合压测（待接入） | all, boundingBox, clear, click, contentFrame, count, dblclick, evaluate 等；完整导出组见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-metric_event | [metric_event](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/metric_event_mapping.go) | 浏览器/混合压测（待接入） | tag | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-mouse | [mouse](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/mouse_mapping.go) | 浏览器/混合压测（待接入） | click, dblClick, down, up, move | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-page | [page](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/page_mapping.go) | 浏览器/混合压测（待接入） | bringToFront, check, click, close, content, context, dblclick, dispatchEvent 等；完整导出组见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-request | [request](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/request_mapping.go) | 浏览器/混合压测（待接入） | allHeaders, frame, headerValue, headers, headersArray, isNavigationRequest, method, postData 等；完整导出组见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-response | [response](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/response_mapping.go) | 浏览器/混合压测（待接入） | allHeaders, body, frame, headerValue, headerValues, headers, headersArray, json 等；完整导出组见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-route | [route](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/route_mapping.go) | 浏览器/混合压测（待接入） | abort, continue, fulfill, request | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-touchscreen | [touchscreen](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/touchscreen_mapping.go) | 浏览器/混合压测（待接入） | tap | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| BROWSER-worker | [worker](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/browser/worker_mapping.go) | 浏览器/混合压测（待接入） | url | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |

## 7. 指标、标签与原生阈值

25个内置指标、3个gRPC流指标和9个浏览器指标。平台适配器计数/耗时不等于这些原生指标全部被采集并展示。
| ID | 官方名 / 固定版本出处 | 中文入口 | 配置路径 | 执行路径 | 报告路径 | 测试证据 | 表单状态 | 脚本状态 |
|---|---|---|---|---|---|---|---|---|
| METRIC-vus | [vus](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L5) | 原生指标报告（待接入） | k6 native vus | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-vus_max | [vus_max](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L6) | 原生指标报告（待接入） | k6 native vus_max | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-iterations | [iterations](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L7) | 原生指标报告（待接入） | k6 native iterations | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-iteration_duration | [iteration_duration](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L8) | 原生指标报告（待接入） | k6 native iteration_duration | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-dropped_iterations | [dropped_iterations](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L9) | 原生指标报告（待接入） | k6 native dropped_iterations | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-checks | [checks](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L11) | 原生指标报告（待接入） | k6 native checks | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-group_duration | [group_duration](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L12) | 原生指标报告（待接入） | k6 native group_duration | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_reqs | [http_reqs](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L14) | 原生指标报告（待接入） | k6 native http_reqs | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_req_failed | [http_req_failed](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L15) | 原生指标报告（待接入） | k6 native http_req_failed | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_req_duration | [http_req_duration](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L16) | 原生指标报告（待接入） | k6 native http_req_duration | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_req_blocked | [http_req_blocked](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L17) | 原生指标报告（待接入） | k6 native http_req_blocked | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_req_connecting | [http_req_connecting](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L18) | 原生指标报告（待接入） | k6 native http_req_connecting | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_req_tls_handshaking | [http_req_tls_handshaking](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L19) | 原生指标报告（待接入） | k6 native http_req_tls_handshaking | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_req_sending | [http_req_sending](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L20) | 原生指标报告（待接入） | k6 native http_req_sending | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_req_waiting | [http_req_waiting](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L21) | 原生指标报告（待接入） | k6 native http_req_waiting | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-http_req_receiving | [http_req_receiving](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L22) | 原生指标报告（待接入） | k6 native http_req_receiving | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-ws_sessions | [ws_sessions](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L24) | 原生指标报告（待接入） | k6 native ws_sessions | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-ws_msgs_sent | [ws_msgs_sent](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L25) | 原生指标报告（待接入） | k6 native ws_msgs_sent | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-ws_msgs_received | [ws_msgs_received](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L26) | 原生指标报告（待接入） | k6 native ws_msgs_received | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-ws_ping | [ws_ping](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L27) | 原生指标报告（待接入） | k6 native ws_ping | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-ws_session_duration | [ws_session_duration](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L28) | 原生指标报告（待接入） | k6 native ws_session_duration | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-ws_connecting | [ws_connecting](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L29) | 原生指标报告（待接入） | k6 native ws_connecting | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-grpc_req_duration | [grpc_req_duration](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L31) | 原生指标报告（待接入） | k6 native grpc_req_duration | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-data_sent | [data_sent](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L33) | 原生指标报告（待接入） | k6 native data_sent | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-data_received | [data_received](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/builtin.go#L34) | 原生指标报告（待接入） | k6 native data_received | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-grpc_streams | [grpc_streams](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/grpc/metrics.go) | gRPC报告（待接入） | k6 native grpc_streams | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-grpc_streams_msgs_sent | [grpc_streams_msgs_sent](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/grpc/metrics.go) | gRPC报告（待接入） | k6 native grpc_streams_msgs_sent | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-grpc_streams_msgs_received | [grpc_streams_msgs_received](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/grpc/metrics.go) | gRPC报告（待接入） | k6 native grpc_streams_msgs_received | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-browser_web_vital_ttfb | [browser_web_vital_ttfb](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_web_vital_ttfb | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-browser_web_vital_lcp | [browser_web_vital_lcp](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_web_vital_lcp | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-browser_web_vital_cls | [browser_web_vital_cls](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_web_vital_cls | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-browser_web_vital_inp | [browser_web_vital_inp](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_web_vital_inp | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-browser_web_vital_fcp | [browser_web_vital_fcp](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_web_vital_fcp | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-browser_data_sent | [browser_data_sent](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_data_sent | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-browser_data_received | [browser_data_received](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_data_received | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-browser_http_req_duration | [browser_http_req_duration](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_http_req_duration | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-browser_http_req_failed | [browser_http_req_failed](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/browser/k6ext/metrics.go) | 浏览器报告（待接入） | k6 native browser_http_req_failed | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| METRIC-CUSTOM | [Counter/Rate/Gauge/Trend](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/js/modules/k6/metrics/metrics.go) | 原生脚本 / 高级配置（待开发） | 任意指标、add、类型与标签 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-TAGS | [tags/metadata/submetrics](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/tags.go) | 原生脚本 / 高级配置（待开发） | 系统/用户标签、过滤子指标、全局和场景/请求范围 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| METRIC-THRESHOLDS | [thresholds/abortOnFail/delayAbortEval](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/metrics/thresholds.go) | 原生脚本 / 高级配置（待开发） | 全局/接口原生阈值与中止；平台SLA不代替此能力 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |

## 8. CLI、输出、秘密源和可选依赖

当前root注册命令和输出构造器为准。已移除输出仍可能保留抛错构造器；不属于可用输出。
| ID | 官方名 / 固定版本出处 | 中文入口 | 配置路径 | 执行路径 | 报告路径 | 测试证据 | 表单状态 | 脚本状态 |
|---|---|---|---|---|---|---|---|---|
| CLI-run | [k6 run](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/run.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-archive | [k6 archive](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/archive.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-inspect | [k6 inspect](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/inspect.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-new | [k6 new](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/new.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-deps | [k6 deps](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/deps.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-stats | [k6 stats](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/stats.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-version | [k6 version](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/version.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-features | [k6 features](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/features.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-x | [k6 x](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/x_registry.go) | 原生脚本 / 高级配置（待开发） | 网页尚未开放任意CLI参数；run/version当前仅内部固定调用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-cloud | [k6 cloud/cloud run/local-execution](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/cloud.go) | Cloud托管 | 账号、网络、凭据及服务额度 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| CLI-RUNTIME | [runtime flags](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/runtime_options.go) | 原生脚本 / 高级配置（待开发） | include-system-env-vars/compatibility-mode/type/env/no-thresholds/summary-mode/summary-export/new-machine-readable-summary/traces-output | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-CONFIG | [config/JSON flags](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/config.go) | 原生脚本 / 高级配置（待开发） | out/linger/no-usage-report/features；WebDashboard/noArchiveUpload/collectors；当前固定禁用usage report | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-ROOT | [root flags](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/root.go) | 原生脚本 / 高级配置（待开发） | secret-source/log-output/log-format/config/no-color/verbose/quiet/address/profiling；全部注册与参数见源码 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| CLI-OPTIONS | [option flags/--once](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/options.go) | 原生脚本 / 高级配置（待开发） | Options CLI映射；--once是原生1VU1轮快捷方式，平台debug另有60秒上界 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| FEATURE-FLAGS | [registered features](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/features) | 原生脚本 / 高级配置（待开发） | 按固定注册目录记录功能、默认和生命周期；features入口本身不表示平台可启用 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OUTPUT-json | [json](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 原生输出待集成；平台原始CSV不是原生CSV输出器 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OUTPUT-csv | [csv](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 原生输出待集成；平台原始CSV不是原生CSV输出器 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OUTPUT-influxdb | [influxdb](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 原生输出待集成；平台原始CSV不是原生CSV输出器 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OUTPUT-experimental-prometheus-rw | [experimental-prometheus-rw](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 原生输出待集成；平台原始CSV不是原生CSV输出器 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OUTPUT-web-dashboard | [web-dashboard](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 原生输出待集成；平台原始CSV不是原生CSV输出器 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OUTPUT-experimental-opentelemetry | [experimental-opentelemetry](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 原生输出待集成；experimental-opentelemetry为deprecated别名，推荐opentelemetry；平台CSV不是原生CSV输出器 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OUTPUT-opentelemetry | [opentelemetry](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 原生输出待集成；平台原始CSV不是原生CSV输出器 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OUTPUT-cloud | [cloud](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 原生输出待集成；平台原始CSV不是原生CSV输出器 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OUTPUT-kafka | [kafka](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 已移除；替代扩展另行登记 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| OUTPUT-statsd | [statsd](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 已移除；替代扩展另行登记 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| OUTPUT-datadog | [datadog](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生输出/导出（待接入） | 已移除；替代扩展另行登记 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 该版本无此功能 | 该版本无此功能 |
| OUTPUT-SUMMARY | [terminal/handleSummary/summary-export](https://github.com/grafana/k6/tree/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/lib/summary) | 原生脚本 / 高级配置（待开发） | 自定义终端/文件/JSON摘要；当前handleSummary为空 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OUTPUT-TRACES | [OpenTelemetry traces](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/runtime_options.go) | Trace输出 | traces-output=otel及外部接收端 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| SECRET-file | [secret source file](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/secretsource/file/file.go) | 秘密源（待接入） | k6/secrets与--secret-source；cloud自动注入，禁止用户显式指定cloud源 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SECRET-mock | [secret source mock](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/secretsource/mock/mock.go) | 秘密源（待接入） | k6/secrets与--secret-source；cloud自动注入，禁止用户显式指定cloud源 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| SECRET-url | [secret source url](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/secretsource/url/url.go) | 秘密源（待接入） | k6/secrets与--secret-source；cloud自动注入，禁止用户显式指定cloud源 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| SECRET-cloud | [secret source cloud](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/secretsource/cloud/cloud.go) | 秘密源（待接入） | k6/secrets与--secret-source；cloud自动注入，禁止用户显式指定cloud源 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| EXT-JS | [k6/x/*/xk6 JS extensions](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/ext/ext.go) | 原生脚本 / 高级配置（待开发） | 逐扩展固定包、构建、版本、权限与测试；包括redis替代扩展 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| EXT-OUTPUT | [output extensions](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/outputs.go) | 原生脚本 / 高级配置（待开发） | 逐输出器固定版本/凭据/兼容性/测试 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| EXT-SECRETS | [secret-source extensions](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/secretsource/extension.go) | 原生脚本 / 高级配置（待开发） | 逐秘密源固定版本/权限/脱敏/测试 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OPS-WORKER | [isolated workers/distributed execution](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/execution_segment.go) | 原生脚本 / 高级配置（待开发） | 本地单进程不等于独立多worker、全局账号租约或集中分位数 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |
| OPS-OPERATOR | [k6 Operator](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/lib/execution_segment.go) | 原生脚本 / 高级配置（待开发） | Operator是外部集成，并非此引擎内置；其版本与Kubernetes节点另行验收 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 需要可选依赖 | 需要可选依赖 |
| OPS-GOVERNANCE | [CI/schedules/audit/retention/monitoring](https://github.com/grafana/k6/blob/0ae3c2989e5b7d24e5d18080aa72aa968ad5015f/internal/cmd/run.go) | 原生脚本 / 高级配置（待开发） | 平台集成层：原模块结构保留；k6统一预检/冻结/治理待后续块 | 尚未接入 | 尚未接入 | 固定源码核对；无平台运行验收 | 待开发 | 待开发 |

## 9. 升级与验收维护

1. 固定新二进制、源码commit、适配器版本、镜像ID和二进制hash，保留旧矩阵与历史运行冻结信息。工作目录HEAD不作为任意binary的出处。
2. 对比执行器、Options/CLI字段、模块方法组、全局注册、原生/协议/浏览器指标、outputs/secrets/features；记录新增、移除和语义变化，完成相应入口和异常测试后才提升状态。
3. API的verification.differences只报告版本差异，不会自动推导新增能力已支持。Docker需核验原fingerprint；变化或探测失败给出禁用原因。status使用原15秒缓存，不读Git/源码目录，不返回账号、密码、快照、网络名称或本机文件路径。
4. 表单与脚本分别验收；更新本文、CAPABILITIES与证据。外部扩展按实际引入逐一登记，不宣称所有社区扩展可用。Cloud是可选外部服务，不计入免费本地默认能力。

## 10. 证据与剩余工作

- 能力响应与旧引擎兼容：`apps/perf_testing/tests/test_k6_capabilities.py`，含版本差异、fingerprint变化、未知版本、不可用runner、缓存、秘密字段排除与实际snapshot拒绝规则。
- 已有子集：test_k6_engine.AdapterContractTests、test_k6_worker、test_k6_profile、test_k6_debug、test_k6_report、test_k6_stop_race。
- [固定源码符号库存](../../evidence/p1-delivery/k6-source-capability-inventory.json)只是输入库存；本文补齐方法组、全局API、browser/gRPC指标、CLI、输出、秘密源与外部依赖范围。
- [固定轮数短测执行10](../../evidence/concurrency-probe/probe-10.json)、[网页调试执行12](../../evidence/p1-delivery/debug-ui-execution-12.json)、[提前停止执行14](../../evidence/p1-delivery/debug-early-stop-14.json)、[历史时限验证日志](../../evidence/final-k6-readiness-tests-20260915.log)。历史日志不改写成本块新测试结果。
- 本块无新增真实业务负载。新增页面摘要、刷新和不可用状态由主执行者记录实际点击证据。
- 矩阵建立不是完整能力交付；后续按[开发计划](plans/2026-09-15-k6-full-delivery.md)继续，不能用P0短测代替P1–P6全范围验收。

## C13-A 隔离候选状态（2026-09-16）

本节只记录隔离副本的既有流程修复。候选尚未移入本地运行服务，独立规格/质量审查和实际浏览器集成验收均待完成；C12 实际浏览器下载门禁仍保留，C13-B 与 C14 依赖不变。

- 已实现、隔离回归通过：平台密码/SMS/注册登录统一按服务器颁发 JWT 的 `exp` 调度；同标签并发 401 单次刷新、每请求至多重放一次；导出 GET 保留 `responseType: blob`。客户端解码只用于调度，服务端有效期、签名校验、轮换及黑名单均未修改。
- 多标签优先使用 Web Locks；无此能力时使用有界租约协调。租约不能证明绝对互斥；遇到无法确定的刷新拒绝，本标签要求重新登录并停止自动刷新，保留共享会话以避免删除另一标签尚在完成的有效轮换。较旧刷新/退出响应不能覆盖新登录；退出本地状态立即生效，服务端黑名单请求以捕获的凭据尝试发送、最多等待10秒后跳转；网络失败不等于服务端已完成注销。原SSO兑换分支的旧存储写入也已通过兼容回归。
- 场景初始模型、环境和文件依赖加载期间禁止编辑/保存/调试/启动；已选账号池待加载也阻止动作。加载失败显示重试并保留已加载配置。窄窗口标题/环境布局和“全部账号”提示已调整，实际宽窄窗口视觉检查仍待 root 完成。
- 返回 `k6_start_unconfirmed` 且 `retryable=false` 时明确提示结果未知，关闭启动弹窗并禁用再次提交，提供按项目和场景过滤的“执行历史 / 当前运行”入口。该响应没有执行 ID，不能视作一定未启动或自动重复发压。
- 旧预检/入口夹具已补当前模型接口，原断言保留。隔离验证：retry1 的 126 前端测试及两条独立路由探针通过；后端源码未变，沿用首候选的 296 后端相关测试（含原 18 入口组），本次未重复运行；这是代码回归证据，不代表真实浏览器下载、示例业务身份或负载验收通过。

