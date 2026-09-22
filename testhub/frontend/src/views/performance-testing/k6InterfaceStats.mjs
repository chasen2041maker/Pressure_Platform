const FAILURE_LABELS = {
  TransportError: '网络传输失败（未记录更细原因）',
  ConnectionRefused: '连接被拒绝',
  ConnectionReset: '连接被重置',
  ConnectTimeout: '建立连接超时',
  RequestTimeout: '请求超时',
  BrokenPipe: '连接中断，无法写入',
  DNSNotFound: '域名解析失败',
  InvalidURL: '请求地址无效',
  BlockedTarget: '目标地址被阻止',
  HTTPFailed: 'HTTP 请求失败',
  AssertionFailed: '断言失败（响应不符合预期）',
  ExtractionFailed: '提取失败（缺少后续需要的数据）',
  RequestFailed: '请求失败（未记录更细原因）',
  WSNegativeAck: 'WS 回执 ok 不为 true', WSCommandTimeout: 'WS 命令回执超时', WSHeartbeatTimeout: 'WS 心跳超时',
  WSConnectTimeout: 'WS 建连超时', WSSessionTimeout: 'WS 会话超时', WSLoadExpired: 'WS 负载时限结束',
  WSCloseTimeout: 'WS 关闭超时', WSUnexpectedClose: 'WS 对端提前关闭', WSTransportError: 'WS 传输失败',
  WSBinaryFrame: 'WS 收到不支持的二进制帧', WSFrameTooLarge: 'WS 帧超出大小限制', WSInvalidJSON: 'WS 帧不是有效 JSON',
  WSPreparationFailed: 'WS 命令准备失败'
}

function count(value) {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isSafeInteger(number) && number >= 0 ? number : null
}

export function formatK6Count(value) {
  return value == null ? '暂无数据' : value.toLocaleString('zh-CN')
}

function failureReason(stat, failed, errorTop, live) {
  if (failed == null) return '暂无统计'
  if (failed === 0) return live ? '本次采样尚无失败' : '无失败'
  if (live) return '运行中：失败原因在结束后汇总'
  const details = Array.isArray(stat.error_detail) && stat.error_detail.length
    ? stat.error_detail
    : errorTop.filter(item => item.sample_step === (stat.step_name || stat.name))
  let explained = 0
  const reasons = details.flatMap(item => {
    const occurrences = count(item.count)
    if (!occurrences) return []
    explained += occurrences
    const label = FAILURE_LABELS[item.type || item.error_type] || '未归类的请求失败'
    return [`${formatK6Count(occurrences)} 次：${label}`]
  })
  if (explained < failed) reasons.push(`另有 ${formatK6Count(failed - explained)} 次失败未提供分类`)
  return reasons.join('；') || '失败原因未提供'
}

