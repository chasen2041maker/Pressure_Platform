"""场景变量与占位符解析。

引用语法沿用平台既有的 ``${varName}``：
1. 先用虚拟用户上下文（场景变量 + 提取结果）替换命名变量；
2. 剩余未命中的 ``${func(args)}`` 交给 apps.core.variable_resolver 处理动态函数
   （如 ``${random_string(8)}`` / ``${timestamp()}``），不重复造轮子。
"""
import csv
import os
import random
import re
import string
import time
import uuid

PLACEHOLDER_RE = re.compile(r'\$\{([^{}]+)\}')
#: 双花括号占位符（与接口测试模块的 {{varName}} 风格保持一致）
DOUBLE_BRACE_RE = re.compile(r'\{\{([^{}]+?)\}\}')
#: 函数式占位符，如 ${random_string(8)} / ${timestamp()}
FUNC_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\s*\(.*\)$', re.S)

SECRET_MASK = '******'

_CHARSETS = {
    'alnum': string.ascii_letters + string.digits,
    'alpha': string.ascii_letters,
    'digit': string.digits,
    'lower': string.ascii_lowercase,
    'upper': string.ascii_uppercase,
    'hex': '0123456789abcdef',
}


def load_csv_file(path, encoding='utf-8'):
    """加载 CSV 为 [{col: value}]，返回 (rows, columns)。"""
    if not path or not os.path.exists(path):
        return [], []
    rows = []
    columns = []
    with open(path, 'r', encoding=encoding, errors='replace', newline='') as fh:
        reader = csv.DictReader(fh)
        columns = list(reader.fieldnames or [])
        for row in reader:
            rows.append({(k or '').strip(): (v or '') for k, v in row.items()})
    return rows, columns


