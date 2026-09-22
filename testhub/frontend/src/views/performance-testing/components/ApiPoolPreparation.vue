<template>
  <section class="pool-preparation" :aria-label="pt('title')">
    <div class="section-heading"><h3>{{ pt('title') }}</h3><span>{{ pt('sequence') }}</span></div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" />
    <el-button v-if="error && !busy && !pending" @click="loadConfig">{{ pt('reloadConfig') }}</el-button>
    <details :open="configExpanded" @toggle="configExpanded = $event.target.open" class="shared-settings">
      <summary>{{ pt('sharedSettings') }}<span v-if="loaded"> · {{ configSummary }}</span></summary>
      <p class="hint">{{ pt('configHint') }}</p>
    <el-form v-loading="loading" label-position="top" class="config-grid" :disabled="disabled || busy || !!pending || !loaded">
      <el-form-item :label="pt('globalEnvironment')"><el-select v-model="form.global_environment" clearable :placeholder="pt('optional')">
        <el-option v-for="env in choices.environments.filter(item => item.scope === 'GLOBAL')" :key="env.id" :value="env.id" :label="env.name" />
      </el-select></el-form-item>
      <el-form-item :label="pt('environment')"><el-select v-model="form.environment" clearable :placeholder="pt('selectEnvironment')">
        <el-option v-for="env in choices.environments.filter(item => item.scope !== 'GLOBAL')" :key="env.id" :value="env.id" :label="env.name" />
      </el-select></el-form-item>
      <el-form-item :label="pt('accountPool')"><el-select v-model="form.account_pool_version" clearable @change="changePool">
        <el-option v-for="pool in choices.account_pool_versions" :key="pool.id" :value="pool.id" :label="`${pool.pool_name} · v${pool.version} (${pool.row_count ?? '—'})`" />
      </el-select></el-form-item>
      <el-form-item v-if="selectedPool?.groups?.length" :label="pt('group')"><el-select v-model="form.account_pool_group">
        <el-option value="" :label="pt('allGroups')" />
        <el-option v-for="group in selectedPool.groups" :key="group.id" :value="group.id" :label="`${group.id} (${group.count})`" />
      </el-select></el-form-item>
      <el-form-item :label="pt('tokenVariable')"><el-select v-model="form.token_variable" clearable filterable :placeholder="pt('variableOnly')">
        <el-option v-for="name in selectedPool?.field_names || []" :key="name" :value="name" :label="name" />
      </el-select></el-form-item>
      <el-form-item :label="pt('identityVariable')"><el-select v-model="form.identity_variable" clearable filterable :placeholder="pt('variableOnly')">
        <el-option v-for="name in selectedPool?.field_names || []" :key="name" :value="name" :label="name" />
      </el-select></el-form-item>
    </el-form>
      <el-button :disabled="disabled || !loaded || busy || !!pending || !dirty" @click="saveConfig">{{ pt('saveConfig') }}</el-button>
    </details>
    <div class="selection-counts"><span>{{ pt('selectedCount', { count: selectedRows.length }) }}</span><span>{{ pt('eligibleCount', { count: eligibleRows.length }) }}</span><span>{{ pt('skippedCount', { count: skippedRows.length }) }}</span></div>
    <div class="pool-actions">
      <el-button type="primary" :disabled="disabled || !loaded || busy || !!pending || !catalogVersion" :loading="preparing" @click="prepare">{{ pt('prepare') }}</el-button>
      <el-button :disabled="disabled || !loaded || busy || !!pending || dirty || !verifiable" :loading="verifying" @click="verify()">{{ pt('verify') }}</el-button>
      <el-button v-if="pending && !verifying && !polling" @click="resumeVerification">{{ pt('queryVerification') }}</el-button>
    </div>
    <p class="hint">{{ selectedRows.length ? pt('prepareSelected', { count: selectedRows.length }) : pt('prepareWhole') }}</p>
    <p class="selection-summary">{{ pt('verifySelection', { count: eligibleRows.length, methods: methodSummary || '—' }) }}</p>
    <details v-if="skippedRows.length" class="skipped-list"><summary>{{ pt('skippedDetails', { count: skippedRows.length }) }}</summary><ul><li v-for="row in skippedRows" :key="row.id">{{ row.method }} {{ row.path }} — {{ skipReason(row) }}</li></ul></details>
    <el-alert v-if="preparation" type="info" :closable="false" :title="pt('preparedSummary', { count: preparation.prepared_count, ready: preparation.ready_count ?? '—', blocked: preparation.blocked_count })" />
    <p v-if="preparation?.user_preserved_count" class="hint">{{ pt('preservedEdits', { count: preparation.user_preserved_count }) }}</p>
    <div v-if="batch" class="verification" aria-live="polite">
      <strong>{{ pt('batchStatus', { status: pt(`batch.${batch.status}`) }) }}</strong>
      <span v-if="batch.execution_id"> · {{ pt('execution', { id: batch.execution_id }) }}</span>
      <ul v-if="batch.results?.length"><li v-for="item in batch.results" :key="item.prepared_id">
        {{ resultLabel(item) }} · {{ pt(`verdict.${item.verdict || 'evidence_invalid'}`) }}
        <span v-if="Number.isFinite(item.total)"> ({{ pt('resultCounts', { total: item.total, success: item.success, failed: item.failed }) }})</span>
        <details v-if="item.dependency_results?.length" class="setup-results">
          <summary>{{ pt('setupResults') }}</summary>
          <ul><li v-for="step in item.dependency_results" :key="step.step_id">
            {{ step.source_key }} · {{ pt(`verdict.${step.verdict || 'evidence_invalid'}`) }}
            <span v-if="Number.isFinite(step.total)"> ({{ pt('resultCounts', { total: step.total, success: step.success, failed: step.failed }) }})</span>
          </li></ul>
        </details>
      </li></ul>
    </div>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { getPerfApiPoolConfig, updatePerfApiPoolConfig, preparePerfApiPool, verifyPerfApiPool, getPerfApiPoolVerification } from '@/api/performance-testing'
