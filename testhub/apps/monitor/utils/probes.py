"""监控中心探测模块。

平移 script_monit_api 的 5 类探测逻辑，做成「配置驱动 + 纯函数」：
- LOGIN  登录态可用性：模拟登录 → 取 token → 断言校验（支持可配置 method/host/port/JSONPath 提取/多断言）
- HTTP   接口存活：GET/POST → 校验 HTTP 状态码 / 业务码 / 子串（含可选前置登录）
- ONLINE 在线率：登录 → 调统计接口 → 解析 rate，低于阈值判失败
- DOCKER 容器状态：HTTP 访问 Docker daemon API（2375，免 SDK 依赖）→ 校验容器 running/重启次数
- SL651  遥测链路：TCP 建连 → 发测试报文 → 等 ACK；可选 MySQL 工况新鲜度/状态检查

每个探针接收 (target, config)，返回统一的 ProbeResult。
target 提供 url/method/host/port（通用字段），config 为解密后的 check_config（特异性配置）。
"""
import base64
import re
import socket
import time
import urllib3
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests
from jsonpath_ng import parse as jsonpath_parse
from jsonpath_ng.exceptions import JsonPathParserError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# SL651 默认测试报文（与原脚本一致，已验证可收发）
DEFAULT_SL651_FRAME = (
    "7E7E0000008888881234320078020205260615102001F1F1000088888848F0F0260615"
    "1020F46000000000FFFFFFFFFFFFFFFF1A1900000020190000002619000000F5C0000700"
    "0700070007FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF391A00000738121398761200008720"
    "E87C05428820AC11D942770813CC28000000032ECC28040000000003AE69"
)


@dataclass
class ProbeResult:
    ok: bool
    message: str
    latency_ms: Optional[int] = None
    http_status: Optional[int] = None
    detail: dict = field(default_factory=dict)


def _clip(s, n=300):
    s = str(s)
    return s if len(s) <= n else s[:n] + "..."


def _safe_json_dict(resp):
    """把响应体解析为 dict；解析失败或解析结果不是 dict（顶层是字符串/数组/数字）时返回 None。

    监控目标接口偶尔返回裸 JSON 字符串（如 "pong"）或非对象结构，
    直接 resp.json().get(...) 会在非 dict 上抛 AttributeError 导致 500。
    本函数统一兜底，调用方据此走 HTTP 状态码判定或返回明确的「非 JSON 对象」错误。
    """
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _friendly_http_error(e):
    """把 requests/urllib3 底层异常转成面向运维与用户的中文可读文案。

    默认实现会把 urllib3 的 `HTTPSConnectionPool(host=..., port=443):
    Read timed out.` 这类原始堆栈片段直接塞进告警正文，既不专业也难读。
    这里按异常类型映射到稳定的中文描述，避免暴露底层实现细节。
    """
    if isinstance(e, requests.exceptions.ReadTimeout):
        return "请求读取超时：服务器在限定时间内未返回响应"
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return "连接超时：无法在限定时间内与服务器建立连接"
    if isinstance(e, requests.exceptions.ConnectionError):
        reason = str(e)
        if "Name or service not known" in reason or "getaddrinfo" in reason:
            return "连接失败：域名无法解析（请检查 host / 域名配置）"
        if "refused" in reason:
            return "连接失败：目标端口被拒绝（服务未启动或端口错误）"
        return "连接失败：无法与服务器建立连接"
    if isinstance(e, requests.exceptions.SSLError):
        return "SSL/TLS 证书校验失败（请检查协议或关闭严格校验）"
    if isinstance(e, requests.exceptions.ProxyError):
        return "代理连接失败"
    if isinstance(e, requests.exceptions.TooManyRedirects):
        return "重定向次数过多"
    if isinstance(e, requests.exceptions.HTTPError):
        return f"HTTP 协议错误：{e}"
    return f"请求异常：{e}"


