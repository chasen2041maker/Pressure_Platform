<template>
  <div class="cfg-form">
    <div class="mode-bar">
      <el-radio-group v-model="mode" size="small" @change="onModeChange">
        <el-radio-button value="form">&#x25CF; 表单模式</el-radio-button>
        <el-radio-button value="json">{ } JSON 模式</el-radio-button>
        <el-radio-button value="debug">&#x1F50D; 调试模式</el-radio-button>
      </el-radio-group>
      <el-button v-if="mode==='form'" size="small" :icon="Refresh" @click="resetToTemplate">重置为模板</el-button>
    </div>

    <!-- 表单模式 -->
    <div v-if="mode==='form'" class="form-fields">
      <template v-for="field in currentFields" :key="field.key">
        <template v-if="shouldShow(field)">

          <!-- 折叠嵌套表单 -->
          <div v-if="field.type==='nested'" class="nested-section">
            <div class="nested-header" @click="toggleNested(field.key)">
              <span class="nested-toggle">{{ expandedNested[field.key] ? '&#x25BC;' : '&#x25B6;' }}</span>
              <span class="nested-label">{{ field.label }}</span>
              <span class="nested-hint">{{ hasNestedValues(field) ? '\uFF08\u5DF2\u914D\u7F6E\uFF09' : '\uFF08\u53EF\u9009\uFF09' }}</span>
            </div>
            <div v-if="expandedNested[field.key]" class="nested-body">
              <div v-for="sub in field.fields" :key="field.key+'.'+sub.key" class="field-row">
                <label class="field-label">{{ sub.label }}<span v-if="sub.required" class="required">*</span></label>
                <div class="field-control">
                  <el-input v-if="sub.type==='text'" :model-value="nestedRaw(field.key, sub.key)"
                            @update:model-value="v => setNested(field.key, sub.key, v)"
                            :placeholder="sub.placeholder||''" size="small" />
                  <el-input v-else-if="sub.type==='password'" :model-value="nestedRaw(field.key, sub.key)"
                            @update:model-value="v => setNested(field.key, sub.key, v)"
                            show-password size="small" :placeholder="sub.placeholder||''" />
                  <el-input-number v-else-if="sub.type==='number'" :model-value="nestedRaw(field.key, sub.key)"
                                   @update:model-value="v => setNested(field.key, sub.key, v)"
                                   :min="sub.min||0" :max="sub.max||99999" controls-position="right" size="small" style="width:140px" />
                  <el-switch v-else-if="sub.type==='switch'" :model-value="nestedRaw(field.key, sub.key)"
                             @update:model-value="v => setNested(field.key, sub.key, v)" />
                  <el-select v-else-if="sub.type==='select'" :model-value="nestedRaw(field.key, sub.key)"
                             @update:model-value="v => setNested(field.key, sub.key, v)" size="small" style="width:140px">
                    <el-option v-for="o in (sub.options||[])" :key="o.value||o" :label="o.label||o" :value="o.value||o" />
                  </el-select>
                  <el-input v-else-if="sub.type==='textarea'" :model-value="nestedRaw(field.key, sub.key)"
                            @update:model-value="v => setNested(field.key, sub.key, v)"
                            type="textarea" :rows="3" size="small" />
                  <div v-if="sub.hint" class="field-hint">{{ sub.hint }}</div>
                </div>
              </div>
              <el-button size="small" type="danger" link @click="clearNested(field.key)">清空</el-button>
            </div>
          </div>

          <!-- 键值对 -->
          <div v-else-if="field.type==='keyvalue'" class="kv-section">
            <label class="field-label">{{ field.label }}</label>
            <div v-for="(pair,idx) in kvPairs(field.key)" :key="field.key+'-kv-'+idx" class="kv-row">
              <el-input :model-value="pair.key" @update:model-value="v => renameKv(field.key, idx, v)" placeholder="Key" size="small" style="width:160px" />
              <span class="kv-eq">=</span>
              <el-input :model-value="pair.value" @update:model-value="v => updateKv(field.key, idx, v)" placeholder="Value" size="small" style="width:240px" />
              <el-button size="small" type="danger" link :icon="Remove" @click="removeKv(field.key, idx)" />
            </div>
            <el-button size="small" :icon="Plus" @click="addKv(field.key)">添加</el-button>
          </div>

          <!-- 标签列表 -->
          <div v-else-if="field.type==='tags'" class="field-row">
            <label class="field-label">{{ field.label }}</label>
            <div class="field-control">
              <el-input v-model="tagInput" :placeholder="field.placeholder||'输入后回车'" size="small" @keyup.enter="addTag(field.key)" style="width:200px" />
              <div v-if="getRaw(field.key) && getRaw(field.key).length" class="tag-list">
                <el-tag v-for="(tag,ti) in getRaw(field.key)" :key="ti" closable @close="removeTag(field.key,ti)" size="small" style="margin:2px">{{ tag }}</el-tag>
              </div>
            </div>
          </div>

          <!-- 普通字段 -->
          <div v-else class="field-row">
            <label class="field-label">{{ field.label }}<span v-if="field.required" class="required">*</span></label>
            <div class="field-control">
              <el-input v-if="field.type==='text'" :model-value="getRaw(field.key)" @update:model-value="v => setRaw(field.key, v)" :placeholder="field.placeholder||''" size="small" />
              <el-input v-else-if="field.type==='password'" :model-value="getRaw(field.key)" @update:model-value="v => setRaw(field.key, v)" show-password size="small" :placeholder="field.placeholder||''" />
              <el-input-number v-else-if="field.type==='number'" :model-value="getRaw(field.key)" @update:model-value="v => setRaw(field.key, v)" :min="field.min||0" :max="field.max||99999" controls-position="right" size="small" style="width:140px" />
              <el-switch v-else-if="field.type==='switch'" :model-value="getRaw(field.key)" @update:model-value="v => setRaw(field.key, v)" />
              <el-select v-else-if="field.type==='select'" :model-value="getRaw(field.key)" @update:model-value="v => setRaw(field.key, v)" size="small" style="width:160px">
                <el-option v-for="o in (field.options||[])" :key="o.value||o" :label="o.label||o" :value="o.value||o" />
              </el-select>
              <el-input v-else-if="field.type==='textarea'" :model-value="getRaw(field.key)" @update:model-value="v => setRaw(field.key, v)" type="textarea" :rows="field.rows||3" size="small" />
              <div v-if="field.hint" class="field-hint">{{ field.hint }}</div>
            </div>
          </div>

        </template>
      </template>
      <div class="mode-footer">
        <el-button size="small" :icon="Monitor" :loading="testing" @click="runDebugTest">&#x26A1; 调试测试</el-button>
      </div>
      <DebugTestResult v-if="testResult" :result="testResult" @close="testResult=null" />
    </div>

    <!-- JSON 模式 -->
    <div v-if="mode==='json'" class="json-editor">
      <el-input v-model="jsonText" type="textarea" :rows="12" class="cfg-editor" placeholder='{ "key": "value" }' @input="onJsonInput" />
      <div v-if="jsonError" class="json-error">&#x26A0; JSON 格式有误：{{ jsonError }}</div>
      <div v-else class="json-ok">&#x2714; JSON 格式正确</div>
      <div class="mode-footer">
        <el-button size="small" :icon="Monitor" :loading="testing" @click="runDebugTest">&#x26A1; 调试测试</el-button>
      </div>
      <DebugTestResult v-if="testResult" :result="testResult" @close="testResult=null" />
    </div>

    <!-- 调试模式 -->
    <div v-if="mode==='debug'" class="debug-view">

      <!-- 缺失字段警告 -->
      <el-alert v-if="missingFields.length" :title="'以下必填字段未填写：' + missingFields.join('、')" type="error" :closable="false" show-icon class="debug-alert" />
      <el-alert v-else type="success" :title="debugSummary" :closable="false" show-icon class="debug-alert" />

      <!-- 字段详情表 -->
      <div class="debug-section-title">字段详情</div>
      <div class="debug-table">
        <div v-for="(item,idx) in debugFields" :key="idx" class="debug-row">
          <span class="debug-label">{{ item.label }}</span>
          <span class="debug-value" :class="{ 'debug-value-missing': !item.filled, 'debug-value-pwd': item.sensitive }">{{ item.display }}</span>
          <span class="debug-status">
            <el-tag v-if="item.required && !item.filled" size="small" type="danger" effect="dark">缺失</el-tag>
            <el-tag v-else-if="item.filled" size="small" type="success" effect="plain">&#x2713;</el-tag>
            <el-tag v-else size="small" type="info" effect="plain">-</el-tag>
          </span>
        </div>
      </div>

      <!-- 最终发送的 JSON -->
      <div class="debug-section-title">最终发送给后端的 JSON</div>
      <pre class="debug-json">{{ debugJson }}</pre>
    </div>

    <!-- 调试测试结果 -->
    <div v-if="testResult" class="test-result">
      <div class="test-result-header">
        <span class="test-result-title">&#x26A1; 调试测试结果</span>
        <el-button size="small" text :icon="Close" @click="testResult=null" />
      </div>
      <div class="test-result-body">
        <div class="test-status-row">
          <span class="test-label">状态</span>
          <el-tag v-if="testResult.ok" type="success" size="large">&#x2714; 成功</el-tag>
          <el-tag v-else type="danger" size="large">&#x2716; 失败</el-tag>
          <span class="test-latency" v-if="testResult.latency_ms != null">{{ testResult.latency_ms }} ms</span>
        </div>
        <div class="test-detail-row">
          <span class="test-label">消息</span>
          <span class="test-value" :class="{ 'test-err': !testResult.ok }">{{ testResult.message }}</span>
        </div>
        <div class="test-detail-row" v-if="testResult.http_status">
          <span class="test-label">HTTP 状态码</span>
          <span class="test-value">{{ testResult.http_status }}</span>
        </div>
        <div class="test-detail-row" v-if="testResult.detail && Object.keys(testResult.detail).length">
          <span class="test-label">响应详情</span>
          <pre class="test-json">{{ JSON.stringify(testResult.detail, null, 2) }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Remove, Refresh, Monitor, Close } from '@element-plus/icons-vue'
