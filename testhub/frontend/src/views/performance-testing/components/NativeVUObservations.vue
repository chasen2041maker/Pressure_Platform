<template>
  <el-card shadow="never" class="native-vu-card">
    <template #header><strong>VU 配置与实际观测</strong></template>
    <dl class="native-values">
      <div><dt>配置 VU</dt><dd>{{ count(configuredVus) }}</dd></div>
      <div><dt>原生活动 VU（最近观测）</dt><dd>{{ count(model.latest) }}</dd></div>
      <div><dt>{{ scriptLabel }}</dt><dd>{{ count(scriptUsers) }}</dd></div>
    </dl>
    <p class="native-note">配置 VU 是目标数量。k6 原生活动 VU 包含等待、登录等阶段，不等于同时在途的 HTTP 请求；脚本活动用户来自脚本事件，也不等于原生 VU。</p>
    <p v-if="!model.count" class="native-empty" role="status">{{ emptyNotice }}</p>
    <div v-show="model.count" ref="chartRef" class="native-chart" role="img" aria-label="k6 原生活动 VU 离散观测曲线，缺失与观测边界处断线" />
    <p class="native-note">{{ displayNotice }}</p>
    <p v-if="model.mixedRunners" class="native-note" role="status">收到多个运行实例的观测，时间偏移不能直接比较，因此最近值显示未知。各实例分别断线展示；窗口已满时，迟到的新实例不会替换已有点。</p>
    <p class="native-note">横轴为采集请求起止时间的中点；提示显示原始时间区间。0 表示确实观测到 0；缺失、序号跳跃、更换运行实例、时间倒退、迟到或运行状态不确定处断线。连线仅辅助阅读，不证明点间持续并发。</p>
    <dl class="native-evidence">
      <div><dt>采集器收到的尝试</dt><dd>{{ count(evidence.received) }}</dd></div>
      <div><dt>有效 / 缺失</dt><dd>{{ count(evidence.valid) }} / {{ count(evidence.missing) }}</dd></div>
      <div><dt>低于目标 / 迟到</dt><dd>{{ count(evidence.below) }} / {{ count(evidence.late) }}</dd></div>
      <div><dt>完整持久化</dt><dd>{{ evidence.persistence }}</dd></div>
      <div><dt>全程持续并发</dt><dd>{{ evidence.sustained }}</dd></div>
    </dl>
    <p class="native-note">摘要口径：采集器已收到（collector_received），不代表观测已全部保存。上方次数直接读取摘要，不由本页抽样图推算。</p>
  </el-card>
</template>

<script setup>
import { computed, ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { projectNativeVu, nativeVuEvidence, NATIVE_VU_DISPLAY_LIMIT } from '../nativeVuObservations.mjs'

const props = defineProps({
  executionId: { type: Number, required: true },
  observations: { type: Array, default: () => [] },
  summary: { type: Object, default: null },
  configuredVus: { type: Number, default: null },
  scriptUsers: { type: Number, default: null },
  scriptLabel: { type: String, default: '脚本活动用户' },
  mode: { type: String, default: 'monitor' }
})
const chartRef = ref(null)
let chart = null
let sizeObserver = null
let disposed = false
const model = computed(() => projectNativeVu(props.observations, props.executionId))
const evidence = computed(() => nativeVuEvidence(props.summary, props.executionId))
const displayNotice = computed(() => props.mode === 'report'
  ? `报告接口最多返回 1000 条时序采样，本图最多保留其中最近 ${NATIVE_VU_DISPLAY_LIMIT} 次原生观测，可能不包含全程。不能据此计算全程覆盖率或判定持续并发。`
  : `本图最多保留最近 ${NATIVE_VU_DISPLAY_LIMIT} 次原生观测；最近观测不是此刻保证值，不能据此判定全程持续并发。`)
const emptyNotice = computed(() => evidence.value.reason === 'unsupported_runner'
  ? '原生 VU 未采集：当前运行方式未提供此项观测。'
  : evidence.value.received > 0 ? '本页未取得原生 VU 时序观测；采集器摘要不能替代观测点。'
    : '原生 VU 未采集或本页未取得观测。旧记录无法补采，不能以脚本活动用户代替或补成 0。')
const count = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : '未知'

function renderChart() {
  if (disposed || !chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value)
    if (typeof ResizeObserver !== 'undefined') {
      sizeObserver = new ResizeObserver(resize)
      sizeObserver.observe(chartRef.value)
    }
  }
  chart.setOption({
    animation: false,
    grid: { left: 48, right: 20, top: 35, bottom: 35, containLabel: true },
    tooltip: { trigger: 'item', renderMode: 'richText', formatter: item => {
      const row = item.data
      if (!row.interval) return '观测边界'
      return `原生活动 VU：${count(row.value[1])}\n运行实例：${row.runner.slice(0, 8)} · 序号 ${row.sequence}\n采集区间：${row.interval[0]}–${row.interval[1]} ms\n往返：${row.rtt} ms${row.uncertain ? '\n迟到、缺失或状态不确定' : ''}`
    } },
    xAxis: { type: 'value', name: '秒', min: 0 },
    yAxis: { type: 'value', name: '原生活动 VU', min: 0, minInterval: 1 },
    series: [{ name: '原生活动 VU', type: 'line', smooth: false, showSymbol: true, symbolSize: 5,
      connectNulls: false, data: model.value.points, itemStyle: { color: '#409eff' } }]
  }, { notMerge: true })
  chart.resize()
}
function resize() { chart?.resize() }
watch(model, () => nextTick(renderChart))
onMounted(() => { nextTick(renderChart); window.addEventListener('resize', resize) })
onUnmounted(() => {
  disposed = true
  window.removeEventListener('resize', resize)
  sizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.native-vu-card { margin: 16px 0; }
.native-values, .native-evidence { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 0 0 12px; }
.native-values dt, .native-evidence dt { color: var(--el-text-color-secondary); font-size: 13px; }
.native-values dd { margin: 6px 0 0; font-size: 24px; font-weight: 600; font-variant-numeric: tabular-nums; }
.native-evidence { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); margin-top: 16px; }
.native-evidence dd { margin: 6px 0 0; font-variant-numeric: tabular-nums; }
.native-note { margin: 10px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.native-empty { margin: 16px 0; line-height: 1.6; }
.native-chart { height: 230px; width: 100%; }
@media (max-width: 640px) { .native-values { grid-template-columns: 1fr; gap: 12px; } }
</style>
