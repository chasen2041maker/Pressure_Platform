<template>
  <div class="judge-dashboard">
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="card-header">
          <div>
            <h2 class="page-title">{{ $t('llmJudge.title') }}</h2>
            <p class="page-desc">{{ $t('llmJudge.subtitle') }}</p>
          </div>
          <el-radio-group v-model="days" size="small" @change="loadData">
            <el-radio-button :value="7">{{ $t('llmJudge.dashboard.last7Days') }}</el-radio-button>
            <el-radio-button :value="30">{{ $t('llmJudge.dashboard.last30Days') }}</el-radio-button>
          </el-radio-group>
        </div>
      </template>

      <!-- 统计卡片 -->
      <div class="stat-grid">
        <div class="stat-item">
          <div class="stat-icon total"><el-icon><DataLine /></el-icon></div>
          <div class="stat-body">
            <div class="stat-label">{{ $t('llmJudge.dashboard.totalRecords') }}</div>
            <div class="stat-value">{{ stats.total || 0 }}</div>
          </div>
        </div>
        <div class="stat-item">
          <div class="stat-icon veto"><el-icon><CircleClose /></el-icon></div>
          <div class="stat-body">
            <div class="stat-label">{{ $t('llmJudge.dashboard.vetoedCount') }}</div>
            <div class="stat-value">{{ stats.vetoed_count || 0 }}</div>
          </div>
        </div>
        <div class="stat-item">
          <div class="stat-icon pass"><el-icon><CircleCheck /></el-icon></div>
          <div class="stat-body">
            <div class="stat-label">{{ $t('llmJudge.dashboard.passRate') }}</div>
            <div class="stat-value">{{ fmtPct(stats.pass_rate) }}</div>
          </div>
        </div>
        <div class="stat-item">
          <div class="stat-icon mean"><el-icon><TrendCharts /></el-icon></div>
          <div class="stat-body">
            <div class="stat-label">{{ $t('llmJudge.dashboard.meanScore') }}</div>
            <div class="stat-value">{{ fmtNum(stats.mean_score) }}</div>
          </div>
        </div>
        <div class="stat-item">
          <div class="stat-icon latency"><el-icon><Timer /></el-icon></div>
          <div class="stat-body">
            <div class="stat-label">{{ $t('llmJudge.dashboard.avgLatency') }}</div>
            <div class="stat-value">{{ Math.round(stats.avg_latency_ms || 0) }}ms</div>
          </div>
        </div>
        <div class="stat-item">
          <div class="stat-icon cache"><el-icon><Coin /></el-icon></div>
          <div class="stat-body">
            <div class="stat-label">{{ $t('llmJudge.dashboard.cacheHitRate') }}</div>
            <div class="stat-value">{{ fmtPct(stats.cache_hit_rate) }}</div>
          </div>
        </div>
      </div>

      <div v-if="!stats.total" class="empty-state">
        <el-empty :description="$t('llmJudge.dashboard.noData')" />
      </div>

      <template v-else>
        <!-- 图表行 -->
        <el-row :gutter="16" class="chart-row">
          <el-col :xs="24" :sm="12">
            <div class="chart-block">
              <h4 class="block-title">{{ $t('llmJudge.dashboard.zoneDistribution') }}</h4>
              <div class="bar-list">
                <div v-for="z in zoneBars" :key="z.key" class="bar-item">
                  <span class="bar-label">{{ z.label }}</span>
                  <div class="bar-track">
                    <div :class="['bar-fill', z.key]" :style="{ width: z.pct + '%' }"></div>
                  </div>
                  <span class="bar-count">{{ z.count }}</span>
                </div>
              </div>
            </div>
          </el-col>
          <el-col :xs="24" :sm="12">
            <div class="chart-block">
              <h4 class="block-title">{{ $t('llmJudge.dashboard.labelDistribution') }}</h4>
              <div class="bar-list">
                <div v-for="l in labelBars" :key="l.key" class="bar-item">
                  <span class="bar-label">{{ l.label }}</span>
                  <div class="bar-track">
                    <div :class="['bar-fill', l.key]" :style="{ width: l.pct + '%' }"></div>
                  </div>
                  <span class="bar-count">{{ l.count }}</span>
                </div>
              </div>
            </div>
          </el-col>
        </el-row>

        <el-row :gutter="16" class="chart-row">
          <el-col :xs="24" :sm="12">
            <div class="chart-block">
              <h4 class="block-title">{{ $t('llmJudge.dashboard.vetoTop5') }}</h4>
              <div v-if="vetoTop5.length" class="bar-list">
                <div v-for="(v, i) in vetoTop5" :key="i" class="bar-item">
                  <span class="bar-label" :title="v.rule">{{ v.rule }}</span>
                  <div class="bar-track">
                    <div class="bar-fill veto" :style="{ width: vetoPct(v.count) + '%' }"></div>
                  </div>
                  <span class="bar-count">{{ v.count }}{{ $t('llmJudge.dashboard.count') }}</span>
                </div>
              </div>
              <div v-else class="no-data-tip">{{ $t('llmJudge.single.noFindings') }}</div>
            </div>
          </el-col>
          <el-col :xs="24" :sm="12">
            <div class="chart-block">
              <h4 class="block-title">{{ $t('llmJudge.dashboard.dailyTrend') }}</h4>
              <div class="trend-chart">
                <div v-for="(d, i) in trendBars" :key="i" class="trend-col">
                  <div class="trend-bar-wrap">
                    <div class="trend-bar" :style="{ height: d.height + '%' }"
                      :title="`${d.day}: ${fmtNum(d.mean_score)}`"></div>
                  </div>
                  <span class="trend-day">{{ d.shortDay }}</span>
                </div>
              </div>
            </div>
          </el-col>
        </el-row>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { DataLine, CircleClose, CircleCheck, TrendCharts, Timer, Coin } from '@element-plus/icons-vue'
