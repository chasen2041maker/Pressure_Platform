<template>
  <div class="load-profile-editor">
    <el-form :model="form" label-width="156px" label-position="left" size="default">
      <el-form-item :label="t('performanceTesting.loadModel.label')">
        <el-radio-group v-model="form.model" @change="onModelChange">
          <el-radio-button value="CONCURRENCY">{{ t('performanceTesting.loadModel.CONCURRENCY') }}</el-radio-button>
          <el-radio-button value="RAMPING" :disabled="engine === 'K6'">{{ t('performanceTesting.loadModel.RAMPING') }}</el-radio-button>
          <el-radio-button value="RPS" :disabled="engine === 'K6'">{{ t('performanceTesting.loadModel.RPS') }}</el-radio-button>
          <el-radio-button value="SPIKE" :disabled="engine === 'K6'">{{ t('performanceTesting.loadModel.SPIKE') }}</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <!-- 固定并发 -->
      <template v-if="form.model === 'CONCURRENCY'">
        <el-form-item :label="t('performanceTesting.loadModel.concurrency')">
          <el-input-number v-model="form.concurrency" :min="1" :max="limits.max_concurrency" />
        </el-form-item>
        <el-form-item v-if="engine === 'K6'" label="每个用户执行轮数">
          <el-input-number v-model="form.iterations_per_vu" :min="0" :max="1000000" />
          <span class="field-help">0 表示按时间运行；大于 0 表示每人完成指定轮数</span>
        </el-form-item>
        <el-form-item :label="engine === 'K6' && form.iterations_per_vu > 0 ? '最长运行时间' : t('performanceTesting.loadModel.duration')">
          <el-input-number v-model="form.duration" :min="1" :max="limits.max_duration" />
          <span class="unit">{{ t('performanceTesting.common.seconds') }}</span>
        </el-form-item>
        <el-form-item v-if="engine !== 'K6'" :label="t('performanceTesting.loadModel.rampUp')" :title="t('performanceTesting.loadModel.rampUpTip')">
          <el-input-number v-model="form.ramp_up" :min="0" :max="limits.max_duration" />
          <span class="unit">{{ t('performanceTesting.common.seconds') }}</span>
        </el-form-item>
      </template>

      <!-- 阶梯加压 -->
      <template v-else-if="form.model === 'RAMPING'">
        <el-form-item :label="t('performanceTesting.loadModel.stages')">
          <div class="stages">
            <div v-for="(stage, idx) in form.stages" :key="idx" class="stage-row">
              <span class="stage-idx">{{ t('performanceTesting.loadModel.stageIndex', { index: idx + 1 }) }}</span>
              <el-input-number v-model="stage.duration" :min="1" :max="limits.max_duration" size="small" />
              <span class="unit">{{ t('performanceTesting.common.seconds') }}</span>
              <el-input-number v-model="stage.target" :min="0" :max="limits.max_concurrency" size="small" />
              <span class="unit">{{ t('performanceTesting.loadModel.concurrency') }}</span>
              <el-button v-if="form.stages.length > 1" text type="danger" :icon="Delete" @click="removeStage(idx)" />
            </div>
            <el-button v-if="form.stages.length < 20" :icon="Plus" text type="primary" @click="addStage">
              {{ t('performanceTesting.loadModel.addStage') }}
            </el-button>
          </div>
        </el-form-item>
      </template>

      <!-- 固定 RPS -->
      <template v-else-if="form.model === 'RPS'">
        <el-form-item :label="t('performanceTesting.loadModel.targetRps')">
          <el-input-number v-model="form.target_rps" :min="1" :max="limits.max_target_rps" />
        </el-form-item>
        <el-form-item :label="t('performanceTesting.loadModel.duration')">
          <el-input-number v-model="form.duration" :min="1" :max="limits.max_duration" />
          <span class="unit">{{ t('performanceTesting.common.seconds') }}</span>
        </el-form-item>
        <el-form-item :label="t('performanceTesting.loadModel.maxConcurrency')" :title="t('performanceTesting.loadModel.maxConcurrencyTip')">
          <el-input-number v-model="form.max_concurrency" :min="0" :max="limits.max_concurrency" />
        </el-form-item>
      </template>

      <!-- 尖峰冲击 -->
      <template v-else-if="form.model === 'SPIKE'">
        <el-form-item :label="t('performanceTesting.loadModel.spikeBase')">
          <el-input-number v-model="form.baseline_concurrency" :min="0" :max="limits.max_concurrency" />
        </el-form-item>
        <el-form-item :label="t('performanceTesting.loadModel.spikePeak')">
          <el-input-number v-model="form.spike_concurrency" :min="1" :max="limits.max_concurrency" />
        </el-form-item>
        <el-form-item :label="t('performanceTesting.loadModel.spikeHold')">
          <el-input-number v-model="form.spike_duration" :min="1" :max="limits.max_duration" />
          <span class="unit">{{ t('performanceTesting.common.seconds') }}</span>
        </el-form-item>
        <el-form-item :label="t('performanceTesting.loadModel.spikeTimes')">
          <el-input-number v-model="form.spike_times" :min="1" :max="100" />
          <span class="unit">{{ t('performanceTesting.common.times') }}</span>
        </el-form-item>
      </template>

      <el-form-item v-if="engine !== 'K6'" :label="t('performanceTesting.loadModel.maxRequests')" :title="t('performanceTesting.loadModel.maxRequestsTip')">
        <el-input-number v-model="form.max_requests" :min="0" :max="100000000" />
        <span class="unit">{{ t('performanceTesting.common.times') }}</span>
      </el-form-item>
    </el-form>

    <el-alert v-if="engine === 'K6'" type="info" :closable="false" show-icon
      title="k6 当前支持固定并发和顺序流程。轮数模式的业务请求数 = 用户数 × 每人轮数 × 启用的业务步骤数；前置登录另计。提前停止、登录失败或超时会减少完成数量。" />
    <div v-if="!(engine === 'K6' && form.iterations_per_vu > 0)" class="preview" :style="{ width: '100%' }">
      <div class="preview-title">
        <el-icon><TrendCharts /></el-icon>
        <span>{{ t('performanceTesting.loadModel.preview') }}</span>
      </div>
      <div ref="previewRef" class="preview-chart"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import * as echarts from 'echarts'
