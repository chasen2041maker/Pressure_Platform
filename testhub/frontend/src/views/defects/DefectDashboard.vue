<template>
  <div class="page-container defect-dashboard">
    <div class="page-header">
      <h1 class="page-title">缺陷看板</h1>
      <div class="header-actions">
        <el-button @click="loadDashboard" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" @click="router.push('/defects/create')">
          <el-icon><Plus /></el-icon>
          新建缺陷
        </el-button>
      </div>
    </div>

    <el-row :gutter="16" class="metric-grid">
      <el-col v-for="metric in metrics" :key="metric.key" :xs="12" :sm="12" :md="8" :lg="4">
        <el-card shadow="hover" class="metric-card" :class="`metric-${metric.key}`">
          <div class="metric-value">{{ metric.value }}</div>
          <div class="metric-label">{{ metric.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <div class="charts-container">
      <div class="chart-row">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span>状态分布</span>
              <el-button text type="primary" @click="router.push('/defects/list')">查看列表</el-button>
            </div>
          </template>
          <div ref="statusChartRef" class="chart-body"></div>
          <el-empty v-if="statusDistribution.length === 0" description="暂无状态数据" />
        </el-card>

        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span>严重级别分布</span>
              <span class="subtle-note">高风险优先跟进</span>
            </div>
          </template>
          <div ref="severityChartRef" class="chart-body"></div>
          <el-empty v-if="severityDistribution.length === 0" description="暂无严重级别数据" />
        </el-card>
      </div>

      <div class="chart-row">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span>版本与模块分布</span>
              <span class="subtle-note">定位集中风险区域</span>
            </div>
          </template>
          <div ref="moduleChartRef" class="chart-body"></div>
          <el-empty v-if="moduleDistribution.length === 0" description="暂无模块数据" />
        </el-card>

        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span>处理人负载</span>
              <span class="subtle-note">按当前处理人统计</span>
            </div>
          </template>
          <div ref="assigneeChartRef" class="chart-body"></div>
          <el-empty v-if="assigneeDistribution.length === 0" description="暂无处理人数据" />
        </el-card>
      </div>

      <el-card shadow="never" class="chart-card trend-card">
        <template #header>
          <div class="card-header">
            <span>缺陷趋势</span>
            <el-button text type="primary" @click="router.push('/defects/reports')">查看报表</el-button>
          </div>
        </template>
        <div ref="trendChartRef" class="chart-body trend-body"></div>
        <el-empty v-if="trendRows.length === 0" description="暂无趋势数据" />
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { getDefectSummary, getDefectTrend } from '@/api/defects'
import {
  getOptionLabel,
  severityOptions,
  statusOptions
} from './options'

const router = useRouter()
const loading = ref(false)
const summary = ref({})
const trendRows = ref([])

const statusChartRef = ref(null)
const severityChartRef = ref(null)
const moduleChartRef = ref(null)
const assigneeChartRef = ref(null)
const trendChartRef = ref(null)

let statusChart = null
let severityChart = null
let moduleChart = null
let assigneeChart = null
let trendChart = null

const chartPalette = ['#2F6BFF', '#17B26A', '#FF9F43', '#F04438', '#7A5AF8', '#12B3A8', '#8E6CFF', '#FDB022']

const numberOf = (value) => Number(value || 0)
const firstDefined = (...values) => values.find(value => value !== undefined && value !== null && value !== '')
const summaryNumber = (...keys) => numberOf(firstDefined(...keys.map(key => summary.value[key])))
const totalCount = computed(() => summaryNumber('total', 'total_count'))
const closedCount = computed(() => summaryNumber('closed', 'closed_count'))
const openCount = computed(() => {
  const explicitOpen = firstDefined(summary.value.open, summary.value.open_count)
  return explicitOpen !== undefined ? numberOf(explicitOpen) : Math.max(totalCount.value - closedCount.value, 0)
})

const metrics = computed(() => [
  { key: 'total', label: '总缺陷', value: totalCount.value },
  { key: 'open', label: '待处理', value: openCount.value },
  { key: 'resolved', label: '待回归', value: summaryNumber('resolved', 'resolved_count') },
  { key: 'closed', label: '已关闭', value: closedCount.value },
  { key: 'severe', label: '高风险', value: summaryNumber('severe', 'critical', 'critical_count') },
  { key: 'overdue', label: '逾期', value: summaryNumber('overdue', 'overdue_count') }
])

const normalizeDistribution = (data, options) => {
  if (!data) return []
  const rows = Array.isArray(data)
    ? data
    : Object.entries(data).map(([key, value]) => ({ key, value }))

  return rows
    .map(item => {
      const key = item.key ?? item.status ?? item.severity ?? item.priority ?? item.version__name ?? item.module ?? item.assignee__username ?? item.name
      const rawLabel = item.version__name ?? item.module ?? item.assignee__username ?? item.name ?? key
      return {
        key,
        label: options ? getOptionLabel(options, key) : (rawLabel || '未设置'),
        value: numberOf(item.count ?? item.value)
      }
    })
    .filter(item => item.label && item.value > 0)
}

const statusDistribution = computed(() => normalizeDistribution(
  summary.value.status_distribution || summary.value.by_status || summary.value.status || summary.value.status_counts,
  statusOptions
))

const severityDistribution = computed(() => normalizeDistribution(
  summary.value.severity_distribution || summary.value.by_severity || summary.value.severity || summary.value.severity_counts,
  severityOptions
))

const moduleDistribution = computed(() => normalizeDistribution(summary.value.module_distribution, null).slice(0, 8))
const assigneeDistribution = computed(() => normalizeDistribution(summary.value.assignee_distribution, null).slice(0, 8))

const normalizeTrendRows = (data) => {
  const rows = Array.isArray(data) ? data : (data?.results || data?.items || [])
  return rows.map(item => ({
    date: item.date || item.day || item.created_date || '-',
    created: numberOf(item.created || item.new || item.created_count),
    closed: numberOf(item.closed || item.closed_count)
  }))
}

const initCharts = () => {
  if (statusChartRef.value && !statusChart) statusChart = echarts.init(statusChartRef.value)
  if (severityChartRef.value && !severityChart) severityChart = echarts.init(severityChartRef.value)
  if (moduleChartRef.value && !moduleChart) moduleChart = echarts.init(moduleChartRef.value)
  if (assigneeChartRef.value && !assigneeChart) assigneeChart = echarts.init(assigneeChartRef.value)
  if (trendChartRef.value && !trendChart) trendChart = echarts.init(trendChartRef.value)
  window.addEventListener('resize', handleResize)
}

const disposeCharts = () => {
  window.removeEventListener('resize', handleResize)
  statusChart?.dispose()
  severityChart?.dispose()
  moduleChart?.dispose()
  assigneeChart?.dispose()
  trendChart?.dispose()
  statusChart = null
  severityChart = null
  moduleChart = null
  assigneeChart = null
  trendChart = null
}

const handleResize = () => {
  statusChart?.resize()
  severityChart?.resize()
  moduleChart?.resize()
  assigneeChart?.resize()
  trendChart?.resize()
}

const renderCharts = () => {
  if (statusChart) {
    statusChart.setOption({
      color: chartPalette,
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, left: 'center' },
      series: [{
        type: 'pie',
        radius: ['45%', '72%'],
        center: ['50%', '44%'],
        itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
        label: { formatter: '{b}\n{c}' },
        data: statusDistribution.value.map(item => ({ value: item.value, name: item.label }))
      }]
    })
  }

  if (severityChart) {
    severityChart.setOption({
      color: ['#F04438', '#FF6B57', '#FF9F43', '#FDB022', '#17B26A'],
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { top: 20, left: 20, right: 20, bottom: 20, containLabel: true },
      xAxis: { type: 'value', splitLine: { lineStyle: { color: '#f2f4f7' } } },
      yAxis: {
        type: 'category',
        data: severityDistribution.value.map(item => item.label),
        axisTick: { show: false }
      },
      series: [{
        type: 'bar',
        barWidth: 18,
        data: severityDistribution.value.map(item => item.value),
        showBackground: true,
        backgroundStyle: { color: '#f2f4f7', borderRadius: 999 }
      }]
    })
  }

  if (moduleChart) {
    moduleChart.setOption({
      color: chartPalette,
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { top: 28, left: 20, right: 16, bottom: 20, containLabel: true },
      xAxis: {
        type: 'category',
        data: moduleDistribution.value.map(item => item.label || '未设置'),
        axisLabel: { interval: 0, rotate: 20 }
      },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f2f4f7' } } },
      series: [{
        type: 'bar',
        barMaxWidth: 28,
        data: moduleDistribution.value.map(item => item.value),
        itemStyle: { borderRadius: [8, 8, 0, 0] }
      }]
    })
  }

  if (assigneeChart) {
    assigneeChart.setOption({
      color: ['#12B3A8'],
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { top: 20, left: 24, right: 20, bottom: 20, containLabel: true },
      xAxis: { type: 'value', splitLine: { lineStyle: { color: '#f2f4f7' } } },
      yAxis: {
        type: 'category',
        data: assigneeDistribution.value.map(item => item.label || '未指派'),
        axisTick: { show: false }
      },
      series: [{
        type: 'bar',
        barWidth: 18,
        data: assigneeDistribution.value.map(item => item.value),
        itemStyle: { borderRadius: 999 }
      }]
    })
  }

  if (trendChart) {
    trendChart.setOption({
      color: ['#2F6BFF', '#17B26A'],
      tooltip: { trigger: 'axis' },
      legend: { top: 0, right: 0 },
      grid: { top: 40, left: 20, right: 20, bottom: 24, containLabel: true },
      xAxis: {
        type: 'category',
        data: trendRows.value.map(item => item.date),
        boundaryGap: false
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#f2f4f7' } }
      },
      series: [
        {
          name: '新增',
          type: 'line',
          smooth: true,
          symbolSize: 8,
          areaStyle: { opacity: 0.12 },
          data: trendRows.value.map(item => item.created)
        },
        {
          name: '关闭',
          type: 'line',
          smooth: true,
          symbolSize: 8,
          areaStyle: { opacity: 0.08 },
          data: trendRows.value.map(item => item.closed)
        }
      ]
    })
  }
}

const loadDashboard = async () => {
  loading.value = true
  try {
    const [summaryRes, trendRes] = await Promise.all([
      getDefectSummary(),
      getDefectTrend({ days: 14 })
    ])
    summary.value = summaryRes.data || {}
    trendRows.value = normalizeTrendRows(trendRes.data)
    await nextTick()
    initCharts()
    renderCharts()
  } catch (error) {
    ElMessage.warning('缺陷看板接口暂不可用，已显示空数据')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadDashboard()
})

onBeforeUnmount(() => {
  disposeCharts()
})
</script>

<style lang="scss" scoped>
.header-actions {
  display: flex;
  gap: 10px;
}

.metric-grid {
  margin-bottom: 16px;
}

.metric-card {
  margin-bottom: 16px;
  overflow: hidden;
  position: relative;

  &::before {
    content: '';
    position: absolute;
    inset: 0 auto 0 0;
    width: 4px;
    background: #2f6bff;
  }

  .metric-value {
    font-size: 28px;
    font-weight: 700;
    color: #1d2939;
    line-height: 1.1;
  }

  .metric-label {
    margin-top: 8px;
    color: #667085;
  }
}

.metric-open::before { background: #f79009; }
.metric-resolved::before { background: #7a5af8; }
.metric-closed::before { background: #17b26a; }
.metric-severe::before { background: #f04438; }
.metric-overdue::before { background: #12b3a8; }

.charts-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.chart-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.chart-card {
  min-height: 360px;
}

.trend-card {
  min-height: 420px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.subtle-note {
  color: #98a2b3;
  font-size: 12px;
}

.chart-body {
  width: 100%;
  height: 280px;
}

.trend-body {
  height: 320px;
}

@media (max-width: 991px) {
  .chart-row {
    grid-template-columns: 1fr;
  }
}
</style>
