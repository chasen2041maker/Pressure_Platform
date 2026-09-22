# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TestHub is an AI-driven test management platform built with Django 4.2 (backend) + Vue 3 (frontend). It provides test case management, API testing, UI automation testing, APP (mobile) automation testing, test data generation (data factory), user behavior analytics, AI-powered requirement analysis, and test case generation capabilities.

## Common Commands

```bash
# Activate the virtual environment (Windows PowerShell)
.venv\Scripts\Activate.ps1
# Activate the virtual environment (macOS/Linux)
source .venv/bin/activate
```

### Backend (Django)

```bash
# Start development server (HTTP only; no WebSocket)
python manage.py runserver

# Start with WebSocket support (required for APP automation real-time progress)
daphne -b 0.0.0.0 -p 8000 backend.asgi:application

# Database migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run all scheduled tasks (API testing + UI automation)
python manage.py run_all_scheduled_tasks

# Initialize UI automation locator strategies
python manage.py init_locator_strategies

# Download webdrivers for UI automation
python manage.py download_webdrivers

# Load APP automation UI component pack from YAML (add --overwrite to replace)
python manage.py load_component_pack
```

All management commands live in `apps/core/management/commands/`.

### Frontend (Vue 3 + Vite)

```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Lint code
npm run lint
```

### Docker (optional)

```bash
# Build & push images
./build-and-push.sh
# Full stack via docker-compose
docker-compose up -d
```

See `.docker_env`, `docker-compose.yml`, and `Dockerfile.backend` for container configuration.

## Architecture

### Backend Structure (`apps/`)

The Django project uses a modular app structure under `apps/`:

- **users**: User authentication and profile management (custom User model)
- **projects**: Project and team management
- **testcases**: Manual test case management with steps, attachments, comments
- **testsuites**: Test suite organization
- **executions**: Test plan execution and result tracking
- **reports**: Test report generation
- **reviews**: Test case review workflow with templates and assignments
- **versions**: Version/release management
- **requirement_analysis**: AI-powered requirement document parsing (PDF/Word/TXT) and test case generation
- **assistant**: Dify AI chatbot integration
- **api_testing**: API testing module (HTTP/WebSocket, environments, scheduled tasks, Allure reports)
- **ui_automation**: UI automation with Selenium/Playwright, element management, page objects, AI intelligent mode
- **app_automation**: APP/mobile automation testing via Airtest + image recognition (OCR), with device management, WebSocket real-time execution progress, and Allure reports
- **core**: Cross-module shared functionality; hosts all management commands (`run_all_scheduled_tasks`, `init_locator_strategies`, `download_webdrivers`, `load_component_pack`) and a variable resolver
- **data_factory**: Test data generation and utility tools (Chinese names, phones, ID cards, bank cards, JSON/string/encoding tools, barcodes, QR codes)
- **analytics**: Optional user behavior analytics/event tracking (埋点), conditionally enabled via `ANALYTICS_ENABLED`

### Frontend Structure (`frontend/src/`)

- **views/**: Page components organized by feature module (incl. `api-testing`, `app-automation`, `data-factory`, `ui-automation`, `requirement-analysis`, `configuration`, `notification`)
- **api/**: API service layer
- **stores/**: Pinia state management
- **router/**: Vue Router configuration
- **components/**: Shared components
- **layout/**: Layout components
- **locales/**: vue-i18n internationalization (zh-hans / en / ja / ko)
- **utils/**: Shared utilities (HTTP client, app-automation helpers, code generator, request models, analytics tracker)

### Key Configuration Files

- `backend/settings.py`: Django settings (database, REST framework, CORS, Celery, email, Channels, analytics)
- `backend/asgi.py`: ASGI entry; enables WebSocket (Channels) when `channels` is installed, degrades to HTTP-only otherwise
- `backend/urls.py`: Root URL configuration
- `frontend/vite.config.js`: Vite build configuration
- `.env`: Environment variables (DB credentials, API keys, Redis, SMS, email config)
- `.env.example`: Reference environment variable template
- `docker-compose.yml` / `Dockerfile.backend` / `build-and-push.sh`: Docker deployment

## API Structure

All API endpoints are prefixed with `/api/`:
- `/api/auth/` and `/api/users/`: User authentication
- `/api/projects/`: Project management
- `/api/testcases/`: Test case CRUD
- `/api/testsuites/`: Test suite management
- `/api/executions/`: Test execution
- `/api/reports/`: Report generation
- `/api/reviews/`: Review workflow
- `/api/versions/`: Version management
- `/api/assistant/`: AI assistant chat
- `/api/requirement-analysis/`: AI requirement analysis
- `/api/` (api_testing): API testing endpoints
- `/api/ui-automation/`: UI automation endpoints
- `/api/app-automation/`: APP automation endpoints
- `/api/core/`: Core shared endpoints
- `/api/data-factory/`: Data factory / test data tools
- `/api/analytics/`: Analytics events (only when `ANALYTICS_ENABLED`)

WebSocket endpoint: `ws/app-automation/executions/<execution_id>/` (real-time APP automation execution progress).

API documentation available at `/api/docs/` (Swagger) and `/api/redoc/` (ReDoc).

## ASGI / WebSocket

The backend uses Django Channels for WebSocket support. `backend/asgi.py` wraps the ASGI app in a `ProtocolTypeRouter` routing `websocket` traffic to `apps/app_automation` consumers. Run with **Daphne** to enable WebSockets (`daphne backend.asgi:application`); plain `runserver` only serves HTTP. Redis (`REDIS_URL`) is the channel layer backend.

## Database

MySQL 8.0+ with `utf8mb4` charset. Configuration via environment variables:
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`

Redis is optional but required for APP automation WebSocket / Channels:
- `REDIS_URL` (e.g. `redis://127.0.0.1:6379/0`)

## AI Integration

The platform supports multiple AI providers configured in `requirement_analysis.AIModelConfig`:
- DeepSeek, Qwen (通义千问), SiliconFlow (硅基流动), Zhipu (智谱), Xiaomi (小米), OpenAI-compatible APIs
- AI roles: `writer` (测试用例编写专家), `reviewer` (测试评审专家), `browser_use_text` (Browser Use 文本模式)

UI automation AI mode uses `browser-use` library with LangChain for intelligent browser automation (`apps/ui_automation/ai_agent.py`).

## Testing Prompt Templates

Custom prompts for AI test case generation are defined in:
- `docs/tester.md`: Test case writer persona and output format
- `docs/tester_pro.md`: Test case reviewer persona

## Key Dependencies

Backend: Django REST Framework, drf-spectacular, django-filter, Celery, httpx, Selenium, Playwright, browser-use, langchain-openai, Channels + Daphne + channels-redis (WebSocket), Airtest + EasyOCR + OpenCV (APP automation), PyPDF/python-docx/reportlab/openpyxl (document handling), python-barcode/qrcode/pyzbar, Redis.

Frontend: Vue 3, Element Plus, Pinia, Vue Router, Axios, ECharts, Monaco Editor, vue-i18n, vuedraggable, curlconverter, dayjs, lodash-es, xlsx.

## Commit 规范
- 默认不自动提交代码
- 多个相关修改应合并为一个 commit
- commit message 格式：`<type>: <简短描述>`
- 提交前必须运行 lint 和测试
