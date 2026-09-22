<template>
  <div class="perf-execution-list">
    <div class="page-head">
      <div>
        <h2 class="page-title">{{ t('performanceTesting.execution.title') }}</h2>
        <p class="page-sub">{{ t('performanceTesting.execution.subtitle') }}</p>
      </div>
      <div class="head-actions">
        <el-tooltip :content="t('performanceTesting.execution.reapTip')" placement="top">
          <el-button :icon="RefreshLeft" :loading="reaping" @click="handleReap">
            {{ t('performanceTesting.execution.reapStale') }}
          </el-button>
        </el-tooltip>
        <el-button
          type="primary"
          :icon="DataAnalysis"
          :disabled="selection.length < 2"
          @click="goCompare"
        >
          {{ t('performanceTesting.execution.compare') }}
          <span v-if="selection.length">({{ selection.length }})</span>
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
          :placeholder="t('performanceTesting.execution.filterStatus')"
          @change="reload"
        >
          <el-option
            v-for="s in statusOptions"
            :key="s"
            :label="t('performanceTesting.status.' + s)"
            :value="s"
          />
        </el-select>
        <el-select
          v-model="filters.sla_result"
          clearable
          class="f-item-sm"
          :placeholder="t('performanceTesting.execution.filterSla')"
          @change="reload"
        >
          <el-option
            v-for="s in slaOptions"
            :key="s"
            :label="t('performanceTesting.sla.' + s)"
            :value="s"
          />
        </el-select>
        <el-select
          v-model="filters.trigger_type"
          clearable
          class="f-item-sm"
          :placeholder="t('performanceTesting.execution.triggerType')"
          @change="reload"
        >
          <el-option
            v-for="s in triggerOptions"
            :key="s"
            :label="t('performanceTesting.trigger.' + s)"
            :value="s"
          />
        </el-select>
        <el-input
          v-model="filters.search"
          clearable
          class="f-item"
          :placeholder="t('performanceTesting.execution.executionNo')"
          :prefix-icon="Search"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-button :icon="Search" @click="reload">{{ t('performanceTesting.common.search') }}</el-button>
        <el-button :icon="Refresh" @click="resetFilters">{{ t('performanceTesting.common.reset') }}</el-button>
      </div>
    </el-card>

    <el-card shadow="never" class="table-card">
      <el-table
        v-loading="loading"
        :data="rows"
        row-key="id"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="46" :selectable="canSelect" reserve-selection />
        <el-table-column :label="t('performanceTesting.execution.executionNo')" min-width="200">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="goDetail(row)">{{ row.execution_no }}</el-link>
            <div class="sub-line">{{ row.scenario_name }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.common.project')" prop="project_name" min-width="130" />
        <el-table-column :label="t('performanceTesting.common.status')" width="130">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">
              {{ t('performanceTesting.status.' + row.status) }}
            </el-tag>
            <el-progress
              v-if="isActive(row)"
              :percentage="Math.round(row.progress || 0)"
              :stroke-width="3"
              :show-text="false"
              class="row-progress"
            />
          </template>
        </el-table-column>
        <el-table-column label="SLA" width="90">
          <template #default="{ row }">
            <el-tag :type="slaTagType(row.sla_result)" size="small" effect="plain">
              {{ t('performanceTesting.sla.' + row.sla_result) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="TPS / 业务 RPS" width="130" align="right">
          <template #default="{ row }">{{ fmtNum(row.tps) }} {{ row.engine === 'K6' ? 'RPS' : 'TPS' }}</template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.metric.p95Rt')" width="100" align="right">
          <template #default="{ row }">{{ row.p95_rt != null ? row.p95_rt + ' ms' : '-' }}</template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.metric.errorRate')" width="90" align="right">
          <template #default="{ row }">
            <span :class="{ 'err-hl': (row.error_rate || 0) > 0 }">
              {{ row.error_rate != null ? row.error_rate + '%' : '-' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.metric.duration')" width="90" align="right">
          <template #default="{ row }">{{ row.duration ? formatDuration(row.duration) : '-' }}</template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.execution.triggerType')" width="110">
          <template #default="{ row }">
            <el-tag :type="triggerTagType(row.trigger_type)" size="small" effect="plain">
              {{ t('performanceTesting.trigger.' + row.trigger_type) }}
            </el-tag>
            <div v-if="row.scheduled_task_name" class="sub-line">{{ row.scheduled_task_name }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.execution.executedBy')" prop="executed_by_name" width="100" />
        <el-table-column :label="t('performanceTesting.execution.startTime')" width="160">
          <template #default="{ row }">{{ formatTime(row.start_time || row.created_at) }}</template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.common.actions')" width="190" fixed="right">
          <template #default="{ row }">
            <el-button v-if="isActive(row)" link type="warning" @click="goMonitor(row)">
              {{ t('performanceTesting.execution.monitor') }}
            </el-button>
            <el-button v-else link type="primary" @click="goDetail(row)">
              {{ t('performanceTesting.execution.viewReport') }}
            </el-button>
            <el-button v-if="isActive(row)" link type="danger" @click="handleStop(row)">
              {{ t('performanceTesting.execution.stop') }}
            </el-button>
            <el-dropdown v-else trigger="click" @command="(c) => onCommand(c, row)">
              <el-button link>{{ t('performanceTesting.common.more') }}</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="baseline" :disabled="row.status !== 'COMPLETED'">
                    {{ t('performanceTesting.execution.setBaseline') }}
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
          <el-empty :description="t('performanceTesting.common.empty')" />
        </template>
      </el-table>

      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        class="pager"
        @size-change="reload"
        @current-change="fetchList"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh, RefreshLeft, DataAnalysis } from '@element-plus/icons-vue'

import { statusTagType, slaTagType, triggerTagType, formatTime, formatDuration, apiError } from './shared'
import {
  getPerfExecutions, deletePerfExecution, stopPerfExecution, reapStalePerfExecutions,
  setBaselineFromExecution, getPerfProjects, getPerfScenarios
} from '@/api/performance-testing'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const statusOptions = ['PENDING', 'PREPARING', 'RUNNING', 'STOPPING', 'COMPLETED', 'FAILED', 'STOPPED', 'TIMEOUT']
const slaOptions = ['PASSED', 'FAILED', 'NOT_EVALUATED']
const triggerOptions = ['MANUAL', 'SCHEDULED', 'API', 'CI']
const ACTIVE_STATUSES = ['PENDING', 'PREPARING', 'RUNNING', 'STOPPING']

const loading = ref(false)
const reaping = ref(false)
const rows = ref([])
const projects = ref([])
const scenarios = ref([])
const selection = ref([])
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const filters = reactive({
  project: null, scenario: null, status: '', sla_result: '', trigger_type: '', search: ''
})

let timer = null

function isActive(row) {
  return ACTIVE_STATUSES.includes(row.status)
}
function canSelect(row) {
  return row.status === 'COMPLETED'
}
function fmtNum(v) {
  return v == null ? '-' : Number(v).toFixed(1)
}

async function loadProjects() {
  try {
    const res = await getPerfProjects({ page_size: 200 })
    projects.value = res.data.results || res.data || []
  } catch (e) {
    projects.value = []
  }
}

async function loadScenarios() {
  try {
    const res = await getPerfScenarios({ page_size: 200, project: filters.project || undefined })
    scenarios.value = res.data.results || res.data || []
  } catch (e) {
    scenarios.value = []
  }
}

function onProjectChange() {
  filters.scenario = null
  loadScenarios()
  reload()
}

async function fetchList() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value, ordering: '-created_at' }
    if (filters.project) params.project = filters.project
    if (filters.scenario) params.scenario = filters.scenario
    if (filters.status) params.status = filters.status
    if (filters.sla_result) params.sla_result = filters.sla_result
    if (filters.trigger_type) params.trigger_type = filters.trigger_type
    if (filters.search) params.search = filters.search
    const res = await getPerfExecutions(params)
    rows.value = res.data.results || res.data || []
    total.value = res.data.count ?? rows.value.length
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.common.empty')))
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  fetchList()
}

function resetFilters() {
  Object.assign(filters, {
    project: null, scenario: null, status: '', sla_result: '', trigger_type: '', search: ''
  })
  loadScenarios()
  reload()
}

function onSelectionChange(val) {
  // 最多对比 5 条，超出直接截断并提示，避免图表挤成一团
  if (val.length > 5) {
    ElMessage.warning(t('performanceTesting.comparison.selectTip'))
    selection.value = val.slice(0, 5)
    return
  }
  selection.value = val
}

function goCompare() {
  if (selection.value.length < 2) {
    ElMessage.warning(t('performanceTesting.execution.selectToCompare'))
    return
  }
  router.push({
    path: '/performance-testing/comparison',
    query: { ids: selection.value.map(r => r.id).join(',') }
  })
}

function goDetail(row) {
  router.push(`/performance-testing/executions/${row.id}`)
}
function goMonitor(row) {
  router.push(`/performance-testing/executions/${row.id}/monitor`)
}

async function handleStop(row) {
  try {
    await ElMessageBox.confirm(
      t('performanceTesting.execution.stopConfirm'),
      t('performanceTesting.execution.stop'),
      { type: 'warning' }
    )
  } catch (e) {
    return
  }
  try {
    await stopPerfExecution(row.id)
    ElMessage.success(t('performanceTesting.execution.stopped'))
    fetchList()
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.execution.stop')))
  }
}

async function handleReap() {
  reaping.value = true
  try {
    const res = await reapStalePerfExecutions()
    const count = res.data?.reaped ?? 0
    ElMessage.success(count
      ? t('performanceTesting.execution.reapDone', { count })
      : t('performanceTesting.execution.reapNone'))
    fetchList()
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.execution.reapStale')))
  } finally {
    reaping.value = false
  }
}

async function onCommand(command, row) {
  if (command === 'baseline') {
    try {
      await setBaselineFromExecution({ execution_id: row.id })
      ElMessage.success(t('performanceTesting.common.saveSuccess'))
    } catch (e) {
      ElMessage.error(apiError(e, t('performanceTesting.execution.setBaseline')))
    }
  } else if (command === 'delete') {
    try {
      await ElMessageBox.confirm(
        t('performanceTesting.execution.deleteConfirm'),
        t('performanceTesting.common.delete'),
        { type: 'warning' }
      )
    } catch (e) {
      return
    }
    try {
      await deletePerfExecution(row.id)
      ElMessage.success(t('performanceTesting.common.deleteSuccess'))
      fetchList()
    } catch (e) {
      ElMessage.error(apiError(e, t('performanceTesting.common.delete')))
    }
  }
}

onMounted(async () => {
  if (route.query.project) filters.project = Number(route.query.project)
  if (route.query.scenario) filters.scenario = Number(route.query.scenario)
  await Promise.all([loadProjects(), loadScenarios()])
  fetchList()
  // 列表里可能有运行中的执行，10s 刷一次进度即可，不用上 WebSocket
  timer = setInterval(() => {
    if (rows.value.some(isActive)) fetchList()
  }, 10000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style lang="scss" scoped>
.perf-execution-list { padding: 16px; }

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
.sub-line { font-size: 12px; color: #909399; margin-top: 2px; }
.row-progress { margin-top: 4px; }
.err-hl { color: #f5222d; font-weight: 600; }
.pager { display: flex; justify-content: flex-end; padding: 12px 14px 0; }
</style>