import { debugTest } from '@/api/monitor'

const props = defineProps({
  modelValue: { type: Object, default: () => ({}) },
  type: { type: String, default: 'HTTP' },
  // 基础信息字段（LOGIN 类型探测需要，避免与探测配置重复）
  url: { type: String, default: '' },
  method: { type: String, default: 'GET' },
  host: { type: String, default: '' },
  port: { type: Number, default: null },
})
const emit = defineEmits(['update:modelValue'])

const mode = ref('form')
const jsonText = ref('')
const jsonError = ref('')
const expandedNested = ref({})
const tagInput = ref('')
const testResult = ref(null)
const testing = ref(false)

// ========== Schema ==========
const FIELD_SCHEMAS = {
  LOGIN: [
    { key: 'endpoint', label: '登录路径', type: 'text', placeholder: '/api/login', hint: '与基础信息的 URL/主机:端口 拼接为完整登录地址（若基础信息 URL 已是完整地址则无需填写）' },
    { key: 'username', label: '用户名', type: 'text', required: true },
    { key: 'password', label: '密码', type: 'password', required: true },
    { key: 'is_base64', label: 'Base64 编码密码', type: 'switch', hint: '部分接口使用 base64 编码密码传输' },
    { key: 'username_field', label: 'POST 用户名字段名', type: 'text', placeholder: '默认 username' },
    { key: 'token_path', label: 'Token 提取路径', type: 'text', placeholder: '默认 obj.token', hint: '支持 JSONPath（如 $.data.access_token）或点号路径（obj.token）' },
    { key: 'uid_path', label: 'UID 提取路径', type: 'text', placeholder: '默认 obj.id', hint: '支持 JSONPath（如 $.data.user.id）或点号路径（obj.id）' },
    { key: 'timeout', label: '超时（秒）', type: 'number', min: 1, max: 120 },
    { key: 'assertions_json', label: '断言规则（JSON数组）', type: 'textarea', rows: 5, placeholder: '[{"field":"$.data.code","operator":"equals","expect":200,"message":"业务码异常"}]', hint: 'JSON 数组格式。支持操作符: equals/not_equals/contains/not_contains/exists/not_exists/gt/lt/gte/lte/regex。exists/not_exists 不需要 expect。字段路径支持 JSONPath($.data.users[0].name) 和点号路径(status)' },
  ],
  HTTP: [
    { key: 'url', label: '请求地址', type: 'text', required: true, placeholder: 'https://example.com/health' },
    { key: 'method', label: '请求方法', type: 'select', options: ['GET', 'POST', 'PUT', 'DELETE'] },
    { key: 'headers', label: '请求头', type: 'keyvalue', hint: '可选的自定义请求头' },
    { key: 'body', label: '请求体（JSON）', type: 'textarea', rows: 4, hint: 'POST/PUT 时使用' },
    { key: 'check_type', label: '校验方式', type: 'select', options: [
      { value: 'http_status', label: 'HTTP 状态码' },
      { value: 'json_code', label: '业务码（JSON）' },
      { value: 'contains', label: '响应包含字符串' },
      { value: 'not_contains', label: '响应不包含字符串' },
    ]},
    { key: 'expected_status', label: '预期状态码', type: 'number', min: 100, max: 599, showWhen: (d) => d.check_type === 'http_status' || d.check_type === 'json_code' },
    { key: 'expect_contains', label: '预期包含/排除文本', type: 'text', showWhen: (d) => d.check_type === 'contains' || d.check_type === 'not_contains' },
    { key: 'login', label: '前置登录配置', type: 'nested',
      fields: [
        { key: 'login_url', label: '登录地址', type: 'text', required: true },
        { key: 'username', label: '用户名', type: 'text', required: true },
        { key: 'password', label: '密码', type: 'password', required: true },
        { key: 'is_base64', label: 'Base64 编码', type: 'switch' },
        { key: 'username_field', label: 'POST 用户名字段', type: 'text', placeholder: '默认 username' },
        { key: 'token_path', label: 'Token 路径', type: 'text', placeholder: '默认 obj.token' },
      ]},
    { key: 'timeout', label: '超时（秒）', type: 'number', min: 1, max: 120 },
  ],
  ONLINE: [
    { key: 'login', label: '前置登录配置', type: 'nested',
      fields: [
        { key: 'login_url', label: '登录地址', type: 'text', required: true },
        { key: 'username', label: '用户名', type: 'text', required: true },
        { key: 'password', label: '密码', type: 'password', required: true },
      ]},
    { key: 'base_url', label: '基础地址', type: 'text', placeholder: 'https://example.com' },
    { key: 'statistics', label: '统计接口配置', type: 'nested', required: true,
      fields: [
        { key: 'endpoint', label: '统计地址', type: 'text', required: true, placeholder: '/api/statistics' },
        { key: 'method', label: '请求方法', type: 'select', options: ['POST', 'GET'] },
      ]},
    { key: 'labelname', label: '标签名称', type: 'text', hint: '统计数据中用于标识的字段名' },
    { key: 'warning_threshold', label: '告警阈值（%）', type: 'number', min: 1, max: 100 },
    { key: 'token_header', label: 'Token 传输 Header', type: 'text', placeholder: '默认 Authorization' },
    { key: 'timeout', label: '超时（秒）', type: 'number', min: 1, max: 120 },
  ],
  DOCKER: [
    { key: 'host', label: 'Docker 地址', type: 'text', required: true, placeholder: '172.16.0.10' },
    { key: 'port', label: '端口', type: 'number', min: 1, max: 65535 },
    { key: 'tls', label: '启用 TLS', type: 'switch', hint: 'Docker daemon 使用 TLS 连接' },
    { key: 'containers', label: '监控容器', type: 'tags', placeholder: '输入容器名回车添加' },
    { key: 'max_restart', label: '最大重启次数', type: 'number', min: 0, max: 99, hint: '超过此重启次数判异常' },
    { key: 'timeout', label: '超时（秒）', type: 'number', min: 1, max: 60 },
  ],
  SL651: [
    { key: 'host', label: 'TCP 地址', type: 'text', required: true, placeholder: '172.16.0.20' },
    { key: 'port', label: '端口', type: 'number', min: 1, max: 65535 },
    { key: 'connect_timeout', label: '连接超时（秒）', type: 'number', min: 1, max: 60 },
    { key: 'rw_timeout', label: '读写超时（秒）', type: 'number', min: 1, max: 60 },
    { key: 'ack_wait_timeout', label: 'ACK 等待超时（秒）', type: 'number', min: 1, max: 60 },
    { key: 'frame', label: '测试报文（HEX）', type: 'textarea', rows: 3, hint: '十六进制测试报文' },
    { key: 'db', label: '关联库表检测', type: 'nested',
      fields: [
        { key: 'host', label: '数据库地址', type: 'text' },
        { key: 'port', label: '端口', type: 'number', min: 1, max: 65535 },
        { key: 'user', label: '用户名', type: 'text' },
        { key: 'password', label: '密码', type: 'password' },
        { key: 'db', label: '数据库名', type: 'text' },
        { key: 'table', label: '监测表名', type: 'text' },
        { key: 'time_field', label: '时间字段', type: 'text', placeholder: '默认 time' },
        { key: 'where_clause', label: 'WHERE 条件', type: 'text', placeholder: '例如 status=1' },
        { key: 'status_data_max_lag', label: '最大延迟（秒）', type: 'number', min: 1, max: 86400 },
        { key: 'status_field', label: '状态字段', type: 'text' },
        { key: 'online_value', label: '在线标识值', type: 'text', placeholder: '默认 1' },
      ]},
  ],
}