export function buildK6InterfaceRows({ stats = [], steps = [], errorTop = [], live = false } = {}) {
  const definitions = steps.filter(step => step.enabled !== false)
  const used = new Set()
  const metricId = stat => stat.step_id != null ? String(stat.step_id) : String(stat.url || '').startsWith('step:') ? stat.url.slice(5) : null
  const rows = definitions.map((step, index) => {
    const id = step.id ?? `legacy:${index + 1}`
    const rawId = String(step.id ?? index + 1)
    const ambiguous = definitions.filter((item, n) => String(item.id ?? n + 1) === rawId).length > 1
    let matches = stats.map((stat, n) => ({ stat, n })).filter(({ stat, n }) => !used.has(n) &&
      (stat.step_id != null ? String(stat.step_id) === String(id) : !ambiguous && metricId(stat) === rawId))
    if (!matches.length && definitions.filter(s => s.name === step.name && s.method === step.method).length === 1) {
      matches = stats.map((stat, n) => ({ stat, n })).filter(({ stat, n }) => !used.has(n) && metricId(stat) == null &&
        (stat.step_name || stat.name) === step.name && (!stat.method || stat.method === step.method))
    }
    const match = matches.length === 1 ? matches[0] : null
    if (match) used.add(match.n)
    return { step, stat: match?.stat || {}, id, ambiguous: !match && ambiguous }
  })
  stats.forEach((stat, n) => { if (!used.has(n)) rows.push({ step: {}, stat, id: stat.step_id ?? `unattributed:${n}`, ambiguous: true }) })
  return rows.map(({ step, stat, id, ambiguous }) => {
    const name = step.name || stat.step_name || stat.name || `步骤 ${id}`
    const total = count(stat.total), failed = count(stat.failed)
    const success = count(stat.success) ?? (total != null && failed != null && total >= failed ? total - failed : null)
    const uniqueName = rows.filter(item => (item.step.name || item.stat.step_name || item.stat.name) === name).length === 1
    return { key: String(id), stepId: id, step_name: name, name, method: stat.method || step.method || '—', isSetup: step.is_setup === true,
      protocol: step.protocol || stat.protocol || 'HTTP', latencyKind: (step.protocol || stat.protocol) === 'SSE' ? 'stream' : (step.protocol || stat.protocol) === 'WEBSOCKET' ? 'session' : 'request',
      requestPath: step.request_path || null,
      phase: stat.phase || step.auth_phase || (step.name ? step.is_setup ? 'setup' : 'business' : 'unknown'),
      total, success, failed, errorRate: total > 0 && failed != null ? `${(failed / total * 100).toFixed(2)}%` : '暂无数据',
      error_rate: total > 0 && failed != null ? failed / total * 100 : null,
      ...Object.fromEntries(['min_rt', 'max_rt', 'p90_rt', 'p99_rt'].map(key => [key, total > 0 ? stat[key] ?? null : null])),
      avg_rt: total > 0 ? stat.avg_rt ?? null : null, p95_rt: total > 0 ? stat.p95_rt ?? null : null, tps: stat.tps ?? null,
      attribution: ambiguous ? '历史归属不明确或未采样；保留独立数据，不按同名合并' : '',
      failureReason: failureReason({ ...stat, step_name: name }, failed, uniqueName ? errorTop : [], live)
    }
  })
}

export const K6_PHASE_LABELS = { login: '登录，不计业务总计', refresh: '刷新，不计业务总计', setup: '前置，不计业务总计', business: '业务', unknown: '历史记录未保存阶段' }
export const K6_DEBUG_LABELS = {
  failed: '失败', body_omitted: '正文未显示',
  passed: '通过', not_sent: '未发送：依赖变量或请求渲染失败', transport_failed: '传输失败',
  http_failed: 'HTTP 失败', assertion_failed: '断言失败', extraction_failed: '提取或认证输出校验失败',
  mismatch: '实际值不等于预期值 / 路径不存在', invalid_json: '响应不是有效 JSON', missing: '路径缺失或值为空', skipped: '未执行',
  invalid_type: '输出类型无效：访问 Token 必须是字符串', empty: '访问 Token 为空或只有空白',
  invalid_expiry: '有效秒数无效：必须是有限数值且大于提前刷新秒数', cookie_missing: '未收到指定 Cookie（Set-Cookie）',
  auth_omitted: '认证响应已隐藏', unavailable: '没有可用响应', non_json_omitted: '非 JSON / 二进制正文已省略',
  binary_omitted: '二进制正文已省略', oversize_omitted: '正文过大，已省略', json: 'JSON 结构预览（所有标量值隐藏，未识别字段匿名化）',
  pending: '未收到结果：可能未触发、被前置失败阻断或请求仍在途中'
}

export function buildK6DebugRows(details, steps = []) {
  const definitions = steps.filter(step => step.enabled !== false)
  const rows = (Array.isArray(details?.steps) ? details.steps.slice(0, 100) : []).map((item, index) => {
    const step = definitions.find(step => String(step.id) === String(item.step_id)) || {}
    return { ...item, key: `${item.step_id}:${index}`, name: step.name || `步骤 ${item.step_id}`,
      protocol: step.protocol || item.protocol || 'HTTP',
      commandName: step.protocol === 'WEBSOCKET' ? step.websocket_commands?.find(command => command.index === item.command_index)?.name : null,
      phase: ['login', 'refresh'].includes(step.auth_phase) ? step.auth_phase : step.is_setup ? 'setup' : 'business' }
  })
  if (details && !details.truncated) definitions.forEach(step => {
    if (!rows.some(row => String(row.step_id) === String(step.id))) rows.push({ key: `pending:${step.id}`, step_id: step.id, name: step.name,
      phase: step.auth_phase || (step.is_setup ? 'setup' : 'business'), outcome: 'pending', assertions: [], extractors: [] })
  })
  return rows
}
