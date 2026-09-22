// 性能测试模块共享工具：纯函数（不依赖 i18n 实例），仅返回 el-tag 类型或 i18n key 路径。
// 文本翻译在各页面用 t('performanceTesting.status.' + status) 完成。

export function statusTagType(status) {
  switch (status) {
    case 'RUNNING':
    case 'PREPARING':
    case 'STOPPING':
      return 'warning'
    case 'COMPLETED':
      return 'success'
    case 'FAILED':
    case 'TIMEOUT':
      return 'danger'
    case 'STOPPED':
      return 'info'
    default:
      return 'info'
  }
}

export function slaTagType(result) {
  switch (result) {
    case 'PASSED':
      return 'success'
    case 'FAILED':
      return 'danger'
    default:
      return 'info'
  }
}

export function triggerTagType(type) {
  switch (type) {
    case 'MANUAL':
      return 'primary'
    case 'SCHEDULED':
      return 'warning'
    case 'API':
    case 'CI':
      return 'success'
    default:
      return 'info'
  }
}

export function engineTagType(engine) {
  // 未安装引擎不可选：由页面拿 engineStatus 判定，这里仅做展示
  return engine === 'BUILTIN' ? 'success' : 'primary'
}

// 统一提取后端错误文案：DRF 可能返回 {error}、{detail} 或 {field: [msg]}
// 注意 @/utils/api 的响应拦截器返回的是完整 axios response，错误体在 e.response.data
export function apiError(e, fallback = '') {
  const data = e?.response?.data
  if (typeof data === 'string' && data) return data
  if (data && typeof data === 'object') {
    if (data.error) return String(data.error)
    if (data.detail) return String(data.detail)
    const first = Object.values(data).find(v => Array.isArray(v) && v.length)
    if (first) return String(first[0])
  }
  return fallback || e?.message || ''
}

export function formatTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function formatDuration(seconds) {
  const s = Number(seconds) || 0
  // 秒级保留两位小数；先取整再做分/时换算，避免 JS 浮点取模
  // 产生 5m0.8600000000000136s 之类的噪声（300.86 % 60 的浮点误差）
  if (s < 60) {
    const v = Math.round(s * 100) / 100
    return v >= 60 ? '1m' : `${v}s`  // 59.999 四舍五入到 60 时进位为 1m
  }
  const total = Math.round(s)
  const m = Math.floor(total / 60)
  const r = total % 60
  if (m < 60) return `${m}m${r ? r + 's' : ''}`
  const h = Math.floor(m / 60)
  return `${h}h${m % 60}m`
}
