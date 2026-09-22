"""JMeter .jmx 脚本静态解析。

为什么需要它：
- 「上传脚本」模式下，平台不再拥有 load_config 这一唯一真相源——线程数、时长、
  目标域名全都藏在用户上传的 .jmx 里。如果不解析，preflight 就成了摆设：
  用户可以上传一个 5000 线程压生产库的脚本，平台一路放行。
- 解析结果同时用于前端展示（上传后立刻看到脚本摘要，确认传对了文件）。

设计约束：
- 纯函数 + 仅依赖标准库，不触碰 DB / Django settings，便于 unittest 直跑。
- 解析失败不抛异常，统一以 {'valid': False, 'error': ...} 返回，调用方决定如何提示。
- 对 JMeter 属性函数（如 ${__P(threads,50)}）不做求值，识别为「不可静态判定」，
  由上层降级为 warning 而不是 error——否则参数化脚本会被一刀切拒绝。
"""
import os
import xml.etree.ElementTree as ET

# 单个 .jmx 的体积上限：正常脚本几十 KB，超过 10MB 基本是误传或塞了监听器结果
MAX_JMX_SIZE = 10 * 1024 * 1024

# 线程组标签（取 tag 最后一段，兼容 bzm 插件的全限定类名）
THREAD_GROUP_TAGS = {
    'ThreadGroup',
    'SetupThreadGroup',
    'PostThreadGroup',
    'ConcurrencyThreadGroup',
    'ArrivalsThreadGroup',
    'FreeFormArrivalsThreadGroup',
    'SteppingThreadGroup',
    'UltimateThreadGroup',
}
# 不计入压测并发的辅助线程组
NON_LOAD_THREAD_GROUP_TAGS = {'SetupThreadGroup', 'PostThreadGroup'}

PROP_TAGS = ('stringProp', 'boolProp', 'intProp', 'longProp', 'doubleProp', 'floatProp')


def _local(tag):
    """取标签名最后一段：com.blazemeter...ConcurrencyThreadGroup -> ConcurrencyThreadGroup。"""
    return str(tag or '').rsplit('.', 1)[-1]


def _props(elem):
    """收集元素的直接子属性节点，返回 {name: text}。"""
    out = {}
    for child in list(elem):
        if child.tag in PROP_TAGS:
            name = child.attrib.get('name')
            if name:
                out[name] = (child.text or '').strip()
    return out


def _nested_props(elem):
    """收集 elementProp 内部一层的属性（LoopController.loops 等藏在这里）。"""
    out = {}
    for child in list(elem):
        if child.tag == 'elementProp':
            out.update(_props(child))
    return out


def _is_dynamic(value):
    """是否含 JMeter 变量/函数，无法静态求值。"""
    return '${' in str(value or '')


def _to_int(value, default=None):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _to_bool(value):
    return str(value or '').strip().lower() == 'true'


