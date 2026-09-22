"""MCP 工具的实际执行动作与项目权限过滤。

危险工具（run_*/create_*）的执行体统一在此实现，preview/confirm 工具
与监控页「手动批准」共用同一入口，保证行为一致。
"""
import hashlib
import json
import logging

from django.db.models import Q

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# 权限过滤：四类项目均按 owner/members 过滤，与各模块视图 get_queryset 一致
# --------------------------------------------------------------------- #

def accessible_projects(user):
    from apps.projects.models import Project
    return Project.objects.filter(Q(owner=user) | Q(members=user)).distinct()


def accessible_api_projects(user):
    from apps.api_testing.models import ApiProject
    return ApiProject.objects.filter(Q(owner=user) | Q(members=user)).distinct()


def accessible_ui_projects(user):
    from apps.ui_automation.models import UiProject
    return UiProject.objects.filter(Q(owner=user) | Q(members=user)).distinct()


def accessible_perf_projects(user):
    from apps.perf_testing.models import PerfProject
    return PerfProject.objects.filter(Q(owner=user) | Q(members=user)).distinct()


def args_digest(arguments: dict) -> str:
    """参数摘要（仅存哈希，不落原始参数）。"""
    try:
        raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        raw = repr(arguments)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


#: 参数脱敏：键名含以下子串的字段值替换为 ***
_SENSITIVE_KEY_PARTS = ('password', 'token', 'key', 'secret', 'credential')
#: args_brief 最大长度（截断控制表体积）
ARGS_BRIEF_MAX_LEN = 2000


def _mask_value_tree(value):
    """递归脱敏：dict 按键名判断，list 逐项递归。"""
    if isinstance(value, dict):
        masked = {}
        for k, v in value.items():
            if any(part in str(k).lower() for part in _SENSITIVE_KEY_PARTS):
                masked[k] = '***'
            else:
                masked[k] = _mask_value_tree(v)
        return masked
    if isinstance(value, list):
        return [_mask_value_tree(item) for item in value]
    return value


