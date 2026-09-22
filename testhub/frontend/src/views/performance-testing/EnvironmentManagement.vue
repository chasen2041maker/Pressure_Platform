<template>
  <div class="perf-environments">
    <div class="page-header">
      <div><h2>{{ et('title') }}</h2><p class="subtitle">{{ et('subtitle') }}</p></div>
      <el-button type="primary" :icon="Plus" :disabled="!canCreate" @click="openForm()">{{ et('create') }}</el-button>
    </div>
    <el-alert v-if="pageError" type="error" :closable="false" show-icon :title="pageError" class="page-alert">
      <el-button link type="primary" @click="initialize">{{ t('performanceTesting.common.retry') }}</el-button>
    </el-alert>
    <el-alert v-if="permissions && !permissions.can_manage_global" type="info" :closable="false" :title="et('globalReadOnly')" class="page-alert" />
    <div class="filter-bar">
      <el-input v-model="keyword" clearable :placeholder="et('searchPlaceholder')" :aria-label="et('searchPlaceholder')" @clear="search" @keyup.enter="search" />
      <el-select v-model="scopeFilter" clearable :placeholder="et('allScopes')" :aria-label="et('scope')" @change="onScopeFilter">
        <el-option :label="et('PROJECT')" value="PROJECT" /><el-option :label="et('GLOBAL')" value="GLOBAL" />
      </el-select>
      <el-select v-model="projectFilter" clearable filterable :disabled="scopeFilter === 'GLOBAL' || !permissions" :placeholder="t('performanceTesting.common.allProjects')" :aria-label="t('performanceTesting.common.project')" @change="search">
        <el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" />
      </el-select>
      <el-select v-model="activeFilter" clearable :placeholder="et('allPreferences')" :aria-label="et('preferred')" @change="search">
        <el-option :label="et('preferred')" value="true" /><el-option :label="et('notPreferred')" value="false" />
      </el-select>
      <el-button :icon="Search" @click="search">{{ t('performanceTesting.common.search') }}</el-button>
      <el-button :icon="Refresh" :loading="loading" @click="initialize">{{ t('performanceTesting.common.refresh') }}</el-button>
    </div>
    <el-table v-loading="loading" :data="list" border size="small">
      <el-table-column prop="name" :label="et('name')" min-width="160" show-overflow-tooltip />
      <el-table-column :label="et('scope')" width="100"><template #default="{ row }">{{ et(row.scope) }}</template></el-table-column>
      <el-table-column prop="project_name" :label="t('performanceTesting.common.project')" min-width="140"><template #default="{ row }">{{ row.project_name || et('allProjectsShared') }}</template></el-table-column>
      <el-table-column prop="base_url" :label="t('performanceTesting.editor.baseUrl')" min-width="220" show-overflow-tooltip><template #default="{ row }">{{ row.base_url || et('baseInherit') }}</template></el-table-column>
      <el-table-column :label="et('version')" width="90"><template #default="{ row }">v{{ row.version }}</template></el-table-column>
      <el-table-column :label="et('preferred')" width="95"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" size="small" effect="plain">{{ row.is_active ? et('preferred') : et('notPreferred') }}</el-tag></template></el-table-column>
      <el-table-column :label="t('performanceTesting.common.actions')" width="330" fixed="right">
        <template #default="{ row }">
          <el-button size="small" :disabled="!canWrite(row) || !!busyId" @click="openForm(row)">{{ t('performanceTesting.common.edit') }}</el-button>
          <el-button size="small" :disabled="!canCreate || !!busyId" @click="openForm(row, true)">{{ t('performanceTesting.common.copy') }}</el-button>
          <el-button size="small" :disabled="!canWrite(row) || !!busyId" @click="togglePreferred(row)">{{ row.is_active ? et('unsetPreferred') : et('setPreferred') }}</el-button>
          <el-button size="small" type="danger" :disabled="!canWrite(row) || !!busyId" @click="remove(row)">{{ t('performanceTesting.common.delete') }}</el-button>
        </template>
      </el-table-column>
      <template #empty><el-empty :description="pageError ? et('loadFailed') : et('empty')"><el-button v-if="canCreate" type="primary" @click="openForm()">{{ et('create') }}</el-button></el-empty></template>
    </el-table>
    <p class="subtitle">{{ et('preferredTip') }}</p>
    <div class="pager"><el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" @current-change="load" /></div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="min(940px, 96vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @closed="closeForm">
      <el-skeleton v-if="dialogLoading" :rows="6" />
      <template v-else>
        <el-alert v-if="dialogError" class="page-alert" type="error" :closable="false" show-icon :title="dialogError" />
        <el-alert v-if="copyMode" class="page-alert" type="warning" :closable="false" show-icon :title="et('copyTip')" />
        <el-form :model="form" label-width="120px" :disabled="saving || !dialogReady">
          <el-form-item :label="et('name')" required><el-input v-model="form.name" maxlength="200" :placeholder="et('namePlaceholder')" /></el-form-item>
          <el-row :gutter="16">
            <el-col :sm="12"><el-form-item :label="et('scope')" required>
              <el-select v-model="form.scope" @change="onFormScope"><el-option :label="et('PROJECT')" value="PROJECT" /><el-option :label="et('GLOBAL')" value="GLOBAL" :disabled="!permissions?.can_manage_global" /></el-select>
            </el-form-item></el-col>
            <el-col :sm="12"><el-form-item :label="t('performanceTesting.common.project')" :required="form.scope === 'PROJECT'">
              <el-select v-model="form.project" filterable :disabled="form.scope === 'GLOBAL'" :placeholder="t('performanceTesting.common.selectProject')"><el-option v-for="project in projects" :key="project.id" :label="project.name" :value="project.id" /></el-select>
            </el-form-item></el-col>
          </el-row>
          <el-form-item :label="t('performanceTesting.editor.baseUrl')"><el-input v-model="form.base_url" placeholder="http://api1:3000" /><div class="field-tip">{{ et('runnerTip') }}</div></el-form-item>
          <el-row :gutter="16">
            <el-col :sm="12"><el-form-item :label="t('performanceTesting.editor.verifySsl')"><el-switch v-model="form.verify_ssl" /><span class="switch-label">{{ form.verify_ssl ? et('tlsOn') : et('tlsOff') }}</span></el-form-item></el-col>
            <el-col :sm="12"><el-form-item :label="et('preferred')"><el-switch v-model="form.is_active" /></el-form-item></el-col>
          </el-row>
          <EnvironmentFields v-if="dialogReady" :key="formKey" v-model:headers="form.headers" v-model:variables="form.variables" :project="form.project" :copy-state="copyMode ? copySecrets : null" @invalid="fieldsInvalid = $event" />
        </el-form>
      </template>
      <template #footer>
        <el-button :disabled="saving" @click="dialogVisible = false">{{ t('performanceTesting.common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" :disabled="dialogLoading || !dialogReady || !canWrite(form) || fieldsInvalid" @click="save">{{ t('performanceTesting.common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Refresh } from '@element-plus/icons-vue'
import { getPerfProjects, getPerfEnvironmentPermissions, getPerfEnvironments, getPerfEnvironment, createPerfEnvironment, updatePerfEnvironment, deletePerfEnvironment } from '@/api/performance-testing'
import EnvironmentFields from './components/EnvironmentFields.vue'
import { canWriteEnvironment, copyEnvironment, missingCopySecrets, latestRequestGate } from './environmentForm.mjs'
import { apiError } from './shared'

const { t } = useI18n()
const route = useRoute()
const et = (key, params) => t(`performanceTesting.environment.${key}`, params || {})
const list = ref([])
const projects = ref([])
const permissions = ref(null)
const loading = ref(true)
const pageError = ref('')
const keyword = ref('')
const scopeFilter = ref('')
const projectFilter = ref('')
const activeFilter = ref('')
const page = ref(1)
const pageSize = 20
const total = ref(0)
const busyId = ref(null)
const listRequests = latestRequestGate()
const accessRequests = latestRequestGate()
const dialogRequests = latestRequestGate()
const canCreate = computed(() => !!permissions.value && (permissions.value.can_manage_global || projects.value.length > 0))
const canWrite = row => canWriteEnvironment(row, permissions.value)
const dialogVisible = ref(false)
const dialogLoading = ref(false)
const dialogReady = ref(false)
const dialogError = ref('')
const editingId = ref(null)
const copyMode = ref(false)
const copySecrets = ref(null)
const saving = ref(false)
const fieldsInvalid = ref(false)
const formKey = ref(0)
const emptyForm = () => ({ name: '', scope: 'PROJECT', project: null, base_url: '', headers: {}, variables: [], verify_ssl: true, is_active: false })
const form = ref(emptyForm())
const dialogTitle = computed(() => copyMode.value ? et('copy') : editingId.value ? et('edit') : et('create'))

function errorText(error, fallback) {
  const status = error?.response?.status
  if (status === 403) return et('permissionDenied')
  if (status === 409) return et('deleteReferenced')
  return apiError(error, et(fallback))
}
async function initialize() {
  const request = accessRequests.begin()
  listRequests.begin()
  loading.value = true
  permissions.value = null
  projects.value = []
  pageError.value = ''
  try {
    const [access, projectList] = await Promise.all([getPerfEnvironmentPermissions(), getPerfProjects({ page_size: 0 })])
    if (!accessRequests.isCurrent(request)) return
    permissions.value = access.data
    projects.value = (projectList.data.results || projectList.data || []).filter(project => access.data.project_ids.includes(project.id))
    if (projectFilter.value && !projects.value.some(project => project.id === projectFilter.value)) projectFilter.value = ''
    if (scopeFilter.value !== 'GLOBAL' && !projectFilter.value && route.query.project && projects.value.some(project => project.id === Number(route.query.project))) projectFilter.value = Number(route.query.project)
    await load()
  } catch (error) {
    if (accessRequests.isCurrent(request)) { pageError.value = errorText(error, 'permissionsFailed'); list.value = [] }
  } finally {
    if (accessRequests.isCurrent(request)) loading.value = false
  }
}
async function load() {
  if (!permissions.value) return
  const request = listRequests.begin()
  loading.value = true
  pageError.value = ''
  try {
    const params = { page: page.value, page_size: pageSize }
    if (keyword.value.trim()) params.search = keyword.value.trim()
    if (scopeFilter.value) params.scope = scopeFilter.value
    if (projectFilter.value) params.project = projectFilter.value
    if (activeFilter.value) params.is_active = activeFilter.value
    const { data } = await getPerfEnvironments(params)
    if (!listRequests.isCurrent(request)) return
    list.value = data.results || data || []
    total.value = data.count ?? list.value.length
  } catch (error) {
    if (listRequests.isCurrent(request)) { pageError.value = errorText(error, 'loadFailed'); list.value = [] }
  } finally {
    if (listRequests.isCurrent(request)) loading.value = false
  }
}
function search() { page.value = 1; load() }
function onScopeFilter() { if (scopeFilter.value === 'GLOBAL') projectFilter.value = ''; search() }
function onFormScope() { form.value.project = form.value.scope === 'GLOBAL' ? null : projectFilter.value || projects.value[0]?.id || null }
async function openForm(row = null, copy = false) {
  if (saving.value || busyId.value || (row && !copy ? !canWrite(row) : !canCreate.value)) return
  const request = dialogRequests.begin()
  editingId.value = row && !copy ? row.id : null
  copyMode.value = copy
  copySecrets.value = null
  dialogError.value = ''
  dialogReady.value = false
  fieldsInvalid.value = false
  form.value = { ...emptyForm(), project: projectFilter.value || projects.value[0]?.id || null }
  if (!projects.value.length && permissions.value.can_manage_global) form.value.scope = 'GLOBAL'
  dialogVisible.value = true
  dialogLoading.value = Boolean(row)
  try {
    if (row) {
      const { data } = await getPerfEnvironment(row.id)
      if (!dialogRequests.isCurrent(request)) return
      if (copy) {
        const copied = copyEnvironment(data)
        form.value = copied.form
        form.value.name = et('copyName', { name: data.name })
        copySecrets.value = copied.secrets
        if (!canWrite(form.value)) { form.value.scope = 'PROJECT'; onFormScope() }
      } else {
        form.value = Object.fromEntries(Object.keys(emptyForm()).map(key => [key, data[key]]))
      }
    }
    formKey.value++
    dialogReady.value = true
  } catch (error) {
    if (dialogRequests.isCurrent(request)) dialogError.value = errorText(error, 'loadFailed')
  } finally {
    if (dialogRequests.isCurrent(request)) dialogLoading.value = false
  }
}
function closeForm() { dialogRequests.begin(); form.value = emptyForm(); copySecrets.value = null; dialogReady.value = false }
async function save() {
  if (saving.value || !dialogReady.value || dialogLoading.value || fieldsInvalid.value || !canWrite(form.value)) return
  dialogError.value = ''
  if (!form.value.name.trim()) { dialogError.value = et('nameRequired'); return }
  if (form.value.scope === 'PROJECT' && !form.value.project) { dialogError.value = t('performanceTesting.scenario.projectRequired'); return }
  if (copyMode.value && missingCopySecrets(copySecrets.value).length) { dialogError.value = et('copyNeedsSecrets'); return }
  saving.value = true
  try {
    const payload = { ...form.value, name: form.value.name.trim() }
    if (editingId.value) await updatePerfEnvironment(editingId.value, payload)
    else await createPerfEnvironment(payload)
    dialogVisible.value = false
    ElMessage.success(t('performanceTesting.common.saveSuccess'))
    await load()
  } catch (error) {
    dialogError.value = errorText(error, 'saveFailed')
  } finally { saving.value = false }
}
async function togglePreferred(row) {
  if (!canWrite(row) || busyId.value) return
  busyId.value = row.id
  pageError.value = ''
  try { await updatePerfEnvironment(row.id, { is_active: !row.is_active }); await load() }
  catch (error) { pageError.value = errorText(error, 'saveFailed') }
  finally { busyId.value = null }
}
async function remove(row) {
  if (!canWrite(row) || busyId.value) return
  try { await ElMessageBox.confirm(t('performanceTesting.common.deleteConfirm', { name: row.name }), t('performanceTesting.common.tips'), { type: 'warning', confirmButtonText: t('performanceTesting.common.confirm'), cancelButtonText: t('performanceTesting.common.cancel') }) }
  catch { return }
  if (!canWrite(row) || busyId.value) return
  busyId.value = row.id
  pageError.value = ''
  try {
    await deletePerfEnvironment(row.id)
    if (list.value.length === 1 && page.value > 1) page.value--
    await load()
    ElMessage.success(t('performanceTesting.common.deleteSuccess'))
  } catch (error) { pageError.value = errorText(error, 'deleteFailed') }
  finally { busyId.value = null }
}
onMounted(initialize)
onBeforeUnmount(() => { listRequests.begin(); accessRequests.begin(); dialogRequests.begin() })
</script>

<style scoped>
.perf-environments { padding: 16px; }
.page-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.page-header h2 { margin: 0; font-size: 20px; text-wrap: balance; }
.subtitle, .field-tip { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.7; margin: 8px 0; }
.page-alert { margin-bottom: 14px; }
.filter-bar { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.filter-bar > .el-input { width: 220px; }
.filter-bar > .el-select { width: 160px; }
.pager { display: flex; justify-content: flex-end; margin-top: 14px; }
.switch-label { margin-left: 10px; font-size: 12px; }
:deep(.el-table) { font-variant-numeric: tabular-nums; }
@media (max-width: 640px) { .page-header { align-items: flex-start; } .filter-bar > .el-input, .filter-bar > .el-select { width: 100%; } }
</style>
