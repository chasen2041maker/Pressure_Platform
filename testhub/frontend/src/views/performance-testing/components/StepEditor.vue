<template>
  <div class="step-editor">
    <el-alert v-if="isK6" type="info" :closable="false" :title="t('performanceTesting.auth.flowTip')" />
    <RequestReadinessPanel v-if="form.source_metadata && Object.keys(form.source_metadata).length"
      :metadata="form.source_metadata" :readiness="readiness || form.readiness" :stale="readinessStale"
      :preparation="form.preparation || {}" editable @update:preparation="value => form.preparation = value" />
    <el-form :model="form" label-width="90px" size="default">
      <el-form-item :label="t('performanceTesting.websocket.protocol')">
        <el-select :model-value="form.protocol || 'HTTP'" :disabled="!canChangeProtocol(form, isK6)" @update:model-value="changeProtocol">
          <el-option label="HTTP" value="HTTP" :disabled="!canSelectProtocol(form, isK6, 'HTTP')" />
          <el-option label="WebSocket" value="WEBSOCKET" :disabled="!canSelectProtocol(form, isK6, 'WEBSOCKET')" />
          <el-option label="SSE" value="SSE" :disabled="!canSelectProtocol(form, isK6, 'SSE')" />
        </el-select>
        <span v-if="!canChangeProtocol(form, isK6)" class="protocol-tip">{{ t('performanceTesting.websocket.manualOnly') }}</span>
      </el-form-item>
      <el-row :gutter="12">
        <el-col :xs="24" :sm="12" :xl="10">
          <el-form-item :label="t('performanceTesting.editor.stepName')">
            <el-input v-model="form.name" :placeholder="t('performanceTesting.scenario.name')" />
          </el-form-item>
        </el-col>
        <el-col :xs="24" :sm="12" :xl="6">
          <el-form-item :label="t('performanceTesting.editor.method')">
            <el-select v-model="form.method" :disabled="isWebsocket" style="width: 100%">
              <el-option v-for="m in (isSse ? ['GET', 'POST'] : methods)" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>
        </el-col>
        <el-col :xs="12" :xl="4" class="switch-col">
          <el-form-item :label="t('performanceTesting.editor.stepEnabled')">
            <el-switch v-model="form.enabled" />
          </el-form-item>
        </el-col>
        <el-col :xs="12" :xl="4" class="switch-col">
          <el-form-item :label="t('performanceTesting.editor.stepSetup')" :title="t('performanceTesting.editor.setupTip')">
            <el-switch v-model="form.is_setup" />
          </el-form-item>
        </el-col>
      </el-row>
      <el-form-item :label="t('performanceTesting.editor.url')">
        <el-input
          v-model="form.url"
          :placeholder="t('performanceTesting.editor.urlPlaceholder')"
          @change="syncUrlQueryParams"
          @keyup.enter="syncUrlQueryParams"
        />
      </el-form-item>

      <ExecutionPolicyEditor v-if="isK6" v-model="form.execution_policy" :setup-step="form.is_setup" />
      <template v-if="isWebsocket">
        <div class="kv-title">{{ t('performanceTesting.editor.headers') }}</div>
        <div class="kv-scroll" role="region" :aria-label="t('performanceTesting.editor.headers')" tabindex="0"><KeyValueEditor v-model="headersKV" /></div>
        <WebSocketStepEditor v-model="form.websocket_config" v-model:draft="form._websocketDraft" :access-variable="accessVariable" />
        <el-form-item :label="t('performanceTesting.editor.thinkTime')"><el-input-number v-model="thinkTimeMs" :min="0" :max="60000" /><span class="unit">ms</span></el-form-item>
      </template>
      <el-tabs v-else v-model="activeTab" class="step-tabs">
        <el-tab-pane :label="t('performanceTesting.editor.tabRequest')" name="request">
          <el-row :gutter="12" class="request-fields">
            <el-col :xs="24" :xl="12">
              <div class="kv-title">{{ t('performanceTesting.editor.headers') }}</div>
              <div class="kv-scroll" role="region" :aria-label="t('performanceTesting.editor.headers')" tabindex="0">
                <KeyValueEditor v-model="headersKV" />
              </div>
            </el-col>
            <el-col :xs="24" :xl="12">
              <div class="kv-title">{{ t('performanceTesting.editor.params') }}</div>
              <div class="kv-scroll" role="region" :aria-label="t('performanceTesting.editor.params')" tabindex="0">
                <KeyValueEditor v-model="paramsKV" />
              </div>
            </el-col>
          </el-row>
          <el-form-item :label="t('performanceTesting.editor.bodyType')" style="margin-top: 10px">
            <el-select v-model="form.body_type" style="width: 160px" @change="bodyTypeChanged">
              <el-option label="NONE" value="NONE" />
              <el-option label="JSON" value="JSON" />
              <el-option v-if="!isSse" label="FORM" value="FORM" />
              <el-option v-if="!isSse" label="BINARY" value="BINARY" />
              <el-option v-if="!isSse" label="RAW" value="RAW" />
              <el-option v-if="!isSse" label="XML" value="XML" />
            </el-select>
          </el-form-item>
          <el-input
            v-if="!['NONE', 'BINARY'].includes(form.body_type)"
            v-model="form.body"
            type="textarea"
            :rows="5"
            :placeholder="t('performanceTesting.editor.body')"
          />

          <!-- FORM 使用 multipart；BINARY 只允许一个文件作为整个请求体。 -->
          <div v-if="!isSse && (['FORM', 'BINARY'].includes(form.body_type) || form.files.length)" class="files-block">
            <div class="kv-title">
              {{ t('performanceTesting.editor.files') }}
              <span class="files-tip">{{ t('performanceTesting.editor.filesTip') }}</span>
            </div>
            <el-table :data="form.files" size="small" border>
              <el-table-column :label="t('performanceTesting.editor.fileField')" min-width="120">
                <template #default="{ row }">
                  <el-input v-model="row.field" size="small" placeholder="file" />
                </template>
              </el-table-column>
              <el-table-column :label="t('performanceTesting.editor.fileSelect')" min-width="240">
                <template #default="{ row }">
                  <div class="file-pick">
                    <el-select
                      v-model="row.file_id"
                      size="small"
                      style="flex: 1"
                      :placeholder="t('performanceTesting.editor.filePlaceholder')"
                    >
                      <el-option v-for="f in fileOptionsFor(row)" :key="f.id" :label="f.name" :value="f.id" />
                    </el-select>
                    <el-upload :show-file-list="false" :before-upload="(f) => onPickFile(f, row)">
                      <el-button size="small" :icon="Upload">
                        {{ t('performanceTesting.editor.fileUpload') }}
                      </el-button>
                    </el-upload>
                  </div>
                </template>
              </el-table-column>
              <el-table-column :label="t('performanceTesting.common.actions')" width="70">
                <template #default="{ $index }">
                  <el-button text type="danger" :icon="Delete" @click="form.files.splice($index, 1)" />
                </template>
              </el-table-column>
            </el-table>
            <el-button class="add-row" :icon="Plus" text type="primary" @click="addFileRow">
              {{ t('performanceTesting.editor.addRow') }}
            </el-button>
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="isSse" :label="t('performanceTesting.sse.config')" name="sse">
          <SseStepEditor v-model="form.sse_config" v-model:draft="form._sseDraft" />
        </el-tab-pane>
        <el-tab-pane v-if="!isSse" :label="t('performanceTesting.editor.extractors')" name="extractors">
          <div class="kv-title">{{ t('performanceTesting.editor.extractors') }}</div>
          <el-table :data="form.extractors" size="small" border>
            <el-table-column :label="t('performanceTesting.editor.extractorName')" min-width="120">
              <template #default="{ row }"><el-input v-model="row.name" size="small" /></template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.editor.extractorType')" width="130">
              <template #default="{ row }">
                <el-select v-model="row.type" size="small">
                  <el-option label="JSON_PATH" value="JSON_PATH" />
                  <el-option label="REGEX" value="REGEX" />
                  <el-option label="HEADER" value="HEADER" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.editor.extractorExpr')" min-width="160">
              <template #default="{ row }"><el-input v-model="row.expr" size="small" /></template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.common.actions')" width="70">
              <template #default="{ $index }">
                <el-button text type="danger" :icon="Delete" @click="form.extractors.splice($index, 1)" />
              </template>
            </el-table-column>
          </el-table>
          <el-button class="add-row" :icon="Plus" text type="primary" @click="form.extractors.push({ name: '', type: 'JSON_PATH', expr: '' })">
            {{ t('performanceTesting.editor.addRow') }}
          </el-button>
        </el-tab-pane>

        <el-tab-pane v-if="!isSse" :label="t('performanceTesting.editor.assertions')" name="assertions">
          <div class="kv-title">{{ t('performanceTesting.editor.assertions') }}</div>
          <el-table :data="form.assertions" size="small" border>
            <el-table-column :label="t('performanceTesting.editor.assertionType')" width="140">
              <template #default="{ row }">
                <el-select v-model="row.type" size="small">
                  <el-option v-for="a in assertionTypes" :key="a" :label="a" :value="a" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column v-if="showJsonPath" :label="t('performanceTesting.editor.extractorExpr')" min-width="140">
              <template #default="{ row }"><el-input v-if="row.type === 'JSON_PATH'" :model-value="row.expr || row.json_path || ''" size="small" placeholder="$.data.code" @update:model-value="value => setAssertionPath(row, value)" /></template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.editor.assertionExpected')" min-width="140">
              <template #default="{ row }"><el-input v-model="row.expected" size="small" /></template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.common.actions')" width="70">
              <template #default="{ $index }">
                <el-button text type="danger" :icon="Delete" @click="form.assertions.splice($index, 1)" />
              </template>
            </el-table-column>
          </el-table>
          <el-button class="add-row" :icon="Plus" text type="primary" @click="form.assertions.push({ type: 'STATUS_CODE', expected: '' })">
            {{ t('performanceTesting.editor.addRow') }}
          </el-button>
        </el-tab-pane>

        <el-tab-pane :label="t('performanceTesting.editor.thinkTime')" name="think">
          <el-form-item :label="t('performanceTesting.editor.thinkTime')" :title="t('performanceTesting.editor.thinkTimeTip')">
            <el-input-number v-model="thinkTimeMs" :min="0" :max="60000" />
            <span class="unit">{{ t('performanceTesting.common.ms') }}</span>
          </el-form-item>
        </el-tab-pane>
      </el-tabs>
    </el-form>
  </div>