import { Delete, Plus, TrendCharts } from '@element-plus/icons-vue'
import { computeLoadProfilePoints, computeAxisBounds } from './loadProfile.js'

const props = defineProps({
  engine: { type: String, default: '' },
  modelValue: { type: Object, default: () => ({}) },
  limits: {
    type: Object,
    default: () => ({
      max_concurrency: 1000,
      max_target_rps: 100000,
      max_duration: 3600,
      max_concurrent_executions: 5
    })
  }
})
const emit = defineEmits(['update:modelValue'])

const { t } = useI18n()
const previewRef = ref(null)
let chart = null

function defaultForm() {
  return {
    model: 'CONCURRENCY',
    concurrency: 50,
    duration: 300,
    ramp_up: 30,
    stages: [{ duration: 60, target: 20 }, { duration: 180, target: 100 }, { duration: 60, target: 0 }],
    target_rps: 200,
    max_concurrency: 0,
    baseline_concurrency: 10,
    spike_concurrency: 200,
    spike_duration: 30,
    spike_times: 3,
    max_requests: 0,
    iterations_per_vu: 0
  }
}

const form = reactive({ ...defaultForm(), ...(props.modelValue || {}) })

// 防止 props↔form 双向 watch 形成递归更新（与 StepEditor 同构修复）
let syncing = false

function syncFromModel() {
  syncing = true
  try {
    Object.assign(form, defaultForm(), props.modelValue || {})
  } finally {
    syncing = false
  }
  renderPreview()
}

/** 深比较两个 load_config 对象是否内容一致，用于 emit 前短路。 */
function sameLoadConfig(a, b) {
  if (!a && !b) return true
  if (!a || !b) return false
  if (a.model !== b.model) return false
  const sa = a.stages || []
  const sb = b.stages || []
  if (sa.length !== sb.length) return false
  for (let i = 0; i < sa.length; i++) {
    if (Number(sa[i].duration) !== Number(sb[i].duration) ||
        Number(sa[i].target) !== Number(sb[i].target)) return false
  }
  const scalarKeys = ['concurrency', 'duration', 'ramp_up', 'target_rps',
    'max_concurrency', 'baseline_concurrency', 'spike_concurrency',
    'spike_duration', 'spike_times', 'max_requests', 'iterations_per_vu']
  for (const k of scalarKeys) {
    if (Number(a[k] ?? 0) !== Number(b[k] ?? 0)) return false
  }
  return true
}

function emitChange() {
  // 由 syncFromModel 触发的 form 变化不需要再向上 emit，否则形成闭环
  if (syncing) return
  const out = {}
  for (const [k, v] of Object.entries(form)) {
    if (k === 'stages') {
      out.stages = (form.stages || []).map(s => ({ duration: Number(s.duration) || 0, target: Number(s.target) || 0 }))
    } else {
      out[k] = v
    }
  }
  // 内容未变则不向上 emit，避免 props.modelValue 引用变化触发 syncFromModel 死循环
  if (sameLoadConfig(out, props.modelValue)) return
  emit('update:modelValue', out)
  renderPreview()
}

