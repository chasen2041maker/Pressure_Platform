// 通知渠道表单纯逻辑（无 Vue 依赖，可单测）。
// 抽离自 NotificationChannels.vue，便于 node:test 覆盖：
//   - defaultConfig / CONFIG_KEYS：各类型默认配置与允许字段
//   - isCiphertext：判断是否为 Fernet 密文（gAAAAA 前缀）
//   - normalizeEditConfig：把后端 retrieve 的 config 规整为可编辑表单值
//       * 密文 / null / undefined 的字段清空为 ''，提示用户重填，杜绝二次加密
//       * 仅保留该类型应有的字段，缺失补默认值
//   - buildSavePayload：构造提交 payload，只携带该类型允许的字段（剔除跨类型残留）
//   - configSummary：列表里的配置摘要

export const CHANNEL_TYPES = ['DINGTALK', 'WECOM', 'EMAIL']

// 各类型允许出现在提交 config 中的字段（用于剔除切换类型后的残留字段）
export const CONFIG_KEYS = {
  DINGTALK: ['webhook_url', 'secret', 'at_all'],
  WECOM: ['webhook_url', 'mentioned_list', 'at_all'],
  EMAIL: ['host', 'port', 'username', 'password', 'use_ssl', 'receivers'],
}

export function defaultConfig(type) {
  if (type === 'DINGTALK') return { webhook_url: '', secret: '', at_all: true }
  if (type === 'WECOM') return { webhook_url: '', mentioned_list: [], at_all: false }
  return { host: '', port: 465, username: '', password: '', use_ssl: true, receivers: [] }
}

// Fernet 令牌固定以 gAAAAA 开头（urlsafe base64 的版本+时间戳头）
export function isCiphertext(v) {
  return typeof v === 'string' && v.startsWith('gAAAAA')
}

// 把后端 retrieve 返回的 config 规整为可编辑表单值。
// cfg 可能为 { webhook_url: 'gAAAAA...', secret: 'gAAAAA...' }（旧密钥无法解密）
// 或 { webhook_url: null }（后端解密失败置空），或正常明文。
export function normalizeEditConfig(cfg, type) {
  const base = defaultConfig(type)
  const src = cfg && typeof cfg === 'object' ? cfg : {}
  const out = { ...base }
  for (const k of Object.keys(base)) {
    const v = src[k]
    if (v === null || v === undefined) {
      out[k] = base[k] // 缺失用默认值（布尔/数字/空串）
    } else if (isCiphertext(v)) {
      out[k] = '' // 密文清空，提示用户重填（避免二次加密）
    } else {
      out[k] = v
    }
  }
  return out
}

// 构造提交 payload。只携带该类型允许的字段，杜绝跨类型残留脏数据。
export function buildSavePayload(form, opts = {}) {
  const { mentionedText = '', receiversText = '' } = opts
  const keys = CONFIG_KEYS[form.type] || []
  const clean = {}
  for (const k of keys) {
    if (k in form.config) clean[k] = form.config[k]
  }
  if (form.type === 'WECOM') {
    clean.mentioned_list = String(mentionedText)
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
  }
  if (form.type === 'EMAIL') {
    clean.receivers = String(receiversText)
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
  }
  return { name: form.name, type: form.type, enabled: form.enabled, config: clean }
}

export function configSummary(c) {
  if (!c) return '-'
  if (c.webhook_url) return c.webhook_url === '******' ? 'Webhook ●' : c.webhook_url
  if (c.host) return c.host + (c.port ? ':' + c.port : '')
  if (Array.isArray(c.receivers) && c.receivers.length) return c.receivers.join(', ')
  if (Array.isArray(c.mentioned_list) && c.mentioned_list.length) return c.mentioned_list.join(', ')
  return '—'
}