def _jsonpath_extract(data, path):
    """从 dict 中按路径提取值。支持两种路径语法：

    1. JSONPath 表达式（以 `$.` 开头，如 ``$.data.users[0].name``）：
       使用 jsonpath-ng 解析，支持数组索引、通配符、过滤器等完整 JSONPath 语法。

    2. 传统点号路径（向后兼容，如 ``obj.token``、``data.access_token``）：
       按 '.' 分割后逐层 .get()，简单高效。

    返回提取到的值，失败（路径不存在/JSONPath 语法错误/非 dict）返回 None。
    """
    if not path or not isinstance(data, dict):
        return None
    path_str = str(path).strip()
    if not path_str:
        return None

    # JSONPath 表达式：以 $. 开头
    if path_str.startswith("$.") or path_str.startswith("$["):
        try:
            expr = jsonpath_parse(path_str)
            matches = expr.find(data)
            if matches:
                return matches[0].value
            return None
        except (JsonPathParserError, Exception):
            return None

    # 传统点号路径（向后兼容）
    cur = data
    for part in path_str.split("."):
        if isinstance(cur, dict) and part:
            cur = cur.get(part)
        else:
            return None
    return cur


# 常见后端风格的 token/uid 路径回退表（配置路径取不到时按序尝试）
# 覆盖：若依/RuoYi 网关(data.access_token)、经典 obj.token、扁平 token 等
# 支持 JSONPath 和点号路径混合
TOKEN_FALLBACK_PATHS = (
    "obj.token", "data.access_token", "data.token", "access_token", "token",
)
UID_FALLBACK_PATHS = (
    "obj.id", "obj.uid", "data.user_id", "data.uid", "data.id", "uid", "id",
)


def _extract_with_fallback(data, primary_path, fallback_paths):
    """先按配置路径取值；取不到再按回退表尝试。返回 (value, used_path)。

    支持 JSONPath 表达式（$.开头）和传统点号路径（向后兼容）。
    """
    val = _jsonpath_extract(data, primary_path)
    if val is not None:
        return val, primary_path
    for p in fallback_paths:
        if p == primary_path:
            continue
        val = _jsonpath_extract(data, p)
        if val is not None:
            return val, p
    return None, None


# ---------- 断言校验 ----------

# 断言操作符及其比较逻辑
def _assert_compare(value, operator, expect):
    """对提取到的值执行断言比较。返回 (ok: bool, description: str)。"""
    op = (operator or "").strip().lower()
    str_val = str(value) if value is not None else ""

    if op == "equals":
        # 宽松相等：尝试数值比较
        try:
            ok = float(value) == float(expect)
        except (TypeError, ValueError):
            ok = str(value) == str(expect)
        return ok, f"期望值={expect}，实际值={value}"

    elif op == "not_equals":
        try:
            ok = float(value) != float(expect)
        except (TypeError, ValueError):
            ok = str(value) != str(expect)
        return ok, f"期望不等于={expect}，实际值={value}"

    elif op == "contains":
        ok = str(expect) in str_val
        return ok, f"期望包含 [{expect}]，实际值={_clip(value)}"

    elif op == "not_contains":
        ok = str(expect) not in str_val
        return ok, f"期望不包含 [{expect}]，实际值={_clip(value)}"

    elif op == "exists":
        ok = value is not None
        return ok, f"字段存在={ok}"

    elif op == "not_exists":
        ok = value is None
        return ok, f"字段不存在={ok}"

    elif op == "gt":
        try:
            ok = float(value) > float(expect)
        except (TypeError, ValueError):
            ok = False
        return ok, f"期望 > {expect}，实际值={value}"

    elif op == "lt":
        try:
            ok = float(value) < float(expect)
        except (TypeError, ValueError):
            ok = False
        return ok, f"期望 < {expect}，实际值={value}"

    elif op == "gte":
        try:
            ok = float(value) >= float(expect)
        except (TypeError, ValueError):
            ok = False
        return ok, f"期望 >= {expect}，实际值={value}"

    elif op == "lte":
        try:
            ok = float(value) <= float(expect)
        except (TypeError, ValueError):
            ok = False
        return ok, f"期望 <= {expect}，实际值={value}"

    elif op == "regex":
        try:
            ok = bool(re.search(str(expect), str_val))
        except re.error:
            ok = False
        return ok, f"正则 /{expect}/ 匹配" + ("" if ok else f"失败，实际值={_clip(value)}")

    else:
        return False, f"未知断言操作符: {operator}"


