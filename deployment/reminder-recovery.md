# 自选股提醒执行与恢复

仅支持 K6 的 `portfolio_reminder` 条件写入组。普通 HTTP、WebSocket、SSE 执行保持各自合同。业务服务必须先发布并启用 `/api/v1/portfolio/reminder-capabilities` 的 `recovery: {version: "v1", enabled: true}`；路由存在或 HTTP 200 本身不能授权启动。

## 配置与冻结

在场景“变量与环境”保存四个连续、已保存的 HTTP 步骤：PUT 规则、GET PUT 命令回执、GET 当前规则、DELETE 清理。四步使用相同执行策略，`max_runs_per_vu=1`，固定 VU 范围，每执行最多 1000 个资源。复制场景会映射四个步骤 ID。恢复未启用时不能执行带新 `precondition` 或 `put_request_key` 的条件写入。

`runtime_config.resource_recovery` 为 `{}` 或以下对象；字段不接受额外属性、字符串数字或布尔数字：

```json
{"version":1,"kind":"portfolio_reminder","group_id":"reminder_once","put_step_id":11,"receipt_step_id":12,"get_step_id":13,"delete_step_id":14,"stock_code":"sz000001","max_resources":1000}
```

PUT 必须提供完整六条件（字符串阈值）、完整 policy 和 `precondition:{mode:"absent"}`。所有值在本次执行中固定。正式 policy 至少启用一个通知渠道；关闭 App 推送不能证明消息中心无增量，清理完成也不能撤回已经触发的通知。测试阈值与通知预算需由实际业务资源单独确认。

回执路径为 `/api/v1/portfolio/reminder-commands/{{rr_put_digest}}`。DELETE 的原始正文由执行冻结替换为精确源 PUT 键与 fingerprint。上轮本平台已清理的规则可以使用受签名 lineage 转换为 typed JSON revision 条件，不能手填“已恢复”或用 CSV 字符串代替整数 revision。

执行前持久化私密快照、签名 intent/state、作用域 registry，最后写入 ready 文件；ready 前从日志目录到私密根目录的父层逐级 fsync，包括由快照提前创建的已有目录。任一同步失败均不写 ready，保留作用域占用并禁止启动。幂等键由本次不可复用 UUID、VU、put/delete/recover 角色产生。fingerprint 对固定 method/path/原样 UTF-8 正文计算。intent 不保存 Token；Token 仍取原版本私密账号快照。

## 正规接口池验证与导入

通过接口池已有准备编辑接口，在同一股票的 PUT、DELETE 和可选 GET 的 `preparation.resource_recovery` 保存同一声明：

```json
{"version":1,"kind":"portfolio_reminder","group_id":"reminder_once","stock_code":"sz000001","max_resources":1000}
```

保存遵循目录版本及 prepared revision 的 CAS；声明及请求变更使旧证据失效。旧编辑器遗漏声明时服务保留原值，显式 `{}` 才移除。页面重新保存也保留声明。DELETE 保存为 JSON `{}`；PUT 必须使用上述 absent 前置条件。已有目录和历史执行不增加、替换或补写“已通过”状态。

DELETE 的 `{}` 是平台生成请求的模板，并非发送正文。准备校验仅在严格声明有效时投影清理请求；场景预检还要求完整四步、本人账号绑定和精确 DELETE 步骤 ID，并使用本次执行的并发配置。投影仅用于按原始来源 schema 检查，不保存或发送预览键。冻结每个参与账号的实际 PUT、DELETE 及恢复请求时再次校验来源正文及幂等键 schema，同时核对生成角色、键、fingerprint 和 digest；任一失败均不能写 ready。来源 schema 的 required、字段类型和约束保持不变，普通未声明 DELETE `{}` 仍按正式合同报缺字段。

正常批量验证必须完整选中同声明的 PUT、DELETE，可同时选真实 GET；这次批次仅包含这一组，不支持混入其他接口或 setup。服务按正常步骤与场景 serializer 校验，生成连续 PUT、回执 GET、规则 GET、DELETE 并映射真实步骤 ID；回执以及未选择的 GET 是无目录来源的辅助业务步骤。冻结仍由正常执行入口增加 `/me`，固定 1 VU、1 轮；每个真实目录目标分别关联其执行步骤，辅助请求不冒充新增目录接口。

证据同时要求冻结前 prepared HMAC、签名 ready/plan、精确可重建的 plan 和完整冻结转换一致；包含 typed lineage、所有私密 CSV 字段、环境、账号版本及原请求。必须持久化完整五步各一次真实成功，原目标业务断言成功，并由恢复守护确证 `cleaned=1`、无 pending/conflict/cancelled，才可复用。后台取消未知 PUT 只说明资源安全，不能让未执行或失败的接口通过。恢复历史 unknown 请求尝试保留原计数；后续已确证清理不因历史只读 ACK 丢失而永久阻止复用。

