"""monitor 探针单元测试（纯函数级，mock requests，不依赖 DB）。

回归背景（2026-07-23）：
目标「国信智能助手登录接口」的登录响应为 RuoYi 网关风格
{code: 200, data: {access_token: ...}}，而 check_config 沿用了模板默认
token_path=obj.token，_login 取不到 token 误判 DOWN。
修复：配置路径取不到时按常见路径自动回退。
"""
from unittest import mock

import pytest

from apps.monitor.utils.probes import _login, _jsonpath_extract, _run_assertions, probe_http, probe_online


def _mk_resp(payload, status_code=200):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


BASE_CFG = {
    "login_url": "https://example.com/api/login",
    "username": "monit",
    "password": "x",
}


# 一个 feature-group：token 提取（配置路径 + 回退 + 全失败），参数化各边界
@pytest.mark.parametrize(
    "cfg_extra, payload, expect_ok, expect_token",
    [
        # 1) 回归用例：配置 obj.token，实际 token 在 data.access_token → 应回退成功
        (
            {"token_path": "obj.token"},
            {"code": 200, "msg": None,
             "data": {"access_token": "JWT-A", "expires_in": 525600}},
            True, "JWT-A",
        ),
        # 2) 配置路径本身命中 → 直接使用，不受回退影响
        (
            {"token_path": "obj.token"},
            {"status": 200, "obj": {"token": "JWT-B", "id": 7}},
            True, "JWT-B",
        ),
        # 3) 自定义非常规路径命中
        (
            {"token_path": "result.auth.jwt"},
            {"code": 200, "result": {"auth": {"jwt": "JWT-C"}}},
            True, "JWT-C",
        ),
        # 4) 顶层 token 回退
        (
            {"token_path": "obj.token"},
            {"code": 200, "token": "JWT-D"},
            True, "JWT-D",
        ),
        # 5) 哪里都没有 token → 失败，且提示已尝试的路径
        (
            {"token_path": "obj.token"},
            {"code": 200, "data": {"expires_in": 100}},
            False, None,
        ),
    ],
)
def test_login_token_extraction(cfg_extra, payload, expect_ok, expect_token):
    cfg = {**BASE_CFG, **cfg_extra}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(payload)):
        r = _login(cfg)
    assert r.ok is expect_ok, r.message
    if expect_ok:
        assert r.detail.get("token") == expect_token
    else:
        # 失败信息需可诊断：包含配置路径
        assert "obj.token" in r.message


def test_jsonpath_extract_basic():
    """传统点号路径提取（向后兼容）。"""
    d = {"a": {"b": {"c": 1}}}
    assert _jsonpath_extract(d, "a.b.c") == 1
    assert _jsonpath_extract(d, "a.x") is None
    assert _jsonpath_extract(d, "") is None


def test_login_non_object_json():
    """回归：登录响应为裸 JSON 字符串(非对象)时不应 500，应给出明确错误。"""
    cfg = {**BASE_CFG}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp("pong")):
        r = _login(cfg)
    assert r.ok is False
    assert "非 JSON 对象" in r.message


def test_http_json_code_bare_string_ok():
    """回归：json_code 模式遇到裸 JSON 字符串响应时回落到 HTTP 状态码(200→成功)，不 500。"""
    target = mock.Mock(method=None, url=None)
    cfg = {"url": "https://example.com/api/ping", "check_type": "json_code",
           "expect_code": 200}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp("pong", status_code=200)):
        r = probe_http(target, cfg)
    assert r.ok is True


def test_http_json_code_bare_string_status_mismatch():
    """裸字符串且状态码不符期望 → 明确失败，不 500。"""
    target = mock.Mock(method=None, url=None)
    cfg = {"url": "https://example.com/api/ping", "check_type": "json_code",
           "expect_code": 200}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp("pong", status_code=500)):
        r = probe_http(target, cfg)
    assert r.ok is False
    assert "非 JSON 对象" in r.message