def _run_assertions(data, assertions):
    """对响应 data 执行断言列表校验。

    assertions: [{"field": "$.data.code", "operator": "equals", "expect": 200, "message": "业务码异常"}, ...]
    返回 (all_ok: bool, failures: list[str], passed: int, total: int)
    """
    if not assertions:
        return True, [], 0, 0
    failures = []
    passed = 0
    for i, rule in enumerate(assertions):
        field = rule.get("field", "")
        operator = rule.get("operator", "equals")
        expect = rule.get("expect")
        custom_msg = rule.get("message", "")

        val = _jsonpath_extract(data, field)
        ok, desc = _assert_compare(val, operator, expect)
        if ok:
            passed += 1
        else:
            label = f"断言{i+1}" + (f"[{custom_msg}]" if custom_msg else "")
            failures.append(f"{label}失败: 字段 {field} {desc}")
    return len(failures) == 0, failures, passed, len(assertions)


def _login(login_cfg, target_method=None, target_host=None, target_port=None, target_url=None):
    """通用登录：返回带 token/uid 的 ProbeResult。

    URL / 方法 / 主机 / 端口 来源于模型的「基础信息」字段（不再从 check_config 读取）：
      - 登录 URL 构造（优先级从高到低）：
          1. target_url（基础信息的 URL 字段，既可以是完整 URL 也可以是 base）
          2. target_host + target_port + cfg.endpoint 拼接
          3. cfg.login_url / cfg.base_url + cfg.endpoint（兼容旧配置）
      - 请求方法：target.method（基础信息字段），回退 "POST"

    断言配置：
      - assertions: [{field, operator, expect, message}, ...]
        若配置了 assertions，以断言结果为准（跳过旧的 status/code 硬编码校验）；
        若未配置 assertions，走旧版兼容逻辑（status==200/code==200 + token 提取）。
    """
    # ---------- 构造登录 URL ----------
    endpoint = login_cfg.get("endpoint", "")
    scheme = login_cfg.get("scheme", "https")
    login_url = None

    # 方式 1：target.url（基础信息的 URL 字段）优先
    if target_url:
        target_url = str(target_url).strip()
        if target_url.startswith("http://") or target_url.startswith("https://"):
            # 完整 URL：直接使用；若有 endpoint，urljoin 拼接
            login_url = urljoin(target_url, endpoint) if endpoint else target_url
        elif endpoint:
            # target.url 是裸 host（或 host:port），与 endpoint 拼接
            login_url = f"{scheme}://{target_url.rstrip('/')}{endpoint}"
        else:
            # target.url 缺少协议前缀，补上
            login_url = target_url if target_url.startswith("http") else f"{scheme}://{target_url}"

    # 方式 2：target.host + target.port + endpoint 拼接
    if not login_url and target_host and endpoint:
        port_str = f":{target_port}" if target_port else ""
        login_url = f"{scheme}://{target_host}{port_str}{endpoint}"

    # 方式 3：cfg.login_url（兼容旧配置，存量数据可能没有填基础信息）
    if not login_url:
        login_url = login_cfg.get("login_url")

    # 方式 4：cfg.base_url + endpoint（兼容旧配置）
    if not login_url:
        base = login_cfg.get("base_url")
        if base and endpoint:
            login_url = urljoin(base, endpoint)

    if not login_url:
        return ProbeResult(False, "登录配置缺少 URL：请在基础信息中填写 URL/主机/端口，或在探测配置中填写 endpoint")

    # ---------- 凭证 ----------
    username_field = login_cfg.get("username_field", "username")
    username = login_cfg.get(username_field) or login_cfg.get("username")
    password = login_cfg.get("password")
    if not username or password is None:
        return ProbeResult(False, "登录配置缺少 username/password")
    is_b64 = bool(login_cfg.get("is_base64"))
    pw = base64.b64encode(str(password).encode()).decode() if is_b64 else str(password)
    body = {username_field: username, "password": pw}

    # ---------- 请求方法（来自基础信息字段，不再硬编码 POST）----------
    method = (target_method or login_cfg.get("method") or "POST").upper()

    # ---------- Token/UID 路径（支持 JSONPath）----------
    token_path = login_cfg.get("token_path", "obj.token")
    uid_path = login_cfg.get("uid_path", "obj.id")
    timeout = int(login_cfg.get("timeout", 15))

    # ---------- 断言配置 ----------
    assertions = login_cfg.get("assertions")
    # 兼容前端 textarea 传入的 JSON 字符串（assertions_json 键）
    if not assertions:
        assertions_json_str = login_cfg.get("assertions_json", "")
        if assertions_json_str and isinstance(assertions_json_str, str):
            try:
                import json
                parsed = json.loads(assertions_json_str)
                if isinstance(parsed, list):
                    assertions = parsed
            except (json.JSONDecodeError, ValueError):
                pass

    t0 = time.monotonic()
    try:
        resp = requests.request(method, login_url, json=body, timeout=timeout, verify=False)
        ms = int((time.monotonic() - t0) * 1000)
        data = _safe_json_dict(resp)

        # ---------- 自定义断言模式（优先）----------
        if assertions:
            if data is None:
                return ProbeResult(False, f"登录响应非 JSON 对象: {_clip(resp.text)}",
                                    ms, resp.status_code, {"body": _clip(resp.text)})
            all_ok, failures, passed_cnt, total_cnt = _run_assertions(data, assertions)
            if not all_ok:
                return ProbeResult(False, f"断言校验失败 ({passed_cnt}/{total_cnt}): {'; '.join(failures)}",
                                    ms, resp.status_code,
                                    {"resp": _clip(data), "assertions": {"passed": passed_cnt, "total": total_cnt, "failures": failures}})

            # 断言全部通过 → 提取 token（如果配置了 token_path）
            token, token_used = _extract_with_fallback(data, token_path, TOKEN_FALLBACK_PATHS)
            uid, _ = _extract_with_fallback(data, uid_path, UID_FALLBACK_PATHS)
            detail = {"token_len": len(str(token)) if token else 0}
            if token:
                detail["token"] = str(token)
                detail["token_path_used"] = token_used
            if uid:
                detail["uid"] = uid
            detail["assertions_passed"] = passed_cnt
            return ProbeResult(True, f"登录成功，断言全部通过({passed_cnt}/{total_cnt})",
                                ms, resp.status_code, detail)

        # ---------- 兼容旧逻辑（无 assertions 配置时）----------
        if resp.status_code != 200:
            return ProbeResult(False, f"HTTP {resp.status_code}", ms, resp.status_code,
                                {"body": _clip(resp.text)})
        if data is None:
            return ProbeResult(False, f"登录响应非 JSON 对象: {_clip(resp.text)}",
                                ms, resp.status_code, {"body": _clip(resp.text)})
        ok = (data.get("status") == 200 or data.get("code") == 200 or data.get("status") is True)
        if not ok:
            return ProbeResult(False, f"登录业务码异常 status={data.get('status')} code={data.get('code')}",
                                ms, resp.status_code, {"resp": _clip(data)})
        token, token_used = _extract_with_fallback(data, token_path, TOKEN_FALLBACK_PATHS)
        uid, _ = _extract_with_fallback(data, uid_path, UID_FALLBACK_PATHS)
        if not token:
            tried = [token_path] + [p for p in TOKEN_FALLBACK_PATHS if p != token_path]
            return ProbeResult(False,
                                f"响应未找到 token (已尝试路径: {', '.join(tried)})",
                                ms, resp.status_code, {"resp": _clip(data)})
        note = "" if token_used == token_path else f"（自动回退路径 {token_used}）"
        return ProbeResult(True, f"登录成功{note}", ms, resp.status_code,
                           {"token": str(token), "uid": uid,
                            "token_path_used": token_used})
    except requests.RequestException as e:
        return ProbeResult(False, f"登录失败：{_friendly_http_error(e)}", int((time.monotonic() - t0) * 1000))
    except ValueError as e:
        return ProbeResult(False, f"登录响应非 JSON: {e}", int((time.monotonic() - t0) * 1000))