</template>

<script setup>
import { ref, reactive, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Delete, Plus, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import KeyValueEditor from '@/views/api-testing/components/KeyValueEditor.vue'
import RequestReadinessPanel from './RequestReadinessPanel.vue'
import WebSocketStepEditor from './WebSocketStepEditor.vue'
import SseStepEditor from './SseStepEditor.vue'
import ExecutionPolicyEditor from './ExecutionPolicyEditor.vue'
import { canChangeProtocol, convertStepProtocol } from '../websocketStepForm.mjs'
import { canSelectProtocol } from '../sseStepForm.mjs'
import { typedRows, typedObject, sameJsonValue } from '../apiCatalogForm.mjs'
import { parseUrlQueryString, mergeQueryPairsIntoRows } from '@/utils/urlParams'

// uploadFiles：当前项目下 file_type=UPLOAD 的数据文件列表，由父组件（ScenarioEditor）
// 统一拉取并下发；新文件上传同样由父组件完成（upload-file 事件），保证只有一处上传入口
const props = defineProps({
  modelValue: { type: Object, default: () => ({}) },
  uploadFiles: { type: Array, default: () => [] },
  readiness: { type: Object, default: null },
  isK6: Boolean,
  accessVariable: { type: String, default: 'access_token' },
  readinessStale: Boolean
})
const emit = defineEmits(['update:modelValue', 'upload-file'])
const { t } = useI18n()

const methods = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']
const assertionTypes = ['STATUS_CODE', 'CONTAINS', 'NOT_CONTAINS', 'JSON_PATH', 'RESPONSE_TIME', 'REGEX']
const activeTab = ref('request')
// think_time 在模型/引擎里是 {type,min,max} 字典，UI 用单个数字（毫秒）表示。
// 独立成 ref，避免与 form.think_time 混用导致 syncFromModel/emitChange 互相改写触发递归。
const thinkTimeMs = ref(0)

const form = reactive({
  name: '', enabled: true, is_setup: false, method: 'GET', url: '',
  body_type: 'NONE', body: '', files: [], extractors: [], assertions: [], think_time: 0,
  headers: {}, params: {}
})
const headersKV = ref([])
const paramsKV = ref([])
const isWebsocket = computed(() => form.protocol === 'WEBSOCKET')
const isSse = computed(() => form.protocol === 'SSE')

async function changeProtocol(protocol) {
  if (!canSelectProtocol(form, props.isK6, protocol) || protocol === (form.protocol || 'HTTP')) return
  try {
    await ElMessageBox.confirm(t(protocol === 'SSE' || form.protocol === 'SSE' ? 'performanceTesting.sse.switchNotice' : 'performanceTesting.websocket.switchNotice'), t('performanceTesting.websocket.protocol'), { type: 'warning' })
    const next = convertStepProtocol({ ...form, headers: kvToObj(headersKV.value, form.headers), params: kvToObj(paramsKV.value, form.params) }, protocol, props.accessVariable)
    Object.assign(form, next)
    headersKV.value = objToKV(next.headers)
    paramsKV.value = objToKV(next.params)
    activeTab.value = protocol === 'SSE' ? 'sse' : 'request'
  } catch { /* Cancellation preserves the current protocol and draft. */ }
}

const showJsonPath = computed(() => (form.assertions || []).some(a => a.type === 'JSON_PATH'))

function objToKV(obj) {
  return typedRows(obj)
}
function kvToObj(kv, original) { return typedObject(kv, original) }

// 自动拾取 URL 中的查询参数，解析后填入参数表格，并从 URL 中移除查询串
// 触发时机：URL 输入框失焦（change）或按下回车
function syncUrlQueryParams() {
  if (isWebsocket.value) return
  const parsed = parseUrlQueryString(form.url || '')
  if (!parsed) return
  paramsKV.value = mergeQueryPairsIntoRows(paramsKV.value, parsed.pairs)
  form.url = parsed.baseUrl + parsed.fragment
  ElMessage.success(`已自动识别 ${parsed.pairs.length} 个 URL 参数`)
}

function syncFromModel() {
  for (const key of Object.keys(form)) delete form[key]
  Object.assign(form, {
    name: '', enabled: true, is_setup: false, method: 'GET', url: '',
    body_type: 'NONE', body: '', files: [], extractors: [], assertions: [], think_time: 0,
    headers: {}, params: {}
  }, JSON.parse(JSON.stringify(props.modelValue || {})))
  form.files = (form.files || []).map(f => ({
    field: f.field || '', file_id: f.file_id || null,
    filename: f.filename || '', content_type: f.content_type || ''
  }))
  form.extractors = (form.extractors || []).map(e => ({ name: e.name || '', type: e.type || 'JSON_PATH', expr: e.expr || '' }))
  form.assertions = (form.assertions || []).map(a => ({ ...a, type: a.type || 'STATUS_CODE', expected: Object.hasOwn(a, 'expected') ? a.expected : '' }))
  // think_time 在模型/引擎里是 {type,min} 字典；UI 用单个数字（毫秒）表示。
  // 把字典的 min 反解到独立数字输入框，保存时再包回 FIXED 字典（见 emitChange）。
  const tt = props.modelValue?.think_time
  thinkTimeMs.value = typeof tt === 'number' ? tt : (Number((tt && tt.min) || 0) || 0)
  headersKV.value = objToKV(form.headers)
  paramsKV.value = objToKV(form.params)
}

// 规范化 think_time 为数字 min，用于内容比较，避免 {type,min} 与数字的格式差异造成误判。
function normalize(v) {
  const c = { ...(v || {}) }
  const tt = c.think_time
  c.think_time = typeof tt === 'number' ? tt : (tt ? Number(tt.min) || 0 : 0)
  return c
}
function sameContent(a, b) {
  return sameJsonValue(normalize(a), normalize(b))
}

function setAssertionPath(rule, value) {
  // 保留来源字段；同时存在两个别名时，用户修改应同步更新，避免执行歧义。
  if (Object.hasOwn(rule, 'expr')) rule.expr = value
  if (Object.hasOwn(rule, 'json_path') || !Object.hasOwn(rule, 'expr')) rule.json_path = value
}

function emitChange() {
  const out = { ...form }
  out.headers = kvToObj(headersKV.value, form.headers)
  out.params = kvToObj(paramsKV.value, form.params)
  out.think_time = { type: 'FIXED', min: Number(thinkTimeMs.value) || 0 }
  // 文件字段只保留填了字段名的行；file_id 为空 = 导入占位/未选文件，后端按 warning 提示
  out.files = (form.files || [])
    .filter(f => (f.field || '').trim())
    .map(f => ({
      field: f.field.trim(), file_id: f.file_id || null,
      filename: f.filename || '', content_type: f.content_type || ''
    }))
  out.extractors = (form.extractors || []).filter(e => e.name && e.name.trim()).map(e => ({ name: e.name, type: e.type, expr: e.expr }))
  // 编辑其他字段不能丢失路径、运算符或整条断言；缺项由保存校验明确提示。
  out.assertions = (form.assertions || []).map(a => ({ ...a }))
  // 内容未变则不向上 emit，避免 props.modelValue 被改写后又触发 syncFromModel 形成递归更新。
  if (sameContent(out, props.modelValue)) return
  emit('update:modelValue', out)
}

watch(() => props.modelValue, syncFromModel, { immediate: true, deep: true })
watch([form, headersKV, paramsKV, thinkTimeMs], emitChange, { deep: true })

// ------------------------------------------------------------------ //
// multipart 文件字段
// ------------------------------------------------------------------ //
function addFileRow() {
  form.files.push({ field: 'file', file_id: null, filename: '', content_type: '' })
}
function bodyTypeChanged(value) {
  if (value === 'BINARY') {
    form.body = ''
    if (!form.files.length) addFileRow()
  }
}

// 行内下拉候选 = 项目上传文件列表；若该行引用的文件已被删除（跨会话/他人删除），
// 仍把旧引用挂在候选里显示文件名，避免下拉变成空值让用户摸不着头脑
function fileOptionsFor(row) {
  if (row.file_id && !props.uploadFiles.some(f => f.id === row.file_id)) {
    return [...props.uploadFiles, { id: row.file_id, name: row.filename || `#${row.file_id}` }]
  }
  return props.uploadFiles
}

// before-upload 返回 false 拦截 el-upload 默认提交，转交父组件走统一上传接口
function onPickFile(file, row) {
  emit('upload-file', { file, row })
  return false
}
</script>

<style lang="scss" scoped>
.step-editor {
  min-width: 0;
  max-width: 100%;
  .step-tabs { min-width: 0; width: 100%; }
  .request-fields { row-gap: 16px; }
  .kv-scroll { max-width: 100%; overflow-x: auto; }
  .kv-scroll :deep(.key-value-editor) { min-width: 590px; }
  .kv-scroll:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
  .switch-col :deep(.el-form-item) { margin-bottom: 18px; }
  .kv-title { font-size: 13px; font-weight: 600; color: #595959; margin-bottom: 6px; }
  .add-row { margin-top: 8px; }
  .unit { margin-left: 6px; color: #8c8c8c; font-size: 12px; }
  .protocol-tip { margin-left: 12px; color: var(--el-text-color-secondary); font-size: 13px; }
  .files-block { margin-top: 12px; }
  .files-tip { margin-left: 8px; font-weight: 400; font-size: 12px; color: #c0c4cc; }
  .file-pick { display: flex; align-items: center; gap: 6px; }
}
</style>