def test_online_non_object_json():
    """回归：在线率统计响应为裸 JSON 字符串时不应 500（登录成功 + 统计非对象）。"""
    target = mock.Mock(method=None, url=None)
    cfg = {"login": {**BASE_CFG}, "base_url": "https://example.com",
           "statistics": {"endpoint": "/stat"}}
    login_resp = _mk_resp({"code": 200, "obj": {"token": "JWT-X", "id": 1}})
    stat_resp = _mk_resp("pong", status_code=200)

    # _login 现在使用 requests.request（不再是 requests.post）
    # 用 side_effect 区分两次调用：第一次返回登录响应，第二次返回统计响应
    def _fake_request(method, url, **kwargs):
        if "/stat" in url:
            return stat_resp
        return login_resp

    with mock.patch("apps.monitor.utils.probes.requests.request",
                    side_effect=_fake_request):
        r = probe_online(target, cfg)
    assert r.ok is False
    assert "非 JSON 对象" in r.message


def test_http_form_string_body_sent_as_data():
    """回归：body 为 application/x-www-form-urlencoded 字符串时，必须用 data= 原样发送，
    不能用 json=（否则 requests 会把字符串 JSON 化加引号，目标端按表单解析即 400）。"""
    target = mock.Mock(method=None, url=None)
    body = "_o=https%3A%2F%2Fwww.bjgxhy.com&d=wKQAB..."
    cfg = {
        "url": "https://banti.baidu.com/dr",
        "method": "POST",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        "body": body,
        "check_type": "http_status",
        "expected_status": 200,
    }
    captured = {}

    def _fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _mk_resp({"c": 200}, status_code=200)

    with mock.patch("apps.monitor.utils.probes.requests.request",
                    side_effect=_fake_request):
        r = probe_http(target, cfg)
    assert "json" not in captured, "表单字符串体不应走 json=，否则会被 JSON 化"
    assert captured.get("data") == body, "表单字符串应原样作为 data 发送"
    assert r.ok is True


def test_http_dict_body_json_content_type_sent_as_json():
    """对照：body 为 dict 且未指定表单 Content-Type 时，应走 json=。"""
    target = mock.Mock(method=None, url=None)
    cfg = {
        "url": "https://example.com/api",
        "method": "POST",
        "body": {"a": 1, "b": 2},
        "check_type": "http_status",
        "expected_status": 200,
    }
    captured = {}

    def _fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _mk_resp({"code": 200}, status_code=200)

    with mock.patch("apps.monitor.utils.probes.requests.request",
                    side_effect=_fake_request):
        r = probe_http(target, cfg)
    assert "json" in captured, "dict 体默认应走 json="
    assert captured.get("data") is None


def test_http_json_code_custom_field():
    """回归：json_code 模式支持自定义业务码字段名（json_field），
    例如目标接口返回 {"c":200,...} 时用 json_field='c' 判定。"""
    target = mock.Mock(method=None, url=None)
    cfg = {"url": "https://example.com/api/x", "check_type": "json_code",
           "expect_code": 200, "json_field": "c"}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"c": 200, "msg": "ok"}, status_code=200)):
        r = probe_http(target, cfg)
    assert r.ok is True


def test_http_json_code_string_code_matches_int_expect():
    """业务码为字符串 "200" 时应能匹配期望整数 200（容错）。"""
    target = mock.Mock(method=None, url=None)
    cfg = {"url": "https://example.com/api/x", "check_type": "json_code",
           "expect_code": 200, "json_field": "code"}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": "200"}, status_code=200)):
        r = probe_http(target, cfg)
    assert r.ok is True


def test_http_json_code_missing_field_reports_field_name():
    """业务码字段缺失时，错误信息应点明缺失的字段名，便于排查。"""
    target = mock.Mock(method=None, url=None)
    cfg = {"url": "https://example.com/api/x", "check_type": "json_code",
           "expect_code": 200, "json_field": "c"}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": 200}, status_code=200)):
        r = probe_http(target, cfg)
    assert r.ok is False
    assert "[c]" in r.message


# ============================================================
# 新增测试：JSONPath 提取 / 方法配置 / 断言校验 / URL 构建
# ============================================================

