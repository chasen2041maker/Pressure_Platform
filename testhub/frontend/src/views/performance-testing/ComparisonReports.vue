<template>
  <div class="perf-comparison-reports">
    <el-alert title="k6 峰值按 UTC 固定1秒完成桶计算，非滚动峰值；旧版或时间戳不完整的峰值与窗口率未验证，显示未采集，不计算增幅。平均 RPS 保留整场口径。" type="info" :closable="false" />
    <div class="page-head">
      <div>
        <h2 class="page-title">{{ t('performanceTesting.comparisonReport.title') }}</h2>
        <p class="page-sub">{{ t('performanceTesting.comparisonReport.subtitle') }}</p>
      </div>
      <div class="head-actions">
        <el-select
          v-model="filterProject"
          clearable
          filterable
          class="f-item"
          :placeholder="t('performanceTesting.common.allProjects')"
          @change="loadReports"
        >
          <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" @click="loadReports">
          {{ t('performanceTesting.common.refresh') }}
        </el-button>
      </div>
    </div>

    <el-card shadow="never">
      <el-table :data="reports" v-loading="loading" stripe>
        <el-table-column prop="title" :label="t('performanceTesting.comparisonReport.reportTitle')"
                         min-width="240" show-overflow-tooltip />
        <el-table-column :label="t('performanceTesting.comparisonReport.executionsCol')" min-width="180">
          <template #default="{ row }">
            <el-tag v-for="id in row.execution_ids" :key="id" size="small" class="exec-tag"
                    :type="id === row.reference_execution_id ? 'warning' : 'info'" effect="plain">
              #{{ id }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.comparisonReport.aiAnalysis')" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.has_ai_analysis" size="small" type="success">
              {{ t('performanceTesting.comparisonReport.viewDetail') }}
            </el-tag>
            <el-tag v-else size="small" type="info">
              {{ t('performanceTesting.comparisonReport.aiNone') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" :label="t('performanceTesting.comparisonReport.createdBy')"
                         width="120" />
        <el-table-column :label="t('performanceTesting.comparisonReport.createdAt')" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :label="t('performanceTesting.common.actions')" width="150" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewReport(row)">
              {{ t('performanceTesting.comparisonReport.viewDetail') }}
            </el-button>
            <el-button link type="danger" @click="removeReport(row)">
              {{ t('performanceTesting.common.delete') }}
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="t('performanceTesting.comparisonReport.empty')" :image-size="80" />
        </template>
      </el-table>
    </el-card>

    <!-- 详情对话框：指标矩阵 + AI 分析 -->
    <el-dialog v-model="detailVisible" :title="detail?.title || t('performanceTesting.comparisonReport.detailTitle')"
               width="920px" top="5vh" destroy-on-close>
      <div v-if="detail" v-loading="detailLoading">
        <h4 class="section-title">{{ t('performanceTesting.comparisonReport.metricMatrix') }}</h4>
        <el-table :data="matrixRows" size="small" border stripe>
          <el-table-column prop="label" :label="t('performanceTesting.comparison.metricName')"
                           width="150" fixed />
          <el-table-column
            v-for="e in snapshotExecutions"
            :key="e.id"
            :label="e.execution_no"
            min-width="150"
            align="center"
          >
            <template #header>
              <div class="col-head">
                <span>{{ e.execution_no }}</span>
                <el-tag v-if="e.is_reference" size="small" type="warning" effect="plain">
                  {{ t('performanceTesting.comparisonReport.baseline') }}
                </el-tag>
              </div>
            </template>
            <template #default="{ row }">
              <div class="cell-val">{{ formatMetric(row.key, e.summary?.[row.key]) }}</div>
              <div v-if="!e.is_reference && row.key in (e.delta_pct || {})" class="cell-delta">
                {{ deltaText(e.delta_pct[row.key]) }}
              </div>
            </template>
          </el-table-column>
        </el-table>

        <h4>接口对比</h4>
        <p>{{ detail.snapshot?.alignment_notice || '历史报告：请核对接口对齐依据' }}</p>
        <el-table :data="detail.snapshot?.step_comparison || []" border size="small">
          <el-table-column prop="step_name" label="接口" min-width="150" />
          <el-table-column prop="scenario_id" label="场景 ID" width="90" />
          <el-table-column prop="step_id" label="步骤 ID" width="90" />
          <el-table-column prop="match_basis" label="对齐依据" min-width="180" />
          <el-table-column v-for="(run, index) in snapshotExecutions" :key="run.id" :label="run.execution_no" min-width="200">
            <template #default="{ row }">RPS/TPS {{ formatMetric('tps', row.values[index]?.tps) }} · P95 {{ formatMetric('p95_rt', row.values[index]?.p95_rt) }} · 失败率 {{ formatMetric('error_rate', row.values[index]?.error_rate) }}</template>
          </el-table-column>
        </el-table>
        <h4 class="section-title">{{ t('performanceTesting.comparisonReport.aiAnalysis') }}</h4>
        <div v-if="detail.ai_analysis" class="ai-content markdown-body"
             v-html="renderMarkdown(detail.ai_analysis)"></div>
        <el-empty v-else :description="t('performanceTesting.comparisonReport.aiNone')" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { marked } from 'marked'
import {
  getPerfProjects,
  getPerfComparisonReports,
  getPerfComparisonReport,
  deletePerfComparisonReport
} from '@/api/performance-testing'
import { formatTime, apiError } from './shared'

const { t } = useI18n()

const loading = ref(false)
const projects = ref([])
const reports = ref([])
const filterProject = ref(null)

const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)

const hasK6 = computed(() => snapshotExecutions.value.some(e => e.engine === 'K6' || e.load_snapshot?._engine === 'K6'))
const rateLabel = computed(() => hasK6.value ? '业务 RPS（k6）/ TPS（其他）' : t('performanceTesting.metric.tps'))
const METRIC_META = {
  total_requests: { i18n: 'metric.totalRequests', unit: '' },
  tps: { i18n: 'metric.tps', unit: '' },
  peak_tps: { i18n: 'metric.peakTps', unit: '' },
  avg_rt: { i18n: 'metric.avgRt', unit: 'ms' },
  p90_rt: { i18n: 'metric.p90Rt', unit: 'ms' },
  p95_rt: { i18n: 'metric.p95Rt', unit: 'ms' },
  p99_rt: { i18n: 'metric.p99Rt', unit: 'ms' },
  max_rt: { i18n: 'metric.maxRt', unit: 'ms' },
  error_rate: { i18n: 'metric.errorRate', unit: '%' }
}

const snapshotExecutions = computed(() => detail.value?.snapshot?.executions || [])
const matrixRows = computed(() =>
  (detail.value?.snapshot?.metric_keys || Object.keys(METRIC_META)).map(key => {
    const meta = METRIC_META[key] || {}
    return { key, label: key === 'tps' ? rateLabel.value : key === 'peak_tps' && hasK6.value ? '峰值业务 RPS（k6）/ TPS（其他）' : meta.i18n ? t('performanceTesting.' + meta.i18n) : key }
  })
)

function formatMetric(key, val) {
  if (val === null || val === undefined || val === '') return '-'
  const num = Number(val)
  if (!Number.isFinite(num)) return '未采集'
  if (key === 'total_requests') return num.toLocaleString()
  const meta = METRIC_META[key] || {}
  const fixed = Math.abs(num) >= 100 ? num.toFixed(0) : num.toFixed(2)
  return `${fixed}${meta.unit || ''}`
}

function deltaText(pct) {
  if (pct === null || pct === undefined) return '-'
  return `${pct > 0 ? '+' : ''}${pct}%`
}

function renderMarkdown(text) {
  return marked.parse(text || '')
}

async function loadProjects() {
  try {
    const res = await getPerfProjects({ page_size: 200 })
    projects.value = res.data.results || res.data || []
  } catch (e) {
    projects.value = []
  }
}

async function loadReports() {
  loading.value = true
  try {
    const params = filterProject.value ? { project: filterProject.value } : {}
    const res = await getPerfComparisonReports(params)
    reports.value = res.data.items || []
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.comparisonReport.loadFailed')))
    reports.value = []
  } finally {
    loading.value = false
  }
}

async function viewReport(row) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const res = await getPerfComparisonReport(row.id)
    detail.value = res.data
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.comparisonReport.loadFailed')))
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function removeReport(row) {
  try {
    await ElMessageBox.confirm(
      t('performanceTesting.comparisonReport.deleteConfirm'),
      t('performanceTesting.common.confirm'),
      { type: 'warning' }
    )
    await deletePerfComparisonReport(row.id)
    ElMessage.success(t('performanceTesting.comparisonReport.deleteSuccess'))
    await loadReports()
  } catch (e) {
    if (e !== 'cancel' && e?.message !== 'cancel') {
      ElMessage.error(apiError(e, t('performanceTesting.comparisonReport.deleteFailed')))
    }
  }
}

onMounted(() => {
  loadProjects()
  loadReports()
})
</script>

<style lang="scss" scoped>
.perf-comparison-reports { padding: 16px; }

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
  .page-title { margin: 0; font-size: 20px; font-weight: 600; color: #303133; }
  .page-sub { margin: 4px 0 0; font-size: 13px; color: #909399; }
  .head-actions { display: flex; gap: 8px; align-items: center; }
}
.f-item { width: 200px; }
.exec-tag { margin-right: 4px; }

.section-title {
  margin: 14px 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  &:first-child { margin-top: 0; }
}
.col-head { display: flex; align-items: center; justify-content: center; gap: 6px; }
.cell-val { font-size: 13px; color: #303133; }
.cell-delta { margin-top: 2px; font-size: 12px; color: #909399; }

.ai-content {
  padding: 12px 14px;
  background: #fafafa;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.7;
  max-height: 400px;
  overflow-y: auto;
  :deep(h1), :deep(h2), :deep(h3) { margin: 10px 0 6px; font-size: 14px; }
  :deep(p) { margin: 6px 0; }
  :deep(ul), :deep(ol) { padding-left: 20px; margin: 6px 0; }
}
</style>
