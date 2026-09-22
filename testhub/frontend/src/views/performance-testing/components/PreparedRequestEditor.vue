<template>
  <section v-loading="loading" class="prepared-editor">
    <el-alert v-if="error" type="error" :title="error" :closable="false" />
    <div v-if="data" class="editor-heading"><strong>{{ data.operation.method }} {{ data.operation.path || data.operation.request_path }}</strong>
      <el-tag :type="data.prepared.status === 'passed' ? 'success' : 'info'">{{ pt(`status.${data.prepared.status}`) }}</el-tag>
      <el-tag effect="plain">{{ pt('preparedProtocol', { protocol: protocolLabel(data.prepared.protocol) }) }}</el-tag>
    </div>
    <p class="hint">{{ pt('editHint') }}</p>
    <template v-if="data">
      <div v-if="data.prepared.preparation_note" class="preparation-note"><strong>{{ pt('preparationNote') }}</strong><p>{{ data.prepared.preparation_note }}</p></div>
      <div v-if="data.prepared.setup_steps?.length" class="setup-summary">
        <strong>{{ pt('setupTitle') }}</strong><p class="hint">{{ pt('setupHint') }}</p>
        <ul><li v-for="(step, index) in data.prepared.setup_steps" :key="step.key || index">
          <code>{{ step.method }} {{ step.path }}</code> · {{ pt('setupRevision', { revision: step.revision }) }}
          <span v-if="step.outputs?.length"> · {{ pt('setupOutputs', { names: step.outputs.join(', ') }) }}</span>
        </li></ul>
      </div>
      <div class="editor-summary">{{ pt('editSummary', { count: data.prepared.gaps?.length || 0 }) }}</div>
      <el-button v-if="canEnableSse" :disabled="loading || saving || disabled || rawDirty" @click="enableSse">{{ t('performanceTesting.sse.enable') }}</el-button>
      <el-button v-if="canEnableWebsocket" :disabled="loading || saving || disabled || rawDirty || !accessVariable" @click="enableWebsocket">{{ t('performanceTesting.websocket.enable') }}</el-button>
      <p v-if="(canEnableWebsocket || request.protocol === 'WEBSOCKET') && !accessVariable" class="hint">{{ t('performanceTesting.websocket.preparedAuthRequired') }}</p>
      <SseStepEditor v-if="request.protocol === 'SSE'" :model-value="request.sse_config || {}" :draft="sseDraft" :disabled="loading || saving || disabled || rawDirty" @update:draft="updateSseDraft" />
      <WebSocketStepEditor v-if="request.protocol === 'WEBSOCKET'" :key="`${projectId}:${requestId}`" :model-value="request.websocket_config || {}" :draft="websocketDraft" :access-variable="accessVariable" :disabled="loading || saving || disabled || rawDirty || !accessVariable" @update:draft="updateWebsocketDraft" />
      <el-form label-position="top" :disabled="loading || saving || disabled || rawDirty" class="field-form">
        <div v-for="field in fields" :key="field.field" class="field-card">
          <el-form-item :label="`${field.field}${field.required ? ' *' : ''}`">
            <div class="field-input">
              <el-input :model-value="displayValue(field)" :type="field.type === 'json' ? 'textarea' : 'text'" :rows="field.type === 'json' ? 6 : undefined" :aria-label="field.field" :placeholder="field.description || pt('enterValue')" @update:model-value="value => updateField(field, value)" />
              <el-select v-if="field.type !== 'json'" :model-value="null" clearable :placeholder="pt('useVariable')" :aria-label="pt('variableFor', { field: field.field })" @change="name => useVariable(field, name)">
                <el-option v-for="name in data.known_variable_names" :key="name" :label="name" :value="name" />
              </el-select>
            </div>
            <span class="field-note">{{ field.type }}<template v-if="field.description"> · {{ field.description }}</template></span>
          </el-form-item>
          <p v-for="gap in fieldGaps(field)" :key="gap.code + gap.field" class="field-reason">{{ gap.message }}</p>
          <el-checkbox v-if="field.resource || field.provenance === 'example'" :model-value="preparation.confirmed_fields.includes(field.field)" @change="value => confirmField(field.field, value)">
            {{ pt('confirmValue') }}
          </el-checkbox>
        </div>
      </el-form>
      <UploadFileFields v-if="['FORM', 'BINARY'].includes(request.body_type)" :key="`${projectId}:${requestId}`" :project-id="projectId" :model-value="request.files || []" :disabled="loading || saving || disabled || rawDirty" @update:model-value="updateFiles" @busy="value => fileBusy = value" />
      <div v-if="otherGaps.length" class="other-gaps"><strong>{{ pt('otherGaps') }}</strong>
        <ul><li v-for="gap in otherGaps" :key="gap.code + gap.field">{{ gap.field }} — {{ gap.message }}</li></ul>
      </div>
      <details class="advanced-editor">
        <summary>{{ pt('advancedRequest') }}</summary>
        <p class="hint">{{ pt('advancedHint') }}</p>
        <el-input v-model="rawText" type="textarea" :rows="14" :disabled="saving || disabled" :aria-label="pt('advancedRequest')" spellcheck="false" />
        <el-button :disabled="!rawDirty || saving || disabled" @click="applyRaw">{{ pt('applyJson') }}</el-button>
        <el-checkbox v-if="request.body_type !== 'NONE'" v-model="preparation.body_reviewed" :disabled="saving || disabled">{{ pt('bodyReviewed') }}</el-checkbox>
      </details>
      <div class="editor-actions">
        <el-button type="primary" :loading="saving" :disabled="disabled || loading || fileBusy" @click="save">{{ pt('saveAndCheck') }}</el-button>
        <el-button :disabled="saving || disabled" @click="reload">{{ pt('reloadRequest') }}</el-button>
        <span>{{ dirty ? pt('unsaved') : pt(data.prepared.revision ? 'savedForReuse' : 'notSavedYet') }}</span>
      </div>
    </template>
    <el-button v-else-if="!loading" @click="load">{{ t('performanceTesting.common.retry') }}</el-button>
  </section>