const currentFields = computed(() => FIELD_SCHEMAS[props.type] || [])

// ========== 本地数据 ==========
const localData = ref({ ...(props.modelValue || {}) })
watch(() => props.modelValue, (v) => {
  if (v) localData.value = { ...v }
  testResult.value = null  // 切换目标时清除上次调试结果
}, { deep: true })
function emitData() { emit('update:modelValue', { ...localData.value }) }

// ========== 字段读写 ==========
function getRaw(key) { return localData.value[key] }
function setRaw(key, v) { localData.value[key] = v; emitData() }

function nestedRaw(pkey, ckey) {
  const p = localData.value[pkey]
  return p ? p[ckey] : undefined
}
function setNested(pkey, ckey, v) {
  if (!localData.value[pkey]) localData.value[pkey] = {}
  localData.value[pkey][ckey] = v
  emitData()
}

// ========== 条件显示 ==========
function shouldShow(field) {
  if (!field.showWhen) return true
  return field.showWhen(localData.value)
}

// ========== 嵌套展开 ==========
function toggleNested(key) {
  const e = { ...expandedNested.value }
  e[key] = !e[key]
  expandedNested.value = e
}

function hasNestedValues(field) {
  const v = localData.value[field.key]
  return v && typeof v === 'object' && Object.keys(v).length > 0 && Object.values(v).some(x => x !== undefined && x !== null && x !== '')
}

