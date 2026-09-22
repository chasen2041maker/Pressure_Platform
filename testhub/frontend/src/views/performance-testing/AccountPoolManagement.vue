<template>
  <div class="account-pools">
    <header class="page-header"><div><h2>{{ at('title') }}</h2><p class="tip">{{ at('subtitle') }}</p></div>
      <el-button type="primary" :disabled="!project || loading || !!pageError || saving" @click="openImport()">{{ at('create') }}</el-button>
    </header>
    <div class="toolbar">
      <el-select v-model="project" :disabled="loading || saving" :placeholder="at('projectRequired')" @change="changeProject">
        <el-option v-for="item in projects" :key="item.id" :value="item.id" :label="item.name" />
      </el-select>
      <el-button :loading="loading" @click="initialize">{{ t('performanceTesting.common.refresh') }}</el-button>
    </div>
    <p class="tip">{{ at('verifiedTip') }}</p>
    <el-alert v-if="pageError" type="error" :closable="false" :title="pageError"><el-button link type="primary" @click="initialize">{{ t('performanceTesting.common.retry') }}</el-button></el-alert>
    <el-empty v-else-if="!loading && !projects.length" :description="at('noProjects')"><el-button type="primary" @click="router.push('/performance-testing/projects')">{{ t('performanceTesting.project.create') }}</el-button></el-empty>
    <el-table v-else v-loading="loading" :data="pools" :empty-text="at('noPools')">
      <el-table-column prop="name" :label="at('name')" min-width="180" />
      <el-table-column :label="at('version')" width="100"><template #default="{ row }">{{ row.latest_version ? `v${row.latest_version.version}` : '—' }}</template></el-table-column>
      <el-table-column :label="at('count')" width="120"><template #default="{ row }">{{ row.latest_version?.row_count ?? '—' }}</template></el-table-column>
      <el-table-column :label="t('performanceTesting.common.actions')" min-width="290"><template #default="{ row }">
        <el-button link type="primary" @click="openHistory(row)">{{ at('history') }}</el-button>
        <el-button link type="primary" @click="openImport(row)">{{ at('newVersion') }}</el-button>
        <el-button link type="danger" :disabled="!!busyId" @click="removePool(row)">{{ t('performanceTesting.common.delete') }}</el-button>
      </template></el-table-column>
    </el-table>

    <el-dialog v-model="importVisible" :title="editingPool ? at('newVersion') : at('create')" width="min(760px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @closed="closeImport">
      <el-alert type="info" :closable="false" :title="editingPool ? at('versionNotice') : at('inspectTip')" />
      <el-form label-position="top" class="import-form" :disabled="saving">
        <el-form-item :label="at('name')"><el-input v-model="form.name" :disabled="!!editingPool" maxlength="200" :placeholder="at('namePlaceholder')" /></el-form-item>
        <el-form-item :label="at('file')">
          <input ref="fileInput" type="file" accept=".csv,.json" :aria-label="at('file')" :disabled="saving" @change="onFileChange" />
          <p class="tip">{{ at('fileTip') }}</p>
          <el-button v-if="file && !inspection" :loading="inspecting" :disabled="saving" @click="inspectFile">{{ at('retryInspect') }}</el-button>
        </el-form-item>
        <template v-if="inspection">
          <el-alert type="success" :closable="false" :title="at('inspectReady', { count: inspection.row_count })" />
          <el-form-item :label="at('identity')"><el-select v-model="form.identity_column" :placeholder="at('identityRequired')"><el-option v-for="column in inspection.columns" :key="column" :value="column" :label="column" /></el-select><p class="tip">{{ at('identityTip') }}</p></el-form-item>
          <el-form-item :label="at('groupColumn')"><el-select v-model="form.group_column"><el-option value="" :label="at('noGrouping')" /><el-option v-for="column in inspection.columns" :key="column" :value="column" :label="column" /></el-select><p class="tip">{{ at('groupTip') }}</p></el-form-item>
          <h3>{{ at('mapping') }}</h3><p class="tip">{{ at('mappingTip') }}</p>
          <div v-for="(row, index) in form.mapping" :key="index" class="mapping-row">
            <el-input v-model="row.name" :aria-label="`${at('variableName')} ${index + 1}`" :placeholder="at('variableName')" maxlength="64" />
            <span aria-hidden="true">←</span>
            <el-select v-model="row.column" :aria-label="`${at('sourceColumn')} ${index + 1}`" :placeholder="at('sourceColumn')"><el-option v-for="column in inspection.columns" :key="column" :value="column" :label="column" /></el-select>
            <el-button :aria-label="`${t('performanceTesting.common.delete')} ${index + 1}`" @click="form.mapping.splice(index, 1)">{{ t('performanceTesting.common.delete') }}</el-button>
          </div>
          <el-button @click="form.mapping.push({ name: '', column: '' })">{{ at('addMapping') }}</el-button>
          <p class="tip">{{ at('previewTip') }}</p>
          <el-table :data="inspection.rows" max-height="200"><el-table-column v-for="column in inspection.columns" :key="column" :label="column" min-width="120"><template #default>******</template></el-table-column></el-table>
        </template>
      </el-form>
      <el-alert v-for="(message, index) in importErrors" :key="index" type="error" :closable="false" :title="message" class="error" />
      <template #footer><el-button :disabled="saving" @click="importVisible = false">{{ t('performanceTesting.common.cancel') }}</el-button><el-button type="primary" :disabled="!inspection || inspecting" :loading="saving" @click="submitImport">{{ at('confirmImport') }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="historyVisible" :title="`${historyPool?.name || ''} · ${at('history')}`" width="min(850px, 95vw)" @closed="closeHistory">
      <div v-loading="historyLoading">
        <el-alert v-if="historyError" type="error" :closable="false" :title="historyError"><el-button link type="primary" @click="openHistory(historyPool)">{{ t('performanceTesting.common.retry') }}</el-button></el-alert>
        <template v-else>
          <el-select :model-value="historyVersion?.id" :placeholder="at('version')" @change="loadVersionDetail"><el-option v-for="version in historyVersions" :key="version.id" :value="version.id" :label="`v${version.version} · ${at('count')} ${version.row_count}`" /></el-select>
          <template v-if="historyVersion">
            <el-descriptions :column="1" border class="version-details">
              <el-descriptions-item :label="at('identity')">{{ historyVersion.identity_column }}</el-descriptions-item>
              <el-descriptions-item :label="at('createdAt')">{{ historyVersion.created_at }}</el-descriptions-item>
              <el-descriptions-item :label="at('hash')">{{ historyVersion.content_hash }}</el-descriptions-item>
              <el-descriptions-item :label="at('mapping')"><div v-for="(column, name) in historyVersion.field_mapping" :key="name">{{ name }} ← {{ column }}</div></el-descriptions-item>
              <el-descriptions-item v-if="historyVersion.groups?.length" :label="at('group')"><span v-for="group in historyVersion.groups" :key="group.id" class="group">{{ group.label }} · {{ group.count }}</span><p class="tip">{{ at('groupTip') }}</p></el-descriptions-item>
            </el-descriptions>
            <h3>{{ at('preview') }}</h3><p class="tip">{{ at('previewTip') }}</p>
            <el-table :data="preview?.rows || []" max-height="220"><el-table-column v-for="column in preview?.columns || []" :key="column" :label="column" min-width="120"><template #default>******</template></el-table-column></el-table>
            <h3>{{ at('usage') }}</h3>
            <p v-if="!usage?.scenarios?.length && !usage?.executions?.length" class="tip">{{ at('noUsage') }}</p>
            <div class="usage-links"><el-button v-for="scenario in usage?.scenarios || []" :key="`s${scenario.id}`" link type="primary" @click="router.push(`/performance-testing/scenarios/${scenario.id}`)">{{ at('scenarios') }}：{{ scenario.name }}</el-button>
              <el-button v-for="execution in usage?.executions || []" :key="`e${execution.id}`" link type="primary" @click="router.push(`/performance-testing/executions/${execution.id}`)">{{ at('executions') }} #{{ execution.id }} · {{ execution.status }}</el-button></div>
            <el-button type="danger" plain :disabled="!!busyId || historyLoading" @click="removeVersion">{{ t('performanceTesting.common.delete') }} v{{ historyVersion.version }}</el-button>
          </template>
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPerfProjects, getPerfEnvironmentPermissions, getPerfAccountPools, inspectPerfAccountPool,
  createPerfAccountPool, createPerfAccountPoolVersion, getPerfAccountPoolVersions, previewPerfAccountPoolVersion,
  getPerfAccountPoolUsage, deletePerfAccountPool, deletePerfAccountPoolVersion } from '@/api/performance-testing'