def inspect_jmx_text(text):
    """解析 .jmx 文本，返回结构化摘要。

    返回:
        {
          'valid': bool, 'error': str,
          'test_plan_name': str, 'jmeter_version': str,
          'thread_groups': [{name, type, enabled, num_threads, ramp_time,
                             duration, loops, scheduler, dynamic}],
          'total_threads': int|None,   # None 表示存在动态表达式，无法静态判定
          'max_duration': int|None,
          'sampler_count': int, 'controller_count': int,
          'hosts': [str], 'csv_datasets': [{filename, variable_names}],
          'has_dynamic_props': bool,
        }
    """
    result = {
        'valid': False,
        'error': '',
        'test_plan_name': '',
        'jmeter_version': '',
        'thread_groups': [],
        'total_threads': 0,
        'max_duration': 0,
        'sampler_count': 0,
        'controller_count': 0,
        'hosts': [],
        'csv_datasets': [],
        'has_dynamic_props': False,
    }

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        result['error'] = f'.jmx 不是合法的 XML：{exc}'
        return result

    if _local(root.tag) != 'jmeterTestPlan':
        result['error'] = (f'.jmx 根节点应为 <jmeterTestPlan>，实际为 <{root.tag}>，'
                           f'请确认上传的是 JMeter 测试计划而不是其它 XML')
        return result

    result['jmeter_version'] = root.attrib.get('jmeter', '')

    hosts = set()
    total_threads = 0
    max_duration = 0
    threads_dynamic = False
    duration_dynamic = False

    for elem in root.iter():
        tag = _local(elem.tag)

        if tag == 'TestPlan' and not result['test_plan_name']:
            result['test_plan_name'] = elem.attrib.get('testname', '')
            continue

        if tag in THREAD_GROUP_TAGS:
            props = _props(elem)
            nested = _nested_props(elem)
            raw_threads = props.get('ThreadGroup.num_threads') or props.get('TargetLevel') or ''
            raw_ramp = props.get('ThreadGroup.ramp_time') or props.get('RampUp') or ''
            raw_duration = props.get('ThreadGroup.duration') or props.get('Hold') or ''
            raw_loops = nested.get('LoopController.loops') or props.get('Iterations') or ''
            dynamic = any(_is_dynamic(v) for v in (raw_threads, raw_ramp, raw_duration, raw_loops))

            group = {
                'name': elem.attrib.get('testname', tag),
                'type': tag,
                'enabled': elem.attrib.get('enabled', 'true') != 'false',
                'num_threads': _to_int(raw_threads),
                'ramp_time': _to_int(raw_ramp),
                'duration': _to_int(raw_duration),
                'loops': _to_int(raw_loops),
                'scheduler': _to_bool(props.get('ThreadGroup.scheduler')),
                'dynamic': dynamic,
            }
            result['thread_groups'].append(group)

            # setup/teardown 线程组不构成压力，禁用的也不算
            if tag in NON_LOAD_THREAD_GROUP_TAGS or not group['enabled']:
                continue
            if dynamic:
                result['has_dynamic_props'] = True
            if group['num_threads'] is None:
                threads_dynamic = True
            else:
                total_threads += group['num_threads']
            if group['duration'] is None:
                if dynamic:
                    duration_dynamic = True
            else:
                max_duration = max(max_duration, group['duration'])
            continue

        if tag == 'CSVDataSet':
            props = _props(elem)
            result['csv_datasets'].append({
                'filename': props.get('filename', ''),
                'variable_names': props.get('variableNames', ''),
            })
            continue

        if tag.endswith('Sampler') or tag.endswith('SamplerProxy'):
            result['sampler_count'] += 1
            domain = _props(elem).get('HTTPSampler.domain', '')
            if domain and not _is_dynamic(domain):
                hosts.add(domain.strip().lower())
            continue

        if tag == 'ConfigTestElement':
            # HTTP Request Defaults：域名常常只写在这里
            domain = _props(elem).get('HTTPSampler.domain', '')
            if domain and not _is_dynamic(domain):
                hosts.add(domain.strip().lower())
            continue

        if tag.endswith('Controller'):
            result['controller_count'] += 1

    if not result['thread_groups']:
        result['error'] = '.jmx 中没有找到任何线程组（ThreadGroup），无法执行压测'
        return result

    result['total_threads'] = None if threads_dynamic else total_threads
    result['max_duration'] = None if duration_dynamic else max_duration
    result['hosts'] = sorted(hosts)
    result['valid'] = True
    return result


def inspect_jmx_file(path):
    """从磁盘读取并解析 .jmx。文件不存在/超限时返回 valid=False。"""
    if not path or not os.path.exists(path):
        return {'valid': False, 'error': f'.jmx 文件不存在：{path}'}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return {'valid': False, 'error': f'读取 .jmx 失败：{exc}'}
    if size > MAX_JMX_SIZE:
        return {'valid': False,
                'error': f'.jmx 体积 {size // 1024 // 1024}MB 超过上限 '
                         f'{MAX_JMX_SIZE // 1024 // 1024}MB'}
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError as exc:
        return {'valid': False, 'error': f'读取 .jmx 失败：{exc}'}
    return inspect_jmx_bytes(raw)


def inspect_jmx_bytes(raw):
    """从字节流解析 .jmx，自动处理 BOM 与非 UTF-8 编码。"""
    if not raw:
        return {'valid': False, 'error': '.jmx 文件为空'}
    for encoding in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode('utf-8', errors='replace')
    return inspect_jmx_text(text)


def summarize(meta):
    """把解析结果压缩成落库用的精简摘要（避免把整棵树塞进 JSONField）。"""
    meta = meta or {}
    return {
        'test_plan_name': meta.get('test_plan_name', ''),
        'jmeter_version': meta.get('jmeter_version', ''),
        'thread_group_count': len(meta.get('thread_groups') or []),
        'thread_groups': [
            {k: g.get(k) for k in ('name', 'type', 'enabled', 'num_threads',
                                   'ramp_time', 'duration', 'scheduler', 'dynamic')}
            for g in (meta.get('thread_groups') or [])[:20]
        ],
        'total_threads': meta.get('total_threads'),
        'max_duration': meta.get('max_duration'),
        'sampler_count': meta.get('sampler_count', 0),
        'controller_count': meta.get('controller_count', 0),
        'hosts': (meta.get('hosts') or [])[:20],
        'csv_datasets': (meta.get('csv_datasets') or [])[:20],
        'has_dynamic_props': bool(meta.get('has_dynamic_props')),
    }