def test_jsonpath_extract_with_dollar_dot():
    """JSONPath $. 语法提取值。"""
    d = {"data": {"users": [{"name": "Alice"}, {"name": "Bob"}]}}
    assert _jsonpath_extract(d, "$.data.users[0].name") == "Alice"
    assert _jsonpath_extract(d, "$.data.users[1].name") == "Bob"


def test_jsonpath_extract_nonexistent():
    """JSONPath 路径不存在返回 None。"""
    d = {"data": {"code": 200}}
    assert _jsonpath_extract(d, "$.data.token") is None
    assert _jsonpath_extract(d, "$.nonexistent.field") is None


def test_jsonpath_extract_invalid_syntax():
    """非法 JSONPath 语法不抛异常，返回 None。"""
    d = {"a": 1}
    assert _jsonpath_extract(d, "$.[[[invalid") is None


def test_jsonpath_extract_non_dict():
    """非 dict 输入返回 None。"""
    assert _jsonpath_extract("not a dict", "$.a") is None
    assert _jsonpath_extract(None, "$.a") is None


def test_login_custom_method():
    """登录请求方法从 target_method 或 cfg.method 读取（不再硬编码 POST）。"""
    cfg = {**BASE_CFG, "method": "PUT"}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": 200, "token": "JWT-PUT"})):
        r = _login(cfg, target_method="PUT")
    assert r.ok is True
    assert r.detail.get("token") == "JWT-PUT"


def test_login_url_from_host_port_endpoint():
    """host + port + endpoint 拼接登录 URL（通过 target 参数传入）。"""
    cfg = {
        "scheme": "http",
        "endpoint": "/api/auth/login",
        "username": "admin",
        "password": "secret",
    }
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": 200, "token": "JWT-HOST"})):
        r = _login(cfg, target_host="192.168.1.100", target_port=8080)
    assert r.ok is True
    assert r.detail.get("token") == "JWT-HOST"


def test_login_url_host_without_port():
    """host + endpoint（��� port）拼接 URL 不含端口号。"""
    cfg = {
        "endpoint": "/api/login",
        "username": "admin",
        "password": "secret",
    }
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": 200, "token": "JWT-NOPORT"})):
        r = _login(cfg, target_host="example.com")
    assert r.ok is True


def test_login_method_fallback_to_target():
    """target.method 优先于 cfg.method，回退到 POST。"""
    cfg = {**BASE_CFG}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": 200, "token": "JWT-FB"})):
        r = _login(cfg, target_method="PUT", target_host=None, target_port=None, target_url=None)
    assert r.ok is True


def test_login_host_fallback_to_target():
    """通过 target_host + endpoint 构造登录 URL。"""
    cfg = {
        "endpoint": "/api/login",
        "username": "admin",
        "password": "secret",
    }
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": 200, "token": "JWT-THOST"})):
        r = _login(cfg, target_method=None, target_host="target.example.com", target_port=9090, target_url=None)
    assert r.ok is True

def test_login_url_from_target_url():
    """target.url 作为登录 URL 直接使用。"""
    cfg = {
        "username": "admin",
        "password": "secret",
    }
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": 200, "token": "JWT-TURL"})):
        r = _login(cfg, target_url="https://myapi.example.com/api/auth/login")
    assert r.ok is True
    assert r.detail.get("token") == "JWT-TURL"


def test_login_url_target_url_with_endpoint():
    """target.url 为基础地址时，与 endpoint 拼接。"""
    cfg = {
        "endpoint": "/api/login",
        "username": "admin",
        "password": "secret",
    }
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp({"code": 200, "token": "JWT-TE"})):
        r = _login(cfg, target_url="https://myapi.example.com")
    assert r.ok is True


# ---------- 断言测试 ----------

