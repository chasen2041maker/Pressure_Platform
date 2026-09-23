<p align="center"><img src="assets/mark.svg" alt="Pressure Platform" width="56"></p>

# 文档中心

**从第一次启动，到能解释一份压测报告。**

[项目首页](../README.md) · [English overview](../README.en.md) · [能力矩阵](../testhub/docs/k6-capability-matrix.md) · [验证记录](../VALIDATION.md)

## 选择你的起点

| 目标 | 推荐阅读顺序 |
| --- | --- |
| 第一次使用 | [本地启动](getting-started.md) → [接口准备池](../deployment/interface-pool.md) → [能力矩阵](../testhub/docs/k6-capability-matrix.md) |
| 理解实现 | [架构说明](architecture.md) → [k6 适配](../testhub/docs/k6-adapter.md) → [有界执行](../deployment/bounded-execution.md) |
| 评估部署 | [Linux Docker 草稿](../deployment/linux-docker/README.md) → [固定运行时](../deployment/runtime/bounded-sse/README.md) → [验证记录](../VALIDATION.md) |
| 修复或贡献 | [贡献指南](../CONTRIBUTING.md) → [上游来源](../UPSTREAM.md) → [视觉与文档规范](branding.md) |

## 准备与执行

| 文档 | 解决的问题 |
| --- | --- |
| [本地启动指南](getting-started.md) | 如何初始化独立密钥、数据库和管理员，启动本地管理页面 |
| [接口准备池](../deployment/interface-pool.md) | 如何组织准备配置、单轮验证和后续复用 |
| [k6 适配说明](../testhub/docs/k6-adapter.md) | runner 配置、账号身份、运行快照与指标语义 |
| [有界专项执行](../deployment/bounded-execution.md) | 固定 VU 范围、尝试上限、最小间隔及策略跳过 |
| [固定 SSE 运行时](../deployment/runtime/bounded-sse/README.md) | 引擎源码、扩展依赖与构建方法 |

## 理解结果与维护实例

| 文档 | 解决的问题 |
| --- | --- |
| [架构说明](architecture.md) | 页面、API、worker、runner 与数据之间的责任边界 |
| [版本化能力矩阵](../testhub/docs/k6-capability-matrix.md) | 平台实际接入了什么，哪些能力未交付或依赖额外条件 |
| [恢复机制](../deployment/reminder-recovery.md) | 异常执行与恢复记录如何保留上下文 |
| [Linux Docker 部署草稿](../deployment/linux-docker/README.md) | 镜像、私密目录、网络、停止与备份恢复的前置条件 |
| [公开源码验证](../VALIDATION.md) | 已执行检查的日期、范围、跳过项和已知旧套件问题 |

> [!IMPORTANT]
> 历史验收不等于当前版本验收；上游具有某项能力不等于平台已接入；能打开页面不等于 runner 可用；静态测试通过不等于完成部署或容量验收。

## 文档的职责

首页负责定位与导航，本目录负责上手与概念。`deployment/` 和 `testhub/docs/` 中的专项文档保留细节，不因首页改版而被替换。

旧专项文档可能包含不同日期的适配器版本与历史证据链接；当前接入状态应核对能力矩阵、运行时锁与实际引擎状态，不能只取旧文档首段的版本号。公开副本不包含真实业务证据，历史材料的缺失不能用虚构结果补齐。

`SOURCE-MANIFEST.json` 的 `snapshot_date` 为 **2026-09-22**，记录初始公开源码快照；它不是此后每个文档提交的完整性声明。定位当前源码与文档请使用具体 Git 提交。制作新发布包时，应重新生成与该包对应的清单，不复用旧清单证明新产物。
