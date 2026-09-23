# 贡献指南

[项目首页](README.md) · [文档中心](docs/README.md) · [上游来源](UPSTREAM.md)

欢迎改进 Pressure Platform 的可用性、可靠性与文档。优先提交可以复现、范围聚焦、说明边界的改动，不用新增宣传词代替实现或验收。

## 开始之前

先阅读 [本地启动指南](docs/getting-started.md)、[架构说明](docs/architecture.md) 与 [能力矩阵](testhub/docs/k6-capability-matrix.md)。使用独立测试目录、合成接口和测试账号；不要读取业务数据库或对未授权目标发压。

基于当前默认分支创建自己的工作分支。涉及运行器、指标或协议语义的改动，先说明预期行为和兼容性，再扩展实现。保留 TestHub、k6 及其他依赖的来源与许可证信息。

## 提交问题与改动

问题反馈应包含使用的 Git 提交、操作系统、Python / Node 版本、适用时的 runner 模式、最小复现步骤、预期与实际结果，以及脱敏错误。不要上传整个 `runtime/` 或原始业务报告。

一个 PR 尽量只解决一个主题。描述修改动机、涉及的能力、实际运行的检查、未验证部分和可能的兼容性影响。文档-only 改动不应顺带修改业务行为、依赖锁或运行配置。

## 检查入口

完成本地环境初始化后，按改动范围选择相应检查。以下是现有入口，不承诺所有环境无需额外依赖即可运行。

后端维护套件，从仓库根目录进入 `testhub/`：

```sh
cd testhub
python ../deployment/test-runner/run_catalog_tests.py
```

该入口创建并清理独立测试目录。完整旧套件的已知问题见 [VALIDATION.md](VALIDATION.md)，不要通过删除用例或改写权限逻辑隐藏它们。

前端检查，从仓库根目录进入 `testhub/frontend/`：

```sh
cd testhub/frontend
node --test src/views/performance-testing/*.test.mjs
npm run build
```

部署工具静态检查，从仓库根目录运行：

```sh
python -B -m unittest discover -s deployment/linux-docker -p 'test_*.py'
```

静态检查不启动 Docker，也不代替镜像构建、真实协议测试或部署验收。需要本机 k6、Docker 或其他依赖的集成检查，应在对应环境实际执行后单独记录。

## 如何记录验证

结果记录至少说明提交或候选版本、日期、命令、运行环境、通过 / 失败 / 跳过数量，以及仍然未验证的部分。**跳过不是通过；历史结果不是新提交的验收；源码能构建不是目标服务容量证明。**

`VALIDATION.md` 保留 2026-09-22 公开副本的历史记录。新增验收请追加有日期、有范围的记录，不把旧结果无说明地改成当前结果。

## 文档与视觉改动

同步检查 `README.md` 和 `README.en.md` 的定位、入口及支持边界，避免中英文承诺不一致。新导航指向现有文档，不用复制多份运行规范造成版本漂移。

按照 [视觉与文档规范](docs/branding.md) 检查浅色、深色和窄屏阅读。SVG 必须自包含，不引用字体文件、脚本或外部图片；图示需要明确区别于真实产品截图，不能添加未经测量的性能数字或虚构 CI 状态。

`SOURCE-MANIFEST.json` 是带日期的初始源码快照，不是滚动更新的每次提交清单。制作新的发布包时，重新生成与实际产物对应的清单；不要用旧清单证明新产物。

## 公开材料的边界

只使用合成示例或确认可公开的数据。账号、密码、Token、Cookie、私钥、内部域名、完整请求正文及业务执行数据不得出现在 Issues、PR、截图或提交历史中。发现疑似凭据暴露时，不要把凭据再次粘贴到公开讨论。

来源与许可证以 [UPSTREAM.md](UPSTREAM.md)、根 [LICENSE](LICENSE) 及各依赖自带声明为准。