正常“已验证接口”导入要求完整组，重新生成辅助步骤并映射目标场景 ID；每 VU 最多一次，参与范围不超过场景固定并发数及声明 max_resources。已有启用的提醒 PUT/DELETE 或已配置恢复绑定时明确拒绝，需先通过正常场景编辑停用旧步骤、移除旧绑定；导入不偷偷改动其他步骤。

回归标签 `apps.perf_testing.tests.test_prepared_recovery` 覆盖正规保存/派发/证据/导入及故障、重复提交、修订 3/4/5 CAS。原生验收须额外使用合成回环目标走实际 worker、持久统计和独立恢复守护；手造成功统计不能替代这项执行证据。

## 身份与正常执行

仅使用固定账号池版本和 STATIC Bearer。冻结自动在该组之前插入一次真实、正常统计的 `/api/v1/me`，核对字符串 user_id 与 pool 行，并绑定 Token hash、pool hash。身份或任意前序步骤失败会阻断该组剩余步骤。价格与百分比按 Mobile 的 6/4 位十进制字符串比较，关闭条件的精确 `"0"` 输入对应空值；不使用浮点容差。

每参与 VU 正常请求上限为 5（含身份核验），PUT/DELETE 各最多 1 次；每轮不会重建资源。启动另有 1 次能力只读检查。负载请求统计不混入后台恢复请求。

## 独立恢复

容器入口监督 gunicorn 与独立 `manage.py recover_portfolio_reminders` 子进程。Web/执行进程崩溃不依赖 finally 清理。守护异常会重启，连续启动失败使容器失败；没有最近 30 秒有效签名心跳时，提醒组不能启动。独立守护以单调时钟监测实际进度，连续 30 秒无进度时在下一次最多 1 秒间隔的检查中退出整个进程，操作系统释放租约和连接，由监督进程重启。进度只随真实心跳落盘、每资源处理结束或历史执行扫描结束推进，不以整个 tick 计时；8 个正常资源累计超过 30 秒不会误判。其他部署方式必须单独监督该管理命令。

守护持有独占 recovery FileLease；每轮与引擎共用 run lease，只有引擎已退出且执行为终态才恢复。stop、孤儿回收、超时和正常结束都保留冻结标记并由终态扫描接管。每轮最多处理 8 个资源，每访问最多 3 次 HTTP，每资源总预算 24 次，失败退避且预算耗尽保持 pending。恢复序列每次重新 `/me`；不复用之前身份结果来授权写入。

先查正常 DELETE 回执，再查固定 recover 回执；无法确定 PUT 是否已经提交时，通过精确源键/fingerprint 的 DELETE 原子取消或 CAS 清理。读取回执 404 本身绝不算清理成功。已删除必须有原命令回执且 GET 当前规则 404；仍有活动规则即 conflict。取消回执证明该次 PUT 被围栏拦截，不删除同股票其他规则。409 的明确 CONFLICT 保留冲突；处理中或身份/依赖不可用继续有界等待。

恢复禁止代理、重定向、跨 origin 与任意路径/方法；连接及 socket 等待上限 5 秒，响应正文另受从请求开始计算的 5 秒期限与 1 MiB 限制。系统 DNS 与逐字节响应头不由正文期限覆盖，不能将其称为完整请求的 5 秒硬超时；后台进度监督负责终止这类卡住的进程，已记账而响应未确认的请求保留 unknown，重启后仍查询同一幂等键。只持久化固定原因和经过核对的最小回执事实。已完成项不重复 mutation。state fsync 与 registry fsync 之间崩溃由独立守护修复；未修复不能提前解除资源占用。

## 状态和保留

Execution 顶层 `resource_recovery` 与负载执行结果独立。`planned/pending/cleaned/cancelled/conflict` 为资源计数；`requests/reads/writes` 为持久化的请求尝试数，`completed_requests` 为已收到响应数，`unknown_requests` 为响应未确认的尝试数。进程可能在持久化尝试后、真正发出前崩溃，所以不能把尝试数称为服务器实际接收数。缺失、损坏或不一致的意图显示 CORRUPT/未知，不显示伪造的 0。

未恢复执行不能经直接删除、父级级联或保留策略删除。相同 origin、真实 owner、stock 的新执行须等待上一执行恢复；跨账号池版本不能绕过。私密恢复文件和服务端命令/tombstone 不自动过期。回退前先停止新组、处理所有 pending/conflict，保留私密状态和业务命令账本，不能仅回退二进制。

验证使用一次性 SQLite、合成账号、loopback 协议服务和固定原生 k6：覆盖正常每 VU 一次、错身份无写入、固定小数回执、未知响应、持久化故障、停止后迟到 PUT、独立恢复及数据保留。此类测试只证明平台协议行为，不替代真实 Mobile 的事务/故障验收及正式环境低量验证。
