"""URL 基础地址（base URL）变量重写。

接口用例的 URL 常以 ``{{baseUrl}}/login`` 这类变量引用开头，导入压测场景时
用户希望能“选一个基础地址”把它落定（写死）或“保留变量、仅配置场景基址”。

这里只放纯函数（不碰 DB / Django），便于 unittest 直接跑、也便于前后端复用同一套规则。
"""

import re

# 匹配 URL 开头的 baseUrl / base_url 变量引用（大小写不敏感，允许 ``{{ baseUrl }}`` 这种带空格的写法）。
# 兼容 ``{{baseUrl}}``、``{{base_url}}``、``{{ BASEURL }}``、``{{base-url}}``。
_BASE_URL_TOKEN_RE = re.compile(r'^\s*/?\s*\{\{\s*base[_-]?url\s*\}\}\s*', re.IGNORECASE)

# 字面 base_url / baseUrl 前缀（不带花括号）：接口测试模块里大量用例直接把
# ``base_url/jar/login`` 当 URL 存，运行时引擎只解析 {{var}}/${var}，字面前缀
# 必须先归一化成变量引用才能被场景环境基址解析。前导负向后顾避免误伤
# ``xxx_base_url/`` 这类普通路径片段。
_LITERAL_BASE_URL_RE = re.compile(r'^\s*/?(?<![\w-])base[_-]?url(?=/|$)', re.IGNORECASE)

# 路径中连续 2 个以上斜杠但前面不是冒号（即非 scheme 的 `://`）→ 折叠成一个。
_REDUNDANT_SLASH_RE = re.compile(r'(?<!:)/{2,}')


def collapse_redundant_slashes(url):
    """折叠 URL 路径中的冗余双斜杠。

    数据来源（接口用例导入）里偶见 ``{{base_url}}/jar//login`` 这类双斜杠，
    执行时 base_url + path 拼接后会产生 ``https://host/jar//login`` 的错误地址。
    这里统一折叠为单斜杠，同时刻意保留 scheme 的 ``://``（如 ``https://``），
    也不动协议相对地址开头的 ``//``（前无冒号时已有单独规则保护注释）。
    """
    if not url:
        return url or ''
    return _REDUNDANT_SLASH_RE.sub('/', url)


def has_base_url_token(url):
    """该 URL 是否以 baseUrl/base_url 变量引用（含字面前缀）开头。"""
    if not url:
        return False
    return bool(_BASE_URL_TOKEN_RE.match(url) or _LITERAL_BASE_URL_RE.match(url))


def normalize_base_url_token(url):
    """把 URL 开头的字面 ``base_url/`` 前缀归一化为 ``{{base_url}}/`` 变量引用。

    keep 模式导入时使用：运行时 VariableContext 只解析 ``{{var}}`` 与 ``${var}``，
    字面前缀不归一化会导致 base_url 永远无法被场景环境基址替换。
    已经是 ``{{baseUrl}}`` 形式的 URL 原样返回。
    """
    if not url:
        return url or ''
    if _BASE_URL_TOKEN_RE.match(url):
        return url
    m = _LITERAL_BASE_URL_RE.match(url)
    if not m:
        return url
    rest = url[m.end():].lstrip('/')
    return f'{{{{base_url}}}}/{rest}' if rest else '{{base_url}}'


def rewrite_base_url_token(url, base_url):
    """若 ``url`` 以 baseUrl/base_url 变量引用开头，用具体的 ``base_url`` 替换它（归一化斜杠）。

    其它情况（已经是绝对地址、不含该 token、token 不是 baseUrl）一律原样返回，
    保证对既有导入行为零破坏。
    """
    if not base_url:
        return url or ''
    if not url:
        return url or ''
    m = _BASE_URL_TOKEN_RE.match(url)
    if not m:
        # 字面前缀也支持替换（replace 模式），先归一化再命中变量正则
        normalized = normalize_base_url_token(url)
        m = _BASE_URL_TOKEN_RE.match(normalized)
        if not m:
            return url
        url = normalized
    rest = url[m.end():]
    base = base_url.rstrip('/')
    if rest.startswith('/'):
        rest = rest[1:]
    return f'{base}/{rest}' if rest else base