watch(() => props.modelValue, syncFromModel, { deep: true })
watch(form, emitChange, { deep: true })

function onModelChange() { renderPreview() }
function addStage() { form.stages.push({ duration: 60, target: 50 }) }
function removeStage(i) { form.stages.splice(i, 1) }

function computePoints() {
  return computeLoadProfilePoints(form)
}

function renderPreview() {
  if (!previewRef.value) return
  if (!chart) chart = echarts.init(previewRef.value, null, { renderer: 'canvas' })
  const pts = computePoints()
  // X 轴改 value 类型：1) 端点稳定，不被 echarts label interval:auto 吞掉；
  // 2) 数据用 [t, u] 二元组，min/max 受 maxT 控制，比例直观。
  const data = pts.map((p) => [Number(p.t) || 0, Number(p.u) || 0])
  const { xMax, yMax } = computeAxisBounds(pts)
  const yName = form.model === 'RPS' ? 'RPS' : t('performanceTesting.metric.activeUsers')
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '12%', top: '14%', containLabel: true },
    xAxis: {
      type: 'value',
      min: 0,
      max: xMax,
      name: t('performanceTesting.common.seconds'),
      nameLocation: 'end',
      nameGap: 6,
      nameTextStyle: { fontSize: 10, color: '#909399' },
      axisLabel: { fontSize: 10, color: '#606266' },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: yMax,
      name: yName,
      nameLocation: 'end',
      nameGap: 8,
      nameTextStyle: { fontSize: 10, color: '#909399' },
      axisLabel: { fontSize: 10, color: '#606266' }
    },
    series: [{
      type: 'line',
      step: 'end',
      smooth: false,
      data,
      showSymbol: false,
      itemStyle: { color: '#1890ff' },
      areaStyle: {
        opacity: 0.2,
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#1890ff' }, { offset: 1, color: '#fff' }
        ])
      }
    }]
  })
  // 容器在窄→宽切换过过程中，初次 setOption 后再 resize 一次，避免画布尺寸定格在异常宽度上
  chart.resize()
}

// 监听容器自身尺寸变化：el-tabs 切换、菜单折叠等都可能导致 preview 容器宽度变化
let resizeObserver = null

function resize() { if (chart) chart.resize() }

onMounted(() => {
  nextTick(() => {
    renderPreview()
    if (typeof window !== 'undefined' && window.ResizeObserver && previewRef.value) {
      resizeObserver = new ResizeObserver(() => { if (chart) chart.resize() })
      resizeObserver.observe(previewRef.value)
    }
  })
  window.addEventListener('resize', resize)
})

onUnmounted(() => {
  window.removeEventListener('resize', resize)
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style lang="scss" scoped>
.load-profile-editor {
  /* 根容器显式撑满父级可用宽度，
     否则图表在 tabs 切换/侧栏折叠等场景下可能被收缩到内容固有宽度 */
  width: 100%;
  box-sizing: border-box;
  :deep(.el-form-item) { margin-bottom: 20px; }
  :deep(.el-form-item__label) { height: auto; min-height: 32px; align-items: center; line-height: 1.5; padding-right: 16px; }
  :deep(.el-form-item__content) { min-width: 0; }
  :deep(.el-radio-group) { display: flex; flex-wrap: wrap; row-gap: 8px; }
  .field-help { flex-basis: 100%; margin-top: 6px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
  .unit { margin-left: 6px; color: #8c8c8c; font-size: 12px; }
  .stages { width: 100%; }
  .stage-row {
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px;
    .stage-idx { font-size: 13px; color: #595959; min-width: 56px; }
  }
  .preview {
    width: 100%;
    box-sizing: border-box;
    margin-top: 8px;
    border: 1px solid #ebeef5;
    border-radius: 8px;
    padding: 10px 12px;
  }
  .preview-title {
    display: flex; align-items: center; gap: 6px;
    font-size: 13px; font-weight: 600; color: #1f2d3d; margin-bottom: 6px;
    .el-icon { color: #1890ff; }
  }
  /* 高度固定为 200px 让曲线更舒展，宽度跟随 .preview 自适应 */
  .preview-chart { height: 200px; width: 100%; }
}
@media (max-width: 800px) {
  .load-profile-editor :deep(.el-form-item) { display: block; }
  .load-profile-editor :deep(.el-form-item__label) { width: auto !important; justify-content: flex-start; padding: 0 0 6px; }
  .load-profile-editor :deep(.el-form-item__content) { margin-left: 0 !important; }
}
</style>
