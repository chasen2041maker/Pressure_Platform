<template>
  <div class="page-container defect-report">
    <div class="page-header">
      <h1 class="page-title">缺陷报表</h1>
      <div class="header-actions">
        <el-button @click="loadReport" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" @click="downloadReport">
          <el-icon><Download /></el-icon>
          导出报告
        </el-button>
      </div>
    </div>

    <el-row :gutter="16" class="metric-grid">
      <el-col :xs="24" :md="8" v-for="item in reportMetrics" :key="item.label">
        <el-card shadow="hover" class="report-metric" :class="item.className">
          <div class="metric-label">{{ item.label }}</div>
          <div class="metric-value">{{ item.value }}</div>
        </el-card>
      </el-col>
    </el-row>

    <div class="charts-container">
      <div class="chart-row">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span>状态分布</span>
              <span class="subtle-note">全量缺陷状态占比</span>
            </div>
          </template>
          <div ref="statusChartRef" class="chart-body"></div>
          <el-empty v-if="statusRows.length === 0" description="暂无数据" />
        </el-card>

        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span>严重级别与优先级</span>
              <span class="subtle-note">风险优先级矩阵</span>
            </div>
          </template>
          <div ref="severityPriorityChartRef" class="chart-body"></div>
          <el-empty v-if="severityRows.length === 0 && priorityRows.length === 0" description="暂无数据" />
        </el-card>
      </div>

      <div class="chart-row">
        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span>版本分布</span>
              <span class="subtle-note">观察迭代风险集中情况</span>
            </div>
          </template>
          <div ref="versionChartRef" class="chart-body"></div>
          <el-empty v-if="versionRows.length === 0" description="暂无版本数据" />
        </el-card>

        <el-card shadow="never" class="chart-card">
          <template #header>
            <div class="card-header">
              <span>模块与处理人</span>
              <span class="subtle-note">定位拥塞点</span>
            </div>
          </template>
          <div ref="assigneeModuleChartRef" class="chart-body"></div>
          <el-empty v-if="moduleRows.length === 0 && assigneeRows.length === 0" description="暂无数据" />
        </el-card>
      </div>

      <el-card shadow="never" class="chart-card trend-card">
        <template #header>
          <div class="card-header">
            <span>趋势分析</span>
            <span class="subtle-note">近 30 天新增与关闭趋势</span>
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
import { ElMessage } from 'element-plus'
import { Download, Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import dayjs from 'dayjs'
import { exportDefectReport, getDefectSummary, getDefectTrend } from '@/api/defects'
import {
  getOptionLabel,
  priorityOptions,
  severityOptions,
  statusOptions
} from './options'

const loading = ref(false)
const summary = ref({})
const trendRows = ref([])

const statusChartRef = ref(null)
const severityPriorityChartRef = ref(null)
const versionChartRef = ref(null)
const assigneeModuleChartRef = ref(null)
const trendChartRef = ref(null)

let statusChart = null
let severityPriorityChart = null
let versionChart = null
let assigneeModuleChart = null
let trendChart = null

const chartPalette = ['#2F6BFF', '#17B26A', '#FF9F43', '#F04438', '#7A5AF8', '#12B3A8', '#98A2B3', '#FDB022']

const numberOf = (value) => Number(value || 0)
const firstDefined = (...values) => values.find(value => value !== undefined && value !== null && value !== '')
const formatPercent = (value) => {
  const percent = firstDefined(value)
  if (percent === undefined) return '-'
  if (typeof percent === 'string' && percent.includes('%')) return percent
  return `${numberOf(percent)}%`
}

const reportMetrics = computed(() => [
  {
    label: '总缺陷数',
    value: numberOf(firstDefined(summary.value.total, summary.value.total_count)),
    className: 'metric-total'
  },
  {
    label: '缺陷关闭率',
    value: formatPercent(firstDefined(summary.value.close_rate, summary.value.closed_rate)),
    className: 'metric-close-rate'
  },
  {
    label: '高风险缺陷',
    value: numberOf(firstDefined(summary.value.severe, summary.value.critical, summary.value.critical_count)),
    className: 'metric-severe'
  }
])

const rowsFromDistribution = (data, options) => {
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

const statusRows = computed(() => rowsFromDistribution(
  summary.value.status_distribution || summary.value.by_status || summary.value.status_counts,
  statusOptions
))
const severityRows = computed(() => rowsFromDistribution(
  summary.value.severity_distribution || summary.value.by_severity || summary.value.severity_counts,
  severityOptions
))
const priorityRows = computed(() => rowsFromDistribution(
  summary.value.priority_distribution || summary.value.by_priority || summary.value.priority_counts,
  priorityOptions
))
const versionRows = computed(() => rowsFromDistribution(summary.value.version_distribution, null).slice(0, 8))
const moduleRows = computed(() => rowsFromDistribution(summary.value.module_distribution, null).slice(0, 8))
const assigneeRows = computed(() => rowsFromDistribution(summary.value.assignee_distribution, null).slice(0, 8))

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
  if (severityPriorityChartRef.value && !severityPriorityChart) severityPriorityChart = echarts.init(severityPriorityChartRef.value)
  if (versionChartRef.value && !versionChart) versionChart = echarts.init(versionChartRef.value)
  if (assigneeModuleChartRef.value && !assigneeModuleChart) assigneeModuleChart = echarts.init(assigneeModuleChartRef.value)
  if (trendChartRef.value && !trendChart) trendChart = echarts.init(trendChartRef.value)
  window.addEventListener('resize', handleResize)
}

const disposeCharts = () => {
  window.removeEventListener('resize', handleResize)
  statusChart?.dispose()
  severityPriorityChart?.dispose()
  versionChart?.dispose()
  assigneeModuleChart?.dispose()
  trendChart?.dispose()
  statusChart = null
  severityPriorityChart = null
  versionChart = null
  assigneeModuleChart = null
  trendChart = null
}

const handleResize = () => {
  statusChart?.resize()
  severityPriorityChart?.resize()
  versionChart?.resize()
  assigneeModuleChart?.resize()
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
        radius: ['38%', '68%'],
        center: ['50%', '44%'],
        itemStyle: { borderRadius: 10, borderColor: '#fff', borderWidth: 2 },
        label: { formatter: '{b}\n{c}' },
        data: statusRows.value.map(item => ({ value: item.value, name: item.label }))
      }]
    })
  }

  if (severityPriorityChart) {
    severityPriorityChart.setOption({
      color: ['#F04438', '#2F6BFF'],
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { top: 0, right: 0 },
      grid: { top: 40, left: 20, right: 20, bottom: 24, containLabel: true },
      xAxis: {
        type: 'category',
        data: Array.from(new Set([...severityRows.value.map(item => item.label), ...priorityRows.value.map(item => item.label)]))
      },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f2f4f7' } } },
      series: [
        {
          name: '严重级别',
          type: 'bar',
          barMaxWidth: 24,
          data: severityRows.value.map(item => item.value)
        },
        {
          name: '优先级',
          type: 'bar',
          barMaxWidth: 24,
          data: priorityRows.value.map(item => item.value)
        }
      ]
    })
  }

  if (versionChart) {
    versionChart.setOption({
      color: chartPalette,
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { top: 24, left: 20, right: 16, bottom: 20, containLabel: true },
      xAxis: {
        type: 'category',
        data: versionRows.value.map(item => item.label || '未设置'),
        axisLabel: { interval: 0, rotate: 18 }
      },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f2f4f7' } } },
      series: [{
        type: 'bar',
        barMaxWidth: 28,
        data: versionRows.value.map(item => item.value),
        itemStyle: { borderRadius: [8, 8, 0, 0] }
      }]
    })
  }

  if (assigneeModuleChart) {
    assigneeModuleChart.setOption({
      color: ['#12B3A8', '#FDB022'],
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { top: 0, right: 0 },
      grid: { top: 40, left: 20, right: 20, bottom: 24, containLabel: true },
      xAxis: { type: 'value', splitLine: { lineStyle: { color: '#f2f4f7' } } },
      yAxis: {
        type: 'category',
        data: moduleRows.value.map(item => item.label || '未设置'),
        axisTick: { show: false }
      },
      series: [
        {
          name: '模块',
          type: 'bar',
          barWidth: 16,
          data: moduleRows.value.map(item => item.value),
          itemStyle: { borderRadius: 999 }
        },
        {
          name: '处理人',
          type: 'bar',
          barWidth: 16,
          data: moduleRows.value.map((_, index) => assigneeRows.value[index]?.value || 0),
          itemStyle: { borderRadius: 999 }
        }
      ]
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

const loadReport = async () => {
  loading.value = true
  try {
    const [summaryRes, trendRes] = await Promise.all([
      getDefectSummary(),
      getDefectTrend({ days: 30 })
    ])
    summary.value = summaryRes.data || {}
    trendRows.value = normalizeTrendRows(trendRes.data)
    await nextTick()
    initCharts()
    renderCharts()
  } catch (error) {
    summary.value = {}
    trendRows.value = []
    ElMessage.warning('缺陷报表接口暂不可用，已显示空数据')
  } finally {
    loading.value = false
  }
}

const downloadReport = async () => {
  try {
    const response = await exportDefectReport()
    const url = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = url
    link.download = `defect-report-${dayjs().format('YYYYMMDDHHmmss')}.pdf`
    link.click()
    window.URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error('报告导出失败')
  }
}

onMounted(() => {
  loadReport()
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

.report-metric {
  margin-bottom: 16px;
  position: relative;
  overflow: hidden;

  &::before {
    content: '';
    position: absolute;
    inset: 0 auto 0 0;
    width: 4px;
    background: #2f6bff;
  }

  .metric-label {
    color: #667085;
    margin-bottom: 10px;
  }

  .metric-value {
    font-size: 26px;
    font-weight: 700;
    color: #1d2939;
  }
}

.metric-close-rate::before { background: #17b26a; }
.metric-severe::before { background: #f04438; }

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