def test_assertions_all_pass():
    """断言全部通过 → 登录成功。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "$.data.code", "operator": "equals", "expect": 200},
            {"field": "$.data.role", "operator": "equals", "expect": "admin"},
        ],
    }
    resp = {"code": 200, "data": {"code": 200, "role": "admin", "token": "JWT-A1"}}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is True, r.message
    assert r.detail.get("token") == "JWT-A1"
    assert r.detail.get("assertions_passed") == 2


def test_assertions_partial_fail():
    """部分断言失败 → 登录失败，错误信息包含失败详情。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "code", "operator": "equals", "expect": 200},
            {"field": "data.role", "operator": "equals", "expect": "superadmin"},
        ],
    }
    resp = {"code": 200, "data": {"role": "user", "token": "JWT-F1"}}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is False
    assert "断言校验失败" in r.message
    assert "1/2" in r.message


def test_assertions_operator_exists():
    """exists 操作符：字段存在即通过。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "data.token", "operator": "exists"},
        ],
    }
    resp = {"code": 200, "data": {"token": "JWT-EX"}}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is True


def test_assertions_operator_not_exists():
    """not_exists 操作符：字段不存在即通过。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "error", "operator": "not_exists"},
        ],
    }
    resp = {"code": 200, "token": "JWT-NE"}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is True


def test_assertions_operator_contains():
    """contains 操作符。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "data.msg", "operator": "contains", "expect": "成功"},
        ],
    }
    resp = {"code": 200, "data": {"msg": "登录成功", "token": "JWT-CT"}}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is True


def test_assertions_operator_gt_lt():
    """数值比较操作符 gt/lt。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "data.level", "operator": "gt", "expect": 0},
            {"field": "data.level", "operator": "lt", "expect": 10},
        ],
    }
    resp = {"code": 200, "data": {"level": 5, "token": "JWT-GT"}}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is True
    assert r.detail.get("assertions_passed") == 2


def test_assertions_operator_regex():
    """regex 操作符：正则匹配。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "data.token", "operator": "regex", "expect": r"^JWT-"},
        ],
    }
    resp = {"code": 200, "data": {"token": "JWT-REGEX123"}}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is True


def test_assertions_custom_message():
    """断言失败时应包含自定义错误消息。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "code", "operator": "equals", "expect": 0, "message": "业务码异常"},
        ],
    }
    resp = {"code": 200, "token": "JWT-CM"}
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is False
    assert "业务码异常" in r.message


def test_assertions_json_field_extraction():
    """断言提取 JSONPath 深层嵌套字段（$.data.users[0].name）。"""
    cfg = {
        **BASE_CFG,
        "assertions": [
            {"field": "$.data.users[0].name", "operator": "equals", "expect": "Alice"},
            {"field": "$.data.users[0].role", "operator": "equals", "expect": "admin"},
        ],
    }
    resp = {
        "code": 200,
        "data": {
            "users": [{"name": "Alice", "role": "admin"}],
            "token": "JWT-DEEP",
        },
    }
    with mock.patch("apps.monitor.utils.probes.requests.request",
                    return_value=_mk_resp(resp)):
        r = _login(cfg)
    assert r.ok is True
    assert r.detail.get("assertions_passed") == 2
    assert r.detail.get("token") == "JWT-DEEP"


def test_run_assertions_direct():
    """直接测试 _run_assertions 函数。"""
    data = {"code": 200, "data": {"name": "test", "count": 5}}
    assertions = [
        {"field": "code", "operator": "equals", "expect": 200},
        {"field": "data.name", "operator": "equals", "expect": "test"},
        {"field": "data.count", "operator": "gte", "expect": 3},
    ]
    all_ok, failures, passed, total = _run_assertions(data, assertions)
    assert all_ok is True
    assert passed == 3
    assert total == 3
    assert failures == []


def test_run_assertions_partial_direct():
    """直接测试 _run_assertions 部分失败。"""
    data = {"code": 500}
    assertions = [
        {"field": "code", "operator": "equals", "expect": 200},
        {"field": "code", "operator": "gt", "expect": 400},
    ]
    all_ok, failures, passed, total = _run_assertions(data, assertions)
    assert all_ok is False
    assert passed == 1
    assert total == 2
    assert len(failures) == 1