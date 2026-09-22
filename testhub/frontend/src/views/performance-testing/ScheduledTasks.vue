<template>
  <div class="perf-scheduled">
    <div class="page-head">
      <div>
        <h2 class="page-title">{{ t('performanceTesting.scheduled.title') }}</h2>
        <p class="page-sub">{{ t('performanceTesting.scheduled.subtitle') }}</p>
      </div>
      <div class="head-actions">
        <el-button :icon="Refresh" :loading="loading" @click="reload">
          {{ t('performanceTesting.common.refresh') }}
        </el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">
          {{ t('performanceTesting.scheduled.create') }}
        </el-button>
      </div>
    </div>

    <el-card shadow="never" class="filter-card">
      <div class="filter-bar">
        <el-select
          v-model="filters.project"
          clearable
          filterable
          class="f-item"
          :placeholder="t('performanceTesting.common.allProjects')"
          @change="onProjectChange"
        >
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-select
          v-model="filters.scenario"
          clearable
          filterable
          class="f-item"
          :placeholder="t('performanceTesting.common.allScenarios')"
          @change="reload"
        >
          <el-option v-for="s in scenarios" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-select
          v-model="filters.status"
          clearable
          class="f-item-sm"
          :placeholder="t('performanceTesting.common.status')"
          @change="reload"
        >
          <el-option :label="t('performanceTesting.scheduled.ACTIVE')" value="ACTIVE" />
          <el-option :label="t('performanceTesting.scheduled.PAUSED')" value="PAUSED" />
        </el-select>
        <el-select
          v-model="filters.trigger_type"
          clearable
          class="f-item-sm"
          :placeholder="t('performanceTesting.scheduled.triggerType')"
          @change="reload"
        >
          <el-option
            v-for="tp in TRIGGER_TYPES"
            :key="tp"
            :label="t('performanceTesting.scheduled.' + tp)"
            :value="tp"
          />
        </el-select>
        <el-input
          v-model="filters.search"
          clearable
          class="f-item"
          :placeholder="t('performanceTesting.scheduled.name')"
          @keyup.enter="reload"
          @clear="reload"
        >
          <template #append>
            <el-button :icon="Search" @click="reload" />
          </template>
        </el-input>
      </div>
    </el-card>

    <el-card shadow="never" class="table-card">
      <el-table v-loading="loading" :data="rows" size="small" stripe>
        <el-table-column :label="t('performanceTesting.scheduled.name')" min-width="200">
          <template #default="{ row }">
            <div class="cell-main">{{ row.name }}</div>
            <div v-if="row.description" class="sub-line">{{ row.description }}</div>
          </template>
        </el-table-column>

        <el-table-column :label="t('performanceTesting.common.scenario')" min-width="180">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="goScenario(row.scenario)">
              {{ row.scenario_name }}
            </el-link>
            <div class="sub-line">{{ row.project_name }}</div>
          </template>
        </el-table-column>

        <el-table-column :label="t('performanceTesting.scheduled.triggerType')" min-width="180">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">
              {{ t('performanceTesting.scheduled.' + row.trigger_type) }}
            </el-tag>
            <div class="sub-line">{{ triggerDetail(row) }}</div>
          </template>
        </el-table-column>

        <el-table-column :label="t('performanceTesting.common.status')" width="100">
          <template #default="{ row }">
            <el-switch
              :model-value="row.status === 'ACTIVE'"
              :loading="togglingId === row.id"
              @change="handleToggle(row)"
            />
          </template>
        </el-table-column>

        <el-table-column :label="t('performanceTesting.scheduled.nextRun')" min-width="160">
          <template #default="{ row }">
            <div>{{ formatTime(row.next_run_at) }}</div>
            <div class="sub-line">
              {{ t('performanceTesting.scheduled.lastRun') }}: {{ formatTime(row.last_run_at) }}
            </div>
          </template>
        </el-table-column>

        <el-table-column :label="t('performanceTesting.scheduled.runCount')" width="150">
          <template #default="{ row }">
            <el-tooltip :content="t('performanceTesting.scheduled.counterTip')" placement="top">
              <div>
                <div>{{ row.run_count || 0 }} {{ t('performanceTesting.common.times') }}</div>
                <div class="sub-line">
                  <span class="ok">{{ row.success_count || 0 }}</span>
                  /
                  <span class="err">{{ row.fail_count || 0 }}</span>
                  <span v-if="row.run_count"> · {{ successRate(row) }}</span>
                </div>
              </div>
            </el-tooltip>
          </template>
        </el-table-column>

        <el-table-column :label="t('performanceTesting.scheduled.lastError')" min-width="140">
          <template #default="{ row }">
            <el-tooltip v-if="row.last_error" :content="row.last_error" placement="top">
              <span class="err-text">{{ row.last_error }}</span>
            </el-tooltip>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>

        <el-table-column
          :label="t('performanceTesting.common.actions')"
          width="210"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :loading="runningId === row.id"
              @click="handleRunNow(row)"
            >
              {{ t('performanceTesting.scheduled.runNow') }}
            </el-button>
            <el-button link type="primary" @click="openHistory(row)">
              {{ t('performanceTesting.scheduled.viewExecutions') }}
            </el-button>
            <el-dropdown trigger="click" @command="cmd => onCommand(cmd, row)">
              <el-button link type="primary">
                {{ t('performanceTesting.common.more') }}<el-icon><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="edit">
                    {{ t('performanceTesting.common.edit') }}
                  </el-dropdown-item>
                  <el-dropdown-item command="delete" divided>
                    {{ t('performanceTesting.common.delete') }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>

        <template #empty>
          <el-empty :description="t('performanceTesting.common.empty')" :image-size="80" />
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page.current"
          v-model:page-size="page.size"
          :total="page.total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          background
          @size-change="reload"
          @current-change="fetchTasks"
        />
      </div>
    </el-card>

    <!-- 新建 / 编辑 -->
    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? t('performanceTesting.scheduled.edit') : t('performanceTesting.scheduled.create')"
      width="640px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="120px">
        <el-form-item :label="t('performanceTesting.scheduled.name')" prop="name">
          <el-input v-model="form.name" maxlength="200" show-word-limit />
        </el-form-item>

        <el-form-item :label="t('performanceTesting.common.project')">
          <el-select
            v-model="formProject"
            filterable
            clearable
            class="full"
            :placeholder="t('performanceTesting.common.selectProject')"
            @change="onFormProjectChange"
          >
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('performanceTesting.common.scenario')" prop="scenario">
          <el-select
            v-model="form.scenario"
            filterable
            class="full"
            :placeholder="t('performanceTesting.common.selectScenario')"
          >
            <el-option
              v-for="s in formScenarios"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="t('performanceTesting.scheduled.description')">
          <el-input v-model="form.description" type="textarea" :rows="2" maxlength="500" />
        </el-form-item>

        <el-form-item :label="t('performanceTesting.scheduled.triggerType')">
          <el-radio-group v-model="form.trigger_type">
            <el-radio-button v-for="tp in TRIGGER_TYPES" :key="tp" :value="tp">
              {{ t('performanceTesting.scheduled.' + tp) }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item
          v-if="form.trigger_type === 'CRON'"
          :label="t('performanceTesting.scheduled.cronExpression')"
          prop="cron_expression"
        >
          <el-input
            v-model="form.cron_expression"
            :placeholder="t('performanceTesting.scheduled.cronPlaceholder')"
          />
          <div class="preset-row">
            <span class="preset-label">{{ t('performanceTesting.scheduled.cronPresets') }}:</span>
            <el-tag
              v-for="p in cronPresets"
              :key="p.expr"
              size="small"
              effect="plain"
              class="preset-tag"
              @click="form.cron_expression = p.expr"
            >
              {{ p.label }}
            </el-tag>
          </div>
        </el-form-item>

        <el-form-item
          v-if="form.trigger_type === 'INTERVAL'"
          :label="t('performanceTesting.scheduled.intervalMinutes')"
          prop="interval_minutes"
        >
          <el-input-number v-model="form.interval_minutes" :min="1" :max="43200" />
        </el-form-item>

        <el-form-item
          v-if="form.trigger_type === 'ONCE'"
          :label="t('performanceTesting.scheduled.scheduledTime')"
          prop="scheduled_time"
        >
          <el-date-picker
            v-model="form.scheduled_time"
            type="datetime"
            :placeholder="t('performanceTesting.scheduled.scheduledTime')"
          />
        </el-form-item>

        <el-form-item :label="t('performanceTesting.scheduled.notifyOn')">
          <el-radio-group v-model="form.notify_on">
            <el-radio v-for="n in NOTIFY_ON" :key="n" :value="n">
              {{ t('performanceTesting.scheduled.' + n) }}
            </el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item
          v-if="form.notify_on !== 'NEVER'"
          :label="t('performanceTesting.scheduled.notifyChannels')"
        >
          <el-select
            v-model="form.notify_channels"
            multiple
            filterable
            clearable
            class="full"
            :placeholder="t('performanceTesting.scheduled.notifyChannels')"
          >
            <el-option
              v-for="c in channels"
              :key="c.id"
              :label="c.name"
              :value="c.id"
              :disabled="!c.enabled"
            >
              <span>{{ c.name }}</span>
              <span class="opt-sub">{{ c.type }}</span>
            </el-option>
          </el-select>
        </el-form-item>

        <el-form-item :label="t('performanceTesting.common.status')">
          <el-switch
            v-model="form.status"
            active-value="ACTIVE"
            inactive-value="PAUSED"
            :active-text="t('performanceTesting.scheduled.ACTIVE')"
            :inactive-text="t('performanceTesting.scheduled.PAUSED')"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">
          {{ t('performanceTesting.common.cancel') }}
        </el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">
          {{ t('performanceTesting.common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 执行历史 -->
    <el-drawer
      v-model="historyVisible"
      :title="`${historyTask.name || ''} · ${t('performanceTesting.scheduled.viewExecutions')}`"
      size="720px"
      destroy-on-close
    >
      <el-table v-loading="historyLoading" :data="historyRows" size="small" stripe>
        <el-table-column
          prop="execution_no"
          :label="t('performanceTesting.execution.executionNo')"
          min-width="170"
        >
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="goExecution(row)">
              {{ row.execution_no }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.common.status')" width="150">
          <template #default="{ row }">
            <el-tag size="small" :type="statusTagType(row.status)">
              {{ t('performanceTesting.status.' + row.status) }}
            </el-tag>
            <el-tag
              v-if="row.sla_result && row.sla_result !== 'NOT_EVALUATED'"
              size="small"
              class="ml6"
              effect="plain"
              :type="slaTagType(row.sla_result)"
            >
              {{ t('performanceTesting.sla.' + row.sla_result) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.metric.tps')" width="90">
          <template #default="{ row }">{{ fmtNum(row.tps) }}</template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.metric.errorRate')" width="90">
          <template #default="{ row }">{{ fmtNum(row.error_rate) }}%</template>
        </el-table-column>
        <el-table-column
          :label="t('performanceTesting.execution.startTime')"
          min-width="150"
        >
          <template #default="{ row }">{{ formatTime(row.start_time || row.created_at) }}</template>
        </el-table-column>
        <template #empty>
          <el-empty :description="t('performanceTesting.common.empty')" :image-size="70" />
        </template>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search, ArrowDown } from '@element-plus/icons-vue'
import {
  getPerfProjects,
  getPerfScenarios,
  getPerfScheduledTasks,
  createPerfScheduledTask,
  updatePerfScheduledTask,
  deletePerfScheduledTask,
  togglePerfScheduledTask,
  runPerfScheduledTaskNow,
  getPerfScheduledTaskExecutions
} from '@/api/performance-testing'
import { getChannels } from '@/api/monitor'
import { statusTagType, slaTagType, formatTime, apiError } from './shared'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const TRIGGER_TYPES = ['CRON', 'INTERVAL', 'ONCE']
const NOTIFY_ON = ['ALWAYS', 'ON_SLA_FAIL', 'NEVER']

const loading = ref(false)
const saving = ref(false)
const togglingId = ref(null)
const runningId = ref(null)

const rows = ref([])
const projects = ref([])
const scenarios = ref([])
const formScenarios = ref([])
const channels = ref([])

const filters = reactive({
  project: null,
  scenario: null,
  status: null,
  trigger_type: null,
  search: ''
})
const page = reactive({ current: 1, size: 20, total: 0 })

const dialogVisible = ref(false)
const formRef = ref(null)
const formProject = ref(null)
const form = reactive(emptyForm())

const historyVisible = ref(false)
const historyLoading = ref(false)
const historyRows = ref([])
const historyTask = ref({})

const cronPresets = computed(() => [
  { label: t('performanceTesting.scheduled.cronEveryHour'), expr: '0 * * * *' },
  { label: t('performanceTesting.scheduled.cronEveryDay'), expr: '0 2 * * *' },
  { label: t('performanceTesting.scheduled.cronEveryMonday'), expr: '0 3 * * 1' },
  { label: t('performanceTesting.scheduled.cronWorkday'), expr: '0 22 * * 1-5' }
])

const rules = {
  name: [
    { required: true, message: t('performanceTesting.scheduled.nameRequired'), trigger: 'blur' }
  ],
  scenario: [
    {
      required: true,
      message: t('performanceTesting.common.selectScenario'),
      trigger: 'change'
    }
  ],
  cron_expression: [
    {
      required: true,
      message: t('performanceTesting.scheduled.cronRequired'),
      trigger: 'blur'
    }
  ],
  interval_minutes: [
    {
      required: true,
      message: t('performanceTesting.scheduled.intervalRequired'),
      trigger: 'change'
    }
  ],
  scheduled_time: [
    {
      required: true,
      message: t('performanceTesting.scheduled.timeRequired'),
      trigger: 'change'
    }
  ]
}

function emptyForm() {
  return {
    id: null,
    name: '',
    description: '',
    scenario: null,
    trigger_type: 'CRON',
    cron_expression: '0 2 * * *',
    interval_minutes: 60,
    scheduled_time: null,
    status: 'ACTIVE',
    notify_on: 'ON_SLA_FAIL',
    notify_channels: []
  }
}

// ------------------------------------------------------------------ //
// 数据加载
// ------------------------------------------------------------------ //
async function loadProjects() {
  try {
    const res = await getPerfProjects({ page_size: 200 })
    projects.value = res.data.results || res.data || []
  } catch (e) {
    projects.value = []
  }
}

async function loadScenarios(projectId, target) {
  try {
    const params = { page_size: 200 }
    if (projectId) params.project = projectId
    const res = await getPerfScenarios(params)
    const list = res.data.results || res.data || []
    if (target === 'form') formScenarios.value = list
    else scenarios.value = list
  } catch (e) {
    if (target === 'form') formScenarios.value = []
    else scenarios.value = []
  }
}

async function loadChannels() {
  try {
    const res = await getChannels({ page_size: 200 })
    channels.value = res.data.results || res.data || []
  } catch (e) {
    channels.value = []
  }
}

async function fetchTasks() {
  loading.value = true
  try {
    const params = { page: page.current, page_size: page.size }
    if (filters.project) params.project = filters.project
    if (filters.scenario) params.scenario = filters.scenario
    if (filters.status) params.status = filters.status
    if (filters.trigger_type) params.trigger_type = filters.trigger_type
    if (filters.search) params.search = filters.search
    const res = await getPerfScheduledTasks(params)
    rows.value = res.data.results || res.data || []
    page.total = res.data.count || rows.value.length
  } catch (e) {
    ElMessage.error(e?.error || e?.message || t('performanceTesting.common.empty'))
    rows.value = []
    page.total = 0
  } finally {
    loading.value = false
  }
}

function reload() {
  page.current = 1
  fetchTasks()
}

function onProjectChange() {
  filters.scenario = null
  loadScenarios(filters.project)
  reload()
}

// ------------------------------------------------------------------ //
// 新建 / 编辑
// ------------------------------------------------------------------ //
async function openCreate() {
  Object.assign(form, emptyForm())
  formProject.value = filters.project || null
  if (filters.scenario) form.scenario = filters.scenario
  await loadScenarios(formProject.value, 'form')
  dialogVisible.value = true
}

async function openEdit(row) {
  Object.assign(form, {
    id: row.id,
    name: row.name,
    description: row.description || '',
    scenario: row.scenario,
    trigger_type: row.trigger_type,
    cron_expression: row.cron_expression || '0 2 * * *',
    interval_minutes: row.interval_minutes || 60,
    scheduled_time: row.scheduled_time || null,
    status: row.status,
    notify_on: row.notify_on || 'ON_SLA_FAIL',
    notify_channels: [...(row.notify_channels || [])]
  })
  formProject.value = row.project || null
  await loadScenarios(formProject.value, 'form')
  dialogVisible.value = true
}

function onFormProjectChange() {
  form.scenario = null
  loadScenarios(formProject.value, 'form')
}

async function handleSave() {
  const ok = await formRef.value?.validate().catch(() => false)
  if (!ok) return
  saving.value = true
  try {
    // 只提交当前触发方式相关字段，避免把上一次选择的残留值写进库
    const payload = {
      name: form.name,
      description: form.description,
      scenario: form.scenario,
      trigger_type: form.trigger_type,
      status: form.status,
      notify_on: form.notify_on,
      notify_channels: form.notify_on === 'NEVER' ? [] : form.notify_channels,
      cron_expression: form.trigger_type === 'CRON' ? form.cron_expression : '',
      interval_minutes: form.trigger_type === 'INTERVAL' ? form.interval_minutes : null,
      scheduled_time: form.trigger_type === 'ONCE' ? form.scheduled_time : null
    }
    if (form.id) await updatePerfScheduledTask(form.id, payload)
    else await createPerfScheduledTask(payload)
    ElMessage.success(t('performanceTesting.common.saveSuccess'))
    dialogVisible.value = false
    fetchTasks()
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.common.empty')))
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      t('performanceTesting.common.deleteConfirm', { name: row.name }),
      t('performanceTesting.common.delete'),
      { type: 'warning' }
    )
  } catch (e) {
    return
  }
  try {
    await deletePerfScheduledTask(row.id)
    ElMessage.success(t('performanceTesting.common.deleteSuccess'))
    if (rows.value.length === 1 && page.current > 1) page.current -= 1
    fetchTasks()
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.common.empty')))
  }
}

async function handleToggle(row) {
  togglingId.value = row.id
  try {
    const res = await togglePerfScheduledTask(row.id)
    row.status = res.data.status
    row.next_run_at = res.data.next_run_at
    ElMessage.success(
      res.data.status === 'ACTIVE'
        ? t('performanceTesting.scheduled.enabled')
        : t('performanceTesting.scheduled.disabled')
    )
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.common.empty')))
  } finally {
    togglingId.value = null
  }
}

async function handleRunNow(row) {
  runningId.value = row.id
  try {
    const res = await runPerfScheduledTaskNow(row.id)
    ElMessage.success(t('performanceTesting.scheduled.runNowStarted'))
    fetchTasks()
    const execId = res.data?.execution?.id
    if (execId) router.push(`/performance-testing/executions/${execId}/monitor`)
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.common.empty')))
  } finally {
    runningId.value = null
  }
}

async function openHistory(row) {
  historyTask.value = row
  historyVisible.value = true
  historyLoading.value = true
  try {
    const res = await getPerfScheduledTaskExecutions(row.id)
    historyRows.value = res.data.results || res.data || []
  } catch (e) {
    historyRows.value = []
  } finally {
    historyLoading.value = false
  }
}

function onCommand(cmd, row) {
  if (cmd === 'edit') openEdit(row)
  else if (cmd === 'delete') handleDelete(row)
}

// ------------------------------------------------------------------ //
// 展示辅助
// ------------------------------------------------------------------ //
function triggerDetail(row) {
  if (row.trigger_type === 'CRON') return row.cron_expression || '-'
  if (row.trigger_type === 'INTERVAL') {
    return `${row.interval_minutes || 0} min`
  }
  return formatTime(row.scheduled_time)
}

function successRate(row) {
  const total = row.run_count || 0
  if (!total) return '-'
  return `${Math.round((row.success_count || 0) / total * 100)}%`
}

function fmtNum(v) {
  if (v === null || v === undefined || v === '') return '-'
  const n = Number(v)
  return Number.isNaN(n) ? String(v) : (Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(2))
}


function goScenario(id) {
  if (id) router.push(`/performance-testing/scenarios/${id}`)
}

function goExecution(row) {
  const path = ['RUNNING', 'PREPARING', 'PENDING', 'STOPPING'].includes(row.status)
    ? `/performance-testing/executions/${row.id}/monitor`
    : `/performance-testing/executions/${row.id}`
  router.push(path)
}

onMounted(async () => {
  // 场景列表页「定时压测」入口会带 scenario 过来，直接预置过滤条件
  const qsScenario = parseInt(route.query.scenario, 10)
  if (!Number.isNaN(qsScenario)) filters.scenario = qsScenario
  const qsProject = parseInt(route.query.project, 10)
  if (!Number.isNaN(qsProject)) filters.project = qsProject
  await Promise.all([loadProjects(), loadScenarios(filters.project), loadChannels()])
  fetchTasks()
})
</script>

<style lang="scss" scoped>
.perf-scheduled { padding: 16px; }

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
  .page-title { margin: 0; font-size: 20px; font-weight: 600; color: #303133; }
  .page-sub { margin: 4px 0 0; font-size: 13px; color: #909399; }
  .head-actions { display: flex; gap: 8px; }
}

.filter-card { margin-bottom: 12px; :deep(.el-card__body) { padding: 14px; } }
.filter-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.f-item { width: 190px; }
.f-item-sm { width: 130px; }

.table-card { :deep(.el-card__body) { padding: 0 0 12px; } }
.cell-main { font-weight: 500; color: #303133; }
.sub-line {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
  .ok { color: #52c41a; }
  .err { color: #f5222d; }
}
.err-text {
  color: #f5222d;
  font-size: 12px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.muted { color: #c0c4cc; }
.ml6 { margin-left: 6px; }
.pager { display: flex; justify-content: flex-end; padding: 12px 14px 0; }

.full { width: 100%; }
.preset-row { margin-top: 6px; display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.preset-label { font-size: 12px; color: #909399; }
.preset-tag { cursor: pointer; }
.opt-sub { float: right; font-size: 12px; color: #909399; }
</style>