</template>

<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { getPerfPreparedRequest, updatePerfPreparedRequest } from '@/api/performance-testing'
import { requestGate, catalogError, catalogProtocolLabel } from '../apiCatalogForm.mjs'
import UploadFileFields from './UploadFileFields.vue'
import SseStepEditor from './SseStepEditor.vue'
import WebSocketStepEditor from './WebSocketStepEditor.vue'
import { websocketTemplate, parseWebsocketDraft, sourceDeclaresWebsocket } from '../websocketStepForm.mjs'
import { sseTemplate, parseSseDraft, sseDraftErrors, sourceDeclaresSse } from '../sseStepForm.mjs'
import { scalarFields, readField, writeField, checkScalarTypes } from '../preparedRequestForm.mjs'
const props = defineProps({ projectId: { type: [Number, String], required: true }, requestId: { type: [Number, String], required: true }, disabled: Boolean })
const emit = defineEmits(['saved', 'dirty', 'busy'])
const { t } = useI18n()
const pt = (key, values) => t(`performanceTesting.apiPool.${key}`, values)
const clone = value => JSON.parse(JSON.stringify(value))
const data = ref(null), request = ref({}), preparation = ref({ confirmed_fields: [], body_reviewed: false })
const sseDraft = ref(undefined)
const websocketDraft = ref(undefined)
const accessVariable = computed(() => data.value?.auth_access_token_variable || '')
const canEnableWebsocket = computed(() => request.value.protocol !== 'WEBSOCKET' && sourceDeclaresWebsocket(data.value?.operation))
const protocolLabel = value => catalogProtocolLabel(value) || pt('protocolUnknown')
const canEnableSse = computed(() => request.value.protocol !== 'SSE' && sourceDeclaresSse(data.value?.operation))
const fileBusy = ref(false)
const loading = ref(false), saving = ref(false), error = ref(''), saved = ref(''), rawText = ref(''), rawBase = ref('')
const gate = requestGate()
const fields = computed(() => scalarFields(data.value?.operation || {}, request.value))
const signature = () => JSON.stringify({ request: request.value, preparation: preparation.value, sseDraft: sseDraft.value, websocketDraft: websocketDraft.value })
const rawDirty = computed(() => rawText.value !== rawBase.value)
const dirty = computed(() => !!data.value && (signature() !== saved.value || rawDirty.value))
const otherGaps = computed(() => (data.value?.prepared.gaps || []).filter(gap => !fields.value.some(field => field.field === gap.field)))
const owner = () => { const ticket = gate.begin(), project = props.projectId, id = props.requestId; return () => gate.current(ticket) && project === props.projectId && id === props.requestId }
function syncRaw() { rawBase.value = JSON.stringify(request.value, null, 2); rawText.value = rawBase.value }
function hydrate(value) {
  data.value = value; request.value = clone(value.request)
  sseDraft.value = request.value.protocol === 'SSE' ? JSON.stringify(request.value.sse_config || {}, null, 2) : undefined
  websocketDraft.value = request.value.protocol === 'WEBSOCKET' ? JSON.stringify(request.value.websocket_config || {}, null, 2) : undefined
  preparation.value = { confirmed_fields: [...(value.preparation?.confirmed_fields || [])], body_reviewed: !!value.preparation?.body_reviewed,
    ...(value.preparation?.setup_steps ? { setup_steps: clone(value.preparation.setup_steps) } : {}),
    ...(value.preparation?.resource_recovery ? { resource_recovery: clone(value.preparation.resource_recovery) } : {}) }
  saved.value = signature(); syncRaw()
}
async function enableSse() {
  if (!canEnableSse.value || loading.value || saving.value || props.disabled || rawDirty.value) return
  const project = props.projectId, id = props.requestId
  try { await ElMessageBox.confirm(t('performanceTesting.sse.preparedSwitchNotice'), t('performanceTesting.sse.config'), { type: 'warning' }) } catch { return }
  if (project !== props.projectId || id !== props.requestId || loading.value || saving.value || props.disabled || rawDirty.value) return
  request.value = { ...request.value, protocol: 'SSE', sse_config: sseTemplate(), websocket_config: {}, assertions: [], extractors: [], files: [] }
  websocketDraft.value = undefined; sseDraft.value = JSON.stringify(request.value.sse_config, null, 2); syncRaw()
}
function updateSseDraft(value) {
  if (loading.value || saving.value || props.disabled || rawDirty.value) return
  sseDraft.value = value
  const parsed = parseSseDraft(value)
  if (parsed.config) { request.value = { ...request.value, sse_config: parsed.config }; syncRaw() }
}
async function enableWebsocket() {
  if (!canEnableWebsocket.value || loading.value || saving.value || props.disabled || rawDirty.value) return
  if (!accessVariable.value) { error.value = t('performanceTesting.websocket.preparedAuthRequired'); return }
  const project = props.projectId, id = props.requestId
  try { await ElMessageBox.confirm(t('performanceTesting.websocket.preparedSwitchNotice'), t('performanceTesting.websocket.title'), { type: 'warning' }) } catch { return }
  if (project !== props.projectId || id !== props.requestId || !canEnableWebsocket.value || !accessVariable.value || loading.value || saving.value || props.disabled || rawDirty.value) return
  request.value = { ...request.value, protocol: 'WEBSOCKET', method: 'GET', body_type: 'NONE', body: '', params: {}, files: [], assertions: [], extractors: [], sse_config: {},
    headers: Object.fromEntries(Object.entries(request.value.headers || {}).filter(([key]) => !['authorization', 'cookie'].includes(key.toLowerCase()))),
    websocket_config: websocketTemplate(accessVariable.value) }
  sseDraft.value = undefined; websocketDraft.value = JSON.stringify(request.value.websocket_config, null, 2); syncRaw()
}
function updateWebsocketDraft(value) {
  if (loading.value || saving.value || props.disabled || rawDirty.value) return
  websocketDraft.value = value
  const parsed = parseWebsocketDraft(value, accessVariable.value)
  if (parsed.config) { request.value = { ...request.value, websocket_config: parsed.config }; syncRaw() }
}
async function load() {
  const current = owner(); loading.value = true; error.value = ''
  try { const response = await getPerfPreparedRequest(props.projectId, props.requestId); if (current()) hydrate(response.data) }
  catch (e) { if (current()) error.value = catalogError(e, t) }
  finally { if (current()) loading.value = false }
}
async function reload() {
  const project = props.projectId, id = props.requestId
  if (dirty.value) {
    try { await ElMessageBox.confirm(pt('discardEdits'), pt('reloadRequest'), { type: 'warning' }) }
    catch { return }
  }
  if (project === props.projectId && id === props.requestId) await load()
}
function displayValue(field) {
  if (field.type === 'json') return request.value.body || ''
  const value = readField(request.value, data.value.operation, field.field)
  return value == null ? '' : String(value)
}
function fieldGaps(field) { return (data.value?.prepared.gaps || []).filter(gap => gap.field === field.field) }
function updateField(field, value) {
  if (loading.value || saving.value || props.disabled || rawDirty.value) return
  try { request.value = writeField(request.value, data.value.operation, field, value); syncRaw(); error.value = '' }
  catch { error.value = pt('fieldEditError', { field: field.field }) }
}
function updateFiles(files) {
  if (loading.value || saving.value || props.disabled || rawDirty.value) return
  let body = request.value.body
  if (request.value.body_type === 'FORM') {
    try {
      const fields = JSON.parse(body || '{}')
      for (const file of files) if (file.file_id && (fields[file.field] === null || fields[file.field] === '')) delete fields[file.field]
      body = JSON.stringify(fields, null, 2)
    } catch { /* Keep the invalid draft for the editor to correct. */ }
  }
  request.value = { ...request.value, body, files }; syncRaw()
}
function useVariable(field, name) { if (data.value.known_variable_names.includes(name)) updateField(field, `{{${name}}}`) }
function confirmField(field, selected) {
  const values = new Set(preparation.value.confirmed_fields)
  if (selected) values.add(field); else values.delete(field)
  preparation.value.confirmed_fields = [...values]
}
function applyRaw() {
  if (!rawDirty.value) return true
  try {
    const parsed = JSON.parse(rawText.value)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.keys(parsed).some(key => !Object.hasOwn(data.value.request, key))
      || parsed.method !== data.value.operation.method || typeof parsed.url !== 'string'
      || !parsed.headers || Array.isArray(parsed.headers) || typeof parsed.headers !== 'object'
      || !parsed.params || Array.isArray(parsed.params) || typeof parsed.params !== 'object'
      || (parsed.protocol === 'WEBSOCKET' && !sourceDeclaresWebsocket(data.value.operation))
      || !Array.isArray(parsed.assertions)) throw new Error('invalid_request')
    request.value = parsed; sseDraft.value = parsed.protocol === 'SSE' ? JSON.stringify(parsed.sse_config || {}, null, 2) : undefined
    websocketDraft.value = parsed.protocol === 'WEBSOCKET' ? JSON.stringify(parsed.websocket_config || {}, null, 2) : undefined
    syncRaw(); error.value = ''; return true
  } catch { error.value = pt('invalidRequestJson'); return false }
}
async function save() {
  if (saving.value || loading.value || fileBusy.value || props.disabled || !data.value || !applyRaw()) return
  if (request.value.protocol === 'WEBSOCKET' && !accessVariable.value) { error.value = t('performanceTesting.websocket.preparedAuthRequired'); return }
  if (sseDraftErrors([{ ...request.value, _sseDraft: sseDraft.value }]).length) { error.value = t('performanceTesting.sse.invalid'); return }
  if (request.value.protocol === 'WEBSOCKET' && parseWebsocketDraft(websocketDraft.value ?? request.value.websocket_config, accessVariable.value).errors.length) { error.value = t('performanceTesting.websocket.invalid'); return }
  try { checkScalarTypes(request.value, data.value.operation) }
  catch (e) { error.value = pt('fieldTypeError', { field: e.message }); return }
  const current = owner(), submitted = signature(); saving.value = true; error.value = ''
  const payload = { expected_catalog_version: data.value.catalog_version, expected_revision: data.value.prepared.revision,
    request: clone(request.value), preparation: clone(preparation.value) }
  try {
    const response = await updatePerfPreparedRequest(props.projectId, props.requestId, payload)
    if (!current()) return
    if (signature() !== submitted || rawDirty.value) {
      data.value = { ...data.value, catalog_version: response.data.catalog_version, prepared: response.data.prepared }
      error.value = pt('draftChanged')
    } else hydrate(response.data)
    emit('saved', response.data)
  } catch (e) { if (current()) error.value = e?.response?.status === 409 ? pt('conflict') : catalogError(e, t) }
  finally { if (current()) saving.value = false }
}
watch(() => [props.projectId, props.requestId], () => {
  gate.invalidate(); data.value = null; request.value = {}; preparation.value = { confirmed_fields: [], body_reviewed: false }
  sseDraft.value = undefined
  websocketDraft.value = undefined
  saved.value = ''; rawText.value = ''; rawBase.value = ''; saving.value = false; error.value = ''; load()
}, { immediate: true, flush: 'sync' })
watch(dirty, value => emit('dirty', value), { immediate: true, flush: 'sync' })
watch(() => saving.value || loading.value || fileBusy.value, value => emit('busy', value), { immediate: true, flush: 'sync' })
onBeforeUnmount(() => gate.invalidate())
</script>