function clearNested(key) { localData.value[key] = {}; emitData() }

// ========== 键值对 ==========
function kvPairs(key) {
  const obj = localData.value[key]
  if (!obj || typeof obj !== 'object') return []
  return Object.entries(obj).map(([k, v]) => ({ key: k, value: v }))
}
function addKv(key) {
  if (!localData.value[key]) localData.value[key] = {}
  localData.value[key][''] = ''
  emitData()
}
function removeKv(key, idx) {
  const pairs = kvPairs(key)
  const k = pairs[idx]?.key
  if (k !== undefined && localData.value[key]) {
    delete localData.value[key][k]
    emitData()
  }
}
function renameKv(key, idx, newKey) {
  const pairs = kvPairs(key)
  const oldKey = pairs[idx]?.key
  if (oldKey === undefined) return
  const val = localData.value[key][oldKey]
  delete localData.value[key][oldKey]
  localData.value[key][newKey] = val
  emitData()
}
function updateKv(key, idx, newVal) {
  const pairs = kvPairs(key)
  const k = pairs[idx]?.key
  if (k !== undefined) { localData.value[key][k] = newVal; emitData() }
}

// ========== 标签 ==========
function addTag(key) {
  const v = tagInput.value.trim()
  if (!v) return
  if (!Array.isArray(localData.value[key])) localData.value[key] = []
  if (!localData.value[key].includes(v)) { localData.value[key].push(v); emitData() }
  tagInput.value = ''
}
function removeTag(key, idx) {
  if (Array.isArray(localData.value[key])) { localData.value[key].splice(idx, 1); emitData() }
}

