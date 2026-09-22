<template>
  <div class="monitor-dashboard">
    <!-- 头部：标题 + 自动刷新 + 刷新 -->
    <div class="dash-header">
      <div class="dash-title">
        <el-icon><Odometer /></el-icon>
        <span>{{ t('monitor.dashboard.title') }}</span>
      </div>
      <div class="dash-actions">
        <span class="last-updated" v-if="lastUpdated">{{ t('monitor.dashboard.lastUpdated') }}: {{ lastUpdated }}</span>
        <el-tag :type="schedTagType" effect="light" size="small" class="sched-badge">
          <span class="sched-dot" :class="'sched-dot-' + scheduler"></span>
          {{ t('monitor.targets.scheduler.label') }} · {{ t('monitor.targets.scheduler.' + scheduler) }}
        </el-tag>
        <el-switch v-model="autoRefresh" :active-text="t('monitor.dashboard.autoRefresh')" />
        <el-button type="primary" :icon="Refresh" :loading="loading" @click="loadAll">
          {{ t('monitor.dashboard.refresh') }}
        </el-button>
      </div>
    </div>

    <!-- 概览卡片 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="4" v-for="card in statCards" :key="card.key">
        <el-card shadow="hover" class="stat-card" :class="card.key">
          <div class="stat-inner">
            <div class="stat-icon">
              <el-icon><component :is="card.icon" /></el-icon>
            </div>
            <div class="stat-body">
              <div class="stat-val">{{ card.value }}</div>
              <div class="stat-label">{{ card.label }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 24h 可用率趋势 -->
    <el-card shadow="hover" class="chart-card">
      <template #header>{{ t('monitor.dashboard.trendTitle') }}</template>
      <div v-if="hasTrendData" ref="trendChartRef" class="trend-chart"></div>
      <el-empty v-else :description="t('monitor.dashboard.trendEmpty')" />
    </el-card>

    <!-- 按类型分布 + 最近异常 -->
    <el-row :gutter="16" class="mid-row">
      <el-col :span="10">
        <el-card shadow="hover" class="block-card">
          <template #header>{{ t('monitor.dashboard.byTypeTitle') }}</template>
          <div v-for="b in byType" :key="b.type" class="by-type-item">
            <span class="bt-name">{{ b.label }}</span>
            <span class="bt-counts">
              <span class="tag up">{{ b.UP }}</span>
              <span class="tag down">{{ b.DOWN }}</span>
              <span class="tag unknown">{{ b.UNKNOWN }}</span>
            </span>
          </div>
          <el-empty v-if="byType.length === 0" :description="t('monitor.dashboard.empty')" />
        </el-card>
      </el-col>
      <el-col :span="14">
        <el-card shadow="hover" class="block-card">
          <template #header>{{ t('monitor.dashboard.recentFailuresTitle') }}</template>
          <el-table :data="recentFailures" size="small" v-if="recentFailures.length">
            <el-table-column :label="t('monitor.dashboard.col.name')" prop="name" min-width="120" />
            <el-table-column :label="t('monitor.dashboard.col.type')" width="110">
              <template #default="{ row }">{{ typeLabel(row.type) }}</template>
            </el-table-column>
            <el-table-column :label="t('monitor.dashboard.col.lastCheck')" width="160">
              <template #default="{ row }">{{ formatTime(row.checked_at) }}</template>
            </el-table-column>
            <el-table-column :label="t('monitor.dashboard.col.actions')" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">{{ row.message }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-else :description="t('monitor.dashboard.noFailures')" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 目标状态表 -->
    <el-card shadow="hover" class="block-card">
      <template #header>{{ t('monitor.dashboard.targetsTitle') }}</template>
      <el-table :data="targets" size="small" v-loading="loadingTargets">
        <el-table-column :label="t('monitor.dashboard.col.name')" prop="name" min-width="160" />
        <el-table-column :label="t('monitor.dashboard.col.type')" width="130">
          <template #default="{ row }">{{ typeLabel(row.type) }}</template>
        </el-table-column>
        <el-table-column :label="t('monitor.dashboard.col.status')" width="110">
          <template #default="{ row }">
            <span class="status-dot" :class="statusClass(row.status)"></span>
            {{ statusLabel(row.status) }}
          </template>
        </el-table-column>
        <el-table-column :label="t('monitor.dashboard.col.lastCheck')" width="170">
          <template #default="{ row }">{{ row.last_check_at ? formatTime(row.last_check_at) : '-' }}</template>
        </el-table-column>
        <el-table-column :label="t('monitor.dashboard.col.latency')" width="100">
          <template #default="{ row }">{{ row.last_check_at && row.latency_ms != null ? row.latency_ms + ' ms' : '-' }}</template>
        </el-table-column>
        <el-table-column :label="t('monitor.dashboard.col.actions')" width="120" fixed="right">
          <template #default="{ row }">
            <el-button size="small" :loading="row._checking" @click="onCheck(row)">
              {{ t('monitor.dashboard.checkNow') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import * as echarts from 'echarts'
import {
  Odometer, Refresh, CircleCheck, CircleClose, Warning, DataAnalysis, Bell
} from '@element-plus/icons-vue'
import { getDashboard, getTargets, checkTargetNow, getSchedulerStatus } from '@/api/monitor'

const { t } = useI18n()

const loading = ref(false)
const loadingTargets = ref(false)
const dashboard = ref(null)
const targets = ref([])
const autoRefresh = ref(true)
// 调度器在线状态（后端 Redis 心跳判断：online/offline/unknown）
const scheduler = ref('unknown')
const schedTagType = computed(() =>
  scheduler.value === 'online' ? 'success' : scheduler.value === 'offline' ? 'info' : 'warning'
)
function refreshScheduler() {
  getSchedulerStatus().then(r => { scheduler.value = r.data.status }).catch(() => {})
}
const lastUpdated = ref('')

const summary = computed(() => dashboard.value?.summary || { total: 0, up: 0, down: 0, unknown: 0, disabled: 0, availability: null, active_alerts: 0 })
const byType = computed(() => dashboard.value?.by_type || [])
const recentFailures = computed(() => dashboard.value?.recent_failures || [])
const trend = computed(() => dashboard.value?.trend || [])
const hasTrendData = computed(() => trend.value.some(p => p.availability !== null))
const availabilityText = computed(() => {
  const a = summary.value.availability
  return a === null ? '-' : a + '%'
})

// 概览统计卡片：异常(down)明确红色，未知灰，可用率/告警各自强调色
const statCards = computed(() => [
  { key: 'total', icon: Odometer, label: t('monitor.dashboard.summary.total'), value: summary.value.total },
  { key: 'up', icon: CircleCheck, label: t('monitor.dashboard.summary.up'), value: summary.value.up },
  { key: 'down', icon: CircleClose, label: t('monitor.dashboard.summary.down'), value: summary.value.down },
  { key: 'unknown', icon: Warning, label: t('monitor.dashboard.summary.unknown'), value: summary.value.unknown },
  { key: 'avail', icon: DataAnalysis, label: t('monitor.dashboard.summary.availability'), value: availabilityText.value },
  { key: 'alerts', icon: Bell, label: t('monitor.dashboard.summary.alerts'), value: summary.value.active_alerts },
])

const trendChartRef = ref(null)
let trendChart = null

function typeLabel(type) { return t('monitor.dashboard.type.' + type) }
function statusLabel(s) { return t('monitor.dashboard.status.' + s) }
function statusClass(s) { return s === 'UP' ? 'up' : s === 'DOWN' ? 'down' : 'unknown' }
function formatTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

async function loadDashboard() {
  loading.value = true
  try {
    const res = await getDashboard()
    dashboard.value = res.data
    lastUpdated.value = formatTime(new Date().toISOString())
    await nextTick()
    renderChart()
  } catch (e) {
    ElMessage.error('加载看板失败')
  } finally {
    loading.value = false
  }
}

async function loadTargets() {
  loadingTargets.value = true
  try {
    const res = await getTargets({ page_size: 200 })
    targets.value = res.data.results || res.data || []
  } catch (e) {
    /* 忽略，目标列表为空不影响看板 */
  } finally {
    loadingTargets.value = false
  }
}

async function loadAll() {
  await Promise.all([loadDashboard(), loadTargets()])
  refreshScheduler()
}

function renderChart() {
  if (!trendChartRef.value) return
  if (!trendChart) trendChart = echarts.init(trendChartRef.value)
  // ts 为带时区偏移的 ISO 字符串，须经 Date 解析后取本地小时；直接字符串切片会截到原始时区的小时
  const labels = trend.value.map(p => {
    if (!p.ts) return ''
    const d = new Date(p.ts)
    return `${String(d.getHours()).padStart(2, '0')}:00`
  })
  const data = trend.value.map(p => p.availability)
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', top: '12%', containLabel: true },
    xAxis: { type: 'category', boundaryGap: false, data: labels, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { formatter: '{value}%' } },
    series: [{
      name: t('monitor.dashboard.summary.availability'),
      type: 'line', smooth: true, connectNulls: false, data,
      itemStyle: { color: '#1890ff' },
      areaStyle: {
        opacity: 0.25,
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#1890ff' },
          { offset: 1, color: '#fff' }
        ])
      }
    }]
  })
}