def mask_sensitive_args(arguments: dict, max_len: int = ARGS_BRIEF_MAX_LEN) -> str:
    """参数脱敏截断文本：敏感字段值替换为 ***，超长截断，供审计回溯。"""
    try:
        raw = json.dumps(_mask_value_tree(arguments or {}),
                         ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 - 序列化失败降级为 repr
        raw = repr(arguments)
    if len(raw) > max_len:
        raw = raw[:max_len] + '...(已截断)'
    return raw


# --------------------------------------------------------------------- #
# 危险操作执行体：每个函数返回 dict 结果；失败抛 McpActionError
# --------------------------------------------------------------------- #

class McpActionError(Exception):
    """动作执行失败（面向 Agent 的可读错误）。"""


def run_api_suite_action(arguments: dict, user) -> dict:
    from apps.api_testing.models import TestSuite
    from apps.api_testing.suite_runner import start_suite_execution

    suite_id = arguments.get('suite_id')
    suite = TestSuite.objects.filter(
        id=suite_id, project__in=accessible_api_projects(user)
    ).select_related('project').first()
    if not suite:
        raise McpActionError(f'测试套件 {suite_id} 不存在或无权限')

    execution = start_suite_execution(suite, user)
    return {
        'execution_id': execution.id,
        'suite_id': suite.id,
        'suite_name': suite.name,
        'total_requests': execution.total_requests,
        'status': execution.status,
        'hint': '执行已异步启动，可通过执行 ID 查询结果',
    }


def run_ui_case_action(arguments: dict, user) -> dict:
    from apps.ui_automation.models import TestCase
    from apps.ui_automation.mcp_runner import start_case_execution

    case_id = arguments.get('case_id')
    case = TestCase.objects.filter(
        id=case_id, project__in=accessible_ui_projects(user)
    ).select_related('project').first()
    if not case:
        raise McpActionError(f'UI 用例 {case_id} 不存在或无权限')

    engine = arguments.get('engine', 'playwright')
    if engine not in ('playwright', 'selenium'):
        raise McpActionError(f'不支持的引擎: {engine}')
    browser = arguments.get('browser', 'chrome')
    headless = bool(arguments.get('headless', True))

    try:
        execution = start_case_execution(case, user, engine=engine,
                                         browser=browser, headless=headless)
    except ValueError as e:
        raise McpActionError(str(e)) from e
    return {
        'execution_id': execution.id,
        'case_id': case.id,
        'case_name': case.name,
        'engine': engine,
        'status': execution.status,
        'hint': '执行已异步启动，可通过执行 ID 查询结果',
    }


def run_perf_scene_action(arguments: dict, user) -> dict:
    from apps.perf_testing.models import PerfScenario
    from apps.perf_testing.services import executor

    scene_id = arguments.get('scene_id')
    scenario = PerfScenario.objects.filter(
        id=scene_id, project__in=accessible_perf_projects(user)
    ).select_related('project').first()
    if not scenario:
        raise McpActionError(f'压测场景 {scene_id} 不存在或无权限')

    if scenario.has_active_execution():
        raise McpActionError('该场景已有正在执行的压测，请等待结束或先停止')

    execution, check = executor.start_execution(
        scenario, user=user, trigger_type='API')
    if execution is None:
        raise McpActionError(f'执行前检查未通过: {check}')
    return {
        'execution_id': execution.id,
        'execution_no': execution.execution_no,
        'scenario_name': scenario.name,
        'status': execution.status,
        'hint': '压测已异步启动，可通过执行 ID 查询结果',
    }


def create_testcase_action(arguments: dict, user) -> dict:
    from apps.testcases.models import TestCase, TestCaseStep

    project_id = arguments.get('project_id')
    project = accessible_projects(user).filter(id=project_id).first()
    if not project:
        raise McpActionError(f'项目 {project_id} 不存在或无权限')

    data = arguments.get('data') or {}
    title = (data.get('title') or '').strip()
    if not title:
        raise McpActionError('data.title 不能为空')

    steps = data.get('steps') or []
    if not isinstance(steps, list):
        raise McpActionError('data.steps 必须是数组')

    testcase = TestCase.objects.create(
        project=project,
        title=title[:500],
        description=data.get('description', ''),
        preconditions=data.get('preconditions', ''),
        expected_result=data.get('expected_result', ''),
        priority=data.get('priority', 'medium') if data.get('priority') in dict(TestCase.PRIORITY_CHOICES) else 'medium',
        test_type=data.get('test_type', 'functional') if data.get('test_type') in dict(TestCase.TYPE_CHOICES) else 'functional',
        author=user,
    )

    step_objs = []
    for idx, item in enumerate(steps, 1):
        if not isinstance(item, dict):
            continue
        step_objs.append(TestCaseStep(
            testcase=testcase,
            step_number=item.get('step_number', idx),
            action=str(item.get('action', '')),
            expected=str(item.get('expected', '')),
        ))
    if step_objs:
        TestCaseStep.objects.bulk_create(step_objs)

    return {
        'testcase_id': testcase.id,
        'title': testcase.title,
        'project_id': project.id,
        'steps_created': len(step_objs),
    }


#: confirm 分发注册表：tool_name → (执行体, preview 构造函数)
def build_preview(tool_name: str, arguments: dict, user) -> str:
    """构造 preview 文本；无法预览时给出通用描述。"""
    try:
        if tool_name == 'confirm_run_api_suite':
            from apps.api_testing.models import TestSuite
            suite = TestSuite.objects.filter(id=arguments.get('suite_id')).first()
            count = suite.requests.count() if suite else 0
            return f'将执行 API 测试套件「{suite.name if suite else arguments.get("suite_id")}」（约 {count} 个请求）'
        if tool_name == 'confirm_run_ui_case':
            from apps.ui_automation.models import TestCase as UiCase
            case = UiCase.objects.filter(id=arguments.get('case_id')).first()
            return (f'将以 {arguments.get("engine", "playwright")} 无头模式执行 UI 用例'
                    f'「{case.name if case else arguments.get("case_id")}」')
        if tool_name == 'confirm_run_perf_scene':
            from apps.perf_testing.models import PerfScenario
            scene = PerfScenario.objects.filter(id=arguments.get('scene_id')).first()
            load = (scene.get_load_config() if scene else {}) or {}
            return (f'将对压测场景「{scene.name if scene else arguments.get("scene_id")}」发起压测'
                    f'（并发 {load.get("concurrency", "?")}，时长 {load.get("duration", "?")}s）')
        if tool_name == 'confirm_create_testcase':
            data = arguments.get('data') or {}
            return f'将在项目 {arguments.get("project_id")} 创建测试用例「{data.get("title", "未命名")}」'
    except Exception as exc:  # noqa: BLE001 - 预览失败不阻断流程
        logger.warning('构造 preview 失败 tool=%s: %s', tool_name, exc)
    return f'即将执行危险操作 {tool_name}'


ACTION_REGISTRY = {
    'confirm_run_api_suite': run_api_suite_action,
    'confirm_run_ui_case': run_ui_case_action,
    'confirm_run_perf_scene': run_perf_scene_action,
    'confirm_create_testcase': create_testcase_action,
}