// ========== 模式切换 ==========
function onModeChange(val) {
  if (val === 'json') { jsonText.value = JSON.stringify(localData.value, null, 2); jsonError.value = '' }
}
async function runDebugTest() {
  // 如果在 JSON 模式，先解析
  if (mode.value === 'json' && jsonText.value) {
    try {
      localData.value = { ...JSON.parse(jsonText.value) }
      emitData()
      jsonError.value = ''
    } catch (e) {
      ElMessage.warning('JSON 有误，请修正后再测试')
      return
    }
  }
  testing.value = true
  testResult.value = null
  try {
    const res = await debugTest({
      type: props.type,
      check_config: localData.value,
      url: props.url,
      method: props.method,
      host: props.host,
      port: props.port,
    })
    testResult.value = { ...res.data }
  } catch (e) {
    testResult.value = { ok: false, message: e.response?.data?.detail || e.message || '请求失败', latency_ms: null, http_status: null, detail: {} }
  } finally {
    testing.value = false
  }
}
function onJsonInput() {
  try { JSON.parse(jsonText.value); jsonError.value = '' }
  catch (e) { jsonError.value = e.message }
}

function getTemplate(type) {
  if (type === 'LOGIN') return { endpoint: '/api/login', username: 'monitor', password: '******', is_base64: false, username_field: '', token_path: 'obj.token', uid_path: 'obj.id', timeout: 15, assertions_json: '' }
  if (type === 'HTTP') return { url: 'https://example.com/health', method: 'GET', headers: {}, check_type: 'http_status', expected_status: 200, expect_contains: '', timeout: 15 }
  if (type === 'ONLINE') return { login: { login_url: 'https://example.com/api/login', username: 'u', password: '******' }, base_url: 'https://example.com', statistics: { endpoint: '/api/statistics', method: 'POST' }, labelname: '', warning_threshold: 90, timeout: 15 }
  if (type === 'DOCKER') return { host: '172.16.0.10', port: 2375, tls: false, containers: [], max_restart: 3, timeout: 10 }
  return { host: '172.16.0.20', port: 10000, connect_timeout: 15, rw_timeout: 15, ack_wait_timeout: 15, frame: '7E7E0000...', db: { host: '', port: 3306, user: '', password: '', db: '', table: '', time_field: 'time', where_clause: '', status_data_max_lag: 3600, status_field: '', online_value: '1' } }
}