import { requestGate, catalogError } from '../apiCatalogForm.mjs'
const props = defineProps({ projectId: { type: [Number, String], required: true }, catalogVersion: { type: Number, default: null }, selectedRows: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['changed', 'busy', 'single-verification'])
const { t } = useI18n()
const pt = (key, values) => t(`performanceTesting.apiPool.${key}`, values)
const defaults = () => ({ environment: null, global_environment: null, account_pool_version: null, account_pool_group: '', token_variable: 'token', identity_variable: 'user_id' })
const form = reactive(defaults()), choices = reactive({ environments: [], account_pool_versions: [] })
const revision = ref(0), saved = ref(''), loaded = ref(false), loading = ref(false), saving = ref(false), preparing = ref(false), verifying = ref(false), polling = ref(false)
const error = ref(''), preparation = ref(null), batch = ref(null), pending = ref(null)
const resultNames = ref({})
const singleRequestId = ref(null)
const configExpanded = ref(true)
const gate = requestGate()
let timer = null
const selectedPool = computed(() => choices.account_pool_versions.find(pool => pool.id === form.account_pool_version))
const signature = () => JSON.stringify(Object.fromEntries(Object.entries(defaults()).map(([key, fallback]) => [key,
  fallback === null && (form[key] === '' || form[key] == null) ? null : form[key] ?? ''])))
const dirty = computed(() => signature() !== saved.value)
const busy = computed(() => loading.value || saving.value || preparing.value || verifying.value || polling.value)
const canVerify = row => row.prepared?.id && ['unverified', 'passed', 'failed'].includes(row.prepared.status) && !row.prepared.gaps?.length
const eligibleRows = computed(() => props.selectedRows.filter(canVerify))
const skippedRows = computed(() => props.selectedRows.filter(row => !canVerify(row)))
const verifiable = computed(() => eligibleRows.value.length > 0)
const methodSummary = computed(() => [...new Set(eligibleRows.value.map(row => row.method))].map(method => `${method} × ${eligibleRows.value.filter(row => row.method === method).length}`).join(' / '))
const verificationMethods = row => [...new Set([row.method, ...(row.prepared?.verification_methods || []), ...(row.prepared?.setup_steps || []).map(step => step.method)])]
const configSummary = computed(() => {
  const names = [form.global_environment, form.environment].filter(Boolean).map(id => choices.environments.find(env => env.id === id)?.name || pt('bindingMissing'))
  if (selectedPool.value) names.push(`${selectedPool.value.pool_name} v${selectedPool.value.version}`)
  return names.join(' / ') || pt('configNeeded')
})
const skipReason = row => row.prepared?.gaps?.map(gap => `${gap.field}: ${gap.message}`).join('；') || pt(`status.${row.prepared?.status || 'unprepared'}`)
const selectionSignature = () => JSON.stringify(props.selectedRows.map(row => [row.id, row.prepared?.id, row.prepared?.revision, row.prepared?.status]))
const owner = () => { const ticket = gate.begin(), project = props.projectId; return () => gate.current(ticket) && project === props.projectId }
function stopTimer() { clearTimeout(timer); timer = null }
function poolError(e) { return e?.response?.status === 409 ? pt('conflict') : catalogError(e, t) }
function applyConfig(data) {
  Object.assign(form, defaults(), data.config); revision.value = data.revision; saved.value = signature()
  Object.assign(choices, { environments: [], account_pool_versions: [] }, data.choices)
  loaded.value = true
}
function changePool() {
  form.account_pool_group = ''
  const names = selectedPool.value?.field_names || []
  for (const [field, usual] of [['token_variable', 'token'], ['identity_variable', 'user_id']]) {
    if (!names.includes(form[field])) form[field] = names.includes(usual) ? usual : ''
  }
}
async function loadConfig() {
  const current = owner(); loading.value = true; error.value = ''
  try {
    const { data } = await getPerfApiPoolConfig(props.projectId)
    if (!current()) return
    applyConfig(data)
    configExpanded.value = !data.revision
    if (!data.revision) {
      const projectEnvironments = choices.environments.filter(env => env.scope !== 'GLOBAL')
      if (!form.environment && projectEnvironments.length === 1) form.environment = projectEnvironments[0].id
      if (!form.account_pool_version && choices.account_pool_versions.length === 1) { form.account_pool_version = choices.account_pool_versions[0].id; changePool() }
    }
  }
  catch (e) { if (current()) error.value = poolError(e) }
  finally { if (current()) loading.value = false }
}
async function persist(current) {
  if (!dirty.value) return true
  const submitted = signature()
  const { data } = await updatePerfApiPoolConfig(props.projectId, { expected_config_revision: revision.value, config: JSON.parse(submitted) })
  if (!current()) return false
  if (signature() !== submitted) { error.value = pt('draftChanged'); return false }
  applyConfig(data); configExpanded.value = false; emit('changed'); return true
}
async function saveConfig() {
  if (props.disabled || busy.value || pending.value || !loaded.value) return
  const current = owner(); saving.value = true; error.value = ''
  try { await persist(current) } catch (e) { if (current()) error.value = poolError(e) }
  finally { if (current()) saving.value = false }
}
async function prepare() {
  if (props.disabled || busy.value || pending.value || !loaded.value || !props.catalogVersion) return
  const current = owner(), version = props.catalogVersion, ids = props.selectedRows.map(row => row.id)
  preparing.value = true; error.value = ''; preparation.value = null
  try {
    if (!(await persist(current)) || version !== props.catalogVersion) return
    const submitted = signature()
    const { data } = await preparePerfApiPool(props.projectId, { expected_catalog_version: version, expected_config_revision: revision.value, ...(ids.length ? { request_ids: ids } : {}) })
    if (!current() || submitted !== signature() || version !== props.catalogVersion) return
    preparation.value = data; emit('changed')
  } catch (e) { if (current()) error.value = poolError(e) }
  finally { if (current()) preparing.value = false }
}
function canVerifyOne(row) { return !props.disabled && !busy.value && !pending.value && !dirty.value && loaded.value && !!props.catalogVersion && !!canVerify(row) }
function verifyOne(row) { if (canVerifyOne(row)) return verify([row], row.id) }
async function verify(requestedRows = eligibleRows.value, requestId = null) {
  if (props.disabled || busy.value || pending.value || dirty.value || !loaded.value || !requestedRows.length || !requestedRows.every(canVerify)) return
  const current = owner(), version = props.catalogVersion, selected = selectionSignature(), submitted = signature()
  const writes = requestedRows.some(row => row.prepared?.has_writes || verificationMethods(row).some(method => !['GET', 'HEAD', 'OPTIONS'].includes(method)))
  const payload = { selection: requestedRows.map(row => ({ id: row.prepared.id, revision: row.prepared.revision })),
    expected_catalog_version: version, request_key: requestKey(), confirm_writes: writes }
  const names = Object.fromEntries(requestedRows.map(row => [row.prepared.id, `${row.method} ${row.path}`]))
  const skipped = !requestId && skippedRows.value.length ? `\n${pt('skippedDetails', { count: skippedRows.value.length })}\n${skippedRows.value.map(row => `${row.method} ${row.path} — ${skipReason(row)}`).join('\n')}` : ''
  const methods = [...new Set(requestedRows.flatMap(verificationMethods))].join(' / ')
  const setupSteps = [...new Map(requestedRows.flatMap(row => (row.prepared?.setup_steps || []).map(step =>
    [step.key || JSON.stringify([step.source_key, step.revision, step.definition_hash, step.outputs]), step]))).values()]
  const setupNames = setupSteps.map(step => `${step.method} ${step.path}${step.outputs?.length ? ` · ${pt('setupOutputs', { names: step.outputs.join(', ') })}` : ''}`)
  const setupDisclosure = setupNames.length ? `\n${pt('setupConfirmation', { count: setupNames.length })}\n${setupNames.join('\n')}` : ''
  singleRequestId.value = requestId
  verifying.value = true; error.value = ''
  try {
    if (!requestId || writes) await ElMessageBox.confirm(pt('confirmVerification', { count: payload.selection.length, methods }) + setupDisclosure + skipped + (writes ? `\n${pt('writeWarning')}` : ''), pt(requestId ? 'testOnce' : 'verify'), { type: writes ? 'warning' : 'info', customClass: 'api-pool-confirm', confirmButtonText: pt('confirmVerify'), cancelButtonText: t('performanceTesting.common.cancel') })
    if (!current() || (!requestId && selected !== selectionSignature()) || submitted !== signature() || version !== props.catalogVersion || props.disabled) return
    pending.value = { project: props.projectId, payload }; resultNames.value = names; batch.value = null
    await sendVerification(current)
  } catch (e) { if (current() && e !== 'cancel' && e !== 'close') error.value = poolError(e) }
  finally { if (current()) verifying.value = false }
}
function requestKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
function acceptBatch(data, current) {
  batch.value = data
  if (['pending', 'running'].includes(data.status)) {
    polling.value = true
    timer = setTimeout(() => poll(current), 1500)
  } else {
    polling.value = false
    if (['completed', 'failed', 'rejected'].includes(data.status)) { pending.value = null; emit('changed') }
    else error.value = pt('verificationUncertain')
  }
}
async function sendVerification(current) {
  try {
    const { data } = await verifyPerfApiPool(pending.value.project, pending.value.payload)
    if (current()) acceptBatch(data, current)
  } catch (e) {
    if (!current()) return
    if ([400, 403, 404, 409].includes(e?.response?.status)) { pending.value = null; error.value = poolError(e) }
    else error.value = pt('verificationUncertain')
  }
}
async function poll(current) {
  if (!current() || !pending.value) return
  try { const { data } = await getPerfApiPoolVerification(pending.value.project, batch.value.id); if (current()) acceptBatch(data, current) }
  catch { if (current()) { polling.value = false; error.value = pt('verificationUncertain') } }
}
async function resumeVerification() {
  if (!pending.value || busy.value) return
  const current = owner(); verifying.value = true; error.value = ''; stopTimer()
  try { if (batch.value?.id) await poll(current); else await sendVerification(current) }
  finally { if (current()) verifying.value = false }
}
function resultLabel(item) { return resultNames.value[item.prepared_id] || pt('preparedId', { id: item.prepared_id }) }
watch(() => props.projectId, () => {
  gate.invalidate(); stopTimer(); Object.assign(form, defaults()); Object.assign(choices, { environments: [], account_pool_versions: [] })
  revision.value = 0; saved.value = ''; loaded.value = false; loading.value = false; saving.value = false; preparing.value = false; verifying.value = false; polling.value = false
  error.value = ''; preparation.value = null; batch.value = null; pending.value = null; resultNames.value = {}; singleRequestId.value = null; loadConfig()
}, { immediate: true, flush: 'sync' })
watch(() => busy.value || !!pending.value, value => emit('busy', value), { immediate: true })
watch(() => [singleRequestId.value, busy.value, pending.value, batch.value, error.value, dirty.value, loaded.value], () => emit('single-verification', {
  requestId: singleRequestId.value, busy: busy.value, pending: !!pending.value, batch: batch.value, error: error.value,
  dirty: dirty.value, loaded: loaded.value,
}), { immediate: true })
defineExpose({ verifyOne, canVerifyOne, resumeVerification })
onBeforeUnmount(() => { gate.invalidate(); stopTimer() })
</script>

<style scoped>
.pool-preparation { padding: 16px; border: 1px solid var(--el-border-color); border-radius: 6px; min-width: 0; display: grid; gap: 12px; }
.section-heading, .pool-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.section-heading h3 { margin: 0; font-size: 16px; }
.section-heading span, .hint { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.hint, .selection-summary { margin: 0; }
.shared-settings { border-bottom: 1px solid var(--el-border-color-light); padding-bottom: 12px; }
.shared-settings summary, .skipped-list summary { cursor: pointer; font-size: 13px; font-weight: 600; line-height: 1.6; }
.shared-settings summary span { color: var(--el-text-color-secondary); font-weight: 400; }
.shared-settings .hint { margin: 12px 0; }
.selection-counts { display: flex; flex-wrap: wrap; gap: 12px 24px; font-size: 15px; font-weight: 600; }
.skipped-list ul { max-height: 180px; overflow: auto; padding-left: 20px; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
:global(.api-pool-confirm .el-message-box__message) { white-space: pre-line; max-height: 60vh; overflow: auto; overflow-wrap: anywhere; }
.config-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(210px, 100%), 1fr)); gap: 0 16px; min-width: 0; }
.config-grid .el-select { width: 100%; min-width: 0; }
.pool-actions .el-button + .el-button { margin-left: 0; }
.selection-summary, .verification { font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.verification ul { max-height: 240px; overflow: auto; margin: 8px 0 0; padding-left: 20px; }
</style>