function handleResize() { if (trendChart) trendChart.resize() }

async function onCheck(row) {
  row._checking = true
  try {
    await checkTargetNow(row.id)
    ElMessage.success(`已触发 ${row.name} 检测`)
    await loadAll()
  } catch (e) {
    ElMessage.error('检测触发失败')
  } finally {
    row._checking = false
  }
}

let timer = null
function startTimer() {
  stopTimer()
  timer = setInterval(() => { if (autoRefresh.value) loadAll() }, 30000)
}
function stopTimer() { if (timer) { clearInterval(timer); timer = null } }

watch(autoRefresh, (v) => { if (v) startTimer(); else stopTimer() })

onMounted(() => { loadAll(); startTimer(); window.addEventListener('resize', handleResize) })
onUnmounted(() => {
  stopTimer()
  window.removeEventListener('resize', handleResize)
  if (trendChart) { trendChart.dispose(); trendChart = null }
})
</script>

<style lang="scss" scoped>
.monitor-dashboard {
  padding: 16px;
}
.dash-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.dash-title {
  display: flex;
  align-items: center;
  font-size: 20px;
  font-weight: 600;
  color: #1f2d3d;
  .el-icon { margin-right: 8px; color: #1890ff; font-size: 24px; }
}
.dash-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.last-updated {
  color: #8c8c8c;
  font-size: 13px;
}
.sched-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.sched-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #bbb;
}
.sched-dot-online { background: #52c41a; }
.sched-dot-offline { background: #bfbfbf; }
.sched-dot-unknown { background: #faad14; }
.stat-row { margin-bottom: 16px; }
.stat-card {
  border: none;
  border-radius: 10px;
  position: relative;
  overflow: hidden;
  &::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: var(--accent, #1890ff);
  }
  .stat-inner {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .stat-icon {
    flex: 0 0 auto;
    width: 46px; height: 46px;
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px;
    color: var(--accent, #1890ff);
    background: var(--accent-bg, #e6f4ff);
  }
  .stat-body { min-width: 0; }
  .stat-val { font-size: 26px; font-weight: 700; line-height: 1.15; color: var(--accent, #1890ff); }
  .stat-label { font-size: 13px; color: #8c8c8c; margin-top: 2px; }
  &.total { --accent: #1890ff; --accent-bg: #e6f4ff; }
  &.up { --accent: #52c41a; --accent-bg: #f6ffed; }
  &.down { --accent: #ff4d4f; --accent-bg: #fff1f0; }
  &.unknown { --accent: #8c8c8c; --accent-bg: #fafafa; }
  &.avail { --accent: #1890ff; --accent-bg: #e6f4ff; }
  &.alerts { --accent: #fa8c16; --accent-bg: #fff7e6; }
}
.chart-card { margin-bottom: 16px; }
.trend-chart { height: 300px; width: 100%; }
.mid-row { margin-bottom: 16px; }
.block-card { margin-bottom: 16px; }
.by-type-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 4px;
  border-bottom: 1px solid #f0f0f0;
  .bt-name { font-weight: 500; }
  .bt-counts { display: flex; gap: 8px; }
  .tag {
    display: inline-block;
    min-width: 28px;
    text-align: center;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 12px;
    color: #fff;
    &.up { background: #52c41a; }
    &.down { background: #ff4d4f; }
    &.unknown { background: #999; }
  }
}
.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
  &.up { background: #52c41a; }
  &.down { background: #ff4d4f; }
  &.unknown { background: #999; }
}
</style>
