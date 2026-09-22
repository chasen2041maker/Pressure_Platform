<template>
  <div class="judge-single">
    <el-card shadow="never" class="page-card">
      <template #header>
        <div>
          <h2 class="page-title">{{ $t('llmJudge.single.title') }}</h2>
          <p class="page-desc">{{ $t('llmJudge.single.desc') }}</p>
        </div>
      </template>

      <el-form :model="form" label-position="top" class="single-form">
        <el-form-item :label="$t('llmJudge.common.question')">
          <el-input v-model="form.question" type="textarea" :rows="2"
            :placeholder="$t('llmJudge.single.inputQuestion')" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.common.answer')">
          <el-input v-model="form.answer" type="textarea" :rows="5"
            :placeholder="$t('llmJudge.single.inputAnswer')" />
        </el-form-item>
        <el-form-item>
          <div class="gt-header">
            <el-checkbox v-model="form.autoGt">{{ $t('llmJudge.common.autoGt') }}</el-checkbox>
            <el-tooltip :content="$t('llmJudge.single.autoGtTip')" placement="right">
              <el-icon class="tip-icon"><InfoFilled /></el-icon>
            </el-tooltip>
          </div>
          <el-input v-model="form.groundTruth" type="textarea" :rows="3" :disabled="form.autoGt"
            :placeholder="$t('llmJudge.single.inputGroundTruth')" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.common.rubric')">
          <el-select v-model="form.rubric" :placeholder="$t('llmJudge.batch.useDefault')" clearable style="width:100%">
            <el-option v-for="r in rubrics" :key="r.id" :label="r.name + (r.is_default ? '（默认）' : '')" :value="r.id" />
          </el-select>
        </el-form-item>
        <div class="form-actions">
          <el-button type="primary" :loading="loading"
            :disabled="!form.question.trim() || !form.answer.trim()" @click="handleSubmit">
            {{ $t('llmJudge.single.startScore') }}
          </el-button>
          <el-button @click="fillExample">{{ $t('llmJudge.single.exampleFill') }}</el-button>
          <el-button @click="handleReset">{{ $t('llmJudge.common.reset') }}</el-button>
        </div>
      </el-form>
    </el-card>

    <!-- 评分结果 -->
    <el-card v-if="result" shadow="never" class="page-card result-card">
      <template #header>
        <div class="result-header">
          <h3 class="section-title">{{ $t('llmJudge.single.result') }}</h3>
          <div v-if="result.cache_hit" class="cache-tip">
            <el-icon><CircleCheck /></el-icon>
            <span>{{ $t('llmJudge.single.cacheHitTip') }}</span>
          </div>
        </div>
      </template>

      <!-- 元信息 -->
      <div class="meta-line">
        <span class="meta-item" v-if="result.request_id"><b>请求ID：</b><code>{{ result.request_id }}</code></span>
        <span class="meta-item"><b>自动匹配GT：</b>
          <el-tag size="small" :type="result.auto_gt ? 'success' : 'info'">{{ result.auto_gt ? '是' : '否' }}</el-tag>
        </span>
        <span class="meta-item" v-if="result.ground_truth"><b>GT：</b>
          <span class="gt-preview">{{ formatGt(result.ground_truth) }}</span>
        </span>
        <span class="meta-item" v-if="result.created_at"><b>时间：</b>{{ fmtTime(result.created_at) }}</span>
      </div>

      <!-- 核心分数 -->
      <div class="score-board">
        <div class="final-score">
          <div class="score-label">{{ $t('llmJudge.common.score') }}</div>
          <div :class="['score-value', scoreClass(result.final_score)]">{{ fmtNum(result.final_score) }}</div>
        </div>
        <div class="score-split">
          <div class="split-item">
            <span class="split-label">{{ $t('llmJudge.common.ruleScore') }}</span>
            <span class="split-value">{{ fmtNum(result.rule_score) }}</span>
          </div>
          <div class="split-item">
            <span class="split-label">{{ $t('llmJudge.common.llmScore') }}</span>
            <span class="split-value">{{ fmtNum(result.llm_score) }}</span>
          </div>
          <div class="split-item">
            <span class="split-label">{{ $t('llmJudge.common.label') }}</span>
            <el-tag :type="labelTagType(result.overall_label)" effect="dark" size="small">{{ labelText(result.overall_label) }}</el-tag>
          </div>
          <div class="split-item">
            <span class="split-label">{{ $t('llmJudge.common.gateZone') }}</span>
            <el-tag :type="zoneTagType(result.gate_zone)" effect="dark" size="small">{{ zoneText(result.gate_zone) }}</el-tag>
          </div>
          <div class="split-item">
            <span class="split-label">{{ $t('llmJudge.common.vetoed') }}</span>
            <el-tag :type="result.vetoed ? 'danger' : 'success'" effect="plain" size="small">
              {{ result.vetoed ? $t('llmJudge.common.vetoed') : $t('llmJudge.common.success') }}
            </el-tag>
          </div>
          <div class="split-item">
            <span class="split-label">{{ $t('llmJudge.common.latency') }}</span>
            <span class="split-value">{{ result.latency_ms ? result.latency_ms + 'ms' : '—' }}</span>
          </div>
          <div v-if="result.judge_model" class="split-item">
            <span class="split-label">{{ $t('llmJudge.common.model') }}</span>
            <span class="split-value">{{ result.judge_model }}</span>
          </div>
        </div>
      </div>

      <!-- 规则提示 -->
      <div v-if="ruleFindings.length" class="findings-block">
        <h4 class="block-title">{{ $t('llmJudge.single.ruleFindings') }}</h4>
        <div class="findings-list">
          <div v-for="(f, i) in ruleFindings" :key="i" :class="['finding-item', findingClass(f.severity)]">
            <el-tag :type="severityType(f.severity)" size="small" effect="dark">{{ f.severity }}</el-tag>
            <span class="finding-rule">{{ f.rule }}</span>
            <span class="finding-msg">{{ f.message }}</span>
          </div>
        </div>
      </div>
      <div v-else class="no-findings">{{ $t('llmJudge.single.noFindings') }}</div>

      <!-- 维度得分 -->
      <div v-if="dimScores.length" class="dim-block">
        <h4 class="block-title">{{ $t('llmJudge.single.dimensionScores') }}</h4>
        <div class="dim-grid">
          <div v-for="d in dimScores" :key="d.id" class="dim-item">
            <div class="dim-head">
              <span class="dim-name">{{ d.name || d.id }}</span>
              <span :class="['dim-score', scoreClass(d.score)]">{{ d.score }}</span>
            </div>
            <el-progress :percentage="(d.score / 5) * 100" :show-text="false" :stroke-width="8"
              :color="dimColor(d.score)" />
            <div v-if="d.reasoning" class="dim-reasoning">{{ d.reasoning }}</div>
          </div>
        </div>
      </div>

      <!-- LLM 评判理由 -->
      <div v-if="llmReasoning" class="reasoning-block">
        <h4 class="block-title">{{ $t('llmJudge.single.llmReasoning') }}</h4>
        <div class="reasoning-text">{{ llmReasoning }}</div>
      </div>

      <!-- 否决原因 -->
      <div v-if="result.vetoed && vetoReasons.length" class="findings-block">
        <h4 class="block-title">否决原因</h4>
        <ul class="veto-list">
          <li v-for="(r,i) in vetoReasons" :key="i">{{ r }}</li>
        </ul>
      </div>

      <!-- 错误信息 -->
      <div v-if="result.error_message" class="findings-block">
        <h4 class="block-title">错误信息</h4>
        <div class="err-text">{{ result.error_message }}</div>
      </div>

      <div class="submit-another">
        <el-button type="primary" plain @click="handleReset">{{ $t('llmJudge.single.submitAnother') }}</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { InfoFilled, CircleCheck } from '@element-plus/icons-vue'