import { getJudgeDashboardStats } from '@/api/llm-judge'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const days = ref(7)
const stats = ref({})

const loadData = async () => {
  try {
    const res = await getJudgeDashboardStats({ days: days.value })
    stats.value = res.data
  } catch (e) { /* ignore */ }
}

const zoneBars = computed(() => {
  const z = stats.value.zone_distribution || {}
  const total = (z.green || 0) + (z.yellow || 0) + (z.red || 0) || 1
  return [
    { key: 'green', label: t('llmJudge.gate.green'), count: z.green || 0, pct: ((z.green || 0) / total) * 100 },
    { key: 'yellow', label: t('llmJudge.gate.yellow'), count: z.yellow || 0, pct: ((z.yellow || 0) / total) * 100 },
    { key: 'red', label: t('llmJudge.gate.red'), count: z.red || 0, pct: ((z.red || 0) / total) * 100 }
  ]
})

const labelBars = computed(() => {
  const l = stats.value.label_distribution || {}
  const total = Object.values(l).reduce((a, b) => a + b, 0) || 1
  return ['excellent', 'acceptable', 'needs_improvement', 'critical_failure'].map(k => ({
    key: k, label: t(`llmJudge.labels.${k}`), count: l[k] || 0, pct: ((l[k] || 0) / total) * 100
  }))
})

const vetoTop5 = computed(() => stats.value.veto_top5 || [])
const vetoPct = (count) => {
  const max = vetoTop5.value[0]?.count || 1
  return (count / max) * 100
}

const trendBars = computed(() => {
  const daily = stats.value.daily_trend || []
  const max = Math.max(...daily.map(d => d.mean_score || 0), 100) || 100
  return daily.map(d => ({
    day: d.day,
    shortDay: String(d.day).slice(5),
    mean_score: d.mean_score,
    height: ((d.mean_score || 0) / max) * 100
  }))
})

const fmtNum = (v) => (v === null || v === undefined) ? '—' : Number(v).toFixed(1)
const fmtPct = (v) => (v === null || v === undefined) ? '—' : (Number(v) * 100).toFixed(1) + '%'

onMounted(() => { loadData() })
</script>

<style scoped lang="scss">
.judge-dashboard {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-card {
  border-radius: 8px;
  :deep(.el-card__header) { padding: 16px 20px; }
  :deep(.el-card__body) { padding: 20px; }
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.page-title { margin: 0; font-size: 18px; color: #303133; }
.page-desc { margin: 4px 0 0; font-size: 13px; color: #909399; }
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
.stat-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px;
  background: #f5f7fa;
  border-radius: 8px;
  transition: transform 0.2s;
  &:hover { transform: translateY(-2px); }
}
.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  color: #fff;
  flex-shrink: 0;
  &.total { background: #409eff; }
  &.veto { background: #f56c6c; }
  &.pass { background: #67c23a; }
  &.mean { background: #e6a23c; }
  &.latency { background: #909399; }
  &.cache { background: #722ed1; }
}
.stat-body {
  .stat-label { font-size: 12px; color: #909399; margin-bottom: 4px; }
  .stat-value { font-size: 24px; font-weight: 700; color: #303133; }
}
.empty-state { padding: 40px 0; }
.chart-row { margin-bottom: 8px; }
.chart-block {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 16px;
  height: 100%;
}
.block-title {
  font-size: 14px;
  color: #303133;
  margin: 0 0 14px;
  padding-left: 8px;
  border-left: 3px solid #409eff;
}
.bar-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.bar-item {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.bar-label {
  width: 90px;
  text-align: right;
  color: #606266;
  flex-shrink: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.bar-track {
  flex: 1;
  height: 16px;
  background: #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 8px;
  transition: width 0.4s ease;
  &.green { background: #67c23a; }
  &.yellow { background: #e6a23c; }
  &.red { background: #f56c6c; }
  &.excellent { background: #67c23a; }
  &.acceptable { background: #409eff; }
  &.needs_improvement { background: #e6a23c; }
  &.critical_failure { background: #f56c6c; }
  &.veto { background: #f56c6c; }
}
.bar-count {
  width: 40px;
  color: #303133;
  font-weight: 600;
  flex-shrink: 0;
}
.no-data-tip {
  color: #909399;
  font-size: 13px;
}
.trend-chart {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 140px;
  padding: 0 4px;
}
.trend-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
  min-width: 0;
}
.trend-bar-wrap {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.trend-bar {
  width: 70%;
  max-width: 24px;
  background: linear-gradient(180deg, #409eff, #79bbff);
  border-radius: 4px 4px 0 0;
  min-height: 4px;
  transition: height 0.4s ease;
}
.trend-day {
  font-size: 10px;
  color: #909399;
  margin-top: 4px;
  white-space: nowrap;
}
</style>
