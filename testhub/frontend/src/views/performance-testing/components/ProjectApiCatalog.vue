<template>
  <div class="project-catalog">
    <header v-if="!selectable" class="catalog-heading"><h2>{{ pt('poolTitle') }}</h2><p>{{ pt('poolFlow') }}</p></header>
    <p v-else class="hint">{{ t('performanceTesting.apiPool.catalogHint') }}</p>
    <details v-if="!selectable" class="upload-section" :inert="poolBusy" :open="!version || !!initialFile">
      <summary>{{ pt('sourceSection') }}<span v-if="version"> · v{{ version.version }}</span></summary>
      <div class="upload-content">
      <el-radio-group v-model="sourceMode" :disabled="importing" :aria-label="t('performanceTesting.catalog.importMode')">
        <el-radio-button label="url" value="url">{{ t('performanceTesting.catalog.urlMode') }}</el-radio-button>
        <el-radio-button label="file" value="file">{{ t('performanceTesting.catalog.fileMode') }}</el-radio-button>
      </el-radio-group>
      <p class="hint source-hint">{{ t('performanceTesting.catalog.sourceHint') }}</p>
      <div class="upload-controls">
        <label v-if="sourceMode === 'url'" class="source-field" :for="`catalog-source-${projectId}`">
          <span>{{ t('performanceTesting.catalog.sourceUrl') }}</span>
          <el-input :id="`catalog-source-${projectId}`" v-model="sourceUrl" :disabled="importing" clearable
            :placeholder="t('performanceTesting.catalog.sourcePlaceholder')" :aria-label="t('performanceTesting.catalog.sourceUrl')" @keyup.enter="previewFile" />
        </label>
        <label v-else class="file-label">{{ t('performanceTesting.catalog.upload') }}
          <input :key="projectId" type="file" accept=".json,.yaml,.yml" :disabled="importing" @change="pickFile" />
          <span v-if="file" class="file-name">{{ file.name }}</span>
        </label>
        <el-button :disabled="!hasImportInput || importing" :loading="previewing" @click="previewFile">
          {{ t(`performanceTesting.catalog.${sourceMode === 'file' ? 'preview' : savedUrlSelected ? 'checkUpdates' : 'fetchPreview'}`) }}
        </el-button>
      </div>
      <dl v-if="displaySource?.document_url" class="source-meta">
        <dt>{{ t('performanceTesting.catalog.documentUrl') }}</dt><dd><code>{{ displaySource.document_url }}</code></dd>
        <template v-if="displaySource.checked_at"><dt>{{ t('performanceTesting.catalog.checkedAt') }}</dt>
          <dd><time :datetime="displaySource.checked_at">{{ formatCheckedAt(displaySource.checked_at) }}</time></dd></template>
      </dl>
      <el-alert v-if="uploadError" type="error" :closable="false" :title="uploadError" />
      <div v-if="preview" class="preview">
        <strong>{{ t('performanceTesting.catalog.previewSummary', { count: preview.operation_count, version: preview.current_version?.version || 0 }) }}</strong>
        <el-alert v-if="previewUnchanged" class="unchanged-notice" type="success" :closable="false" :title="t('performanceTesting.catalog.previewUnchanged')" />
        <el-collapse>
          <el-collapse-item v-for="kind in ['added', 'removed', 'changed']" :key="kind" :name="kind" :title="`${t('performanceTesting.catalog.' + kind)} (${preview.diff[kind].length})`">
            <ul class="change-list"><li v-for="key in preview.diff[kind]" :key="key">{{ key }}</li></ul>
          </el-collapse-item>
        </el-collapse>
        <p class="hint">{{ t('performanceTesting.catalog.importSnapshotHint') }}</p>
        <el-button type="primary" :loading="importing" :disabled="previewing" @click="confirmImport">{{ t('performanceTesting.catalog.confirmImport') }}</el-button>
      </div>
      </div>
    </details>
    <ApiPoolPreparation v-if="!selectable" ref="poolPreparation" :project-id="projectId" :catalog-version="version?.version || null" :selected-rows="verificationRows" :disabled="editorDirty || editorBusy"
      @busy="poolBusy = $event" @single-verification="singleVerification = $event" @changed="poolChanged" />
    <div class="catalog-toolbar">
      <el-input v-model="filters.search" :placeholder="t('performanceTesting.catalog.search')" clearable :aria-label="t('performanceTesting.catalog.search')" @keyup.enter="search" @clear="search" />
      <el-select v-model="filters.tag" clearable :placeholder="t('performanceTesting.catalog.tag')" :aria-label="t('performanceTesting.catalog.tag')" @change="search">
        <el-option v-for="tag in tags" :key="tag" :value="tag" :label="tag" />
      </el-select>
      <el-select v-model="filters.method" clearable :placeholder="t('performanceTesting.catalog.method')" :aria-label="t('performanceTesting.catalog.method')" @change="search">
        <el-option v-for="method in methods" :key="method" :value="method" :label="method" />
      </el-select>
      <el-button @click="search">{{ t('performanceTesting.common.search') }}</el-button>
      <el-button :loading="loading" @click="load">{{ t('performanceTesting.common.refresh') }}</el-button>
    </div>
    <div class="selection-toolbar">
      <el-checkbox v-model="filters.ready" @change="search">{{ t('performanceTesting.catalog.onlyReady') }}</el-checkbox>
      <el-checkbox v-model="filters.passed" @change="search">{{ pt('onlyPassed') }}</el-checkbox>
      <span>{{ t('performanceTesting.catalog.versionCount', { version: version?.version || 0, count: total }) }}</span>
      <template v-if="selectable">
        <el-button size="small" :disabled="loading || !!error || selectingAll || !rows.length" @click="selectPage">{{ usePrepared ? pt('selectPassedPage') : t('performanceTesting.catalog.selectPage') }}</el-button>
        <el-button size="small" :loading="selectingAll" :disabled="loading || !!error" @click="selectAll">{{ usePrepared ? pt('selectPassed') : t('performanceTesting.catalog.selectAll') }}</el-button>
        <el-button size="small" :disabled="!selectedIds.length && !selectingAll" @click="clearSelection">{{ t('performanceTesting.catalog.clearSelection') }}</el-button>
        <strong>{{ t('performanceTesting.importApi.selected', { count: selectedIds.length }) }}</strong>
      </template>
      <template v-else>
        <div class="selection-actions">
          <el-button size="small" :loading="selectingAll" :disabled="poolBusy || loading || !!error" @click="selectCatalog('all')">{{ pt('selectAllCatalog') }}</el-button>
          <el-button size="small" :disabled="poolBusy || loading || selectingAll || !!error" @click="selectCatalog('filtered')">{{ pt('selectFiltered') }}</el-button>
          <el-button size="small" :disabled="poolBusy || loading || selectingAll || !!error" @click="selectVerifiable">{{ pt('selectVerifiable') }}</el-button>
          <el-button size="small" :disabled="poolBusy || (!verificationRows.length && !selectingAll)" @click="clearVerification">{{ t('performanceTesting.catalog.clearSelection') }}</el-button>
        </div>
        <strong>{{ t('performanceTesting.importApi.selected', { count: verificationRows.length }) }}</strong>
      </template>
    </div>
    <el-alert v-if="error" type="error" :closable="false" :title="error">
      <el-button link type="primary" @click="load">{{ t('performanceTesting.common.retry') }}</el-button>
    </el-alert>
    <div v-else v-loading="loading" class="catalog-list">
      <el-empty v-if="!loading && !rows.length" :description="t('performanceTesting.catalog.empty')">
        <el-button v-if="selectable" @click="emit('manage')">{{ t('performanceTesting.catalog.manage') }}</el-button>
        <el-button v-else @click="search">{{ t('performanceTesting.common.refresh') }}</el-button>
      </el-empty>
      <article v-for="row in rows" :key="row.id" class="request-row">
        <div class="request-heading">
          <el-checkbox v-if="selectable" :model-value="selectedIds.includes(row.id)" :disabled="usePrepared && row.prepared?.status !== 'passed'" :aria-label="row.name + ' ' + row.method + ' ' + row.path" @change="value => toggle(row.id, value)" />
          <el-checkbox v-else :model-value="verificationRows.some(item => item.id === row.id)" :disabled="poolBusy" :aria-label="row.name + ' ' + row.method + ' ' + row.path" @change="value => toggleVerification(row, value)" />
          <el-tag size="small" effect="plain">{{ row.method }}</el-tag>
          <el-tag v-if="row.prepared" size="small" effect="plain">{{ pt('preparedProtocol', { protocol: protocolLabel(row.prepared.protocol) }) }}</el-tag>
          <strong>{{ row.name }}</strong>
          <el-button v-if="!selectable" type="primary" plain size="small" :disabled="poolBusy" @click="openEditor(row.id)">{{ pt(row.prepared?.status === 'blocked' || !row.prepared?.id ? 'fillRequest' : 'editRequest') }}</el-button>
          <el-button link type="primary" :aria-expanded="detailId === row.id" @click="showDetail(row.id)">{{ t('performanceTesting.catalog.details') }}</el-button>
        </div>
        <code class="request-path">{{ row.path }}</code>
        <div class="request-meta"><span>{{ row.tags.join(' / ') }}</span><el-tag size="small" :type="row.prepared?.status === 'passed' ? 'success' : row.prepared?.status === 'failed' ? 'danger' : 'info'">{{ pt(`status.${row.prepared?.status || 'unprepared'}`) }}</el-tag></div>
        <p v-if="row.prepared?.gaps?.length" class="gap-summary">{{ row.prepared.gaps[0].field }}: {{ row.prepared.gaps[0].message }}<span v-if="row.prepared.gaps.length > 1"> · {{ pt('moreGaps', { count: row.prepared.gaps.length }) }}</span></p>
        <p v-if="row.prepared?.last_evidence && row.prepared.status !== 'stale'" class="hint">{{ pt(`verdict.${row.prepared.last_evidence.verdict || 'evidence_invalid'}`) }} · {{ pt('evidenceProtocol', { protocol: protocolLabel(row.prepared.last_evidence.protocol) }) }}</p>
        <div v-if="detailId === row.id" v-loading="detailLoading" class="detail">
          <el-alert v-if="detailError" type="error" :closable="false" :title="detailError"><el-button @click="loadDetail(row.id)">{{ t('performanceTesting.common.retry') }}</el-button></el-alert>
          <template v-else-if="detail">
            <div v-if="row.prepared?.preparation_note" class="preparation-note"><strong>{{ pt('preparationNote') }}</strong><p>{{ row.prepared.preparation_note }}</p></div>
            <div v-if="detail.prepared?.setup_steps?.length" class="setup-summary">
              <strong>{{ pt('setupTitle') }}</strong><p class="hint">{{ pt('setupHint') }}</p>
              <ul class="change-list"><li v-for="(step, index) in detail.prepared.setup_steps" :key="step.key || index">
                <code>{{ step.method }} {{ step.path }}</code> · {{ pt('setupRevision', { revision: step.revision }) }}
                <span v-if="step.outputs?.length"> · {{ pt('setupOutputs', { names: step.outputs.join(', ') }) }}</span>
              </li></ul>
            </div>
            <p class="hint">{{ pt(`status.${detail.prepared?.status || 'unprepared'}`) }} · {{ pt('evidenceHint') }}</p>
            <ul v-if="detail.prepared?.gaps?.length" class="change-list"><li v-for="(gap, index) in detail.prepared.gaps" :key="index">{{ gap.field }}: {{ gap.message }}</li></ul>
            <p v-if="detail.prepared?.last_evidence" class="hint">{{ pt('lastEvidence', { id: detail.prepared.last_evidence.execution_id ?? '—' }) }} · {{ pt('evidenceProtocol', { protocol: protocolLabel(detail.prepared.last_evidence.protocol) }) }}</p>
            <div v-if="detail.prepared?.last_evidence?.dependency_results?.length">
              <strong>{{ pt('setupResults') }}</strong>
              <ul class="change-list"><li v-for="step in detail.prepared.last_evidence.dependency_results" :key="step.step_id">
                {{ step.source_key }} · {{ pt(`verdict.${step.verdict || 'evidence_invalid'}`) }}
                <span v-if="Number.isFinite(step.total)"> ({{ pt('resultCounts', { total: step.total, success: step.success, failed: step.failed }) }})</span>
              </li></ul>
            </div>
            <RequestReadinessPanel :metadata="{ ...detail.operation, version: detail.version?.version }" :readiness="detail.readiness" catalog />
          </template>
        </div>
        <div v-if="!selectable" class="request-test-actions">
          <span v-if="singleVerification.requestId === row.id && singleVerification.error" class="test-error" role="status">{{ singleVerification.error }}</span>
          <span v-else-if="!canVerify(row)" class="hint">{{ pt('testNeedsConfiguration') }}</span>
          <span v-else-if="singleVerification.dirty || editorDirty" class="hint">{{ pt('saveBeforeTest') }}</span>
          <a v-if="row.prepared?.last_evidence?.execution_id" :href="`/performance-testing/executions/${row.prepared.last_evidence.execution_id}/monitor`" target="_blank" rel="noopener">{{ pt('testResult') }}</a>
          <el-button size="small" type="primary" plain :loading="singleVerification.requestId === row.id && singleVerification.busy"
            :disabled="!canRunOnce(row)" :title="pt('testOnceHint')" @click="testOnce(row)">{{ pt(resumeSingle(row) ? 'queryVerification' : 'testOnce') }}</el-button>
        </div>
      </article>
    </div>
    <el-pagination v-model:current-page="page" :page-size="50" :total="total" :pager-count="5" layout="total, prev, pager, next" @current-change="load" />
    <el-drawer :model-value="!!editorId" :title="pt('editRequestTitle')" size="min(840px, 100vw)" append-to-body :before-close="closeEditor" :close-on-click-modal="false" :close-on-press-escape="!editorBusy" :show-close="!editorBusy">
      <PreparedRequestEditor v-if="editorId" :key="`${projectId}:${editorId}`" :project-id="projectId" :request-id="editorId" :disabled="poolBusy" @dirty="editorDirty = $event" @busy="editorBusy = $event" @saved="poolChanged" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, ref, reactive, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPerfApiCatalog, getPerfApiCatalogRequest, previewPerfApiCatalog, importPerfApiCatalog } from '@/api/performance-testing'