import { latestRequestGate } from './environmentForm.mjs'
import { accountImportErrors, mappingIssue, suggestedMapping, buildAccountImport } from './accountPoolForm.mjs'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const at = (key, params) => t(`performanceTesting.accountPool.${key}`, params)
const projects = ref([]), project = ref(null), pools = ref([]), loading = ref(true), pageError = ref(''), busyId = ref(null)
const listRequests = latestRequestGate(), importRequests = latestRequestGate(), historyRequests = latestRequestGate()
const importVisible = ref(false), editingPool = ref(null), file = ref(null), fileInput = ref(null), inspection = ref(null)
const inspecting = ref(false), saving = ref(false), importErrors = ref([])
const emptyForm = () => ({ project: null, name: '', identity_column: '', group_column: '', mapping: [] })
const form = ref(emptyForm())
const historyVisible = ref(false), historyPool = ref(null), historyVersions = ref([]), historyVersion = ref(null)
const historyLoading = ref(false), historyError = ref(''), preview = ref(null), usage = ref(null)

async function initialize() {
  const request = listRequests.begin()
  loading.value = true
  pageError.value = ''
  try {
    const [access, result] = await Promise.all([getPerfEnvironmentPermissions(), getPerfProjects({ page_size: 0 })])
    if (!listRequests.isCurrent(request)) return
    projects.value = (result.data.results || result.data).filter(item => access.data.project_ids.includes(item.id))
    if (!projects.value.some(item => item.id === project.value)) {
      const previous = project.value
      project.value = projects.value.find(item => item.id === Number(route.query.project))?.id || projects.value[0]?.id || null
      if (previous !== project.value) { importVisible.value = false; closeImport(); historyVisible.value = false; closeHistory() }
    }
    await loadPools()
  } catch (error) { if (listRequests.isCurrent(request)) pageError.value = accountImportErrors(error, [], t)[0] }
  finally { if (listRequests.isCurrent(request)) loading.value = false }
}
async function loadPools() {
  const request = listRequests.begin()
  const selectedProject = project.value
  loading.value = true
  pageError.value = ''
  pools.value = []
  try {
    if (!selectedProject) return
    const { data } = await getPerfAccountPools({ project: selectedProject, page_size: 0 })
    if (listRequests.isCurrent(request)) pools.value = (data.results || data).filter(item => item.project === selectedProject)
  } catch (error) { if (listRequests.isCurrent(request)) pageError.value = accountImportErrors(error, [], t)[0] }
  finally { if (listRequests.isCurrent(request)) loading.value = false }
}
function changeProject() { importVisible.value = false; closeImport(); historyVisible.value = false; closeHistory(); loadPools() }
function openImport(pool = null) {
  if (!project.value || saving.value || loading.value || pageError.value) return
  closeImport()
  editingPool.value = pool
  form.value = { ...emptyForm(), project: project.value, name: pool?.name || '' }
  importVisible.value = true
}
function closeImport() {
  importRequests.begin()
  file.value = null
  if (fileInput.value) fileInput.value.value = ''
  inspection.value = null
  inspecting.value = false
  importErrors.value = []
  form.value = emptyForm()
}
function onFileChange(event) {
  if (saving.value) return
  importRequests.begin()
  file.value = event.target.files?.[0] || null
  inspection.value = null
  form.value.identity_column = ''
  form.value.group_column = ''
  form.value.mapping = []
  inspectFile()
}
async function inspectFile() {
  if (saving.value || !importVisible.value || !form.value.project) return
  const request = importRequests.begin()
  importErrors.value = []
  inspection.value = null
  if (!file.value) { importErrors.value = [at('fileRequired')]; inspecting.value = false; return }
  if (!/\.(csv|json)$/i.test(file.value.name)) { importErrors.value = [at('fileFormat')]; inspecting.value = false; return }
  if (!file.value.size || file.value.size > 20 * 1024 * 1024) { importErrors.value = [at('fileSize')]; inspecting.value = false; return }
  inspecting.value = true
  try {
    const data = new FormData()
    data.append('project', String(form.value.project)); data.append('file', file.value)
    const response = await inspectPerfAccountPool(data)
    if (!importRequests.isCurrent(request)) return
    inspection.value = response.data
    const previous = editingPool.value?.latest_version
    const columns = response.data.columns
    form.value.identity_column = columns.includes(previous?.identity_column) ? previous.identity_column : ''
    form.value.group_column = columns.includes(previous?.group_column) ? previous.group_column : ''
    form.value.mapping = previous && Object.values(previous.field_mapping).every(column => columns.includes(column))
      ? Object.entries(previous.field_mapping).map(([name, column]) => ({ name, column })) : suggestedMapping(columns)
  } catch (error) { if (importRequests.isCurrent(request)) importErrors.value = accountImportErrors(error, [], t) }
  finally { if (importRequests.isCurrent(request)) inspecting.value = false }
}
async function submitImport() {
  if (saving.value || inspecting.value || !inspection.value || !file.value || form.value.project !== project.value) return
  const issue = !form.value.name.trim() ? 'nameRequired' : !inspection.value.columns.includes(form.value.identity_column)
    ? 'identityRequired' : mappingIssue(form.value.mapping, inspection.value.columns)
  if (issue) { importErrors.value = [at(issue)]; return }
  saving.value = true
  importErrors.value = []
  const request = importRequests.begin()
  try {
    const payload = buildAccountImport(form.value, file.value)
    if (editingPool.value) await createPerfAccountPoolVersion(editingPool.value.id, payload)
    else await createPerfAccountPool(payload)
    if (!importRequests.isCurrent(request)) return
    importVisible.value = false
    ElMessage.success(t('performanceTesting.common.saveSuccess'))
    await loadPools()
  } catch (error) { if (importRequests.isCurrent(request)) importErrors.value = accountImportErrors(error, inspection.value.columns, t) }
  finally { saving.value = false }
}
async function openHistory(pool) {
  if (!pool) return
  const request = historyRequests.begin()
  historyPool.value = pool
  historyVisible.value = true
  historyLoading.value = true
  historyError.value = ''
  historyVersion.value = null
  try {
    const { data } = await getPerfAccountPoolVersions(pool.id)
    if (!historyRequests.isCurrent(request)) return
    historyVersions.value = data
    if (data.length) await loadVersionDetail(data[0].id)
  } catch (error) { if (historyRequests.isCurrent(request)) historyError.value = accountImportErrors(error, [], t)[0] }
  finally { if (historyRequests.isCurrent(request)) historyLoading.value = false }
}
async function loadVersionDetail(id) {
  const request = historyRequests.begin()
  historyLoading.value = true
  historyError.value = ''
  historyVersion.value = null
  preview.value = null; usage.value = null
  try {
    const [sample, records] = await Promise.all([previewPerfAccountPoolVersion(id), getPerfAccountPoolUsage(id)])
    if (!historyRequests.isCurrent(request)) return
    preview.value = sample.data; usage.value = records.data
    historyVersion.value = historyVersions.value.find(version => version.id === id)
  } catch (error) { if (historyRequests.isCurrent(request)) historyError.value = accountImportErrors(error, [], t)[0] }
  finally { if (historyRequests.isCurrent(request)) historyLoading.value = false }
}
function closeHistory() { historyRequests.begin(); historyVersion.value = null; historyLoading.value = false }
async function confirmDelete(name) {
  try { await ElMessageBox.confirm(t('performanceTesting.common.deleteConfirm', { name }), t('performanceTesting.common.confirm'),
    { type: 'warning', confirmButtonText: t('performanceTesting.common.confirm'), cancelButtonText: t('performanceTesting.common.cancel') }); return true }
  catch { return false }
}
async function removePool(pool) {
  if (busyId.value || !await confirmDelete(pool.name)) return
  busyId.value = pool.id
  try { await deletePerfAccountPool(pool.id); await loadPools() }
  catch (error) { pageError.value = accountImportErrors(error, [], t)[0] }
  finally { busyId.value = null }
}
async function removeVersion() {
  const version = historyVersion.value
  if (!version || busyId.value || !await confirmDelete(`v${version.version}`)) return
  busyId.value = version.id
  try { await deletePerfAccountPoolVersion(version.id); await openHistory(historyPool.value); await loadPools() }
  catch (error) { historyError.value = accountImportErrors(error, [], t)[0] }
  finally { busyId.value = null }
}
onMounted(initialize)
onBeforeUnmount(() => { listRequests.begin(); importRequests.begin(); historyRequests.begin() })
</script>

<style scoped>
.account-pools { padding: 16px; min-width: 0; }
.page-header, .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
h2 { font-size: 20px; margin: 0; } h3 { font-size: 15px; }
.toolbar { justify-content: flex-start; }.toolbar .el-select { width: 260px; }
.tip { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.7; margin: 6px 0; width: 100%; }
.import-form { margin-top: 16px; }.import-form .el-select { width: 100%; }
.mapping-row { display: grid; grid-template-columns: minmax(100px, 1fr) 16px minmax(100px, 1fr) auto; gap: 8px; margin-bottom: 10px; align-items: center; }
.error { margin-top: 10px; }.version-details { margin-top: 14px; overflow-wrap: anywhere; }.group { margin-right: 12px; }
.usage-links { display: flex; flex-direction: column; align-items: flex-start; margin-bottom: 16px; gap: 8px; }
input[type=file] { max-width: 100%; }.el-table { width: 100%; }
@media(max-width: 500px) { .mapping-row { grid-template-columns: minmax(0, 1fr); padding: 10px; border: 1px solid var(--el-border-color); }.mapping-row > span { display: none; }.toolbar .el-select { width: 100%; } }
</style>
