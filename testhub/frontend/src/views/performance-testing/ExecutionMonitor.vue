<template>
  <div class="perf-monitor" v-loading="loading">
    <!-- ---------- 顶栏 ---------- -->
    <div class="monitor-head">
      <div class="head-left">
        <el-button link :icon="ArrowLeft" @click="goBack">{{ t('performanceTesting.common.back') }}</el-button>
        <el-divider direction="vertical" />
        <span class="exec-no">{{ execution.execution_no || '-' }}</span>
        <span class="scenario-name">{{ execution.scenario_name }}</span>
        <el-tag :type="statusTagType(execution.status)" size="small">
          {{ execution.status ? t('performanceTesting.status.' + execution.status) : '-' }}
        </el-tag>
        <el-tag
          v-if="execution.sla_result"
          :type="slaTagType(execution.sla_result)"
          size="small"
          effect="plain"
        >
          SLA {{ t('performanceTesting.sla.' + execution.sla_result) }}
        </el-tag>
      </div>
      <div class="head-right">
        <span class="conn-badge" :class="channel">
          <i class="dot" />{{ channelText }}
        </span>
        <el-button v-if="isActive" type="danger" plain :icon="VideoPause" :disabled="!monitorReady" :loading="stopping" @click="handleStop">
          {{ t('performanceTesting.execution.stop') }}
        </el-button>
        <el-button v-else type="primary" :icon="Document" :disabled="!monitorReady" @click="goReport">
          {{ t('performanceTesting.monitor.goReport') }}
        </el-button>
      </div>
    </div>

    <el-alert v-if="loadError" type="error" :closable="false" show-icon :title="loadError">
      <el-button link type="primary" @click="loadMonitor">重试加载监控</el-button>
    </el-alert>
    <!-- ---------- 进度条 ---------- -->
    <el-card shadow="never" class="progress-card">
      <div class="progress-row">
        <el-progress
          :percentage="Math.min(100, Math.round(progress))"
          :status="progressStatus"
          :stroke-width="14"
          class="main-progress"
        />
        <div class="progress-meta">
          <span>{{ t('performanceTesting.monitor.elapsed') }}: <b>{{ elapsedText }}</b></span>
          <span>{{ t('performanceTesting.monitor.planned') }}: <b>{{ formatDuration(plannedDuration) }}</b></span>
          <span>{{ isK6 ? '脚本活动用户' : t('performanceTesting.metric.activeUsers') }}: <b>{{ latest.active_users ?? '-' }}</b></span>
        </div>
      </div>
      <el-alert
        v-if="execution.error_message"
        type="error"
        :closable="false"
        show-icon
        :title="execution.error_message"
        class="err-alert"
      />
      <el-alert
        v-else-if="abortNotice"
        type="warning"
        :closable="false"
        show-icon
        :title="abortNotice"
        class="err-alert"
      />
    </el-card>

    <!-- ---------- 指标卡 ---------- -->
    <MetricCards :items="metricItems" :cols="6" class="metric-row" />
    <template v-if="isK6">
      <p v-if="isActive" class="interface-note">最近完成秒桶：{{ latest.throughput?.latest_bucket_start_ms ? new Date(latest.throughput.latest_bucket_start_ms).toISOString() : '尚无可验证时间' }}（UTC，区间1秒；暂定值，不代表当前秒流量）。{{ latest.throughput?.reason || '' }}</p>
      <MetricCards :items="k6RequestCards" :cols="4" class="metric-row" />
      <WebSocketMetrics v-if="hasWebsocket" :metrics="displayedMetrics.websocket || null" :steps="execution.steps_snapshot || []" />
      <SseMetrics v-if="hasSse" :metrics="displayedMetrics.sse || null" :steps="execution.steps_snapshot || []" />
      <ExecutionPolicyMetrics :metrics="displayedMetrics.execution_policy || null" :steps="execution.steps_snapshot || []" />
      <ReminderRecoveryStatus :value="execution.resource_recovery" :loading="!monitorReady || recoveryLoading" @refresh="refreshRecovery" />
      <el-alert v-if="hasProtocolStreams" type="info" :closable="false" :title="t('performanceTesting.sse.mixedNotice')" />
      <el-alert v-else type="info" :closable="false" show-icon class="metric-row"
        title="已发起按 HTTP 调用开始计数，不保证服务端已收到；已完成以收到请求结果计数。运行中的未完成数包含在途请求，结束后可能包含被中止请求。业务统计排除前置登录，每个已完成业务请求只计一次成败。完整执行轮次不代表成功事务数。" />
      <el-alert v-if="hasProtocolStreams" type="info" :closable="false" :title="t('performanceTesting.sse.mixedRateNotice')" />
      <el-alert v-else type="info" :closable="false" show-icon class="metric-row"
        :title="`实时业务 RPS 为最近已观测完成秒桶的暂定请求数（UTC固定1秒桶），迟到事件回填原桶；响应时间反映采集窗口。结束后平均RPS按整场时长计算。P95 使用对应请求直方图估算，整场 P95 不是每秒分位数的平均。CPU：${cpuDescription}。单项资源指标不能独立证明发压器容量。尚未收到的计数显示暂无数据，字节数未采集。`" />
    </template>

    <el-card v-if="isK6" shadow="never" class="interface-card">
      <template #header>
        <div class="card-head">
          <strong>每个接口测了多少次</strong>
          <el-tag size="small" effect="plain">{{ isActive ? '采样累计' : '结束后汇总' }}</el-tag>
        </div>
      </template>
      <p class="interface-note">{{ interfaceStatsNotice }}</p>
      <el-table :data="k6InterfaceRows" class="k6-interface-table" border size="small" empty-text="尚未取得接口明细">
              <el-table-column prop="stepId" label="步骤 ID" width="105" />
        <el-table-column label="接口名称" min-width="190">
          <template #default="{ row }">
            {{ row.name }} · #{{ row.stepId }}
            <el-tag size="small" type="info">{{ K6_PHASE_LABELS[row.phase] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="API 地址" min-width="290">
          <template #default="{ row }"><code class="interface-path">{{ row.requestPath || '历史记录未保存地址' }}</code></template>
        </el-table-column>
        <el-table-column prop="method" label="方法" width="80" />
              <el-table-column v-if="hasProtocolStreams" :label="t('performanceTesting.websocket.protocolLatency')" min-width="180"><template #default="{ row }">{{ t(protocolLatencyKey(row.protocol)) }}</template></el-table-column>
        <el-table-column :label="hasProtocolStreams ? '步骤次数（已完成）' : '请求次数（已完成）'" min-width="140" align="right">
          <template #default="{ row }">{{ formatK6Count(row.total) }}</template>
        </el-table-column>
        <el-table-column label="成功次数" min-width="100" align="right">
          <template #default="{ row }"><span class="ok">{{ formatK6Count(row.success) }}</span></template>
        </el-table-column>
        <el-table-column label="失败次数" min-width="100" align="right">
          <template #default="{ row }"><strong :class="{ 'bad-val': row.failed > 0 }">{{ formatK6Count(row.failed) }}</strong></template>
        </el-table-column>
        <el-table-column prop="attribution" label="归属说明" min-width="150" />
              <el-table-column prop="avg_rt" :label="hasProtocolStreams ? '平均步骤耗时 ms' : '平均响应 ms'" width="120"><template #default="{ row }">{{ metricValue(row.avg_rt) }}</template></el-table-column>
              <el-table-column prop="p95_rt" label="累计 P95 ms（估算）" width="170"><template #default="{ row }">{{ metricValue(row.p95_rt) }}</template></el-table-column>
              <el-table-column prop="tps" :label="hasProtocolStreams ? businessRateLabel : '请求 RPS'" width="110"><template #default="{ row }">{{ metricValue(row.tps) }}</template></el-table-column>
              <el-table-column prop="errorRate" label="失败率" width="100" align="right" />
        <el-table-column prop="failureReason" label="失败原因与次数" min-width="290" />
      </el-table>
      <div class="k6-interface-cards">
        <article v-for="row in k6InterfaceRows" :key="row.key" class="k6-interface-item">
          <div class="interface-item-title"><strong>{{ row.name }} · #{{ row.stepId }}</strong><el-tag size="small" effect="plain">{{ row.method }}</el-tag></div>
          <p class="interface-path"><code>{{ row.requestPath || '历史记录未保存地址' }}</code></p>
          <el-tag size="small" type="info">{{ K6_PHASE_LABELS[row.phase] }}</el-tag>
          <p v-if="hasProtocolStreams">{{ t(protocolLatencyKey(row.protocol)) }}</p>
                <dl class="interface-counts">
            <div><dt>{{ hasProtocolStreams ? '步骤（已完成）' : '请求（已完成）' }}</dt><dd>{{ formatK6Count(row.total) }}</dd></div>
            <div><dt>平均 / P95估算（ms）</dt><dd>{{ metricValue(row.avg_rt) }} / {{ metricValue(row.p95_rt) }}</dd></div>
                  <div><dt>{{ hasProtocolStreams ? businessRateLabel : '请求 RPS' }}</dt><dd>{{ metricValue(row.tps) }}</dd></div>
                  <div><dt>成功次数</dt><dd :class="{ ok: row.success != null }">{{ formatK6Count(row.success) }}</dd></div>
            <div><dt>失败次数</dt><dd :class="{ 'bad-val': row.failed > 0 }">{{ formatK6Count(row.failed) }}</dd></div>
          </dl>
          <p v-if="row.attribution" class="interface-note">{{ row.attribution }}</p>
          <p class="interface-failure"><b>失败原因：</b>{{ row.failureReason }}</p>
        </article>
        <el-empty v-if="!k6InterfaceRows.length" description="尚未取得接口明细" :image-size="60" />
      </div>
      <p v-if="hasProtocolStreams" class="interface-note">{{ t('performanceTesting.sse.mixedNotice') }}</p>
      <p v-else class="interface-note">成功 + 失败 = 已完成请求；不含尚未取得结果的请求。前置登录单独标记，不计入上方业务总计。</p>
    </el-card>

    <!-- ---------- 实时曲线 ---------- -->
    <el-card shadow="never" class="chart-card">
      <template #header>
        <div class="card-head">
          <span>{{ isK6 ? (hasProtocolStreams ? '业务步骤/s、步骤耗时与脚本活动用户' : '业务 RPS、响应时间与脚本活动用户') : t('performanceTesting.monitor.chartTitle') }}</span>
          <span class="card-tip">{{ isK6 ? (hasProtocolStreams ? '按采样窗口展示已完成业务步骤' : '按采样窗口展示已完成业务请求') : t('performanceTesting.monitor.chartTip') }}</span>
        </div>
      </template>
      <RealtimeChart :key="chartGeneration" ref="chartRef" :height="320" :preserve-missing="isK6" :rate-label="isK6 ? businessRateLabel : 'TPS'" :active-users-label="isK6 ? '脚本活动用户' : '并发'" />
    </el-card>

    <NativeVUObservations v-if="isK6" :execution-id="executionId" :observations="nativeObservations"
      :summary="execution.summary?.native_vu" :configured-vus="execution.load_snapshot?.concurrency"
      :script-users="latest.active_users" />
    <ExecutionEvidence v-if="isK6" :execution="execution" />
    <el-row :gutter="12">
      <!-- ---------- SLA 阈值实时对照 ---------- -->
      <el-col :span="10">
        <el-card shadow="never" class="sla-card">
          <template #header>{{ t('performanceTesting.sla.title') }}</template>
          <el-table v-if="slaRows.length" :data="slaRows" size="small">
                <el-table-column label="范围" min-width="170"><template #default="{ row }">{{ slaScope(row) }}</template></el-table-column>
                <el-table-column prop="reason_label" label="原因" min-width="180" />
            <el-table-column prop="label" :label="t('performanceTesting.metric.tps')" min-width="140">
              <template #header>{{ t('performanceTesting.sla.threshold') }}</template>
              <template #default="{ row }">{{ row.label }}</template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.sla.threshold')" width="90" align="right">
              <template #default="{ row }">{{ row.comparator }} {{ row.threshold }} {{ row.unit }}</template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.sla.actual')" width="90" align="right">
              <template #default="{ row }">
                <span :class="{ 'bad-val': row.passed === false }">{{ metricValue(row.actual) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('performanceTesting.sla.result')" width="80" align="center">
              <template #default="{ row }">
                {{ slaResult(row) }}
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else :description="t('performanceTesting.sla.notEvaluatedTip')" :image-size="60" />
        </el-card>
      </el-col>

      <!-- ---------- 执行日志 ---------- -->
      <el-col :span="14">
        <el-card shadow="never" class="log-card">
          <template #header>
            <div class="card-head">
              <span>{{ t('performanceTesting.monitor.logTitle') }}</span>
              <el-button link :icon="Refresh" :disabled="!monitorReady" @click="fetchLog()">
                {{ t('performanceTesting.common.refresh') }}
              </el-button>
            </div>
          </template>
          <pre v-if="runLog" ref="logRef" class="log-pre">{{ runLog }}</pre>
          <el-empty v-else :description="t('performanceTesting.monitor.logEmpty')" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <!-- ---------- 接口明细（结束后） ---------- -->
    <el-card v-if="!isActive && !isK6 && requestStats.length" shadow="never" class="stat-card">
      <template #header>{{ t('performanceTesting.monitor.stepDetailTitle') }}</template>
      <el-table v-if="!isK6" :data="requestStats" size="small" border>
        <el-table-column prop="step_name" :label="t('performanceTesting.editor.stepName')" min-width="160" />
        <el-table-column prop="method" label="Method" width="80" />
        <el-table-column prop="total" :label="isK6 ? '已完成请求' : t('performanceTesting.metric.totalRequests')" width="110" align="right" />
        <el-table-column prop="failed" :label="t('performanceTesting.metric.failedRequests')" width="90" align="right" />
        <el-table-column :label="t('performanceTesting.metric.errorRate')" width="100" align="right">
          <template #default="{ row }">
            <span :class="{ 'bad-val': row.error_rate > 0 }">{{ row.error_rate }}%</span>
          </template>
        </el-table-column>
        <el-table-column prop="tps" :label="hasProtocolStreams ? businessRateLabel : isK6 ? '请求 RPS' : t('performanceTesting.metric.tps')" width="110" align="right" />
        <el-table-column prop="avg_rt" :label="t('performanceTesting.metric.avgRt')" width="110" align="right" />
        <el-table-column prop="p95_rt" :label="isK6 ? 'P95（估算）' : t('performanceTesting.metric.p95Rt')" width="110" align="right" />
        <el-table-column prop="max_rt" :label="t('performanceTesting.metric.maxRt')" width="110" align="right" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import ExecutionEvidence from './components/ExecutionEvidence.vue'
import { createSampleCursor, drainSamplePages } from './sampleCursor.mjs'
import { observedMetrics, metricValue, slaScope, slaResult } from './slaReport.mjs'
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, VideoPause, Document, Refresh
} from '@element-plus/icons-vue'

import MetricCards from './components/MetricCards.vue'
import WebSocketMetrics from './components/WebSocketMetrics.vue'
import { hasWebsocketSteps } from './websocketMetrics.mjs'
import { hasSseSteps, protocolLatencyKey } from './sseMetrics.mjs'
import SseMetrics from './components/SseMetrics.vue'
import ReminderRecoveryStatus from './components/ReminderRecoveryStatus.vue'
import ExecutionPolicyMetrics from './components/ExecutionPolicyMetrics.vue'
import RealtimeChart from './components/RealtimeChart.vue'
import NativeVUObservations from './components/NativeVUObservations.vue'
import { appendNativeVu } from './nativeVuObservations.mjs'
import { statusTagType, slaTagType, formatDuration, apiError } from './shared'
import { buildK6InterfaceRows, formatK6Count, K6_PHASE_LABELS } from './k6InterfaceStats.mjs'
import {
  getPerfExecution, getPerfRealtime, stopPerfExecution, getEngineStatus,
  getPerfRunLog, getPerfRequestStats, getPerfScenario
} from '@/api/performance-testing'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const ACTIVE_STATUSES = ['PENDING', 'PREPARING', 'RUNNING', 'STOPPING']
const executionId = computed(() => Number(route.params.id))

const loading = ref(false)
const loadError = ref('')
const stopping = ref(false)
const recoveryLoading = ref(false)
const loadedExecutionId = ref(null)
const emptyExecution = () => ({
  execution_no: '', scenario_name: '', status: '', sla_result: '',
  error_message: '', load_snapshot: {}, sla_detail: [], summary: {}, duration: 0, resource_recovery: null
})
const execution = reactive(emptyExecution())
const progress = ref(0)
const latest = ref({})
const nativeObservations = ref([])
const runLog = ref('')
const requestStats = ref([])
const requestStatsState = ref('pending')
const liveStepStats = ref([])
const stepSnapshotAt = ref(null)
const abortNotice = ref('')
const slaThresholds = ref({})
const chartRef = ref(null)
const chartGeneration = ref(0)
const logRef = ref(null)

// 'ws' | 'polling' | 'closed'
const channel = ref('polling')
let disposed = false
function createMonitorSession(id) {
  return {
    id, cursor: createSampleCursor(), socket: null, pollTimer: null, logTimer: null,
    wsRetryTimer: null, wsSilenceTimer: null, wsRetry: 0, wsGotMessage: false,
    polling: false, finished: false, logRequest: 0, statsRequest: 0, slaRequest: 0
  }
}
let session = createMonitorSession(executionId.value)
function ownsSession(owner) {
  return !disposed && owner === session && Object.is(owner.id, executionId.value)
}
const monitorReady = computed(() => !loading.value && !loadError.value
  && Number.isSafeInteger(executionId.value) && executionId.value > 0 && loadedExecutionId.value === executionId.value)

const isActive = computed(() => ACTIVE_STATUSES.includes(execution.status))
const hasWebsocket = computed(() => hasWebsocketSteps(execution.steps_snapshot || []) || Boolean(execution.summary?.websocket))
const hasSse = computed(() => hasSseSteps(execution.steps_snapshot || []) || Boolean(execution.summary?.sse))
const hasProtocolStreams = computed(() => hasWebsocket.value || hasSse.value)
const businessRateLabel = computed(() => hasProtocolStreams.value ? t('performanceTesting.websocket.mixedRate') : '业务 RPS')
const isK6 = computed(() => execution.load_snapshot?._engine === 'K6'
  || (!execution.load_snapshot?._engine && execution.summary?.http_total != null
    && execution.summary?.business_total != null))
const displayedMetrics = computed(() => isActive.value
  ? { ...(execution.summary || {}), ...latest.value }
  : { ...latest.value, ...(execution.summary || {}) })
const cpuDescription = computed(() => {
  const description = execution.summary?.metric_semantics?.cpu_percent
  return typeof description === 'string' && description.trim() ? description.trim() : '发压资源见指标口径'
})
const k6InterfaceRows = computed(() => buildK6InterfaceRows({
  stats: isActive.value ? liveStepStats.value : execution.summary?.step_metrics || requestStats.value,
  steps: execution.steps_snapshot || [], errorTop: execution.summary?.error_top || [], live: isActive.value
}))
const interfaceStatsNotice = computed(() => {
  if (isActive.value && latest.value.steps_truncated) return `采样仅展示前 ${liveStepStats.value.length} / ${latest.value.steps_total} 个接口，全局请求计数保持完整。`
  if (isActive.value) return stepSnapshotAt.value == null
    ? '等待每接口的首次采样，暂无数据不代表 0 次。失败原因将在任务结束后汇总。'
    : `截至运行第 ${Number(stepSnapshotAt.value).toFixed(1)} 秒的累计采样；收到新采样时更新，失败原因在结束后汇总。`
  if (requestStatsState.value === 'error') return '接口明细读取失败，请刷新页面重试；暂无数据不代表 0 次。'
  if (requestStatsState.value === 'pending') return '正在读取结束后的接口汇总。'
  return '任务已结束：以下为本次执行保存的接口汇总。停止或异常结束时，只统计已取得结果的请求。'
})
const k6RequestCards = computed(() => {
  const s = displayedMetrics.value
  return [
    ['http_started', '已发起 HTTP 请求'], ['http_total', '已完成 HTTP 请求（含登录）'],
    ['http_incomplete', isActive.value ? '未完成 HTTP（含在途）' : '未完成 HTTP 请求'],
    ['failed_requests', '业务失败请求数'],
    ['business_started', '已发起业务请求'], ['business_total', '已完成业务请求'],
    ['business_incomplete', isActive.value ? '未完成业务（含在途）' : '未完成业务请求'],
    ['completed_iterations', '业务完成轮次'], ['executor_iterations', '执行器完成轮次'], ['idle_iterations', '空轮次']
  ].map(([key, label]) => ({
    key, label: hasProtocolStreams.value && (key.startsWith('business_') || key === 'failed_requests') ? label.replace('请求', '步骤') : label, value: (key === 'business_total' ? s.business_total ?? s.total_requests : s[key]) ?? '暂无数据',
    status: !isActive.value && key.endsWith('_incomplete') && s[key] > 0 ? 'warn' : 'normal'
  }))
})
const plannedDuration = computed(() => {
  // build_snapshot 会把算好的总时长写进 _planned_duration，比自己按模型再算一遍可靠
  const snap = execution.load_snapshot || {}
  return Number(snap._planned_duration || snap.duration || 0)
})
const elapsed = computed(() => {
  if (execution.duration) return Math.round(execution.duration)
  return Math.round(latest.value.ts_offset || 0)
})
const elapsedText = computed(() => isK6.value && Number(execution.duration) > 0 && Number(execution.duration) < 1
  ? `${Number(execution.duration).toFixed(2)} 秒`
  : formatDuration(elapsed.value))
const progressStatus = computed(() => {
  if (execution.status === 'COMPLETED') return 'success'
  if (['FAILED', 'TIMEOUT'].includes(execution.status)) return 'exception'
  if (execution.status === 'STOPPED') return 'warning'
  return ''
})
const channelText = computed(() => {
  if (channel.value === 'ws') return t('performanceTesting.monitor.connWs')
  if (channel.value === 'polling') return t('performanceTesting.monitor.connPolling')
  return t('performanceTesting.monitor.connClosed')
})

function num(v, digits = 1) {
  return metricValue(v, digits)
}

const metricItems = computed(() => {
  const s = observedMetrics(displayedMetrics.value, isK6.value)
  const errRate = s.error_rate
  const cpu = isActive.value ? s.cpu_percent : s.peak_load_gen_cpu
  const cpuAvailable = !isK6.value || (cpu != null && (Number(cpu) > 0 || Number(s.cpu_sample_count) > 0 || s.cpu_sampled === true))
  return [
    { label: isK6.value ? (hasProtocolStreams.value ? `${businessRateLabel.value}${isActive.value ? '（最近完成秒桶，暂定）' : '（平均）'}` : (isActive.value ? '最近完成秒桶 RPS（暂定）' : '平均业务 RPS')) : t('performanceTesting.metric.tps'), value: num(s.tps), status: 'normal' },
    { label: isK6.value ? (hasProtocolStreams.value ? t('performanceTesting.websocket.mixedLatency') : '平均业务响应') : t('performanceTesting.metric.avgRt'), value: num(s.avg_rt), unit: 'ms', status: 'normal' },
    { label: isK6.value ? (isActive.value ? '窗口 P95（估算）' : '累计 P95（估算）') : t('performanceTesting.metric.p95Rt'), value: num(s.p95_rt), unit: 'ms', status: 'normal' },
    {
      label: isK6.value ? '业务失败率' : t('performanceTesting.metric.errorRate'),
      value: num(errRate, 2),
      unit: '%',
      status: errRate == null ? 'normal' : Number(errRate || 0) > 1 ? 'bad' : (Number(errRate || 0) > 0 ? 'warn' : 'good')
    },
    {
      label: isK6.value ? (hasProtocolStreams.value ? t('performanceTesting.websocket.mixedCompleted') : '已完成业务请求') : t('performanceTesting.metric.totalRequests'),
      value: s.total_requests ?? '-',
      status: 'normal'
    },
    { label: isK6.value ? (isActive.value ? '发压 CPU（当前）' : '发压 CPU 峰值') : (!isActive.value ? '压力机 CPU 峰值' : t('performanceTesting.metric.cpu')), value: cpuAvailable ? num(cpu) : '暂无数据', unit: cpuAvailable ? '%' : '', status: 'normal' }
  ]
})

const slaRows = computed(() => {
  const detail = execution.sla_detail || []
  if (detail.length) {
    return detail.map(d => ({
      ...d,
      label: isK6.value ? (d.label || '').replace(/TPS/g, businessRateLabel.value) : d.label,
      threshold: d.threshold, actual: d.actual, passed: d.passed
    }))
  }
  if (isK6.value) return [] // Frozen configuration is shown above; only the worker decides cumulative SLA.
  // 运行中还没有终局判定（sla_detail 在压测结束才回写），
  // 这里用场景阈值 + 当前采样做实时对照，让用户压到一半就能看出要不要停
  const thresholds = slaThresholds.value
  if (!thresholds || !Object.keys(thresholds).length) return []
  const map = {
    avg_response_time: ['avg_rt', 'max', t('performanceTesting.metric.avgRt')],
    p90_response_time: ['p90_rt', 'max', t('performanceTesting.metric.p90Rt')],
    p95_response_time: ['p95_rt', 'max', t('performanceTesting.metric.p95Rt')],
    p99_response_time: ['p99_rt', 'max', t('performanceTesting.metric.p99Rt')],
    error_rate: ['error_rate', 'max', t('performanceTesting.metric.errorRate')],
    min_tps: ['tps', 'min', isK6.value ? businessRateLabel.value : t('performanceTesting.metric.tps')]
  }
  const s = latest.value || {}
  return Object.entries(thresholds).map(([key, threshold]) => {
    const meta = map[key]
    if (!meta) return null
    const [field, direction, label] = meta
    const raw = s[field] ?? (execution.summary || {})[field]
    const actual = raw == null ? null : Number(raw)
    return {
      label,
      threshold,
      actual: actual == null ? null : actual.toFixed(2),
      passed: actual == null ? null : direction === 'max' ? actual <= Number(threshold) : actual >= Number(threshold)
    }
  }).filter(Boolean)
})

// ------------------------------------------------------------------ //
// 数据加载
// ------------------------------------------------------------------ //
function applyExecution(data) {
  Object.assign(execution, data)
  progress.value = data.progress || 0
  if (data.summary && Object.keys(data.summary).length) {
    latest.value = { ...latest.value, ...data.summary }
  }
}

async function fetchExecution(owner = session) {
  if (!ownsSession(owner)) return
  const res = await getPerfExecution(owner.id)
  if (!ownsSession(owner)) return
  if (res.data?.id != null && Number(res.data.id) !== owner.id) throw new Error('执行 ID 与当前监控页面不一致，请重试')
  applyExecution(res.data)
  loadedExecutionId.value = owner.id
  return res.data
}

async function refreshRecovery() {
  const owner = session
  if (!ownsSession(owner) || !monitorReady.value || recoveryLoading.value) return
  recoveryLoading.value = true
  try {
    const { data } = await getPerfExecution(owner.id)
    if (!ownsSession(owner)) return
    if (Number(data?.id) !== owner.id) throw new Error('执行 ID 与当前监控页面不一致')
    execution.resource_recovery = data.resource_recovery ?? null
  } catch (error) {
    if (ownsSession(owner)) { execution.resource_recovery = null; ElMessage.error(apiError(error, t('performanceTesting.common.empty'))) }
  } finally {
    if (ownsSession(owner)) recoveryLoading.value = false
  }
}

async function fetchSlaThresholds(scenarioId, owner = session) {
  if (!ownsSession(owner) || isK6.value || !scenarioId) return
  const requestId = ++owner.slaRequest
  try {
    const { data: scenario } = await getPerfScenario(scenarioId)
    if (!ownsSession(owner) || requestId !== owner.slaRequest) return
    slaThresholds.value = (scenario.sla_config || {}).enabled
      ? (scenario.sla_config.thresholds || {})
      : {}
  } catch (e) {
    if (ownsSession(owner) && requestId === owner.slaRequest) slaThresholds.value = {}
  }
}

async function fetchLog(owner = session) {
  if (!ownsSession(owner)) return
  const requestId = ++owner.logRequest
  try {
    const res = await getPerfRunLog(owner.id, { lines: 400 })
    if (!ownsSession(owner) || requestId !== owner.logRequest) return
    runLog.value = res.data?.content || ''
    await nextTick()
    if (ownsSession(owner) && requestId === owner.logRequest && logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight
  } catch (e) {
    /* 日志文件可能尚未创建，静默 */
  }
}

async function fetchRequestStats(owner = session) {
  if (!ownsSession(owner)) return
  const requestId = ++owner.statsRequest
  requestStatsState.value = 'pending'
  try {
    const res = await getPerfRequestStats(owner.id)
    if (!ownsSession(owner) || requestId !== owner.statsRequest) return
    requestStats.value = res.data || []
    requestStatsState.value = 'loaded'
  } catch (e) {
    if (!ownsSession(owner) || requestId !== owner.statsRequest) return
    requestStats.value = []
    requestStatsState.value = 'error'
  }
}

function pushSample(sample, owner = session) {
  if (!ownsSession(owner) || !sample) return
  if (Array.isArray(sample.native_vu_observations) && sample.native_vu_observations.length) {
    nativeObservations.value = appendNativeVu(nativeObservations.value, sample.native_vu_observations, owner.id)
  }
  if (Array.isArray(sample.steps)) {
    liveStepStats.value = sample.steps
    stepSnapshotAt.value = sample.elapsed_seconds ?? sample.ts_offset ?? null
  }
  latest.value = { ...latest.value, ...sample }
  chartRef.value?.push(observedMetrics(sample, isK6.value))
  if (plannedDuration.value > 0 && sample.ts_offset != null) {
    progress.value = Math.min(100, (sample.ts_offset / plannedDuration.value) * 100)
  }
}

// ------------------------------------------------------------------ //
// WebSocket
// ------------------------------------------------------------------ //
function connectWs(owner = session) {
  if (!ownsSession(owner) || owner.finished) return
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${window.location.host}/ws/perf-testing/executions/${owner.id}/`
  let ws
  try {
    ws = new WebSocket(url)
    owner.socket = ws
  } catch (e) {
    startPolling(owner)
    return
  }
  const isCurrent = () => ownsSession(owner) && owner.socket === ws && !owner.finished

  // 看门狗：WS 若能连上但一直收不到任何数据（典型如 Redis/channels 实际不可用，
  // executor 推送被熔断，WS 显示"已连接"却不推送），则主动降级到轮询。
  function startWsWatchdog() {
    if (!isCurrent()) return
    if (owner.wsSilenceTimer) clearTimeout(owner.wsSilenceTimer)
    owner.wsSilenceTimer = setTimeout(() => {
      owner.wsSilenceTimer = null
      if (!isCurrent() || channel.value !== 'ws') return
      if (owner.wsGotMessage) {
        // 最近有过数据，重置计时继续观察
        owner.wsGotMessage = false
        startWsWatchdog()
      } else {
        // 6 秒一条都没收到 → 视为假连接，关掉让 onclose 走降级轮询
        try { ws.close() } catch (e) { /* ignore */ }
      }
    }, 6000)
  }

  ws.onopen = () => {
    if (!isCurrent()) return
    owner.wsRetry = 0
    owner.wsGotMessage = false
    channel.value = 'ws'
    stopPolling(owner)
    startWsWatchdog()
  }

  ws.onmessage = (event) => {
    if (!isCurrent()) return
    owner.wsGotMessage = true
    startWsWatchdog()
    let data
    try {
      data = JSON.parse(event.data)
    } catch (e) {
      return
    }
    if (data.status) execution.status = data.status
    if (data.progress != null) progress.value = data.progress
    if (data.sla_result) execution.sla_result = data.sla_result
    if (Object.hasOwn(data, 'resource_recovery')) execution.resource_recovery = data.resource_recovery
    if (data.summary && Object.keys(data.summary).length) {
      execution.summary = data.summary
      latest.value = { ...latest.value, ...data.summary }
    }
    if (data.sample) pushSample(data.sample, owner)
    if (data.message && data.status === 'STOPPING') abortNotice.value = data.message
    if (!ACTIVE_STATUSES.includes(data.status || execution.status)) onFinished(owner)
  }

  ws.onerror = () => {
    // 出错时不立刻放弃：先重试两次，再降级轮询
    if (isCurrent()) {
      try { ws.close() } catch (e) { /* ignore */ }
    }
  }

  ws.onclose = () => {
    if (!isCurrent()) return
    if (owner.wsSilenceTimer) { clearTimeout(owner.wsSilenceTimer); owner.wsSilenceTimer = null }
    owner.socket = null
    owner.wsRetry += 1
    if (owner.wsRetry <= 2) {
      owner.wsRetryTimer = setTimeout(() => {
        owner.wsRetryTimer = null
        if (ownsSession(owner) && !owner.finished) connectWs(owner)
      }, owner.wsRetry * 1000)
    } else {
      startPolling(owner)
    }
  }
}

// ------------------------------------------------------------------ //
// 轮询降级
// ------------------------------------------------------------------ //
function startPolling(owner = session) {
  if (!ownsSession(owner) || owner.pollTimer || owner.finished) return
  channel.value = 'polling'
  pollOnce(owner)
  owner.pollTimer = setInterval(() => pollOnce(owner), 2000)
}

function stopPolling(owner = session) {
  if (owner.pollTimer) {
    clearInterval(owner.pollTimer)
    owner.pollTimer = null
  }
}

async function drainSamples(owner = session) {
  return drainSamplePages(async params => {
    if (!ownsSession(owner)) throw new Error('监控页面已切换')
    const { data } = await getPerfRealtime(owner.id, params)
    if (!ownsSession(owner)) throw new Error('监控页面已切换')
    return data
  }, owner.cursor, sample => pushSample(sample, owner))
}

async function pollOnce(owner = session) {
  if (!ownsSession(owner) || owner.polling || owner.finished) return
  owner.polling = true
  try {
    const data = await drainSamples(owner)
    if (!ownsSession(owner) || owner.finished) return
    execution.status = data.status
    progress.value = data.progress || progress.value
    execution.sla_result = data.sla_result
    execution.error_message = data.error_message || ''
    if (Object.hasOwn(data, 'resource_recovery')) execution.resource_recovery = data.resource_recovery
    if (data.summary && Object.keys(data.summary).length) execution.summary = data.summary
    if (!ACTIVE_STATUSES.includes(data.status)) await onFinished(owner)
  } catch (e) {
    /* 网络抖动不打断轮询；游标只越过已收到的页。 */
  } finally {
    owner.polling = false
  }
}

// ------------------------------------------------------------------ //
// 收尾
// ------------------------------------------------------------------ //
function stopChannels(owner) {
  stopPolling(owner)
  if (owner.logTimer) { clearInterval(owner.logTimer); owner.logTimer = null }
  if (owner.wsSilenceTimer) { clearTimeout(owner.wsSilenceTimer); owner.wsSilenceTimer = null }
  if (owner.wsRetryTimer) { clearTimeout(owner.wsRetryTimer); owner.wsRetryTimer = null }
  const ws = owner.socket
  owner.socket = null
  if (ws) { try { ws.close() } catch (e) { /* ignore */ } }
}

async function onFinished(owner = session) {
  if (!ownsSession(owner) || owner.finished) return
  owner.finished = true
  stopChannels(owner)
  channel.value = 'closed'
  try {
    await fetchExecution(owner)
    if (!ownsSession(owner)) return
    await Promise.all([fetchLog(owner), fetchRequestStats(owner)])
    if (!ownsSession(owner)) return
    if (execution.sla_result === 'FAILED') {
      ElMessage.warning(t('performanceTesting.monitor.finished'))
    } else {
      ElMessage.success(t('performanceTesting.monitor.finished'))
    }
  } catch (e) {
    if (ownsSession(owner)) loadError.value = apiError(e, t('performanceTesting.common.empty'))
  }
}

async function handleStop() {
  const owner = session
  if (!ownsSession(owner) || !monitorReady.value || !isActive.value || stopping.value) return
  stopping.value = true
  try {
    await ElMessageBox.confirm(
      t('performanceTesting.execution.stopConfirm'),
      t('performanceTesting.execution.stop'),
      { type: 'warning' }
    )
    if (!ownsSession(owner) || !monitorReady.value || !isActive.value) return
    try {
      await stopPerfExecution(owner.id)
      if (ownsSession(owner)) ElMessage.success(t('performanceTesting.execution.stopped'))
    } catch (e) {
      if (ownsSession(owner)) ElMessage.error(apiError(e, t('performanceTesting.execution.stop')))
    }
  } catch (e) {
    /* 用户取消停止确认。 */
  } finally {
    if (ownsSession(owner)) stopping.value = false
  }
}

function goBack() {
  router.push('/performance-testing/executions')
}
function goReport() {
  if (ownsSession(session) && monitorReady.value) router.push(`/performance-testing/executions/${session.id}`)
}

// ------------------------------------------------------------------ //
// 生命周期
// ------------------------------------------------------------------ //
async function loadMonitor() {
  if (disposed) return
  session.finished = true
  stopChannels(session)
  const owner = createMonitorSession(executionId.value)
  session = owner
  loading.value = true
  loadError.value = ''
  stopping.value = false
  recoveryLoading.value = false
  loadedExecutionId.value = null
  Object.keys(execution).forEach(key => delete execution[key])
  Object.assign(execution, emptyExecution())
  progress.value = 0
  latest.value = {}
  nativeObservations.value = []
  runLog.value = ''
  requestStats.value = []
  requestStatsState.value = 'pending'
  liveStepStats.value = []
  stepSnapshotAt.value = null
  abortNotice.value = ''
  slaThresholds.value = {}
  channel.value = 'closed'
  chartRef.value?.reset()
  ++chartGeneration.value
  try {
    if (!Number.isSafeInteger(owner.id) || owner.id <= 0) throw new Error('无效的执行 ID')
    const data = await fetchExecution(owner)
    if (!ownsSession(owner)) return
    if (!ACTIVE_STATUSES.includes(data.status)) {
      // 已结束：只做一次历史回放，不建实时通道
      owner.finished = true
      channel.value = 'closed'
      await drainSamples(owner)
      if (!ownsSession(owner)) return
      progress.value = 100
      await Promise.all([fetchLog(owner), fetchRequestStats(owner)])
      return
    }
    // 先把已有采样点补齐，避免中途进来只看到后半段曲线
    const initial = await drainSamples(owner)
    if (!ownsSession(owner)) return
    if (initial.status && !ACTIVE_STATUSES.includes(initial.status)) {
      await onFinished(owner)
      return
    }
    fetchSlaThresholds(data.scenario, owner)

    let wsOk = false
    try {
      const { data: status } = await getEngineStatus()
      wsOk = !!status.websocket
    } catch (e) {
      wsOk = false
    }
    if (!ownsSession(owner)) return
    if (wsOk && !isK6.value) connectWs(owner)
    else startPolling(owner)

    fetchLog(owner)
    owner.logTimer = setInterval(() => fetchLog(owner), 8000)
  } catch (e) {
    if (ownsSession(owner)) {
      stopChannels(owner)
      channel.value = 'closed'
      loadError.value = apiError(e, t('performanceTesting.common.empty'))
    }
  } finally {
    if (ownsSession(owner)) loading.value = false
  }
}

watch(executionId, loadMonitor, { flush: 'sync' })
onMounted(loadMonitor)

onBeforeUnmount(() => {
  disposed = true
  session.finished = true
  stopChannels(session)
})
</script>

<style lang="scss" scoped>
.perf-monitor { padding: 16px; }

.monitor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #fff;
  border-radius: 6px;

  .head-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .head-right { display: flex; align-items: center; gap: 12px; }
  .exec-no { font-weight: 600; color: #303133; font-family: Menlo, Consolas, monospace; }
  .scenario-name { color: #909399; font-size: 13px; }
}

.conn-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #909399;
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #c0c4cc; }
  &.ws .dot { background: #52c41a; box-shadow: 0 0 0 3px rgba(82, 196, 26, 0.15); }
  &.polling .dot { background: #faad14; box-shadow: 0 0 0 3px rgba(250, 173, 20, 0.15); }
  &.closed .dot { background: #c0c4cc; }
}

.progress-card {
  margin-bottom: 12px;
  :deep(.el-card__body) { padding: 14px; }
  .progress-row { display: flex; align-items: center; gap: 20px; }
  .main-progress { flex: 1; }
  .progress-meta {
    display: flex;
    gap: 18px;
    font-size: 13px;
    color: #909399;
    white-space: nowrap;
    b { color: #303133; }
  }
  .err-alert { margin-top: 10px; }
}

.metric-row { margin-bottom: 12px; }
.chart-card { margin-bottom: 12px; }
.card-head { display: flex; align-items: center; justify-content: space-between; }
.card-tip { font-size: 12px; color: #c0c4cc; font-weight: 400; }

.sla-card, .log-card { height: 320px; :deep(.el-card__body) { padding: 8px 12px; overflow: auto; height: 250px; } }
.ok { color: #52c41a; }
.bad { color: #f5222d; }
.bad-val { color: #f5222d; font-weight: 600; }

.log-pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow: auto;
}

.stat-card { margin-top: 12px; }
.interface-card { margin-bottom: 12px; }
.interface-path { overflow-wrap: anywhere; white-space: normal; user-select: text; }
.interface-note { margin: 0 0 12px; color: #606266; font-size: 13px; line-height: 1.6; }
.interface-note:last-child { margin: 10px 0 0; }
.k6-interface-cards { display: none; }
@media (max-width: 900px) {
  .monitor-head {
    flex-wrap: wrap;
    .head-left { flex: 1 1 100%; flex-wrap: wrap; }
    .head-right { flex: 1 1 100%; flex-wrap: wrap; }
    .exec-no, .scenario-name { overflow-wrap: anywhere; }
    .head-right :deep(.el-button + .el-button) { margin-left: 0; }
  }
  .k6-interface-table { display: none; }
  .k6-interface-cards { display: grid; gap: 12px; }
  .k6-interface-item { border: 1px solid #e4e7ed; border-radius: 6px; padding: 12px; min-width: 0; }
  .interface-item-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; overflow-wrap: anywhere; }
  .interface-counts {
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 12px 0;
    dt { font-size: 12px; color: #606266; }
    dd { margin: 4px 0 0; font-size: 18px; font-weight: 600; overflow-wrap: anywhere; }
  }
  .interface-failure { margin: 0; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
}
</style>