<style scoped>
.prepared-editor { display: grid; gap: 16px; min-width: 0; }
.preparation-note { padding: 10px; border-left: 3px solid var(--el-color-info); background: var(--el-fill-color-light); font-size: 13px; }
.preparation-note p { margin: 4px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.setup-summary { font-size: 13px; overflow-wrap: anywhere; }
.editor-heading, .editor-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.editor-heading strong { overflow-wrap: anywhere; }
.hint, .field-note, .editor-actions span { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; margin: 0; }
.field-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 12px; }
.field-card { padding: 12px; border: 1px solid var(--el-border-color-light); border-radius: 6px; min-width: 0; }
.field-card .el-form-item { margin-bottom: 4px; }
.field-input { display: grid; grid-template-columns: minmax(0, 1fr) 145px; gap: 8px; width: 100%; }
.field-note { display: block; }
.field-reason { color: var(--el-color-warning-dark-2); font-size: 12px; line-height: 1.5; margin: 6px 0; }
.other-gaps { font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.other-gaps ul { max-height: 180px; overflow: auto; padding-left: 20px; }
.advanced-editor { border-top: 1px solid var(--el-border-color-light); padding-top: 12px; }
.advanced-editor summary { cursor: pointer; font-weight: 600; margin-bottom: 12px; }
.advanced-editor .el-button, .advanced-editor .el-checkbox { margin-top: 12px; }
.editor-actions { position: sticky; bottom: -20px; padding: 16px 0; background: var(--el-bg-color); border-top: 1px solid var(--el-border-color-light); z-index: 1; }
.editor-actions .el-button + .el-button { margin-left: 0; }
:deep(.el-checkbox) { height: auto; white-space: normal; }
:deep(.el-checkbox__label) { white-space: normal; }
@media (max-width: 520px) { .field-input { grid-template-columns: minmax(0, 1fr); } }
</style>