import { scoreSingle, getRubricList } from '@/api/llm-judge'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const loading = ref(false)
const result = ref(null)
const rubrics = ref([])

const form = reactive({
  question: '',
  answer: '',
  groundTruth: '',
  autoGt: true,
  rubric: null
})

const loadRubrics = async () => {
  try {
    const res = await getRubricList({ page_size: 100 })
    rubrics.value = res.data.results || res.data
  } catch (e) { /* ignore */ }
}

const handleSubmit = async () => {
  loading.value = true
  result.value = null
  try {
    const payload = {
      question: form.question,
      answer: form.answer,
      auto_gt: form.autoGt
    }
    if (!form.autoGt && form.groundTruth.trim()) {
      payload.ground_truth = { text: form.groundTruth }
    }
    if (form.rubric) payload.rubric = form.rubric
    const res = await scoreSingle(payload)
    result.value = res.data
  } catch (e) {
    const msg = e.response?.data?.detail || e.response?.data?.error || t('llmJudge.common.failure')
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}

const fillExample = () => {
  form.question = '招商银行2024年营业收入是多少？'
  form.answer = '根据招商银行2024年年报，全年营业收入约为3375.37亿元，同比增长约0.5%。'
  form.autoGt = true
  form.groundTruth = ''
}

const handleReset = () => {
  form.question = ''
  form.answer = ''
  form.groundTruth = ''
  form.autoGt = true
  form.rubric = null
  result.value = null
}

const ruleFindings = computed(() => result.value?.rule_findings || [])
const dimScores = computed(() => result.value?.llm_verdict?.dimensions || [])
const llmReasoning = computed(() => result.value?.llm_verdict?.reasoning || '')

const fmtNum = (v) => (v === null || v === undefined) ? '—' : Number(v).toFixed(1)
const fmtTime = (v) => v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '—'
const vetoReasons = computed(() => result.value?.veto_reasons || [])
const formatGt = (gt) => {
  if (!gt) return '—'
  if (typeof gt === 'string') return gt.length > 60 ? gt.slice(0,60)+'…' : gt
  if (typeof gt === 'object') {
    const parts = []
    if (gt.text) parts.push(String(gt.text).slice(0,60))
    if (Array.isArray(gt.values)) parts.push(`${gt.values.length}项指标`)
    return parts.join(' | ') || 'JSON'
  }
  return String(gt)
}
const labelText = (k) => k ? t(`llmJudge.labels.${k}`) : '—'
const zoneText = (k) => k ? t(`llmJudge.gate.${k}`) : '—'
const labelTagType = (k) => ({ excellent: 'success', acceptable: '', needs_improvement: 'warning', critical_failure: 'danger' }[k] || 'info')
const zoneTagType = (k) => ({ green: 'success', yellow: 'warning', red: 'danger' }[k] || 'info')
const severityType = (s) => ({ veto: 'danger', error: 'danger', warn: 'warning', info: 'info' }[s] || 'info')
const findingClass = (s) => ({ veto: 'veto', error: 'error', warn: 'warn' }[s] || '')
const scoreClass = (s) => s >= 85 ? 'high' : s >= 70 ? 'mid' : 'low'
const dimColor = (s) => s >= 4 ? '#67c23a' : s >= 3 ? '#e6a23c' : '#f56c6c'

onMounted(() => { loadRubrics() })
</script>

<style scoped lang="scss">
.judge-single {
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
.section-title { margin: 0; font-size: 16px; color: #303133; }
.gt-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  .tip-icon { color: #909399; cursor: help; }
}
.form-actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
}
.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.cache-tip {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #67c23a;
}
.score-board {
  display: flex;
  gap: 24px;
  padding: 20px;
  background: #f5f7fa;
  border-radius: 8px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.final-score {
  text-align: center;
  min-width: 120px;
  .score-label { font-size: 12px; color: #909399; margin-bottom: 6px; }
  .score-value {
    font-size: 42px;
    font-weight: 700;
    line-height: 1;
    &.high { color: #67c23a; }
    &.mid { color: #e6a23c; }
    &.low { color: #f56c6c; }
  }
}
.score-split {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  align-items: center;
  flex: 1;
}
.split-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  .split-label { font-size: 12px; color: #909399; }
  .split-value { font-size: 16px; font-weight: 600; color: #303133; }
}
.block-title {
  font-size: 14px;
  color: #303133;
  margin: 20px 0 10px;
  padding-left: 8px;
  border-left: 3px solid #409eff;
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
  &.veto { background: #fef0f0; }
  &.error { background: #fef0f0; }
  &.warn { background: #fdf6ec; }
  .finding-rule { font-weight: 600; color: #303133; min-width: 120px; }
  .finding-msg { color: #606266; }
}
.no-findings {
  color: #909399;
  font-size: 13px;
  padding: 8px 0;
}
.dim-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
}
.dim-item {
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
}
.dim-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  .dim-name { font-size: 13px; color: #303133; font-weight: 500; }
  .dim-score {
    font-size: 18px;
    font-weight: 700;
    &.high { color: #67c23a; }
    &.mid { color: #e6a23c; }
    &.low { color: #f56c6c; }
  }
}
.dim-reasoning {
  margin-top: 8px;
  font-size: 12px;
  color: #606266;
  line-height: 1.5;
}
.reasoning-text {
  padding: 14px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 13px;
  color: #303133;
  line-height: 1.6;
  white-space: pre-wrap;
}
.submit-another { margin-top: 20px; text-align: center; }
.meta-line {
  display: flex; flex-wrap: wrap; gap: 10px 20px;
  padding: 10px 14px; background: #fafbfc; border: 1px dashed #ebeef5; border-radius: 6px;
  margin-bottom: 16px; font-size: 12px; color: #606266;
  .meta-item { display: inline-flex; align-items: center; gap: 4px;
    b { font-weight: 500; color: #909399; }
    code { background: #f5f7fa; padding: 2px 6px; border-radius: 4px;
           font-family: Menlo, Consolas, monospace; color: #1b3d6b; }
  }
  .gt-preview { max-width: 400px; display: inline-block; overflow: hidden;
                text-overflow: ellipsis; white-space: nowrap; vertical-align: middle; }
}
.veto-list { margin: 0; padding-left: 20px;
  li { color: #a80011; line-height: 1.8; font-size: 13px; }
}
.err-text {
  padding: 12px 14px; background: #fef0f0; border: 1px solid #fde2e2;
  border-radius: 6px; color: #a80011; white-space: pre-wrap;
  word-break: break-word; font-size: 13px; line-height: 1.6;
}
</style>
