// 通知渠道表单纯逻辑单测（node:test，无需额外框架）。
// 运行：node --test channelForm.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  defaultConfig,
  normalizeEditConfig,
  buildSavePayload,
  configSummary,
  isCiphertext,
  CONFIG_KEYS,
  CHANNEL_TYPES,
} from './channelForm.mjs'

const CIPHER = 'gAAAAABqYcCimHacDvmUfi3BsYS9nTARdmKnxd0WzDIJwQJwM1WfOOZh'

test('isCiphertext 识别 Fernet 令牌', () => {
  assert.equal(isCiphertext(CIPHER), true)
  assert.equal(isCiphertext('https://oapi.dingtalk.com/robot/send?access_token=x'), false)
  assert.equal(isCiphertext(''), false)
  assert.equal(isCiphertext(null), false)
})

test('defaultConfig 各类型结构正确', () => {
  assert.deepEqual(defaultConfig('DINGTALK'), { webhook_url: '', secret: '', at_all: true })
  assert.deepEqual(defaultConfig('WECOM'), { webhook_url: '', mentioned_list: [] })
  assert.deepEqual(defaultConfig('EMAIL'), {
    host: '', port: 465, username: '', password: '', use_ssl: true, receivers: [],
  })
})

test('normalizeEditConfig 把密文 secret/webhook 清空为 ""（防二次加密）', () => {
  const cfg = { webhook_url: CIPHER, secret: CIPHER, at_all: true }
  const out = normalizeEditConfig(cfg, 'DINGTALK')
  assert.equal(out.webhook_url, '', '密文 webhook 应清空')
  assert.equal(out.secret, '', '密文 secret 应清空')
  assert.equal(out.at_all, true)
})

test('normalizeEditConfig 把解密失败返回的 null 视为需重填并清空', () => {
  const cfg = { webhook_url: null, secret: null }
  const out = normalizeEditConfig(cfg, 'DINGTALK')
  assert.equal(out.webhook_url, '')
  assert.equal(out.secret, '')
})

test('normalizeEditConfig 保留正常明文配置', () => {
  const cfg = { webhook_url: 'https://oapi.dingtalk.com/robot/send?access_token=abc', secret: 'SEC123' }
  const out = normalizeEditConfig(cfg, 'DINGTALK')
  assert.equal(out.webhook_url, 'https://oapi.dingtalk.com/robot/send?access_token=abc')
  assert.equal(out.secret, 'SEC123')
})

test('buildSavePayload 切换 DINGTALK->EMAIL 后不含旧类型残留字段', () => {
  // 模拟 onTypeChange 未清理残留：form.config 仍带 DINGTALK 字段
  const form = {
    name: '测试渠道',
    type: 'EMAIL',
    enabled: true,
    config: {
      webhook_url: '', secret: '', at_all: true, // 残留 DINGTALK
      host: 'smtp.example.com', port: 465, username: 'u', password: 'p', use_ssl: true, receivers: [],
    },
  }
  const payload = buildSavePayload(form, { mentionedText: '', receiversText: 'a@x.com,b@y.com' })
  assert.equal(payload.type, 'EMAIL')
  // 关键断言：提交 config 中不得出现 DINGTALK 字段
  for (const k of ['webhook_url', 'secret', 'at_all']) {
    assert.ok(!(k in payload.config), `EMAIL 提交不应包含 ${k}`)
  }
  assert.deepEqual(payload.config.receivers, ['a@x.com', 'b@y.com'])
  assert.equal(payload.config.host, 'smtp.example.com')
})

test('buildSavePayload WECOM 解析 mentionedText 为数组', () => {
  const form = {
    name: '企业微信',
    type: 'WECOM',
    enabled: true,
    config: { webhook_url: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k' },
  }
  const payload = buildSavePayload(form, { mentionedText: 'zhangsan, lisi ,' })
  assert.deepEqual(payload.config.mentioned_list, ['zhangsan', 'lisi'])
  assert.equal(payload.config.webhook_url, 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k')
})

test('buildSavePayload DINGTALK 携带 at_all', () => {
  const form = {
    name: '钉钉',
    type: 'DINGTALK',
    enabled: true,
    config: { webhook_url: 'https://x', secret: 's', at_all: false },
  }
  const payload = buildSavePayload(form)
  assert.equal(payload.config.at_all, false)
  assert.equal(payload.config.webhook_url, 'https://x')
})

test('configSummary 对列表掩码与缺省正确', () => {
  assert.equal(configSummary(null), '-')
  assert.equal(configSummary({ webhook_url: '******' }), 'Webhook ●')
  assert.equal(configSummary({ webhook_url: 'https://x' }), 'https://x')
  assert.equal(configSummary({ host: 'smtp.x', port: 25 }), 'smtp.x:25')
  assert.equal(configSummary({ receivers: ['a@x.com'] }), 'a@x.com')
})

test('CHANNEL_TYPES / CONFIG_KEYS 与类型定义一致', () => {
  assert.deepEqual(CHANNEL_TYPES, ['DINGTALK', 'WECOM', 'EMAIL'])
  assert.ok('DINGTALK' in CONFIG_KEYS && 'WECOM' in CONFIG_KEYS && 'EMAIL' in CONFIG_KEYS)
})