def probe_login(target, cfg):
    """登录态可用性：登录成功且能取到 token 即 UP。

    URL/方法/主机/端口 来源于基础信息的 target 字段（不再从 check_config 读取）。
    支持 JSONPath 表达式提取 token/uid（如 $.data.users[0].token）。
    支持 assertions 自定义断言校验（如校验业务码、角色、权限等字段）。
    """
    lr = _login(cfg,
                target_method=getattr(target, 'method', None),
                target_host=getattr(target, 'host', None),
                target_port=getattr(target, 'port', None),
                target_url=getattr(target, 'url', None))
    if not lr.ok:
        return lr
    return ProbeResult(True, lr.message, lr.latency_ms, lr.http_status,
                        lr.detail)


def probe_http(target, cfg):
    """接口存活：可选前置登录 → 发请求 → 校验状态码/业务码/子串。"""
    token = None
    login_cfg = cfg.get("login")
    if login_cfg:
        lr = _login(login_cfg,
                    target_method=getattr(target, 'method', None),
                    target_host=getattr(target, 'host', None),
                    target_port=getattr(target, 'port', None),
                    target_url=getattr(target, 'url', None))
        if not lr.ok:
            return ProbeResult(False, f"前置登录失败: {lr.message}", lr.latency_ms, lr.http_status)
        token = lr.detail.get("token")

    method = (cfg.get("method") or target.method or "GET").upper()
    url = cfg.get("url") or target.url
    if not url:
        return ProbeResult(False, "缺少请求 url")

    headers = dict(cfg.get("headers") or {})
    if token:
        if cfg.get("token_header") == "X-Token":
            headers["X-Token"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"

    params = cfg.get("params")
    body = cfg.get("body")
    timeout = int(cfg.get("timeout", 15))
    check_type = cfg.get("check_type", "http_status")

    # 请求体处理：尊重 Content-Type，避免把表单/纯文本字符串误当作 JSON 发送。
    # 典型问题：body 为 application/x-www-form-urlencoded 字符串（如 "_o=...&d=..."），
    # 若用 json= 发送，requests 会把字符串 JSON 化（加引号/转义），目标端按表单解析即 400。
    content_type = (headers.get("Content-Type") or "").lower()
    if isinstance(body, str):
        req_kw = {"data": body}                       # 字符串原样发送
    elif isinstance(body, dict):
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            req_kw = {"data": body}                  # 由 requests 按表单编码
        else:
            req_kw = {"json": body}                  # 默认按 JSON 发送
    else:
        req_kw = {}                                  # body 为 None/其他：不发送请求体

    t0 = time.monotonic()
    try:
        resp = requests.request(method, url, headers=headers, params=params,
                                timeout=timeout, verify=False, **req_kw)
        ms = int((time.monotonic() - t0) * 1000)
        if check_type == "json_code":
            expect = int(cfg.get("expect_code", 200))
            data = _safe_json_dict(resp)
            if data is None:
                # 响应非 JSON 或非对象（如裸字符串 "pong"）：无法取业务码，回落 HTTP 状态码判定
                if resp.status_code == expect:
                    return ProbeResult(True, "HTTP 状态码符合（响应无业务码对象）", ms, resp.status_code)
                return ProbeResult(False, "响应非 JSON 对象，无法解析业务码",
                                    ms, resp.status_code, {"body": _clip(resp.text)})
            # 业务码字段名可配置（默认 "code"），兼容响应字段为 "c"/"status" 等场景
            code_field = cfg.get("json_field", "code")
            raw_code = data.get(code_field)
            if raw_code is None:
                return ProbeResult(False, f"响应缺少业务码字段 [{code_field}]",
                                    ms, resp.status_code, {"resp": _clip(data)})
            # 容错：业务码可能是字符串 "200"，与期望整数比较时统一转 int
            try:
                code_val = int(raw_code)
            except (TypeError, ValueError):
                code_val = raw_code
            if code_val == expect:
                return ProbeResult(True, "业务码符合", ms, resp.status_code, {code_field: code_val})
            return ProbeResult(False, f"业务码={raw_code} 期望 {expect}",
                                ms, resp.status_code, {"resp": _clip(data)})

        # 默认：http_status
        expect = int(cfg.get("expected_status", 200))
        if resp.status_code != expect:
            return ProbeResult(False, f"HTTP {resp.status_code} 期望 {expect}",
                                ms, resp.status_code, {"body": _clip(resp.text)})
        contains = cfg.get("expect_contains")
        if contains and contains not in resp.text:
            return ProbeResult(False, f"响应未包含预期子串: {contains}",
                                ms, resp.status_code, {"body": _clip(resp.text)})
        suffix = f"，含预期子串 [{contains}]" if contains else ""
        return ProbeResult(True, f"HTTP 状态符合{suffix}", ms, resp.status_code)
    except requests.RequestException as e:
        return ProbeResult(False, f"接口探测失败：{_friendly_http_error(e)}", int((time.monotonic() - t0) * 1000))


def probe_online(target, cfg):
    """在线率：登录 → 调统计接口 → 解析 rate，低于阈值判失败。"""
    login_cfg = cfg.get("login")
    if not login_cfg:
        return ProbeResult(False, "在线率探测需要 login 配置")
    lr = _login(login_cfg,
                target_method=getattr(target, 'method', None),
                target_host=getattr(target, 'host', None),
                target_port=getattr(target, 'port', None),
                target_url=getattr(target, 'url', None))
    if not lr.ok:
        return ProbeResult(False, f"登录失败: {lr.message}", lr.latency_ms, lr.http_status)

    base_url = cfg.get("base_url") or target.url
    stat_cfg = cfg.get("statistics") or {}
    endpoint = stat_cfg.get("endpoint") or cfg.get("statistics_endpoint")
    if not endpoint:
        return ProbeResult(False, "缺少 statistics.endpoint")
    url = urljoin(base_url, endpoint)

    labelname = cfg.get("labelname")
    method = (stat_cfg.get("method") or "POST").upper()
    token_header = cfg.get("token_header", "X-Token")
    headers = {token_header: lr.detail.get("token")} if lr.detail.get("token") else {}
    payload = {"uid": lr.detail.get("uid"), "pageNo": 1, "pageSize": 20, "labelname": labelname}
    timeout = int(cfg.get("timeout", 15))
    warning = float(cfg.get("warning_threshold", 0))

    t0 = time.monotonic()
    try:
        resp = requests.request(method, url, json=payload, headers=headers,
                                timeout=timeout, verify=False)
        ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            return ProbeResult(False, f"HTTP {resp.status_code}", ms, resp.status_code,
                                {"body": _clip(resp.text)})
        data = _safe_json_dict(resp)
        if data is None:
            return ProbeResult(False, f"在线率响应非 JSON 对象: {_clip(resp.text)}",
                                ms, resp.status_code, {"body": _clip(resp.text)})
        if data.get("status") != 200:
            return ProbeResult(False, f"业务码={data.get('status')} 期望 200",
                                ms, resp.status_code, {"resp": _clip(data)})
        obj = data.get("obj", {}) or {}
        rate = float(obj.get("rate", 0))
        online = int(obj.get("onlineall", 0))
        total = int(obj.get("all", 1))
        ok = rate >= warning
        msg = f"在线率 {rate}% (在线 {online}/{total})" + ("" if ok else f"，低于阈值 {warning}%")
        return ProbeResult(ok, msg, ms, resp.status_code,
                           {"rate": rate, "online": online, "total": total, "threshold": warning})
    except (requests.RequestException, ValueError, TypeError) as e:
        elapsed = int((time.monotonic() - t0) * 1000) if "t0" in dir() else None
        err = _friendly_http_error(e) if isinstance(e, requests.RequestException) else f"响应解析异常：{e}"
        return ProbeResult(False, f"在线率探测失败：{err}", elapsed)


def probe_docker(target, cfg):
    """容器状态：HTTP 访问 Docker daemon API（2375），校验容器 running + 重启次数。

    不依赖 docker SDK，直接走 HTTP（与 docker SDK 的 TCP 客户端等价）。
    """
    host = cfg.get("host") or target.host
    port = int(cfg.get("port") or target.port or 2375)
    tls = bool(cfg.get("tls"))
    containers = cfg.get("containers") or []
    max_restart = int(cfg.get("max_restart", 3))
    timeout = int(cfg.get("timeout", 10))
    scheme = "https" if tls else "http"
    url = f"{scheme}://{host}:{port}/containers/json?all=1"

    t0 = time.monotonic()
    try:
        resp = requests.get(url, timeout=timeout, verify=False)
        ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            return ProbeResult(False, f"Docker API HTTP {resp.status_code}", ms, resp.status_code,
                                {"body": _clip(resp.text)})
        items = resp.json()
        if not isinstance(items, list):
            return ProbeResult(False, f"Docker API 响应非容器数组: {_clip(resp.text)}",
                                ms, resp.status_code, {"body": _clip(resp.text)})
        # 容器可能以 /name 或 name 出现在 Names 列表
        by_name = {}
        for c in items:
            names = c.get("Names") or []
            key = names[0].lstrip("/") if names else c.get("Id", "")
            by_name[key] = c
            by_name[c.get("Id", "")] = c

        problems = []
        for name in containers:
            c = by_name.get(name)
            if c is None:
                problems.append(f"{name}: 容器不存在")
                continue
            if c.get("State") != "running":
                problems.append(f"{name}: 状态 {c.get('State')}")
                continue
            rc = c.get("RestartCount", 0)
            if rc > max_restart:
                problems.append(f"{name}: 重启 {rc} 次(>{max_restart})")

        if problems:
            return ProbeResult(False, "；".join(problems), ms, resp.status_code,
                               {"problems": problems, "total": len(items)})
        if containers:
            return ProbeResult(True, f"全部 {len(containers)} 个容器正常", ms, resp.status_code,
                               {"checked": containers, "total": len(items)})
        return ProbeResult(True, f"Docker 守护进程可达 ({len(items)} 个容器)", ms, resp.status_code,
                           {"total": len(items)})
    except requests.RequestException as e:
        return ProbeResult(False, f"连接 Docker 失败：{_friendly_http_error(e)}", int((time.monotonic() - t0) * 1000))


def _sl651_db_check(db):
    """SL651 数据库工况检查：最新记录新鲜度 + 在线状态。返回 (ok, msg, detail)。"""
    try:
        import pymysql
    except ImportError:
        return False, "pymysql 未安装", {}
    try:
        conn = pymysql.connect(
            host=db["host"], port=int(db["port"]), user=db["user"],
            password=db.get("password", ""), database=db["db"],
            connect_timeout=int(db.get("connect_timeout", 10)), charset="utf8mb4",
        )
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            where = db.get("where_clause")
            where_sql = f"WHERE {where}" if where else ""
            time_field = db["time_field"]
            sql = f"SELECT * FROM {db['table']} {where_sql} ORDER BY {time_field} DESC LIMIT 1"
            cursor.execute(sql)
            row = cursor.fetchone()
        conn.close()
        if not row:
            return False, "数据库无工况记录", {}

        from datetime import datetime
        raw_time = row.get(time_field)
        if not raw_time:
            return False, f"记录缺少时间字段 {time_field}", {}
        if isinstance(raw_time, str):
            try:
                row_time = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return False, f"时间格式无效: {raw_time}", {}
        else:
            row_time = raw_time

        from datetime import datetime as _dt
        lag = (_dt.now() - row_time).total_seconds()
        max_lag = int(db.get("status_data_max_lag", 3600))
        if lag > max_lag:
            return False, f"工况数据过期 {lag:.0f}s(>{max_lag}s)", {"lag": lag}

        status_field = db.get("status_field")
        online_value = db.get("online_value")
        if status_field is not None and online_value is not None:
            cur = row.get(status_field)
            if cur is None:
                return True, "时间新鲜(无状态字段)", {"lag": lag}
            if str(cur).strip().lower() != str(online_value).strip().lower():
                return False, f"工况状态异常 当前 {cur} 期望 {online_value}", {"lag": lag, "status": cur}
        return True, "数据库工况正常", {"lag": lag}
    except Exception as e:
        return False, f"数据库检查异常: {e}", {}


def probe_sl651(target, cfg):
    """遥测链路：TCP 建连 → 发测试报文 → 等 ACK；可选 MySQL 工况检查。"""
    host = cfg.get("host") or target.host
    port = int(cfg.get("port") or target.port or 10000)
    connect_timeout = int(cfg.get("connect_timeout", 15))
    rw_timeout = int(cfg.get("rw_timeout", 15))
    ack_wait = int(cfg.get("ack_wait_timeout", 15))
    frame_hex = cfg.get("frame") or DEFAULT_SL651_FRAME

    t0 = time.monotonic()
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=connect_timeout)
        sock.settimeout(rw_timeout)
        frame = bytes.fromhex(frame_hex)
        sock.sendall(frame)

        reply_found = False
        reply_data = b""
        deadline = time.monotonic() + ack_wait
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                reply_data += chunk
                if any(b in chunk for b in (0x06, 0x04, 0x1B, 0x15)):
                    reply_found = True
                    break
            except socket.timeout:
                continue
            except OSError:
                break

        if not reply_found:
            return ProbeResult(False, f"未收到服务器回复(等待 {ack_wait}s)",
                                int((time.monotonic() - t0) * 1000))

        ms = int((time.monotonic() - t0) * 1000)
        db = cfg.get("db")
        if db:
            db_ok, db_msg, db_detail = _sl651_db_check(db)
            if not db_ok:
                return ProbeResult(False, f"TCP 链路正常但数据库工况异常: {db_msg}", ms,
                                    detail={"reply_len": len(reply_data), "db": db_detail})
            return ProbeResult(True, "TCP 链路正常，数据库工况正常", ms,
                               detail={"reply_len": len(reply_data), "db": db_detail})
        return ProbeResult(True, "TCP 链路正常，已收到服务器回复", ms,
                           detail={"reply_len": len(reply_data),
                                   "reply_hex": reply_data[:32].hex()})
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return ProbeResult(False, f"链路异常: {e}", int((time.monotonic() - t0) * 1000))
    except ValueError as e:
        return ProbeResult(False, f"测试报文非法: {e}", int((time.monotonic() - t0) * 1000))
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


DISPATCH = {
    "LOGIN": probe_login,
    "HTTP": probe_http,
    "ONLINE": probe_online,
    "DOCKER": probe_docker,
    "SL651": probe_sl651,
}


def run_probe(target, cfg):
    """按 target.type 分发到对应探针。"""
    fn = DISPATCH.get(target.type)
    if not fn:
        return ProbeResult(False, f"未知探测类型: {target.type}")
    return fn(target, cfg or {})
