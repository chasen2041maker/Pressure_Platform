<template>
  <div class="perf-comparison">
    <el-alert title="k6 峰值按 UTC 固定1秒完成桶计算，非滚动峰值；旧版或时间戳不完整的峰值与窗口率未验证，显示未采集，不计算增幅。平均 RPS 保留整场口径。k6 P95/P99 使用累计固定桶插值估算，误差受桶宽和样本分布影响。" type="info" :closable="false" />
    <div class="page-head">
      <div>
        <h2 class="page-title">{{ t('performanceTesting.comparison.title') }}</h2>
        <p class="page-sub">{{ t('performanceTesting.comparison.subtitle') }}</p>
      </div>
      <div class="head-actions">
        <el-button :icon="Back" @click="goBack">{{ t('performanceTesting.common.back') }}</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="loadCompare">
          {{ t('performanceTesting.common.refresh') }}
        </el-button>
        <el-button type="primary" :disabled="executions.length < 2" @click="openSaveDialog">
          {{ t('performanceTesting.comparison.saveAsReport') }}
        </el-button>
      </div>
    </div>

    <!-- 选择区：支持直接在本页换一批执行记录 -->
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
          @change="loadCandidates"
        >
          <el-option v-for="s in scenarios" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-select
          v-model="selectedIds"
          multiple
          filterable
          collapse-tags
          collapse-tags-tooltip
          :multiple-limit="5"
          class="f-item-lg"
          :placeholder="t('performanceTesting.comparison.selectExecutions')"
        >
          <el-option
            v-for="e in candidates"
            :key="e.id"
            :label="`${e.execution_no} · ${e.scenario_name}`"
            :value="e.id"
          >
            <span class="opt-no">{{ e.execution_no }}</span>
            <span class="opt-sub">{{ e.scenario_name }} · {{ formatTime(e.created_at) }}</span>
          </el-option>
        </el-select>
        <el-button type="primary" :disabled="selectedIds.length < 2" @click="applySelection">
          {{ t('performanceTesting.execution.compare') }}
        </el-button>
        <span class="hint">{{ t('performanceTesting.comparison.selectTip') }}</span>
      </div>
    </el-card>

    <el-empty
      v-if="!loading && !executions.length"
      :description="t('performanceTesting.comparison.noData')"
    />

    <template v-else>
      <!-- 执行概况卡片 -->
      <div v-loading="loading" class="exec-cards">
        <el-card
          v-for="(e, idx) in executions"
          :key="e.id"
          shadow="never"
          class="exec-card"
          :class="{ 'is-base': idx === 0 }"
        >
          <div class="exec-card-head">
            <el-link type="primary" :underline="false" @click="goReport(e.id)">
              {{ e.execution_no }}
            </el-link>
            <el-tag v-if="idx === 0" size="small" type="warning" effect="plain">
              {{ t('performanceTesting.comparison.baseline') }}
            </el-tag>
          </div>
          <div class="exec-card-name">{{ e.scenario_name }}</div>
          <div class="exec-card-tags">
            <el-tag size="small" :type="statusTagType(e.status)">
              {{ t('performanceTesting.status.' + e.status) }}
            </el-tag>
            <el-tag size="small" :type="slaTagType(e.sla_result)" effect="plain">
              {{ t('performanceTesting.sla.' + (e.sla_result || 'NOT_EVALUATED')) }}
            </el-tag>
          </div>
          <div class="exec-card-meta">
            <span>{{ formatTime(e.created_at) }}</span>
            <span>{{ formatDuration(e.duration) }}</span>
          </div>
          <div class="exec-card-meta">{{ loadSummary(e.load_snapshot) }}</div>
        </el-card>
      </div>

      <!-- 指标对比 -->
      <el-card shadow="never" class="block-card">
        <template #header>
          <div class="block-head">
            <span>{{ t('performanceTesting.comparison.metricCompare') }}</span>
            <span class="block-tip">{{ t('performanceTesting.comparison.baselineTip') }}</span>
          </div>
        </template>
        <el-table :data="metricRows" size="small" border stripe>
          <el-table-column
            prop="label"
            :label="t('performanceTesting.comparison.metricName')"
            width="160"
            fixed
          />
          <el-table-column
            v-for="(e, idx) in executions"
            :key="e.id"
            :label="e.execution_no"
            min-width="170"
            align="center"
          >
            <template #header>
              <div class="col-head">
                <span>{{ e.execution_no }}</span>
                <el-tag v-if="idx === 0" size="small" type="warning" effect="plain">
                  {{ t('performanceTesting.comparison.baseline') }}
                </el-tag>
              </div>
            </template>
            <template #default="{ row }">
              <div class="cell-val">{{ formatMetric(row.key, e.summary[row.key]) }}</div>
              <div v-if="idx > 0" class="cell-delta" :class="deltaClass(row, e)">
                {{ deltaText(row, e) }}
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 曲线叠加 -->
      <el-card shadow="never" class="block-card">
        <template #header>
          <div class="block-head">
            <span>{{ t('performanceTesting.comparison.chartCompare') }}</span>
            <el-radio-group v-model="chartMetric" size="small" @change="renderChart">
              <el-radio-button
                v-for="m in chartMetrics"
                :key="m.key"
                :value="m.key"
              >
                {{ m.label }}
              </el-radio-button>
            </el-radio-group>
          </div>
        </template>
        <div ref="chartRef" class="cmp-chart"></div>
      </el-card>

      <!-- 接口级对比 -->
      <el-card shadow="never" class="block-card">
        <template #header>
          <div class="block-head">
            <span>{{ t('performanceTesting.comparison.stepCompare') }}</span>
            <el-radio-group v-model="stepMetric" size="small">
              <el-radio-button
                v-for="m in stepMetrics"
                :key="m.key"
                :value="m.key"
              >
                {{ m.label }}
              </el-radio-button>
            </el-radio-group>
          </div>
        </template>
        <el-table :data="stepComparison" size="small" border stripe>
          <el-table-column prop="scenario_id" label="场景 ID" width="100" />
          <el-table-column prop="step_id" label="步骤 ID" width="110" />
          <el-table-column prop="match_basis" label="对齐依据" min-width="200" />
          <el-table-column
            prop="step_name"
            :label="t('performanceTesting.editor.stepName')"
            min-width="200"
            fixed
            show-overflow-tooltip
          />
          <el-table-column
            v-for="(e, idx) in executions"
            :key="e.id"
            :label="e.execution_no"
            min-width="150"
            align="center"
          >
            <template #default="{ row }">
              <div class="cell-val">{{ stepValueText(row, idx) }}</div>
              <div v-if="idx > 0" class="cell-delta" :class="stepDeltaClass(row, idx)">
                {{ stepDeltaText(row, idx) }}
              </div>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-if="!stepComparison.length"
          :description="t('performanceTesting.common.empty')"
          :image-size="70"
        />
      </el-card>
    </template>

    <!-- 保存为对照报告 -->
    <el-dialog v-model="saveDialogVisible" :title="t('performanceTesting.comparison.saveAsReport')"
               width="480px" destroy-on-close>
      <el-alert :title="t('performanceTesting.comparison.saveDialogTip')" type="info"
                :closable="false" show-icon class="save-tip" />
      <el-form label-width="90px">
        <el-form-item :label="t('performanceTesting.comparisonReport.reportTitle')">
          <el-input v-model="saveForm.title"
                    :placeholder="t('performanceTesting.comparisonReport.titlePlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('performanceTesting.comparisonReport.aiAnalysis')">
          <el-checkbox v-model="saveForm.aiAnalyze">
            {{ t('performanceTesting.comparisonReport.aiAnalyze') }}
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="saveDialogVisible = false">{{ t('performanceTesting.common.cancel') }}</el-button>
        <el-button type="primary" :loading="savingReport" @click="handleSaveReport">
          {{ t('performanceTesting.common.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Back, Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import {
  getPerfProjects,
  getPerfScenarios,
  getPerfExecutions,
  comparePerfExecutions,
  createPerfComparisonReport
} from '@/api/performance-testing'
import { statusTagType, slaTagType, formatTime, formatDuration, apiError } from './shared'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const loading = ref(false)
const projects = ref([])
const scenarios = ref([])
const candidates = ref([])
const selectedIds = ref([])
const executions = ref([])
const stepComparison = ref([])
const metricKeys = ref([])

const filters = reactive({ project: null, scenario: null })

// 指标方向：true 表示数值越大越好（TPS 类），false 表示越小越好（耗时/错误率）
const hasK6 = computed(() => executions.value.some(e => e.engine === 'K6' || e.load_snapshot?._engine === 'K6'))
const rateLabel = computed(() => hasK6.value ? '业务 RPS（k6）/ TPS（其他）' : t('performanceTesting.metric.tps'))
const METRIC_META = {
  total_requests: { i18n: 'metric.totalRequests', unit: '', higherBetter: true, neutral: true },
  tps: { i18n: 'metric.tps', unit: '', higherBetter: true },
  peak_tps: { i18n: 'metric.peakTps', unit: '', higherBetter: true },
  avg_rt: { i18n: 'metric.avgRt', unit: 'ms', higherBetter: false },
  p90_rt: { i18n: 'metric.p90Rt', unit: 'ms', higherBetter: false },
  p95_rt: { i18n: 'metric.p95Rt', unit: 'ms', higherBetter: false },
  p99_rt: { i18n: 'metric.p99Rt', unit: 'ms', higherBetter: false },
  max_rt: { i18n: 'metric.maxRt', unit: 'ms', higherBetter: false },
  error_rate: { i18n: 'metric.errorRate', unit: '%', higherBetter: false }
}

const metricRows = computed(() =>
  (metricKeys.value.length ? metricKeys.value : Object.keys(METRIC_META)).map(key => {
    const meta = METRIC_META[key] || {}
    return {
      key,
      label: key === 'tps' ? rateLabel.value : key === 'peak_tps' && hasK6.value ? '峰值业务 RPS（k6）/ TPS（其他）' : meta.i18n ? t('performanceTesting.' + meta.i18n) : key,
      unit: meta.unit || '',
      higherBetter: meta.higherBetter,
      neutral: !!meta.neutral
    }
  })
)

const chartMetric = ref('tps')
const chartMetrics = computed(() => [
  { key: 'tps', label: rateLabel.value },
  { key: 'avg_rt', label: t('performanceTesting.metric.avgRt') },
  { key: 'p95_rt', label: t('performanceTesting.metric.p95Rt') },
  { key: 'error_rate', label: t('performanceTesting.metric.errorRate') },
  { key: 'active_users', label: t('performanceTesting.metric.activeUsers') }
])

const stepMetric = ref('avg_rt')
const stepMetrics = computed(() => [
  { key: 'avg_rt', label: t('performanceTesting.metric.avgRt') },
  { key: 'p95_rt', label: t('performanceTesting.metric.p95Rt') },
  { key: 'tps', label: rateLabel.value },
  { key: 'error_rate', label: t('performanceTesting.metric.errorRate') }
])

const SERIES_COLORS = ['#1890ff', '#52c41a', '#fa8c16', '#722ed1', '#f5222d']

const chartRef = ref(null)
let chart = null

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

async function loadScenarios() {
  try {
    const params = { page_size: 200 }
    if (filters.project) params.project = filters.project
    const res = await getPerfScenarios(params)
    scenarios.value = res.data.results || res.data || []
  } catch (e) {
    scenarios.value = []
  }
}

// 只有已完成的执行才有完整 summary，用于候选列表
async function loadCandidates() {
  try {
    const params = { page_size: 50, status: 'COMPLETED', ordering: '-created_at' }
    if (filters.project) params.project = filters.project
    if (filters.scenario) params.scenario = filters.scenario
    const res = await getPerfExecutions(params)
    candidates.value = res.data.results || res.data || []
  } catch (e) {
    candidates.value = []
  }
}

function onProjectChange() {
  filters.scenario = null
  loadScenarios()
  loadCandidates()
}

async function loadCompare() {
  const ids = selectedIds.value
  if (ids.length < 2) {
    executions.value = []
    stepComparison.value = []
    return
  }
  loading.value = true
  try {
    const res = await comparePerfExecutions(ids)
    executions.value = res.data.executions || []
    stepComparison.value = res.data.step_comparison || []
    metricKeys.value = res.data.metric_keys || []
    await nextTick()
    renderChart()
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.comparison.noData')))
    executions.value = []
    stepComparison.value = []
  } finally {
    loading.value = false
  }
}

// 换选择后同步到 URL，便于分享/刷新保持
function applySelection() {
  if (selectedIds.value.length < 2) {
    ElMessage.warning(t('performanceTesting.execution.selectToCompare'))
    return
  }
  router.replace({
    path: '/performance-testing/comparison',
    query: { ids: selectedIds.value.join(',') }
  })
  loadCompare()
}

// ------------------------------------------------------------------ //
// 指标展示
// ------------------------------------------------------------------ //
function formatMetric(key, val) {
  if (val === null || val === undefined || val === '') return '-'
  const meta = METRIC_META[key] || {}
  const num = Number(val)
  if (!Number.isFinite(num)) return '未采集'
  if (key === 'total_requests') return num.toLocaleString()
  const fixed = Math.abs(num) >= 100 ? num.toFixed(0) : num.toFixed(2)
  return `${fixed}${meta.unit || ''}`
}

function deltaText(row, exec) {
  const pct = (exec.delta_pct || {})[row.key]
  if (pct === null || pct === undefined) return '-'
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct}%`
}

// 颜色语义按指标方向判定，不是简单的正负
function deltaClass(row, exec) {
  const pct = (exec.delta_pct || {})[row.key]
  if (pct === null || pct === undefined) return 'flat'
  if (Math.abs(pct) < 1) return 'flat'
  if (row.neutral) return 'neutral'
  const better = row.higherBetter ? pct > 0 : pct < 0
  return better ? 'better' : 'worse'
}

function loadSummary(snapshot) {
  const snap = snapshot || {}
  const model = snap.model
  const dur = formatDuration(snap._planned_duration || snap.duration)
  if (model === 'RPS') {
    return t('performanceTesting.loadModel.summaryRps', { rps: snap.target_rps, duration: dur })
  }
  if (model === 'RAMPING') {
    return t('performanceTesting.loadModel.summaryRamping', {
      stages: (snap.stages || []).length,
      duration: dur
    })
  }
  if (model === 'SPIKE') {
    return t('performanceTesting.loadModel.summarySpike', {
      peak: snap.spike_peak,
      duration: dur
    })
  }
  return t('performanceTesting.loadModel.summary', {
    concurrency: snap.concurrency,
    duration: dur
  })
}

// ------------------------------------------------------------------ //
// 接口级对比
// ------------------------------------------------------------------ //
function stepValue(row, idx) {
  const cell = (row.values || [])[idx]
  if (!cell) return null
  const v = cell[stepMetric.value]
  return v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v)
}

function stepValueText(row, idx) {
  const v = stepValue(row, idx)
  if (v === null || Number.isNaN(v)) return '-'
  const unit = stepMetric.value === 'error_rate' ? '%' : (stepMetric.value === 'tps' ? '' : 'ms')
  return `${Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)}${unit}`
}

function stepDeltaPct(row, idx) {
  const base = stepValue(row, 0)
  const cur = stepValue(row, idx)
  if (base === null || cur === null || !base) return null
  return Math.round((cur - base) / base * 10000) / 100
}

function stepDeltaText(row, idx) {
  const pct = stepDeltaPct(row, idx)
  if (pct === null) return '-'
  return `${pct > 0 ? '+' : ''}${pct}%`
}

function stepDeltaClass(row, idx) {
  const pct = stepDeltaPct(row, idx)
  if (pct === null || Math.abs(pct) < 1) return 'flat'
  const higherBetter = stepMetric.value === 'tps'
  const better = higherBetter ? pct > 0 : pct < 0
  return better ? 'better' : 'worse'
}

// ------------------------------------------------------------------ //
// 曲线叠加
// ------------------------------------------------------------------ //
function renderChart() {
  if (!chartRef.value || !executions.value.length) return
  chart = chart || echarts.init(chartRef.value)

  const key = chartMetric.value
  const meta = key === 'active_users'
    ? { unit: '' }
    : (METRIC_META[key] || { unit: '' })
  const unit = key === 'error_rate' ? '%' : (key.endsWith('_rt') ? 'ms' : (meta.unit || ''))

  const series = executions.value.map((e, idx) => ({
    name: e.execution_no,
    type: 'line',
    smooth: true,
    showSymbol: false,
    // 时序长度可能不同，用 [秒偏移, 值] 二维点位对齐 x 轴
    data: (e.samples || []).map(s => [Math.round(s.ts_offset), s[key]]),
    itemStyle: { color: SERIES_COLORS[idx % SERIES_COLORS.length] },
    lineStyle: { width: idx === 0 ? 2.5 : 1.8, type: idx === 0 ? 'solid' : 'dashed' }
  }))

  chart.setOption({
    grid: { left: 56, right: 24, top: 44, bottom: 40 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v === null || v === undefined ? '-' : `${Number(v).toFixed(2)}${unit}`)
    },
    legend: { top: 6 },
    xAxis: {
      type: 'value',
      name: t('performanceTesting.common.seconds'),
      axisLabel: { formatter: '{value}s' }
    },
    yAxis: { type: 'value', name: unit || undefined },
    series
  }, true)
}

function resizeChart() {
  chart?.resize()
}

// ------------------------------------------------------------------ //
// 保存为对照报告
// ------------------------------------------------------------------ //
const saveDialogVisible = ref(false)
const savingReport = ref(false)
const saveForm = reactive({ title: '', aiAnalyze: true })

function openSaveDialog() {
  saveForm.title = ''
  saveForm.aiAnalyze = true
  saveDialogVisible.value = true
}

async function handleSaveReport() {
  savingReport.value = true
  try {
    await createPerfComparisonReport({
      title: saveForm.title || undefined,
      execution_ids: executions.value.map(e => e.id),
      reference_execution_id: executions.value[0]?.id || null,
      ai_analyze: saveForm.aiAnalyze
    })
    ElMessage.success(t('performanceTesting.comparisonReport.saveSuccess'))
    saveDialogVisible.value = false
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.comparisonReport.saveFailed')))
  } finally {
    savingReport.value = false
  }
}

// ------------------------------------------------------------------ //
// 跳转
// ------------------------------------------------------------------ //
function goReport(id) {
  router.push(`/performance-testing/executions/${id}`)
}

function goBack() {
  router.push('/performance-testing/executions')
}

watch(stepMetric, () => {
  // 表格靠计算函数取值，切换指标只需触发重渲染
  stepComparison.value = [...stepComparison.value]
})

onMounted(async () => {
  const raw = String(route.query.ids || '').trim()
  selectedIds.value = raw
    ? raw.split(',').map(s => parseInt(s, 10)).filter(n => !Number.isNaN(n)).slice(0, 5)
    : []
  await Promise.all([loadProjects(), loadScenarios(), loadCandidates()])
  // URL 带来的 id 可能不在候选列表（跨项目/非 COMPLETED），补进去避免多选框显示空白
  if (selectedIds.value.length) {
    await loadCompare()
    const known = new Set(candidates.value.map(c => c.id))
    executions.value.forEach(e => {
      if (!known.has(e.id)) {
        candidates.value.unshift({
          id: e.id,
          execution_no: e.execution_no,
          scenario_name: e.scenario_name,
          created_at: e.created_at
        })
      }
    })
  }
  window.addEventListener('resize', resizeChart)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chart?.dispose()
  chart = null
})
</script>

<style lang="scss" scoped>
.perf-comparison { padding: 16px; }

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
.f-item-lg { width: 380px; }
.hint { font-size: 12px; color: #909399; }
.opt-no { font-weight: 600; margin-right: 8px; }
.opt-sub { font-size: 12px; color: #909399; }

.exec-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.exec-card {
  :deep(.el-card__body) { padding: 12px 14px; }
  &.is-base { border-color: #faad14; }
  .exec-card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
  }
  .exec-card-name {
    margin-top: 4px;
    font-size: 13px;
    color: #303133;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .exec-card-tags { margin-top: 8px; display: flex; gap: 6px; }
  .exec-card-meta {
    margin-top: 6px;
    font-size: 12px;
    color: #909399;
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }
}

.block-card {
  margin-bottom: 12px;
  :deep(.el-card__header) { padding: 12px 14px; }
  :deep(.el-card__body) { padding: 14px; }
}
.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
  color: #303133;
  .block-tip { font-size: 12px; font-weight: 400; color: #909399; }
}
.col-head { display: flex; align-items: center; justify-content: center; gap: 6px; }
.cell-val { font-size: 13px; color: #303133; }
.cell-delta {
  margin-top: 2px;
  font-size: 12px;
  &.better { color: #52c41a; }
  &.worse { color: #f5222d; }
  &.flat { color: #909399; }
  &.neutral { color: #1890ff; }
}
.cmp-chart { width: 100%; height: 320px; }
.save-tip { margin-bottom: 14px; }
</style>
