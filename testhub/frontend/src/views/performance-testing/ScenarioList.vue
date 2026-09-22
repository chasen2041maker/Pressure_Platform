<template>
  <div class="perf-scenarios">
    <div class="page-header">
      <div class="ph-left">
        <h2>{{ t('performanceTesting.scenario.title') }}</h2>
        <span class="subtitle">{{ t('performanceTesting.scenario.subtitle') }}</span>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">{{ t('performanceTesting.scenario.create') }}</el-button>
    </div>

    <div class="filter-bar">
      <el-select v-model="projectId" :placeholder="t('performanceTesting.common.allProjects')" clearable style="width: 180px" @change="load">
        <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
      </el-select>
      <el-input v-model="keyword" :placeholder="t('performanceTesting.scenario.name')" clearable style="width: 220px" @clear="load" @keyup.enter="load" />
      <el-button :icon="Search" @click="load">{{ t('performanceTesting.common.search') }}</el-button>
    </div>

    <el-table :data="list" v-loading="loading" size="small" border>
      <el-table-column :label="t('performanceTesting.scenario.name')" prop="name" min-width="160" show-overflow-tooltip />
      <el-table-column :label="t('performanceTesting.project.title')" prop="project_name" width="130" show-overflow-tooltip />
      <el-table-column :label="t('performanceTesting.scenario.loadSummary')" width="120">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ t('performanceTesting.loadModel.' + (row.load_model || 'CONCURRENCY')) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.scenario.stepCount')" width="80" align="center" prop="step_count" />
      <el-table-column :label="t('performanceTesting.scenario.lastExecution')" min-width="200">
        <template #default="{ row }">
          <template v-if="row.last_execution">
            <el-tag size="small" :type="statusTagType(row.last_execution.status)">{{ t('performanceTesting.status.' + row.last_execution.status) }}</el-tag>
            <span class="le-metric">{{ (row.last_execution.engine || row.engine) === 'K6' ? '业务 RPS' : 'TPS' }} {{ fmt(row.last_execution.tps) }}</span>
            <span class="le-metric">P95 {{ fmt(row.last_execution.p95_rt) }}</span>
          </template>
          <span v-else class="muted">{{ t('performanceTesting.scenario.neverRun') }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.execution.sla')" width="100">
        <template #default="{ row }">
          <span v-if="row.last_execution">
            <el-tag size="small" :type="slaTagType(row.last_execution.sla_result)">{{ t('performanceTesting.sla.' + (row.last_execution.sla_result || 'NOT_EVALUATED')) }}</el-tag>
          </span>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.common.createdBy')" width="110" prop="created_by_name" />
      <el-table-column :label="t('performanceTesting.common.actions')" width="260" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" :disabled="row.enabled === false" @click="openExecute(row)">{{ t('performanceTesting.scenario.execute') }}</el-button>
          <el-button size="small" @click="goEdit(row)">{{ t('performanceTesting.common.edit') }}</el-button>
          <el-dropdown @command="(c) => onMore(c, row)">
            <el-button size="small">{{ t('performanceTesting.common.more') }}<el-icon><ArrowDown /></el-icon></el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="debug">{{ t('performanceTesting.scenario.debug') }}</el-dropdown-item>
                <el-dropdown-item command="duplicate">{{ t('performanceTesting.scenario.duplicate') }}</el-dropdown-item>
                <el-dropdown-item command="baseline">{{ t('performanceTesting.scenario.setSchedule') }}</el-dropdown-item>
                <el-dropdown-item command="delete" divided>{{ t('performanceTesting.common.delete') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total" layout="total, prev, pager, next" @current-change="load" />
    </div>

    <!-- 执行确认 -->
    <el-dialog v-model="execDialog" :title="t('performanceTesting.execute.title')" width="520px">
      <el-alert :title="t('performanceTesting.execute.confirmTip')" type="warning" :closable="false" show-icon class="tip" />
      <div v-if="preflight" class="preflight">
        <div :class="['pf-banner', preflight.ok ? 'ok' : 'fail']">
          {{ preflight.ok ? t('performanceTesting.execute.preflightPassed') : t('performanceTesting.execute.preflightFailed') }}
        </div>
        <template v-if="preflight.errors && preflight.errors.length">
          <div class="pf-title">{{ t('performanceTesting.execute.errors') }}</div>
          <ul class="pf-list"><li v-for="(e, i) in preflight.errors" :key="i">{{ e }}</li></ul>
        </template>
        <template v-if="preflight.warnings && preflight.warnings.length">
          <div class="pf-title">{{ t('performanceTesting.execute.warnings') }}</div>
          <ul class="pf-list warn"><li v-for="(w, i) in preflight.warnings" :key="i">{{ w }}</li></ul>
        </template>
      </div>
      <el-checkbox v-model="jumpToMonitor" class="jump">{{ t('performanceTesting.execute.jumpToMonitor') }}</el-checkbox>
      <template #footer>
        <el-button @click="execDialog = false">{{ t('performanceTesting.common.cancel') }}</el-button>
        <el-button type="primary" :loading="executing" :disabled="preflight && !preflight.ok" @click="confirmExecute">{{ t('performanceTesting.execute.start') }}</el-button>
      </template>
    </el-dialog>

    <!-- 复制 -->
    <el-dialog v-model="dupDialog" :title="t('performanceTesting.scenario.duplicate')" width="420px">
      <el-form label-width="80px">
        <el-form-item :label="t('performanceTesting.scenario.duplicateName')">
          <el-input v-model="dupName" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dupDialog = false">{{ t('performanceTesting.common.cancel') }}</el-button>
        <el-button type="primary" :loading="duping" @click="confirmDuplicate">{{ t('performanceTesting.common.confirm') }}</el-button>
      </template>
    </el-dialog>

    <!-- 调试结果 -->
    <el-drawer v-model="debugDrawer" :title="t('performanceTesting.editor.debugTitle')" size="560px">
      <div v-if="debugRunning" class="debug-running"><el-icon class="is-loading"><Loading /></el-icon> {{ t('performanceTesting.editor.debugRunning') }}</div>
      <div v-else-if="debugResult">
        <div :class="['db-banner', debugResult.all_passed ? 'ok' : 'fail']">
          {{ debugResult.all_passed ? t('performanceTesting.editor.debugPassed') : t('performanceTesting.editor.debugFailed', { count: debugResult.failed_count || 0 }) }}
        </div>
        <div v-for="(s, i) in debugResult.steps" :key="i" class="db-step">
          <div class="db-step-head">
            <el-tag size="small" :type="s.status === 'PASS' ? 'success' : 'danger'">{{ s.status }}</el-tag>
            <span class="db-step-name">{{ s.name }}</span>
            <span class="db-step-el">⏱ {{ s.elapsed_ms }}ms</span>
          </div>
          <div class="db-detail"><b>{{ t('performanceTesting.editor.debugRequest') }}</b> {{ s.method }} {{ s.url }}</div>
          <div class="db-detail"><b>{{ t('performanceTesting.editor.debugResponse') }}</b> {{ s.status_code }} · {{ s.response_time_ms }}ms</div>
          <div v-if="s.error" class="db-detail err">{{ s.error }}</div>
        </div>
      </div>
      <el-empty v-else :description="t('performanceTesting.editor.debugEmpty')" />
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, ArrowDown, Loading } from '@element-plus/icons-vue'
import {
  getPerfProjects, getPerfScenarios, duplicatePerfScenario, deletePerfScenario,
  executePerfScenario, debugPerfScenario, setBaselineFromExecution
} from '@/api/performance-testing'
import { statusTagType, slaTagType } from './shared'
import { normalizeExecuteResult, normalizePreflight } from './executeResult.mjs'

const { t } = useI18n()
const router = useRouter()
const list = ref([])
const loading = ref(false)
const projects = ref([])
const projectId = ref(null)
const keyword = ref('')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)

const execDialog = ref(false)
const execTarget = ref(null)
const preflight = ref(null)
const jumpToMonitor = ref(true)
const executing = ref(false)

const dupDialog = ref(false)
const dupTarget = ref(null)
const dupName = ref('')
const duping = ref(false)

const debugDrawer = ref(false)
const debugRunning = ref(false)
const debugResult = ref(null)

function fmt(v) {
  if (v === undefined || v === null || v === '') return '-'
  return typeof v === 'number' ? (Math.round(v * 100) / 100) : v
}

async function loadProjects() {
  try {
    const res = await getPerfProjects({ page_size: 200 })
    projects.value = res.data.results || res.data || []
  } catch (e) { /* ignore */ }
}

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (projectId.value) params.project = projectId.value
    if (keyword.value) params.search = keyword.value
    const res = await getPerfScenarios(params)
    list.value = res.data.results || res.data || []
    total.value = res.data.count || list.value.length
  } catch (e) {
    ElMessage.error('加载场景列表失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  if (projects.value.length === 0) { ElMessage.warning(t('performanceTesting.scenario.createFirst')); return }
  router.push('/performance-testing/scenarios/0')
}
function goEdit(row) { router.push(`/performance-testing/scenarios/${row.id}`) }

async function openExecute(row) {
  execTarget.value = row
  preflight.value = null
  execDialog.value = true
  try {
    const res = await executePerfScenario(row.id, {})
    // 直接拿到了 execution 说明 preflight 通过；否则返回 preflight 详情
    preflight.value = normalizeExecuteResult(res)
    if (res.data.execution) {
      finishExecute(res.data.execution)
    }
  } catch (e) {
    const data = e?.response?.data || {}
    preflight.value = {
      ...normalizePreflight(data.preflight),
      errors: (data.preflight?.errors?.length)
        ? data.preflight.errors
        : [data.error || '检查失败'],
    }
  }
}

async function confirmExecute() {
  if (!execTarget.value) return
  executing.value = true
  try {
    const res = await executePerfScenario(execTarget.value.id, {})
    const exec = res.data.execution
    if (!exec) { ElMessage.error(res.data.error || '启动失败'); return }
    finishExecute(exec)
  } catch (e) {
    const data = e?.response?.data || {}
    preflight.value = {
      ...normalizePreflight(data.preflight),
      errors: (data.preflight?.errors?.length)
        ? data.preflight.errors
        : [data.error || '启动失败'],
    }
    if (e?.response?.status !== 400) ElMessage.error(data.error || '启动失败')
  } finally {
    executing.value = false
  }
}

function finishExecute(exec) {
  ElMessage.success(t('performanceTesting.execute.started'))
  execDialog.value = false
  if (jumpToMonitor.value) router.push(`/performance-testing/executions/${exec.id}/monitor`)
  else load()
}

function onMore(command, row) {
  if (command === 'debug') openDebug(row)
  else if (command === 'duplicate') openDuplicate(row)
  else if (command === 'baseline') setBaseline(row)
  else if (command === 'delete') remove(row)
}

async function openDebug(row) {
  debugDrawer.value = true
  debugRunning.value = true
  debugResult.value = null
  try {
    const res = await debugPerfScenario(row.id, {})
    debugResult.value = res.data
  } catch (e) {
    const msg = e?.response?.data?.error || '调试失败'
    ElMessage.error(msg)
    debugResult.value = { all_passed: false, failed_count: 0, steps: [], error: msg }
  } finally {
    debugRunning.value = false
  }
}

function openDuplicate(row) {
  dupTarget.value = row
  dupName.value = t('performanceTesting.scenario.duplicateSuffix', { name: row.name })
  dupDialog.value = true
}
async function confirmDuplicate() {
  if (!dupTarget.value) return
  duping.value = true
  try {
    await duplicatePerfScenario(dupTarget.value.id, { name: dupName.value })
    ElMessage.success(t('performanceTesting.common.saveSuccess'))
    dupDialog.value = false
    await load()
  } catch (e) {
    ElMessage.error(e?.response?.data?.error || '复制失败')
  } finally {
    duping.value = false
  }
}

function setBaseline(row) {
  if (!row.last_execution || row.last_execution.status !== 'COMPLETED') {
    ElMessage.warning('需要一条已完成（COMPLETED）的执行才能设为基线')
    return
  }
  ElMessageBox.confirm(`将 ${row.last_execution.execution_no} 设为基线？`, t('performanceTesting.common.tips'), {
    type: 'info', confirmButtonText: t('performanceTesting.common.confirm'), cancelButtonText: t('performanceTesting.common.cancel')
  }).then(async () => {
    try {
      await setBaselineFromExecution({ execution_id: row.last_execution.id })
      ElMessage.success(t('performanceTesting.common.saveSuccess'))
    } catch (e) {
      ElMessage.error(e?.response?.data?.error || '设置基线失败')
    }
  }).catch(() => {})
}

function remove(row) {
  ElMessageBox.confirm(t('performanceTesting.scenario.deleteConfirm', { name: row.name }), t('performanceTesting.common.tips'), {
    type: 'warning', confirmButtonText: t('performanceTesting.common.confirm'), cancelButtonText: t('performanceTesting.common.cancel')
  }).then(async () => {
    try {
      await deletePerfScenario(row.id)
      ElMessage.success(t('performanceTesting.common.deleteSuccess'))
      await load()
    } catch (e) {
      ElMessage.error(e?.response?.data?.error || '删除失败')
    }
  }).catch(() => {})
}

onMounted(() => { loadProjects(); load() })
</script>

<style lang="scss" scoped>
.perf-scenarios { padding: 16px; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.ph-left { display: flex; align-items: baseline; gap: 10px; h2 { margin: 0; font-size: 20px; } }
.subtitle { color: #8c8c8c; font-size: 13px; }
.filter-bar { display: flex; gap: 10px; margin-bottom: 14px; }
.pager { margin-top: 14px; display: flex; justify-content: flex-end; }
.le-metric { margin-left: 8px; color: #595959; font-size: 12px; }
.muted { color: #bfbfbf; }
.tip { margin-bottom: 12px; }
.preflight { margin: 10px 0; .pf-banner { padding: 8px 12px; border-radius: 6px; font-weight: 600; } .pf-banner.ok { background: #f6ffed; color: #52c41a; } .pf-banner.fail { background: #fff1f0; color: #ff4d4f; } .pf-title { margin: 10px 0 4px; font-weight: 600; } .pf-list { margin: 0; padding-left: 18px; color: #ff4d4f; } .pf-list.warn { color: #faad14; } }
.jump { margin-top: 10px; }
.debug-running { text-align: center; color: #1890ff; padding: 20px; }
.db-banner { padding: 10px 14px; border-radius: 6px; font-weight: 600; margin-bottom: 12px; } .db-banner.ok { background: #f6ffed; color: #52c41a; } .db-banner.fail { background: #fff1f0; color: #ff4d4f; }
.db-step { border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; .db-step-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; } .db-step-name { font-weight: 600; } .db-step-el { color: #8c8c8c; font-size: 12px; margin-left: auto; } .db-detail { font-size: 12px; color: #595959; margin-top: 3px; word-break: break-all; } .db-detail.err { color: #ff4d4f; } }
</style>
