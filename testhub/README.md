# TestHub 智能测试管理平台

<div align="center">

**基于 AI 驱动的全栈测试管理平台**

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-4.2-green.svg)](https://www.djangoproject.com/)
[![Vue](https://img.shields.io/badge/Vue-3.3-brightgreen.svg)](https://vuejs.org/)
[![License](https://img.shields.io/badge/License-GPL_v3-blue.svg)](LICENSE)

</div>

## 📖 项目简介

TestHub 是一个 AI 驱动的全栈测试管理平台，覆盖测试全流程，包含：**AI 需求分析与用例生成**、**测试用例管理与评审**、**API 测试**、**UI 自动化测试（Web）**、**APP 自动化测试（Android）**、**性能测试**、**数据工厂**等模块，助力测试团队全面提效。

## ✨ 核心特性

### 🤖 AI 智能化
- **需求分析**: 上传需求文档（PDF/Word/TXT），AI 自动提取业务需求并生成测试用例
- **智能助手**: 集成 Dify AI 助手，提供测试咨询和问题解答
- **AI 智能模式**: 基于 Browser-use 的智能浏览器自动化，AI 理解页面并自动完成测试（支持文本/视觉两种模式）
- **多模型支持**: DeepSeek、通义千问、硅基流动、OpenAI、Anthropic、Google Gemini 等

### 📋 用例管理与评审
- 完整的用例生命周期管理：创建、编辑、版本控制、归档
- 多维度组织：项目、版本、标签分类；步骤化设计（前置条件 / 操作步骤 / 预期结果）
- 附件与团队协作评论
- 评审流程：多人评审、评审模板与检查清单、多层级评审意见、状态跟踪

### 🌐 API 测试
- 支持 HTTP / WebSocket 协议，树形集合组织，环境变量与变量替换
- 测试套件批量执行，支持断言与执行顺序配置
- 请求历史记录、定时任务（邮件 / Webhook 通知）、Allure 测试报告

### 🖥️ UI 自动化测试（Web）
- Selenium / Playwright 双引擎，多浏览器支持（Chrome / Firefox / Edge）
- 元素库（多种定位策略）+ 页面对象模式（POM）
- 可视化脚本编辑、测试套件批量执行、执行日志 / 截图 / 视频录制
- 定时任务：Cron 表达式、固定间隔、单次执行
- AI 智能模式：AI 理解页面结构自动完成测试任务

### 📱 APP 自动化测试（Android）
- 基于 Airtest 图像识别，支持本地模拟器与远程设备，设备锁定避免资源冲突
- 元素管理（图片 / 坐标 / 区域）与多分辨率适配
- 组件化编排 + UI Flow 流程编排，变量管理（global / local / outputs）
- Celery 异步执行 + Allure 报告，WebSocket 实时进度追踪

### 🚀 性能测试
- 基于 Locust 的压测任务管理与执行，性能指标统计与报告

### 🏭 数据工厂
- 50+ 实用工具：字符处理、编码转换、随机数据、加密解密、测试数据生成、JSON 处理、Crontab 表达式等
- 标签管理与使用记录，支持在 API 测试与 UI 测试中直接引用数据

### 🔐 安全与协作
- JWT 双 Token 机制：自动刷新续期、登出黑名单、防重放攻击
- 多项目管理、成员角色权限控制、版本规划
- 统一通知：邮件 + 企业微信 / 钉钉 / 飞书 Webhook 机器人

## 🏗️ 技术架构

### 后端
- **框架**: Django 4.2 + Django REST Framework
- **数据库**: MySQL 8.0+
- **认证**: JWT（rest_framework_simplejwt）+ Token 黑名单
- **自动化**: Selenium、Playwright、Airtest + OCR、Allure
- **异步与任务**: Celery、APScheduler、Channels + Daphne（WebSocket）
- **AI 集成**: browser-use、langchain-openai，多模型提供商
- **API 文档**: drf-spectacular（Swagger / ReDoc）

### 前端
- Vue 3 + Vite + Element Plus
- Pinia 状态管理、Vue Router、Axios
- ECharts 可视化、Monaco Editor、vue-i18n 国际化

在进行 Web、API 及自动化测试时，不同地区的网络环境可能带来不同的访问体验。IPWO住宅代理，支持多地区网络环境配置，可用于海外网站访问、区域测试及自动化测试场景。

<u>[IPWO](https://www.ipwo.net/?ref=githubplatform)</u>为 TestHub 用户提供更多测试环境选择，让跨地区测试更加灵活。覆盖全球195+地区动静态IP资源，支持免费测试入口，90折扣码：0204
![img.png](static_files/img.png)

## 📁 项目结构

```
testhub_platform/
├── apps/                           # Django 应用模块
│   ├── users/                      # 用户认证与管理
│   ├── projects/                   # 项目管理
│   ├── testcases/                  # 测试用例管理
│   ├── testsuites/                 # 测试套件
│   ├── executions/                 # 测试计划与执行
│   ├── reports/                    # 测试报告
│   ├── defects/                    # 缺陷管理
│   ├── reviews/                    # 用例评审
│   ├── versions/                   # 版本管理
│   ├── core/                       # 核心模块（管理命令、变量解析、通知配置）
│   ├── api_testing/                # API 测试
│   ├── ui_automation/              # UI 自动化测试（含 AI 智能模式）
│   ├── app_automation/             # APP 自动化测试
│   ├── perf_testing/               # 性能测试
│   ├── requirement_analysis/       # AI 需求分析
│   ├── assistant/                  # Dify 智能助手
│   ├── data_factory/               # 数据工厂
│   └── llm_judge/                  # LLM 评测
├── backend/                        # Django 项目配置（settings / urls / asgi）
├── frontend/                       # Vue 3 前端（src: views / api / stores / router）
├── media/                          # 媒体文件（上传文件、截图等）
├── logs/                           # 日志文件
├── allure/                         # Allure 报告工具
├── requirements.txt                # Python 依赖
└── manage.py                       # Django 管理脚本
```

## 🚀 快速开始

### 环境要求

- **Python**: 推荐 3.12，其他版本可能存在兼容性问题
- **Node.js**: 18+（前端构建必需，生产环境可不安装）
- **MySQL**: 8.0+
- **Java**: 17+（可选，Allure 报告生成需要）
- **Redis**: 6.0+（可选，APP 自动化 WebSocket / Celery 需要）
- **浏览器驱动**: ChromeDriver / GeckoDriver（UI 自动化，也可通过管理命令自动下载）

### 后端部署

1. **克隆项目**
```bash
git clone <repository-url>
cd testhub_platform
```

2. **创建虚拟环境并安装依赖**
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux / Mac
source venv/bin/activate

pip install -r requirements.txt
```

3. **配置环境变量**
```bash
# 复制模板并按需修改数据库、Redis、邮箱等配置
cp .env.example .env
```

4. **初始化数据库**
```bash
# 创建数据库
mysql -u root -p -e "CREATE DATABASE testhub CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 执行迁移并创建超级用户
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

5. **初始化模块数据（可选）**
```bash
# UI 自动化元素定位策略
python manage.py init_locator_strategies
# APP 自动化组件库
python manage.py load_component_pack
```

6. **启动服务**
```bash
# Django 开发服务器（仅 HTTP）
python manage.py runserver

# 如需 WebSocket（APP 自动化实时进度），改用 Daphne 启动
daphne -b 0.0.0.0 -p 8000 backend.asgi:application

# 定时任务调度器（API / UI 定时任务，可选）
python manage.py run_all_scheduled_tasks

# Celery worker（APP 自动化异步任务，可选）
celery -A backend worker -l info
```

### 前端部署

```bash
cd frontend
npm install
npm run dev        # 开发模式
npm run build      # 生产构建
```

### 访问应用

- **前端**: http://localhost:3000
- **后端 API**: http://localhost:8000
- **API 文档**: http://localhost:8000/api/docs/
- **Admin 后台**: http://localhost:8000/admin/

### Docker 部署（可选）

```bash
./build-and-push.sh    # 构建并推送镜像
docker-compose up -d   # 一键启动全栈服务
```

## 🔧 配置说明

环境变量统一在 `.env` 中配置（参考 `.env.example`），主要包括：

- **数据库**: `DB_NAME`、`DB_USER`、`DB_PASSWORD`、`DB_HOST`、`DB_PORT`
- **Redis**: `REDIS_URL`（APP 自动化 WebSocket / Celery 需要）
- **AI 模型**: 在「统一配置中心」管理多家提供商的 API Key / Base URL / 模型参数，支持按角色配置（用例编写、用例评审、Browser Use），并提供连接测试
- **UI 自动化**: 执行引擎（Selenium / Playwright）、浏览器、有头 / 无头模式
- **通知**: 邮件（SMTP）与 Webhook 机器人（企业微信 / 钉钉 / 飞书）

## 📄 文档

更多使用说明见 [docs/docs-center](./docs/docs-center/)：

- **[数据工厂使用说明](./docs/docs-center/数据工厂使用说明.md)** / **[快速开始](./docs/docs-center/数据工厂快速开始.md)** / **[功能说明](./docs/docs-center/数据工厂功能说明.md)** / **[API 接口文档](./docs/docs-center/数据工厂API接口文档.md)**
- **[UI 自动化测试执行说明](./docs/docs-center/UI自动化测试执行说明.md)** / **[UI 自动化测试用户使用手册](./docs/docs-center/UI%20自动化测试用户使用手册.md)**
- **[WebDriver 驱动管理优化说明](./docs/docs-center/WebDriver驱动管理优化说明.md)**
- **[用例评审管理功能说明](./docs/docs-center/用例评审管理功能说明.md)**
- **[I18N 国际化使用说明](./docs/docs-center/I18N国际化使用说明.md)**
- **[问题排查指南](./docs/docs-center/问题排查指南.md)**



## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request 来帮助改进项目！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

## 📝 许可证

本项目采用 GPL 3.0 许可证 - 详见 [LICENSE](LICENSE) 文件

## 📧 联系方式

如有问题或建议，欢迎通过 Issue 反馈，也可以扫码添加作者，加入官方交流群。
![img_1.png](static_files/img_1.png)

<u>[TestHub 官网](https://testhub.aisky.cloud)</u>

使用手册、视频教程、学习中心、Skills商店，欢迎访问TestHub官网。
![img_2.png](static_files/img_2.png)
---

<div align="center">
Made with ❤️ by 大刚（公众号：测试开发实战）
</div>