function resetToTemplate() {
  localData.value = { ...getTemplate(props.type) }
  emitData()
}

// ========== 调试模式 ==========
const SENSITIVE_KEYS = ['password', 'secret', 'token', 'webhook']

function isFilled(v) {
  return v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && v.length === 0)
}

function displayVal(key, v) {
  if (!isFilled(v)) return '（未填写）'
  if (SENSITIVE_KEYS.some(sk => key.includes(sk))) return '\u2022'.repeat(8)
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

// 收集所有字段（含嵌套）平铺为调试条目
const debugFields = computed(() => {
  const items = []
  const fields = FIELD_SCHEMAS[props.type] || []
  for (const f of fields) {
    if (f.type === 'nested') {
      // 嵌套字段的子项也加入，带父级前缀
      const nestedVal = localData.value[f.key]
      if (nestedVal && typeof nestedVal === 'object') {
        for (const sub of (f.fields || [])) {
          const v = nestedVal[sub.key]
          items.push({
            label: f.label + ' > ' + sub.label,
            key: f.key + '.' + sub.key,
            value: v,
            display: displayVal(sub.key, v),
            filled: isFilled(v),
            required: sub.required || false,
            sensitive: SENSITIVE_KEYS.some(sk => sub.key.includes(sk)),
          })
        }
      } else {
        // 嵌套对象不存在时显示未配置
        for (const sub of (f.fields || [])) {
          items.push({
            label: f.label + ' > ' + sub.label,
            key: f.key + '.' + sub.key,
            value: undefined,
            display: '（未填写）',
            filled: false,
            required: sub.required || false,
            sensitive: SENSITIVE_KEYS.some(sk => sub.key.includes(sk)),
          })
        }
      }
    } else if (f.type === 'keyvalue') {
      const kv = localData.value[f.key]
      items.push({
        label: f.label,
        key: f.key,
        value: kv,
        display: kv && typeof kv === 'object' && Object.keys(kv).length
          ? Object.entries(kv).map(([k, v]) => k + '=' + v).join('\n')
          : '（无）',
        filled: kv && typeof kv === 'object' && Object.keys(kv).length > 0,
        required: f.required || false,
        sensitive: false,
      })
    } else {
      const v = localData.value[f.key]
      items.push({
        label: f.label,
        key: f.key,
        value: v,
        display: displayVal(f.key, v),
        filled: isFilled(v),
        required: f.required || false,
        sensitive: SENSITIVE_KEYS.some(sk => f.key.includes(sk)),
      })
    }
  }
  return items
})

// 必填缺失字段名列表
const missingFields = computed(() =>
  debugFields.value.filter(item => item.required && !item.filled).map(item => item.label)
)

// 自然语言描述探测行为
const debugSummary = computed(() => {
  const d = localData.value
  const t = props.type
  if (t === 'LOGIN') {
    const url = props.url || (props.host ? 'https://' + props.host + (props.port ? ':' + props.port : '') + (d.endpoint || '') : '（请在基础信息中填写 URL/主机）')
    let assertionCount = 0
    try { const a = JSON.parse(d.assertions_json || '[]'); assertionCount = Array.isArray(a) ? a.length : 0 } catch (e) {}
    return '\u2714\uFE0F 配置正确：' + (props.method || 'POST') + ' ' + url +
      '，' + (d.username ? '账号 ' + d.username : '账号未配置') +
      '，从 ' + (d.token_path || 'obj.token') + ' 提取 Token' +
      (assertionCount > 0 ? '，自定义断言 ' + assertionCount + ' 条' : '，默认校验业务码=200') +
      '，超时 ' + (d.timeout || 15) + ' 秒'
  }
  if (t === 'HTTP') {
    const url = d.url || '（未填）'
    const hasLogin = d.login && isFilled(d.login.login_url)
    return '\u2714\uFE0F 配置正确：' + (d.method || 'GET') + ' ' + url +
      (hasLogin ? '（前置登录：' + d.login.login_url + '）' : '') +
      '，校验方式：' + (d.check_type || 'http_status') +
      '，超时 ' + (d.timeout || 15) + ' 秒'
  }
  if (t === 'ONLINE') {
    const ep = d.statistics?.endpoint || '（未填）'
    const hasLogin = d.login && isFilled(d.login.login_url)
    return '\u2714\uFE0F 配置正确：' + (hasLogin ? d.login.login_url + ' 登录后，' : '') +
      '调统计接口 ' + ep +
      '，阈值 ' + (d.warning_threshold || 90) + '%' +
      '，超时 ' + (d.timeout || 15) + ' 秒'
  }
  if (t === 'DOCKER') {
    return '\u2714\uFE0F 配置正确：监控 Docker ' + (d.host || '（未填）') + ':' + (d.port || 2375) +
      '，容器：' + (Array.isArray(d.containers) && d.containers.length ? d.containers.join(', ') : '全部') +
      '，超时 ' + (d.timeout || 10) + ' 秒'
  }
  if (t === 'SL651') {
    return '\u2714\uFE0F 配置正确：遥测链路 ' + (d.host || '（未填）') + ':' + (d.port || 10000) +
      '，连接超时 ' + (d.connect_timeout || 15) + ' 秒' +
      (d.db?.host ? '，关联库表检测已配置' : '')
  }
  return '\u2714\uFE0F 配置正确'
})

// 最终发送 JSON（去掉空值和敏感占位符）
const debugJson = computed(() => {
  const clean = {}
  const raw = localData.value
  for (const k of Object.keys(raw)) {
    const v = raw[k]
    if (v === undefined || v === null || v === '') continue
    if (v === '******') continue
    if (Array.isArray(v) && v.length === 0) continue
    if (typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0) continue
    if (typeof v === 'object' && !Array.isArray(v)) {
      const sub = {}
      for (const sk of Object.keys(v)) {
        if (v[sk] !== undefined && v[sk] !== null && v[sk] !== '' && v[sk] !== '******') {
          sub[sk] = v[sk]
        }
      }
      if (Object.keys(sub).length > 0) clean[k] = sub
    } else {
      clean[k] = v
    }
  }
  return JSON.stringify(clean, null, 2)
})

watch(mode, (val) => {
  if (val === 'form' && jsonText.value) {
    try { localData.value = { ...JSON.parse(jsonText.value) }; emitData(); jsonError.value = '' }
    catch (e) { /* JSON 解析失败，保留表单状态 */ }
  }
})

defineExpose({
  getConfig: () => ({ ...localData.value }),
  switchToForm: () => { mode.value = 'form' },
})
</script>

<style scoped lang="scss">
.cfg-form { margin-top: 8px; }
.mode-bar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.form-fields { display: flex; flex-direction: column; gap: 0; }
.field-row { display: flex; align-items: flex-start; gap: 12px; padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
.field-row:last-child { border-bottom: none; }
.field-label { width: 140px; flex-shrink: 0; font-size: 13px; line-height: 30px; color: #303133; text-align: right; padding-right: 8px; }
.required { color: #f56c6c; margin-left: 2px; }
.field-control { flex: 1; }
.field-hint { font-size: 12px; color: #909399; margin-top: 2px; }

.nested-section { border: 1px solid #e8e8e8; border-radius: 6px; margin: 6px 0; overflow: hidden; }
.nested-header { display: flex; align-items: center; gap: 6px; padding: 8px 12px; background: #fafafa; cursor: pointer; user-select: none; }
.nested-toggle { font-size: 11px; color: #666; }
.nested-label { font-size: 13px; font-weight: 500; color: #303133; }
.nested-hint { font-size: 12px; color: #909399; }
.nested-body { padding: 8px 12px 12px; border-top: 1px solid #e8e8e8; display: flex; flex-direction: column; gap: 6px; }
.nested-body .field-row { padding: 4px 0; }
.nested-body .field-label { width: 120px; font-size: 12px; line-height: 28px; }
.kv-section { padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
.kv-row { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.kv-eq { color: #999; font-size: 14px; }
.tag-list { margin-top: 4px; }
.json-error { color: #f56c6c; font-size: 12px; margin-top: 4px; }
.json-ok { color: #67c23a; font-size: 12px; margin-top: 4px; }
.cfg-editor { font-family: monospace; }
.mode-footer { display: flex; justify-content: center; margin-top: 12px; }

.debug-view { margin-top: 4px; }
.debug-alert { margin-bottom: 12px; }
.debug-section-title { font-size: 13px; font-weight: 500; margin: 14px 0 8px; color: #303133; }
.debug-table { border: 1px solid #e8e8e8; border-radius: 6px; overflow: hidden; }
.debug-row { display: flex; align-items: center; border-bottom: 1px solid #f0f0f0; }
.debug-row:last-child { border-bottom: none; }
.debug-label { width: 200px; flex-shrink: 0; font-size: 12px; color: #606266; padding: 6px 10px; background: #fafafa; }
.debug-value { flex: 1; font-size: 13px; padding: 6px 10px; color: #303133; white-space: pre-wrap; word-break: break-all; }
.debug-value-missing { color: #f56c6c; font-style: italic; }
.debug-value-pwd { color: #999; letter-spacing: 2px; }
.debug-status { width: 60px; flex-shrink: 0; text-align: center; padding: 6px; }
.debug-json { background: #f7f8fa; border-radius: 6px; padding: 12px; font-family: monospace; font-size: 12px; line-height: 1.6; overflow-x: auto; white-space: pre; }

.test-result { border: 1px solid #e8e8e8; border-radius: 8px; margin-top: 12px; overflow: hidden; }
.test-result-header { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: #fafafa; border-bottom: 1px solid #e8e8e8; }
.test-result-title { font-size: 13px; font-weight: 500; color: #303133; }
.test-result-body { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.test-status-row { display: flex; align-items: center; gap: 12px; }
.test-label { width: 100px; flex-shrink: 0; font-size: 12px; color: #909399; }
.test-latency { font-size: 13px; color: #909399; }
.test-detail-row { display: flex; align-items: flex-start; gap: 12px; }
.test-value { flex: 1; font-size: 13px; color: #303133; word-break: break-all; }
.test-err { color: #f56c6c; }
.test-json { background: #f7f8fa; border-radius: 4px; padding: 8px; font-family: monospace; font-size: 12px; line-height: 1.5; overflow-x: auto; white-space: pre; flex: 1; margin: 0; }
</style>
