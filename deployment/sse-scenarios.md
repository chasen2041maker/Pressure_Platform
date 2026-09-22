# SSE 场景维护合同

SSE 步骤使用 `protocol: "SSE"` 与 `sse_config.version: 1`，通过固定的
`k6/x/testhub-sse` 模块执行。运行时合同版本必须为 `testhub-sse/1`。
普通 HTTP 场景不导入该扩展；含 SSE 的场景在扩展缺失时失败，不能回退成缓冲 HTTP。

## 配置、保存与冻结

手工 K6 步骤可以选择 SSE。来源接口只有在 OpenAPI 成功响应声明
`text/event-stream`，或 Swagger `produces` 包含该类型时才能转换；不按 URL 猜协议。
目录配置与场景编辑共用事件规则编辑器。CRUD、批量保存、复制、目录预配置、依赖组和执行快照
保留相同合同。已创建执行只读取冻结合同；公开执行步骤和报告不包含正文、事件合同、认证值或提取值。

当前支持环境同源 GET/POST、NONE/JSON 正文、标量查询参数，以及每用户 STATIC/BEARER 或
LOGIN/BEARER 认证。SSE 不接受文件、代理或普通响应断言/提取器；它们必须放在事件规则中。
认证凭据仅由认证配置注入 Authorization；禁止在 URL、正文、规则与其他请求头中展开凭据。

| 字段 | 默认 | 范围 |
| --- | ---: | ---: |
| `total_ms` | 30000 | 1–300000，且不超过请求超时与场景时长 |
| `idle_ms` | 10000 | 1–300000，且不超过 total_ms |
| `max_event_bytes` | 262144 | 1–1048576 |
| `max_total_bytes` | 4194304 | 1–16777216 |
| `max_events` | 256 | 1–4096 |

配置最多 128 KiB、16 条规则，每组匹配/断言最多 32 项，每条规则最多 16 个提取器。
发压结束前会缩短当前流的总超时和空闲超时；运行器强停时缺少结束回执的流保留为未完成。

## 声明式事件合同

每条规则包含唯一 `name`、SSE `event` 名称、`match`、`assertions`、`extractors`、
`min_events`、`max_events`、`after`、`without` 和布尔 `terminal`。
每个事件必须恰好匹配一条规则。`after` 要求前面声明的规则已出现，`without` 要求尚未出现；
两者不能引用同一规则。`sequence: {expr: "$.seq", start: 0}` 校验连续整数序号。

条件格式为 `{type: "JSON_PATH", expr: "$.field", operator: "eq", expected: value}`。
支持 `eq/ne/exists/nonempty/blank/positive_int/nonnegative_int/base64/url`。
`blank` 仅接受字符串且 `trim() === ''`；null、缺字段、数字、布尔、数组和对象不匹配。
它和 `nonempty` 均不接受 expected，也不执行类型转换。
`eq/ne` 可用 `expected_count: "earlier_rule"` 代替 expected，以校验结束消息中的实际事件数。
URL 断言检查 HTTP(S) 地址、主机及合法端口，允许签名查询参数；它不验证签名有效性或下载内容。
JSON_PATH 只支持明确的字段和数组索引，不执行脚本。

全局 `assertions` 对每个 JSON 事件执行；`error_conditions` 任一命中立即失败。
具名 `error` 事件始终失败。规则可用 `data: "[DONE]"` 匹配非 JSON 哨兵，但哨兵不能携带
JSON 断言/提取器/序号；终态必须有自己的 JSON 业务断言，或通过 after 链依赖已断言的 JSON 事件。
仅 HTTP 200、单独 `[DONE]`、提前 EOF 或错误事件都不能通过。

提取器格式为 `{name: "stream_id", type: "JSON_PATH", expr: "$.id"}`。
提取值限非空字符串（最多 4096 字符）、有限数字或布尔值。后续规则可通过 `{{stream_id}}`
引用同一流的提取值；只有终态满足全部最小事件计数、运行时成功且连接已关闭，才将值提交给后续步骤。
失败和新一轮运行会清除这些输出。它们不能覆盖账号、认证、输入或已有变量。

编辑器提供 CHAT、MESSAGE_SPEECH、CONTENT_SPEECH 预设，后端不识别业务名称。
预设要求会话归属、有效内容、正确结束状态或音频字段；消息语音序号从 1 开始，内容语音从 0 开始。
新 CHAT 预设以互斥的 `report_nonempty` / `report_blank` 匹配 report 片段，允许空白片段，
但 finish 仍依赖至少一个非空 report，并要求同会话、finish_reason=stop 和最终 DONE。
只有空白片段不能成功。已有已保存合同和执行快照不会自动迁移；重新应用预设才改变草稿。
内容语音缓存命中允许仅终态，但禁止同时出现新的音频片段。修改业务响应结构时必须同步预设和合同测试。
示例业务内容语音服务可能在客户端断开后继续生成缓存；平台关闭 SSE 连接不等于上游供应商已取消计费任务。

## 计数和报告

每条实际发出的 SSE 流计一个 HTTP 请求和一个业务步骤（前置步骤只计 HTTP），事件数量不当成 HTTP QPS。
运行时拒绝请求选项且尚未发出请求时回退发起计数，同时保留运行时失败。
`summary.sse` 和按冻结 step_id 归属的 `stream_metrics` 包含流发起/完成/成功/失败/未完成数、事件数、
流读取字节数、首事件耗时和流结束耗时（count/avg_ms/min_ms/max_ms）。
结束耗时包括成功与失败样本；未结束样本不混入结束均值。首帧即 error 仍记录首事件，强停保留已观察到的首事件。
失败只公开 preparation/transport/business 阶段与固定错误码，不输出对端错误、事件内容或凭据。

新结果额外提供 `diagnostics` 和 `diagnostics_truncated`。定位包含固定 reason/scope、
从 1 开始的 event_index、适用的 rule_index / condition_index，以及同位置失败 count。
scope 为 event/error_condition/global_assertion/rule/rule_assertion/sequence/extractor/terminal/transport；
全局条件序号对应全局列表，规则条件序号对应该规则的 assertions 或 extractors。
terminal 指向未达到最小计数的规则。运行时未观察事件时省略 event_index，不虚构位置。
采集器按冻结合同的事件、规则、条件边界拒绝非法定位，并拒绝额外字段；定位缺失不会改变失败判定。
每步骤及汇总最多保留 32 种不同定位，已保留位置继续累计，其余使 truncated=true；失败总数不截断。
排查应使用逐步骤定位，汇总只聚合相同位置。监控、调试执行页和 HTML 报告显示定位；
JSON/CSV 导出保留相同安全摘要。旧记录未采集定位时不推断历史失败位置。

## 验证入口

在 testhub 目录用隔离测试入口运行：

```text
python ../deployment/test-runner/run_catalog_tests.py apps.perf_testing.tests.test_sse_steps apps.perf_testing.tests.test_sse_diagnostics apps.perf_testing.tests.test_k6_sse_integration
```

`K6_SSE_TEST_BIN` 必须指向已验证且包含固定扩展的候选二进制；未设置时原生测试跳过。
在仓库根目录运行纯事件合同测试：

```text
node --test testhub/apps/perf_testing/tests/test_k6_sse_contract.mjs
```

原生测试只连接回环合成 HTTP/WS/SSE 服务，覆盖混合协议、200 错误事件、首帧错误、提前 EOF、
认证注入、实际 HTTP 分母和依赖组失败阻断。这些测试证明平台能力，不作为真实业务接口验证通过的证据。
迁移双轨为普通应用 0021、pressure_settings 0012；部署须同时携带对应模型与执行器。