class VariableContext:
    """单个虚拟用户的变量上下文。

    - 静态变量（CONSTANT / CSV 行）在用户初始化时确定；
    - 动态变量（RANDOM_* / UUID / TIMESTAMP / ENUM）每轮迭代重新求值；
    - 提取变量由步骤响应写入，供后续步骤引用。
    """

    def __init__(self, definitions, user_index=0, csv_data=None, base_url=None):
        """
        :param definitions: 场景变量定义列表
        :param user_index:  虚拟用户序号（用于 CSV 无锁取行）
        :param csv_data:    {file_id: {'rows': [...], 'columns': [...]}}
        :param base_url:    环境基址（env_config.base_url），会作为 base_url / baseUrl 内置变量注入
        """
        self.definitions = definitions or []
        self.user_index = user_index
        self.csv_data = csv_data or {}
        self.values = {}
        self._enum_cursor = {}
        self._csv_exhausted = False
        # 由提取器(extractors)写入的变量名集合：refresh 时跳过，避免被场景变量定义
        # (如同名 CONSTANT 占位)覆盖回静态值，从而保证跨步骤提取-引用链路稳定。
        self._extracted = set()
        self._base_url = (base_url or '').strip()
        self.refresh()
        # 兼容从接口测试导入的 {{baseUrl}} / {{base_url}}：若用户未显式定义，则用环境基址兜底
        if self._base_url:
            self.values.setdefault('base_url', self._base_url)
            self.values.setdefault('baseUrl', self._base_url)

    @property
    def csv_exhausted(self):
        return self._csv_exhausted

    def refresh(self):
        """重新求值全部场景变量（每轮迭代调用）。

        已被提取器写入的变量(_extracted)跳过——否则同名 CONSTANT 占位会把
        登录提取到的 token 等运行时凭证覆盖回空值，导致后续接口拿不到 token。
        """
        for item in self.definitions:
            name = (item or {}).get('name')
            if not name:
                continue
            if name in self._extracted:
                continue
            try:
                self.values[name] = self._evaluate(item)
            except Exception:  # noqa: BLE001 - 单个变量失败不影响整体
                self.values[name] = ''

    def set(self, name, value):
        """写入提取变量。

        提取值优先于场景变量定义：一旦变量被提取器写入，后续 refresh() 不再
        用定义值覆盖它，保证登录提取的 token 等动态凭证能跨步骤、跨迭代复用。
        """
        if name:
            self.values[name] = value
            self._extracted.add(name)

    def _evaluate(self, item):
        vtype = (item.get('type') or 'CONSTANT').upper()

        if vtype == 'CONSTANT':
            return item.get('value', '')

        if vtype == 'RANDOM_INT':
            lo = int(item.get('min', 0) or 0)
            hi = int(item.get('max', 100) or 0)
            if hi < lo:
                lo, hi = hi, lo
            return random.randint(lo, hi)

        if vtype == 'RANDOM_STRING':
            length = max(int(item.get('length', 8) or 8), 1)
            charset = _CHARSETS.get((item.get('charset') or 'alnum').lower(), _CHARSETS['alnum'])
            return ''.join(random.choice(charset) for _ in range(length))

        if vtype == 'ENUM':
            values = item.get('values') or []
            if not values:
                return ''
            strategy = (item.get('strategy') or 'ROUND_ROBIN').upper()
            if strategy == 'RANDOM':
                return random.choice(values)
            name = item.get('name')
            cursor = self._enum_cursor.get(name, self.user_index)
            self._enum_cursor[name] = cursor + 1
            return values[cursor % len(values)]

        if vtype == 'UUID':
            return str(uuid.uuid4())

        if vtype == 'TIMESTAMP':
            fmt = (item.get('format') or 'ms').lower()
            return int(time.time() * 1000) if fmt == 'ms' else int(time.time())

        if vtype == 'CSV':
            return self._csv_value(item)

        return item.get('value', '')

    def _csv_value(self, item):
        # 字段名以 data_file_id 为准（序列化器/preflight/级联删除都用它），
        # file_id 仅作历史数据兼容。
        file_id = item.get('data_file_id') or item.get('file_id')
        data = self.csv_data.get(str(file_id)) or self.csv_data.get(file_id) or {}
        rows = (data.get('rows') if isinstance(data, dict) else None) or []
        if not rows:
            return ''
        column = item.get('column')
        recycle = item.get('recycle', True)
        if recycle:
            # 每个虚拟用户按序号取行，无锁无冲突
            row = rows[self.user_index % len(rows)]
        else:
            if self.user_index >= len(rows):
                self._csv_exhausted = True
                return ''
            row = rows[self.user_index]
        if column:
            return row.get(column, '')
        # 未指定列时返回第一列
        return next(iter(row.values()), '')

    # ------------------------------------------------------------------ #
    # 占位符替换
    # ------------------------------------------------------------------ #
    def _render_double_braces(self, text):
        """替换 {{var}} 风格占位符（接口测试模块沿用此风格）。"""
        if not isinstance(text, str) or '{{' not in text:
            return text

        def _sub(match):
            key = match.group(1).strip()
            if key in self.values:
                value = self.values[key]
                return '' if value is None else str(value)
            return match.group(0)

        return DOUBLE_BRACE_RE.sub(_sub, text)

    def render(self, text):
        """替换文本中的 {{var}} 与 ${var}，未命中的 ${func()} 交由 core 动态函数解析器。

        注意：只有「函数式」占位符（形如 ``${func(...)}``）才转发给 core，
        普通命名变量未命中时保持原样。否则 core 会把 ``${token}`` 当成未知
        函数，在高并发下每个请求刷一条 WARNING 日志，既污染日志又拖慢压测。
        """
        if not isinstance(text, str):
            return text

        # 1. 先替换接口测试风格的双花括号
        text = self._render_double_braces(text)

        if '${' not in text:
            return text

        has_func = False

        def _sub(match):
            nonlocal has_func
            key = match.group(1).strip()
            if key in self.values:
                value = self.values[key]
                return '' if value is None else str(value)
            if FUNC_RE.match(key):
                has_func = True
            return match.group(0)

        result = PLACEHOLDER_RE.sub(_sub, text)

        if has_func:
            try:
                from apps.core.variable_resolver import resolve_variables
                result = resolve_variables(result)
            except Exception:  # noqa: BLE001 - 动态函数不可用时保持原样
                pass
        return result

    def render_dict(self, data):
        """递归替换 dict / list 中的占位符。"""
        if isinstance(data, dict):
            return {self.render(k) if isinstance(k, str) else k: self.render_dict(v)
                    for k, v in data.items()}
        if isinstance(data, list):
            return [self.render_dict(v) for v in data]
        if isinstance(data, str):
            return self.render(data)
        return data


def mask_secrets(definitions):
    """序列化时脱敏 secret 变量。"""
    result = []
    for item in definitions or []:
        item = dict(item or {})
        if item.get('secret'):
            item['value'] = SECRET_MASK
        result.append(item)
    return result


def merge_secret_values(new_definitions, old_definitions):
    """更新场景时，若 secret 变量值仍是掩码，保留库中原值。"""
    old_map = {(v or {}).get('name'): v for v in (old_definitions or [])}
    result = []
    for item in new_definitions or []:
        item = dict(item or {})
        if item.get('secret') and item.get('value') == SECRET_MASK:
            old = old_map.get(item.get('name')) or {}
            item['value'] = old.get('value', '')
        result.append(item)
    return result
