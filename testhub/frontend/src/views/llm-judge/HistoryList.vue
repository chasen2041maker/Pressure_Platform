<template>
  <div class="judge-history">
    <el-card shadow="never" class="page-card">
      <template #header>
        <div>
          <h2 class="page-title">{{ $t('llmJudge.history.title') }}</h2>
          <p class="page-desc">{{ $t('llmJudge.history.desc') }}</p>
        </div>
      </template>

      <!-- 过滤栏 -->
      <div class="filter-bar">
        <el-input v-model="filters.search" :placeholder="$t('llmJudge.history.search')" clearable
          style="width:240px" @keyup.enter="loadData" @clear="loadData" />
        <el-select v-model="filters.overall_label" :placeholder="$t('llmJudge.history.filterLabel')" clearable
          style="width:140px" @change="loadData">
          <el-option v-for="k in labelKeys" :key="k" :label="t(`llmJudge.labels.${k}`)" :value="k" />
        </el-select>
        <el-select v-model="filters.gate_zone" :placeholder="$t('llmJudge.history.filterZone')" clearable
          style="width:140px" @change="loadData">
          <el-option v-for="k in zoneKeys" :key="k" :label="t(`llmJudge.gate.${k}`)" :value="k" />
        </el-select>
        <el-checkbox v-model="filters.vetoed_only" @change="loadData">{{ $t('llmJudge.history.filterVetoed') }}</el-checkbox>
        <el-button type="primary" @click="loadData">{{ $t('llmJudge.history.search') }}</el-button>
      </div>

      <!-- 表格 -->
      <el-table :data="records" border stripe size="small" v-loading="loading" style="width:100%;margin-top:16px">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="request_id" :label="$t('llmJudge.common.requestId')" width="120" show-overflow-tooltip />
        <el-table-column :label="$t('llmJudge.common.question')" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.question }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.answer')" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.answer }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.score')" width="90">
          <template #default="{ row }">
            <span :class="['score-num', scoreClass(row.final_score)]">{{ fmtNum(row.final_score) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.label')" width="100">
          <template #default="{ row }">
            <el-tag :type="labelTagType(row.overall_label)" size="small">{{ labelText(row.overall_label) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.gateZone')" width="100">
          <template #default="{ row }">
            <el-tag :type="zoneTagType(row.gate_zone)" size="small" effect="plain">{{ zoneText(row.gate_zone) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.vetoed')" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.vetoed" type="danger" size="small">{{ $t('llmJudge.common.vetoed') }}</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.rubric')" width="120" show-overflow-tooltip>
          <template #default="{ row }">{{ row.rubric_name }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.latency')" width="90">
          <template #default="{ row }">{{ row.latency_ms ? row.latency_ms + 'ms' : '—' }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.createdAt')" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.operation')" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="showDetail(row)">{{ $t('llmJudge.common.viewDetail') }}</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrap">
        <el-pagination v-model:current-page="page" v-model:page-size="pageSize"
          :total="total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next"
          @size-change="loadData" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 详情对话框 -->
    <el-dialog v-model="detailVisible" :title="$t('llmJudge.history.detailTitle')" width="780px" top="5vh">
      <div v-if="detail" class="detail-content">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item :label="$t('llmJudge.common.score')">
            <span :class="['score-num', scoreClass(detail.final_score)]">{{ fmtNum(detail.final_score) }}</span>
          </el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.label')">
            <el-tag :type="labelTagType(detail.overall_label)" size="small">{{ labelText(detail.overall_label) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.ruleScore')">{{ fmtNum(detail.rule_score) }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.llmScore')">{{ fmtNum(detail.llm_score) }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.gateZone')">
            <el-tag :type="zoneTagType(detail.gate_zone)" size="small">{{ zoneText(detail.gate_zone) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.vetoed')">{{ detail.vetoed ? $t('llmJudge.common.vetoed') : '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.latency')">{{ detail.latency_ms ? detail.latency_ms + 'ms' : '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.model')">{{ detail.judge_model || '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.cacheHit')">{{ detail.cache_hit ? '✔' : '—' }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.common.createdAt')">{{ fmtTime(detail.created_at) }}</el-descriptions-item>
        </el-descriptions>

        <h4 class="block-title">{{ $t('llmJudge.history.inputQuestion') }}</h4>
        <div class="qa-text">{{ detail.question }}</div>
        <h4 class="block-title">{{ $t('llmJudge.history.inputAnswer') }}</h4>
        <div class="qa-text">{{ detail.answer }}</div>

        <h4 v-if="ruleFindings.length" class="block-title">{{ $t('llmJudge.history.ruleFindings') }}</h4>
        <div v-if="ruleFindings.length" class="findings-list">
          <div v-for="(f, i) in ruleFindings" :key="i" class="finding-item">
            <el-tag :type="severityType(f.severity)" size="small" effect="dark">{{ f.severity }}</el-tag>
            <span class="finding-rule">{{ f.rule }}</span>
            <span class="finding-msg">{{ f.message }}</span>
          </div>
        </div>

        <h4 v-if="dimScores.length" class="block-title">{{ $t('llmJudge.history.dimensionScores') }}</h4>
        <div v-if="dimScores.length" class="dim-grid">
          <div v-for="d in dimScores" :key="d.id" class="dim-item">
            <div class="dim-head">
              <span>{{ d.name || d.id }}</span>
              <span :class="['dim-score', scoreClass(d.score)]">{{ d.score }}</span>
            </div>
            <div v-if="d.reasoning" class="dim-reasoning">{{ d.reasoning }}</div>
          </div>
        </div>

        <h4 v-if="llmReasoning" class="block-title">{{ $t('llmJudge.history.llmVerdict') }}</h4>
        <div v-if="llmReasoning" class="qa-text">{{ llmReasoning }}</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { getRecordList } from '@/api/llm-judge'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const loading = ref(false)
const records = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const detailVisible = ref(false)
const detail = ref(null)

const labelKeys = ['excellent', 'acceptable', 'needs_improvement', 'critical_failure']
const zoneKeys = ['green', 'yellow', 'red']

const filters = reactive({
  search: '',
  overall_label: '',
  gate_zone: '',
  vetoed_only: false
})

const loadData = async () => {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filters.search) params.search = filters.search
    if (filters.overall_label) params.overall_label = filters.overall_label
    if (filters.gate_zone) params.gate_zone = filters.gate_zone
    if (filters.vetoed_only) params.vetoed = true
    const res = await getRecordList(params)
    records.value = res.data.results || res.data
    total.value = res.data.count || records.value.length
  } catch (e) { /* ignore */ } finally {
    loading.value = false
  }
}

const showDetail = (row) => {
  detail.value = row
  detailVisible.value = true
}

const ruleFindings = computed(() => detail.value?.rule_findings || [])
const dimScores = computed(() => detail.value?.llm_verdict?.dimensions || [])
const llmReasoning = computed(() => detail.value?.llm_verdict?.reasoning || '')

const fmtNum = (v) => (v === null || v === undefined) ? '—' : Number(v).toFixed(1)
const fmtTime = (v) => v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '—'
const labelText = (k) => k ? t(`llmJudge.labels.${k}`) : '—'
const zoneText = (k) => k ? t(`llmJudge.gate.${k}`) : '—'
const labelTagType = (k) => ({ excellent: 'success', acceptable: '', needs_improvement: 'warning', critical_failure: 'danger' }[k] || 'info')
const zoneTagType = (k) => ({ green: 'success', yellow: 'warning', red: 'danger' }[k] || 'info')
const severityType = (s) => ({ veto: 'danger', error: 'danger', warn: 'warning', info: 'info' }[s] || 'info')
const scoreClass = (s) => s >= 85 ? 'high' : s >= 70 ? 'mid' : 'low'

onMounted(() => { loadData() })
</script>

<style scoped lang="scss">
.judge-history {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-card {
  border-radius: 8px;
  :deep(.el-card__header) { padding: 16px 20px; }
  :deep(.el-card__body) { padding: 20px; }
}
.page-title { margin: 0; font-size: 18px; color: #303133; }
.page-desc { margin: 4px 0 0; font-size: 13px; color: #909399; }
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.pagination-wrap {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.score-num {
  font-weight: 600;
  &.high { color: #67c23a; }
  &.mid { color: #e6a23c; }
  &.low { color: #f56c6c; }
}
.detail-content {
  max-height: 70vh;
  overflow-y: auto;
}
.block-title {
  font-size: 14px;
  color: #303133;
  margin: 18px 0 8px;
  padding-left: 8px;
  border-left: 3px solid #409eff;
}
.qa-text {
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 13px;
  color: #303133;
  line-height: 1.6;
  white-space: pre-wrap;
}
.findings-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.finding-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #f5f7fa;
  font-size: 13px;
  .finding-rule { font-weight: 600; color: #303133; min-width: 120px; }
  .finding-msg { color: #606266; }
}
.dim-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}
.dim-item {
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
}
.dim-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 13px;
  .dim-score {
    font-weight: 700;
    &.high { color: #67c23a; }
    &.mid { color: #e6a23c; }
    &.low { color: #f56c6c; }
  }
}
.dim-reasoning {
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}
</style>