import { requestGate, catalogError, allCatalogPages, mergeSelected, catalogProtocolLabel } from '../apiCatalogForm.mjs'
import RequestReadinessPanel from './RequestReadinessPanel.vue'
import ApiPoolPreparation from './ApiPoolPreparation.vue'
import PreparedRequestEditor from './PreparedRequestEditor.vue'
const props = defineProps({ projectId: { type: [Number, String], required: true }, selectable: Boolean,
  usePrepared: Boolean, selectedIds: { type: Array, default: () => [] }, initialFile: { type: Object, default: null } })
const emit = defineEmits(['update:selectedIds', 'selection-state', 'imported', 'manage'])
const { t } = useI18n()
const pt = (key, values) => t(`performanceTesting.apiPool.${key}`, values)
const protocolLabel = value => catalogProtocolLabel(value) || pt('protocolUnknown')
const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const filters = reactive({ search: '', tag: '', method: '', ready: false, passed: false })
const verificationRows = ref([]), poolBusy = ref(false)
const poolPreparation = ref(null), singleVerification = ref({})
const editorId = ref(null), editorDirty = ref(false), editorBusy = ref(false)
let selectedRecords = new Map()
const page = ref(1), total = ref(0), rows = ref([]), tags = ref([]), version = ref(null)
const loading = ref(false), error = ref(''), selectingAll = ref(false)
const detailId = ref(null), detail = ref(null), detailLoading = ref(false), detailError = ref('')
const file = ref(null), preview = ref(null), previewing = ref(false), importing = ref(false), uploadError = ref('')
const sourceMode = ref('url'), sourceUrl = ref(''), savedSource = ref({})
let sourceInitialized = false, sourceEdited = false
const hasImportInput = computed(() => sourceMode.value === 'url' ? !!sourceUrl.value.trim() : !!file.value)
const savedUrlSelected = computed(() => {
  const current = sourceIdentity(sourceUrl.value)
  return !!current && current === sourceIdentity(savedSource.value.url || '')
})
const displaySource = computed(() => sourceMode.value === 'url' ? preview.value?.source || (savedUrlSelected.value ? savedSource.value : null) : null)
const previewUnchanged = computed(() => !!preview.value?.content_hash && preview.value.content_hash === preview.value.current_version?.content_hash)
const listGate = requestGate(), detailGate = requestGate(), selectionGate = requestGate(), uploadGate = requestGate()
const params = () => ({ search: filters.search.trim(), tag: filters.tag, method: filters.method, ...(filters.ready ? { ready: 'true' } : {}), ...(filters.passed ? { prepared_status: 'passed' } : {}) })
function invalidateSelection() { selectionGate.invalidate(); selectingAll.value = false }
function clearSelection() { invalidateSelection(); publishSelection([]) }
function publishSelection(ids, items = []) {
  for (const item of items) if (!selectedRecords.has(item.id)) selectedRecords.set(item.id, { ...item, _catalogVersion: version.value?.version })
  selectedRecords = new Map([...selectedRecords].filter(([id]) => ids.includes(id)))
  emit('update:selectedIds', ids)
  emit('selection-state', { version: version.value?.version, rows: ids.map(id => selectedRecords.get(id)).filter(Boolean) })
}
function search() { page.value = 1; load() }
async function load() {
  const ticket = listGate.begin(), project = props.projectId
  invalidateSelection(); detailGate.invalidate(); detailId.value = null; detail.value = null
  loading.value = true; error.value = ''; rows.value = []
  try {
    const { data } = await getPerfApiCatalog(project, { ...params(), page: page.value, page_size: 50 })
    if (!listGate.current(ticket) || project !== props.projectId) return
    if (version.value && version.value.version !== data.version?.version) clearSelection()
    rows.value = data.results; total.value = data.count; version.value = data.version; tags.value = data.tags
    if (verificationRows.value.some(row => row._catalogVersion !== data.version?.version)) verificationRows.value = []
    savedSource.value = data.source || {}
    if (!sourceInitialized) {
      if (!sourceEdited && sourceMode.value === 'url') sourceUrl.value = savedSource.value.url || ''
      sourceInitialized = true
    }
  } catch (e) { if (listGate.current(ticket)) error.value = catalogError(e, t) }
  finally { if (listGate.current(ticket)) loading.value = false }
}
function toggle(id, selected) {
  invalidateSelection()
  if (selected && props.usePrepared && rows.value.find(row => row.id === id)?.prepared?.status !== 'passed') return
  if (selected) addSelection([rows.value.find(row => row.id === id) || { id }])
  else publishSelection(props.selectedIds.filter(value => value !== id))
}
function addSelection(items) {
  try { publishSelection(mergeSelected(props.selectedIds, items), items) }
  catch { error.value = t('performanceTesting.catalog.selectionLimit') }
}
function selectPage() { invalidateSelection(); addSelection(rows.value.filter(row => props.usePrepared ? row.prepared?.status === 'passed' : row.readiness.ready)) }
async function selectAll() {
  const ticket = selectionGate.begin(), project = props.projectId, selected = [...props.selectedIds]
  selectingAll.value = true; error.value = ''
  try {
    const query = { ...params(), ...(props.usePrepared ? { prepared_status: 'passed' } : { ready: 'true' }) }
    if (props.usePrepared) delete query.ready
    const result = await allCatalogPages(query => getPerfApiCatalog(project, query), query, () => selectionGate.current(ticket) && project === props.projectId)
    if (result) {
      if (result.version?.version !== version.value?.version) throw { response: { status: 409 } }
      const items = props.usePrepared ? result.results.filter(row => row.prepared?.status === 'passed') : result.results
      publishSelection(mergeSelected(selected, items), items)
    }
  } catch (e) {
    if (selectionGate.current(ticket)) error.value = e.message === 'selection_limit' ? t('performanceTesting.catalog.selectionLimit') : catalogError(e, t)
  } finally { if (selectionGate.current(ticket)) selectingAll.value = false }
}
function showDetail(id) {
  if (detailId.value === id) { detailGate.invalidate(); detailId.value = null; return }
  detailId.value = id; loadDetail(id)
}
async function loadDetail(id) {
  const ticket = detailGate.begin(), project = props.projectId
  detail.value = null; detailError.value = ''; detailLoading.value = true
  try {
    const { data } = await getPerfApiCatalogRequest(project, id)
    if (!detailGate.current(ticket) || project !== props.projectId) return
    if (data.version?.version !== version.value?.version) throw { response: { status: 409 } }
    detail.value = data
  } catch (e) { if (detailGate.current(ticket)) detailError.value = catalogError(e, t) }
  finally { if (detailGate.current(ticket)) detailLoading.value = false }
}
function resetUpload() {
  uploadGate.invalidate(); preview.value = null; uploadError.value = ''; previewing.value = false; importing.value = false
}
function canVerify(row) { return !!row.prepared?.id && ['unverified', 'passed', 'failed'].includes(row.prepared.status) && !row.prepared.gaps?.length }
function resumeSingle(row) { return singleVerification.value.requestId === row.id && singleVerification.value.pending && !singleVerification.value.busy }
function canRunOnce(row) { return !editorDirty.value && !editorBusy.value && (resumeSingle(row) || !!poolPreparation.value?.canVerifyOne?.(row)) }
function testOnce(row) {
  if (!canRunOnce(row)) return
  return resumeSingle(row) ? poolPreparation.value.resumeVerification() : poolPreparation.value.verifyOne(row)
}
function toggleVerification(row, selected) {
  if (poolBusy.value) return
  if (selected && verificationRows.value.length >= 2000) { error.value = t('performanceTesting.catalog.selectionLimit'); return }
  invalidateSelection()
  verificationRows.value = verificationRows.value.filter(item => item.id !== row.id)
  if (selected) verificationRows.value.push({ ...row, _catalogVersion: version.value?.version })
}
function clearVerification() { invalidateSelection(); verificationRows.value = [] }
function selectVerifiable() { return selectCatalog('eligible') }
async function selectCatalog(scope) {
  if (poolBusy.value) return
  const ticket = selectionGate.begin(), project = props.projectId
  selectingAll.value = true; error.value = ''
  try {
    const result = await allCatalogPages(query => getPerfApiCatalog(project, query), scope === 'all' ? {} : params(), () => selectionGate.current(ticket) && project === props.projectId)
    if (result) {
      if (result.version?.version !== version.value?.version) throw { response: { status: 409 } }
      const eligible = scope === 'eligible' ? result.results.filter(canVerify) : result.results
      if (eligible.length > 2000) { error.value = t('performanceTesting.catalog.selectionLimit'); return }
      verificationRows.value = eligible.map(row => ({ ...row, _catalogVersion: result.version?.version }))
    }
  } catch (e) { if (selectionGate.current(ticket)) error.value = catalogError(e, t) }
  finally { if (selectionGate.current(ticket)) selectingAll.value = false }
}
async function poolChanged() {
  const project = props.projectId, ids = verificationRows.value.map(row => row.id)
  await load()
  if (project !== props.projectId || !ids.length || JSON.stringify(ids) !== JSON.stringify(verificationRows.value.map(row => row.id))) return
  const ticket = selectionGate.begin()
  try {
    const result = await allCatalogPages(query => getPerfApiCatalog(project, query), {}, () => selectionGate.current(ticket) && project === props.projectId)
    if (result) verificationRows.value = result.results.filter(row => ids.includes(row.id)).map(row => ({ ...row, _catalogVersion: result.version?.version }))
  } catch (e) { if (selectionGate.current(ticket)) { verificationRows.value = []; error.value = catalogError(e, t) } }
}
async function openEditor(id) {
  if (poolBusy.value || editorBusy.value) return
  const project = props.projectId, previous = editorId.value
  if (editorDirty.value && id !== editorId.value) {
    try { await ElMessageBox.confirm(pt('discardEdits'), pt('editRequestTitle'), { type: 'warning' }) } catch { return }
  }
  if (project !== props.projectId || previous !== editorId.value) return
  editorId.value = id; editorDirty.value = false
}
async function closeEditor(done) {
  if (editorBusy.value) return
  const project = props.projectId, previous = editorId.value
  if (editorDirty.value) {
    try { await ElMessageBox.confirm(pt('discardEdits'), pt('editRequestTitle'), { type: 'warning' }) } catch { return }
  }
  if (project !== props.projectId || previous !== editorId.value) return
  editorId.value = null; editorDirty.value = false; done?.()
}
function sourceIdentity(value) {
  try {
    const url = new URL(value.trim())
    url.hash = ''
    return url.href
  } catch { return '' }
}
function formatCheckedAt(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
function pickFile(event) { sourceMode.value = 'file'; file.value = event.target.files?.[0] || null; resetUpload() }
function uploadData(expected) {
  const data = new FormData()
  if (sourceMode.value === 'url') {
    data.append('source_url', sourceUrl.value.trim())
    if (expected !== undefined) data.append('preview_token', preview.value.preview_token)
  } else data.append('file', file.value)
  if (expected !== undefined) data.append('expected_version', String(expected))
  return data
}
async function previewFile() {
  if (!hasImportInput.value || importing.value || previewing.value) return
  if (sourceMode.value === 'url') {
    try {
      const parsed = new URL(sourceUrl.value.trim())
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('invalid_url')
    } catch { uploadError.value = t('performanceTesting.catalog.invalidSourceUrl'); return }
  }
  const ticket = uploadGate.begin(), project = props.projectId, mode = sourceMode.value
  previewing.value = true; preview.value = null; uploadError.value = ''
  try {
    const { data } = await previewPerfApiCatalog(project, uploadData())
    if (!uploadGate.current(ticket) || project !== props.projectId) return
    if (mode === 'url' && (typeof data.preview_token !== 'string' || !data.preview_token)) {
      uploadError.value = t('performanceTesting.catalog.invalidPreview'); return
    }
    preview.value = data
  } catch (e) { if (uploadGate.current(ticket) && project === props.projectId) uploadError.value = catalogError(e, t) }
  finally { if (uploadGate.current(ticket)) previewing.value = false }
}
async function confirmImport() {
  if (!preview.value || importing.value || previewing.value) return
  const ticket = uploadGate.begin(), project = props.projectId, mode = sourceMode.value
  importing.value = true; uploadError.value = ''
  try {
    const { data } = await importPerfApiCatalog(project, uploadData(preview.value.current_version?.version || 0))
    if (!uploadGate.current(ticket) || project !== props.projectId) return
    savedSource.value = data.source || {}
    if (mode === 'file') sourceUrl.value = ''
    preview.value = null; clearSelection(); page.value = 1
    ElMessage.success(t(`performanceTesting.catalog.${data.changed ? 'imported' : 'unchanged'}`))
    emit('imported', data); await load()
  } catch (e) {
    if (uploadGate.current(ticket) && project === props.projectId) { uploadError.value = catalogError(e, t); if (e?.response?.status === 409) preview.value = null }
  } finally { if (uploadGate.current(ticket)) importing.value = false }
}
watch([sourceMode, sourceUrl, file], () => { sourceEdited = true; resetUpload() }, { flush: 'sync' })
watch(() => props.projectId, () => {
  listGate.invalidate(); detailGate.invalidate(); uploadGate.invalidate(); clearSelection()
  Object.assign(filters, { search: '', tag: '', method: '', ready: false, passed: false }); page.value = 1; tags.value = []; total.value = 0; version.value = null
  verificationRows.value = []; poolBusy.value = false; singleVerification.value = {}
  editorId.value = null; editorDirty.value = false; editorBusy.value = false
  sourceMode.value = props.initialFile ? 'file' : 'url'; sourceUrl.value = ''; savedSource.value = {}
  file.value = props.initialFile; sourceInitialized = false; sourceEdited = false; resetUpload()
  load(); if (file.value) previewFile()
}, { immediate: true, flush: 'sync' })
watch(() => [filters.search, filters.tag, filters.method, filters.ready, filters.passed], invalidateSelection)
watch(() => props.usePrepared, clearSelection)
onBeforeUnmount(() => { listGate.invalidate(); detailGate.invalidate(); selectionGate.invalidate(); uploadGate.invalidate() })
</script>

<style scoped>
.project-catalog { display: grid; gap: 14px; min-width: 0; }
.catalog-heading h2 { font-size: 20px; margin: 0 0 6px; }
.catalog-heading p { margin: 0; font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.6; }
.upload-section summary { cursor: pointer; font-size: 14px; font-weight: 600; }
.upload-content { display: grid; gap: 12px; padding-top: 14px; }
.selection-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.gap-summary { font-size: 12px; line-height: 1.6; color: var(--el-color-warning-dark-2); overflow-wrap: anywhere; margin: 4px 0; }
.upload-section { display: grid; gap: 12px; min-width: 0; padding: 16px; border: 1px solid var(--el-border-color); border-radius: 6px; }
.upload-controls { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; min-width: 0; }
.source-field { display: grid; gap: 6px; flex: 1 1 320px; min-width: 0; font-size: 13px; }
.source-hint { margin: 0; }
.source-meta { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 6px 12px; margin: 0; font-size: 12px; line-height: 1.6; }
.source-meta dt { color: var(--el-text-color-secondary); }
.source-meta dd { margin: 0; overflow-wrap: anywhere; }
.unchanged-notice { margin-top: 10px; }
.file-label { display: grid; gap: 6px; font-size: 13px; max-width: 100%; }
.file-label input { max-width: 100%; }
.file-name { overflow-wrap: anywhere; font-size: 12px; }
.preview, .upload-section > .el-alert { width: 100%; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }
.catalog-toolbar { display: flex; flex-wrap: wrap; gap: 8px; }
.catalog-toolbar .el-input { flex: 1 1 180px; }
.catalog-toolbar .el-select { width: 140px; }
.selection-toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; }
.selection-toolbar .el-button + .el-button { margin-left: 0; }
.catalog-list { min-height: 120px; }
.request-row { border-bottom: 1px solid var(--el-border-color-lighter); padding: 12px 0; }
.request-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.request-heading strong { flex: 1 1 180px; min-width: 0; overflow-wrap: anywhere; }
.request-path { display: block; overflow-wrap: anywhere; padding: 4px 0; font-size: 12px; }
.request-meta { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px; color: var(--el-text-color-secondary); font-size: 12px; }
.preparation-note { margin-top: 8px; padding: 10px; border-left: 3px solid var(--el-color-info); background: var(--el-fill-color-light); font-size: 13px; }
.preparation-note p { margin: 4px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.detail { margin-top: 10px; min-height: 40px; }
.request-test-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px 12px; margin-top: 8px; font-size: 12px; }
.request-test-actions a { color: var(--el-color-primary); }
.test-error { flex: 1 1 240px; color: var(--el-color-danger); overflow-wrap: anywhere; }
.change-list { max-height: 240px; overflow: auto; padding-left: 20px; overflow-wrap: anywhere; }
.el-pagination { flex-wrap: wrap; }
@media (max-width: 600px) {
  .upload-controls > .el-button { width: 100%; }
  .source-meta { grid-template-columns: minmax(0, 1fr); gap: 2px; }
  .source-meta dd + dt { margin-top: 6px; }
}
</style>
