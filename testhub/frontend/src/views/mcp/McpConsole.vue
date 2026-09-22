<template>
  <div class="mcp-console">
    <el-alert
      v-if="showGuide"
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    >
      <template #title>{{ $t('mcp.guideTitle') }}</template>
      <span>{{ $t('mcp.guideDesc') }}</span>
    </el-alert>
    <el-tabs v-model="activeTab" @tab-change="onTabChange">
      <!-- 工具目录 -->
      <el-tab-pane :label="$t('mcp.tabTools')" name="tools">
        <div class="tool-summary">
          <div class="summary-card">
            <div class="summary-num">{{ toolsSummary.total || 0 }}</div>
            <div class="summary-label">{{ $t('mcp.toolsTotal') }}</div>
          </div>
          <div class="summary-card">
            <div class="summary-num text-success">{{ toolsSummary.by_category?.read || 0 }}</div>
            <div class="summary-label">{{ $t('mcp.toolsRead') }}</div>
          </div>
          <div class="summary-card">
            <div class="summary-num text-danger">{{ (toolsSummary.by_category?.preview || 0) + (toolsSummary.by_category?.confirm || 0) }}</div>
            <div class="summary-label">{{ $t('mcp.toolsDanger') }}</div>
          </div>
          <div class="summary-card">
            <div class="summary-num">{{ toolsSummary.calls_7d || 0 }}</div>
            <div class="summary-label">{{ $t('mcp.toolsCalls7d') }}</div>
          </div>
        </div>
        <div class="filter-bar">
          <el-select v-model="toolFilter.category" :placeholder="$t('mcp.filterCategory')" clearable style="width: 150px">
            <el-option v-for="c in categoryOptions" :key="c.value" :label="c.label" :value="c.value" />
          </el-select>
          <el-select v-model="toolFilter.domain" :placeholder="$t('mcp.filterDomain')" clearable style="width: 150px">
            <el-option v-for="d in domainOptions" :key="d.value" :label="d.label" :value="d.value" />
          </el-select>
          <el-input v-model="toolFilter.keyword" :placeholder="$t('mcp.searchTool')" clearable style="width: 220px" />
          <el-button :icon="Refresh" @click="loadTools">{{ $t('common.refresh') }}</el-button>
          <el-button type="primary" :icon="Connection" style="margin-left: auto" @click="openConfig">
            {{ $t('mcp.configBtn') }}
          </el-button>
        </div>
        <el-table :data="filteredTools" v-loading="toolsLoading" stripe>
          <el-table-column :label="$t('mcp.colTool')" width="210">
            <template #default="{ row }"><span class="mono">{{ row.name }}</span></template>
          </el-table-column>
          <el-table-column prop="title" :label="$t('mcp.colTitle')" width="140" />
          <el-table-column :label="$t('mcp.colCategory')" width="110">
            <template #default="{ row }">
              <el-tag :type="categoryTagType(row.category)" size="small">{{ categoryLabel(row.category) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('mcp.colDomain')" width="110">
            <template #default="{ row }">
              <el-tag type="info" size="small" effect="plain">{{ domainLabel(row.domain) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="summary" :label="$t('mcp.colSummary')" min-width="260" show-overflow-tooltip />
          <el-table-column :label="$t('mcp.colCalls')" width="100" align="center">
            <template #default="{ row }">{{ row.stats?.calls || 0 }}</template>
          </el-table-column>
          <el-table-column :label="$t('common.actions')" width="80" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openToolDetail(row)">{{ $t('mcp.detail') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 待确认操作 -->
      <el-tab-pane :label="pendingLabel" name="pending">
        <div class="filter-bar">
          <el-select v-model="pendingFilter.status" :placeholder="$t('mcp.filterStatus')" clearable style="width: 160px" @change="loadPending">
            <el-option v-for="s in statusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
          <el-button :icon="Refresh" @click="loadPending">{{ $t('common.refresh') }}</el-button>
        </div>
        <el-table :data="pendingList" v-loading="pendingLoading" stripe>
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" :label="$t('mcp.colUser')" width="100" />
          <el-table-column prop="tool_name" :label="$t('mcp.colTool')" width="160" show-overflow-tooltip />
          <el-table-column prop="preview" :label="$t('mcp.colPreview')" min-width="240" show-overflow-tooltip />
          <el-table-column :label="$t('mcp.colArguments')" width="200">
            <template #default="{ row }">
              <span class="mono">{{ formatArgs(row.arguments) }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('mcp.colStatus')" width="130">
            <template #default="{ row }">
              <el-tag :type="pendingStatusType(row.status)" size="small">{{ pendingStatusLabel(row.status) }}</el-tag>
              <el-tag v-if="row.status === 'pending' && row.awaiting_human" type="danger" size="small" style="margin-left: 4px">{{ $t('mcp.statusAwaiting') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="expires_at" :label="$t('mcp.colExpires')" width="180">
            <template #default="{ row }">
              {{ formatTime(row.expires_at) }}
              <div v-if="row.status === 'pending'" class="mono countdown" :class="{ expired: remainingMs(row) <= 0 }">
                {{ countdownText(row) }}
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" :label="$t('mcp.colCreated')" width="160">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column :label="$t('common.actions')" width="140" fixed="right">
            <template #default="{ row }">
              <template v-if="row.status === 'pending'">
                <el-button link type="success" size="small" @click="doApprove(row)">{{ $t('mcp.approve') }}</el-button>
                <el-button link type="danger" size="small" @click="doReject(row)">{{ $t('mcp.reject') }}</el-button>
              </template>
              <span v-else class="mono">{{ formatResult(row.result) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-if="pendingTotal > pendingPageSize"
          v-model:current-page="pendingPage"
          :page-size="pendingPageSize"
          :total="pendingTotal"
          layout="prev, pager, next, total"
          @current-change="loadPending"
          style="margin-top: 12px; justify-content: flex-end"
        />
      </el-tab-pane>

      <!-- 调用日志 -->
      <el-tab-pane :label="$t('mcp.tabLogs')" name="logs">
        <div class="filter-bar">
          <el-input v-model="logFilter.tool" :placeholder="$t('mcp.filterTool')" clearable style="width: 200px" @keyup.enter="loadLogs" />
          <el-select v-model="logFilter.status" :placeholder="$t('mcp.filterStatus')" clearable style="width: 140px" @change="loadLogs">
            <el-option v-for="s in logStatusOptions" :key="s.value" :label="s.label" :value="s.value" />
          </el-select>
          <el-date-picker
            v-model="logFilter.range"
            type="datetimerange"
            :start-placeholder="$t('mcp.timeStart')"
            :end-placeholder="$t('mcp.timeEnd')"
            style="width: 340px"
            @change="loadLogs"
          />
          <el-button :icon="Refresh" @click="loadLogs">{{ $t('common.refresh') }}</el-button>
        </div>
        <el-table :data="logList" v-loading="logLoading" stripe>
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" :label="$t('mcp.colUser')" width="100" />
          <el-table-column prop="tool_name" :label="$t('mcp.colTool')" width="160" show-overflow-tooltip />
          <el-table-column prop="client_name" :label="$t('mcp.colClient')" width="140" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.client_name || '-' }}</span></template>
          </el-table-column>
          <el-table-column prop="args_brief" :label="$t('mcp.colArgsBrief')" min-width="220" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono">{{ row.args_brief || row.args_digest || '-' }}</span></template>
          </el-table-column>
          <el-table-column :label="$t('mcp.colResult')" width="80">
            <template #default="{ row }">
              <el-tag :type="logStatusType(row.status)" size="small">{{ logStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="duration_ms" :label="$t('mcp.colDuration')" width="100">
            <template #default="{ row }">{{ row.duration_ms }}ms</template>
          </el-table-column>
          <el-table-column prop="error" :label="$t('mcp.colError')" min-width="200" show-overflow-tooltip />
          <el-table-column prop="created_at" :label="$t('mcp.colCreated')" width="160">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-if="logTotal > logPageSize"
          v-model:current-page="logPage"
          :page-size="logPageSize"
          :total="logTotal"
          layout="prev, pager, next, total"
          @current-change="loadLogs"
          style="margin-top: 12px; justify-content: flex-end"
        />
      </el-tab-pane>
    </el-tabs>

    <!-- 工具详情抽屉 -->
    <el-drawer v-model="detailVisible" :title="detail ? `${detail.title} (${detail.name})` : $t('mcp.toolDetail')" size="600px">
      <template v-if="detail">
        <div class="detail-section">
          <div class="detail-head">{{ $t('mcp.sectionDesc') }}</div>
          <p class="detail-desc">{{ detail.description }}</p>
          <el-alert v-if="detail.paired_with" type="warning" :closable="false" style="margin-top: 8px">
            <span class="mono">{{ $t('mcp.pairedHint', { tool: detail.paired_with }) }}</span>
          </el-alert>
        </div>
        <div class="detail-section">
          <div class="detail-head">{{ $t('mcp.sectionParams') }}</div>
          <el-table v-if="detailParams.length" :data="detailParams" size="small" border>
            <el-table-column :label="$t('mcp.paramName')" width="140">
              <template #default="{ row }"><span class="mono">{{ row.name }}</span></template>
            </el-table-column>
            <el-table-column :label="$t('mcp.paramType')" width="90">
              <template #default="{ row }"><span class="mono">{{ row.type }}</span></template>
            </el-table-column>
            <el-table-column prop="required" :label="$t('mcp.paramRequired')" width="70" />
            <el-table-column prop="desc" :label="$t('mcp.paramDesc')" />
          </el-table>
          <el-empty v-else :description="$t('mcp.noParams')" :image-size="40" />
        </div>
        <div v-if="detailExamples.length" class="detail-section">
          <div class="detail-head">{{ $t('mcp.sectionExamples') }}</div>
          <pre v-for="(ex, i) in detailExamples" :key="i" class="example-block">{{ ex }}</pre>
        </div>
        <div class="detail-section">
          <div class="detail-head">{{ $t('mcp.sectionAnnotations') }}</div>
          <div class="anno-tags">
            <el-tag v-for="(v, k) in detail.annotations" :key="k" :type="v ? 'success' : 'info'" effect="plain" size="small">
              {{ k }} = {{ v }}
            </el-tag>
          </div>
        </div>
        <div class="detail-section">
          <div class="detail-head">{{ $t('mcp.sectionStats') }}</div>
          <div class="tool-summary">
            <div class="summary-card">
              <div class="summary-num">{{ detail.stats?.calls || 0 }}</div>
              <div class="summary-label">{{ $t('mcp.statCalls') }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-num">{{ detail.stats?.success_rate != null ? (detail.stats.success_rate * 100).toFixed(1) + '%' : '-' }}</div>
              <div class="summary-label">{{ $t('mcp.statSuccessRate') }}</div>
            </div>
            <div class="summary-card">
              <div class="summary-num">{{ detail.stats?.avg_duration_ms != null ? detail.stats.avg_duration_ms + 'ms' : '-' }}</div>
              <div class="summary-label">{{ $t('mcp.statAvgDuration') }}</div>
            </div>
          </div>
        </div>
      </template>
    </el-drawer>

    <!-- 接入配置弹窗：配置 JSON 直接复制到其他客户端使用 -->
    <el-dialog v-model="configVisible" :title="$t('mcp.configTitle')" width="680px">
      <div v-loading="configLoading">
        <template v-if="mcpConfig">
          <el-descriptions :column="1" border size="small" style="margin-bottom: 12px">
            <el-descriptions-item :label="$t('mcp.configEndpoint')">
              <span class="mono">{{ mcpConfig.endpoint }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="API Key">
              <span class="mono">{{ mcpConfig.api_key }}</span>
            </el-descriptions-item>
          </el-descriptions>
          <el-alert type="info" :closable="false" style="margin-bottom: 12px">
            {{ $t('mcp.configHint', { count: mcpConfig.tool_count }) }}
          </el-alert>
          <el-tabs v-model="configTab">
            <el-tab-pane v-for="c in clientVariants" :key="c.value" :label="c.label" :name="c.value">
              <pre class="example-block config-block">{{ buildConfigText(c.value) }}</pre>
              <el-button type="primary" size="small" :icon="CopyDocument" @click="copyConfig(c.value)">
                {{ $t('mcp.copy') }}
              </el-button>
            </el-tab-pane>
          </el-tabs>
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Connection, CopyDocument } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { getMcpTools, getMcpToolDetail, getMcpConfig, getCallLogs, getPendingList, approvePending, rejectPending } from '@/api/mcp'

const { t } = useI18n()

const activeTab = ref('tools')

// 当前时刻（驱动过期倒计时显示）
const now = ref(Date.now())
let pollTimer = null
let tickTimer = null

// 工具目录
const toolsLoading = ref(false)
const toolsSummary = ref({})
const toolList = ref([])
const toolFilter = reactive({ category: '', domain: '', keyword: '' })

const categoryOptions = computed(() => [
  { value: 'read', label: t('mcp.catRead') },
  { value: 'preview', label: t('mcp.catPreview') },
  { value: 'confirm', label: t('mcp.catConfirm') },
  { value: 'approval', label: t('mcp.catApproval') }
])
const domainOptions = computed(() => [
  { value: 'project', label: t('mcp.domainProject') },
  { value: 'testcases', label: t('mcp.domainTestcases') },
  { value: 'api-testing', label: t('mcp.domainApi') },
  { value: 'ui-automation', label: t('mcp.domainUi') },
  { value: 'perf-testing', label: t('mcp.domainPerf') },
  { value: 'platform', label: t('mcp.domainPlatform') }
])
const categoryTagType = (c) => ({ read: 'success', preview: 'warning', confirm: 'danger', approval: 'info' }[c] || 'info')
const categoryLabel = (c) => ({ read: t('mcp.catRead'), preview: t('mcp.catPreview'), confirm: t('mcp.catConfirm'), approval: t('mcp.catApproval') }[c] || c)
const domainLabel = (d) => ({ project: t('mcp.domainProject'), testcases: t('mcp.domainTestcases'), 'api-testing': t('mcp.domainApi'), 'ui-automation': t('mcp.domainUi'), 'perf-testing': t('mcp.domainPerf'), platform: t('mcp.domainPlatform') }[d] || d)

const filteredTools = computed(() => {
  const kw = toolFilter.keyword.trim().toLowerCase()
  return toolList.value.filter(item => {
    if (toolFilter.category && item.category !== toolFilter.category) return false
    if (toolFilter.domain && item.domain !== toolFilter.domain) return false
    if (kw && !`${item.name} ${item.title} ${item.summary}`.toLowerCase().includes(kw)) return false
    return true
  })
})

async function loadTools() {
  toolsLoading.value = true
  try {
    const res = await getMcpTools()
    toolsSummary.value = res.data?.summary || {}
    toolList.value = res.data?.tools || []
  } catch (e) {
    ElMessage.error(t('mcp.msgLoadToolsFail'))
  } finally {
    toolsLoading.value = false
  }
}

// 工具详情抽屉
const detailVisible = ref(false)
const detail = ref(null)

const detailParams = computed(() => {
  const schema = detail.value?.input_schema || {}
  const props = schema.properties || {}
  const requiredList = schema.required || []
  return Object.entries(props).map(([name, p]) => ({
    name,
    type: p.type || (Array.isArray(p.anyOf) ? p.anyOf.map(x => x.type).filter(Boolean).join('|') : '-'),
    required: requiredList.includes(name) ? t('mcp.required') : t('mcp.optional'),
    desc: p.description || '-'
  }))
})
const detailExamples = computed(() => (detail.value?.examples || []).map(e => JSON.stringify(e, null, 2)))

async function openToolDetail(row) {
  detailVisible.value = true
  detail.value = null
  try {
    const res = await getMcpToolDetail(row.name)
    detail.value = res.data
  } catch (e) {
    ElMessage.error(t('mcp.msgLoadToolDetailFail'))
    detailVisible.value = false
  }
}

// 接入配置：端点 + 本人长效 API-Key，生成各客户端可直接粘贴的配置 JSON
const configVisible = ref(false)
const configLoading = ref(false)
const mcpConfig = ref(null)
const configTab = ref('claude')

const clientVariants = computed(() => [
  { value: 'claude', label: 'Claude Desktop' },
  { value: 'cursor', label: 'Cursor' },
  { value: 'generic', label: t('mcp.clientGeneric') }
])

function buildConfigText(variant) {
  if (!mcpConfig.value) return ''
  const server = {
    url: mcpConfig.value.endpoint,
    headers: { 'x-mcp-api-key': mcpConfig.value.api_key }
  }
  // Cursor 需显式声明传输类型；Claude Desktop / 通用格式仅 url + headers
  if (variant === 'cursor') server.type = 'streamableHttp'
  return JSON.stringify({ mcpServers: { [mcpConfig.value.server_name]: server } }, null, 2)
}

async function openConfig() {
  configVisible.value = true
  if (mcpConfig.value) return
  configLoading.value = true
  try {
    const res = await getMcpConfig()
    mcpConfig.value = res.data
  } catch (e) {
    ElMessage.error(t('mcp.msgLoadConfigFail'))
    configVisible.value = false
  } finally {
    configLoading.value = false
  }
}

async function copyConfig(variant) {
  const text = buildConfigText(variant)
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    ElMessage.success(t('mcp.copied'))
  } catch (e) {
    ElMessage.error(t('mcp.copyFail'))
  }
}

// 待确认
const pendingLoading = ref(false)
const pendingList = ref([])
const pendingTotal = ref(0)
const pendingPage = ref(1)
const pendingPageSize = ref(20)
const pendingFilter = reactive({ status: '' })
const pendingCount = ref(0)

const pendingLabel = computed(() => pendingCount.value
  ? `${t('mcp.pending')} (${pendingCount.value})`
  : t('mcp.pending'))

const statusOptions = computed(() => [
  { value: 'pending', label: t('mcp.statusPending') },
  { value: 'approved', label: t('mcp.statusApproved') },
  { value: 'rejected', label: t('mcp.statusRejected') },
  { value: 'consumed', label: t('mcp.statusConsumed') },
  { value: 'expired', label: t('mcp.statusExpired') }
])
const pendingStatusType = (s) => ({ pending: 'warning', approved: 'success', rejected: 'danger', consumed: 'info', expired: 'info' }[s] || 'info')
const pendingStatusLabel = (s) => ({ pending: t('mcp.statusPending'), approved: t('mcp.statusApproved'), rejected: t('mcp.statusRejected'), consumed: t('mcp.statusConsumed'), expired: t('mcp.statusExpired') }[s] || s)

// 日志
const logLoading = ref(false)
const logList = ref([])
const logTotal = ref(0)
const logPage = ref(1)
const logPageSize = ref(20)
const logFilter = reactive({ tool: '', status: '', range: null })

const logStatusOptions = computed(() => [
  { value: 'success', label: t('mcp.logStatusSuccess') },
  { value: 'error', label: t('mcp.logStatusError') },
  { value: 'denied', label: t('mcp.logStatusDenied') }
])

const logStatusType = (s) => ({ success: 'success', error: 'danger', denied: 'warning' }[s] || 'info')
const logStatusLabel = (s) => ({ success: t('mcp.logStatusSuccess'), error: t('mcp.logStatusError'), denied: t('mcp.logStatusDenied') }[s] || s)

// 空态接入引导：待确认/日志 Tab 无数据时展示（工具目录不展示）
const showGuide = computed(() => {
  if (activeTab.value === 'tools') return false
  if (pendingLoading.value || logLoading.value) return false
  return activeTab.value === 'pending' ? pendingTotal.value === 0 : logTotal.value === 0
})

const formatTime = (ts) => ts ? dayjs(ts).format('YYYY-MM-DD HH:mm:ss') : ''
const formatArgs = (a) => a ? JSON.stringify(a) : '-'
const formatResult = (r) => r ? JSON.stringify(r) : '-'

// 过期倒计时
const remainingMs = (row) => row.expires_at ? dayjs(row.expires_at).valueOf() - now.value : 0
const countdownText = (row) => {
  const ms = remainingMs(row)
  if (ms <= 0) return t('mcp.expireExpired')
  const totalSec = Math.floor(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return t('mcp.expireRemain', { time: min > 0 ? `${min}m${sec}s` : `${sec}s` })
}

async function loadPending() {
  pendingLoading.value = true
  try {
    const res = await getPendingList({ page: pendingPage.value, status: pendingFilter.status || undefined })
    pendingList.value = res.data?.results || []
    pendingTotal.value = res.data?.count || 0
    // 待确认计数（仅未过滤时统计 pending）
    if (!pendingFilter.status) pendingCount.value = pendingTotal.value
  } catch (e) {
    ElMessage.error(t('mcp.msgLoadPendingFail'))
  } finally {
    pendingLoading.value = false
  }
}

async function loadLogs() {
  logLoading.value = true
  try {
    const params = { page: logPage.value, tool: logFilter.tool || undefined, status: logFilter.status || undefined }
    if (Array.isArray(logFilter.range) && logFilter.range.length === 2) {
      params.created_after = dayjs(logFilter.range[0]).toISOString()
      params.created_before = dayjs(logFilter.range[1]).toISOString()
    }
    const res = await getCallLogs(params)
    logList.value = res.data?.results || []
    logTotal.value = res.data?.count || 0
  } catch (e) {
    ElMessage.error(t('mcp.msgLoadLogsFail'))
  } finally {
    logLoading.value = false
  }
}

function onTabChange(tab) {
  if (tab === 'pending') loadPending()
  else if (tab === 'logs') loadLogs()
  else if (tab === 'tools' && !toolList.value.length) loadTools()
}

async function doApprove(row) {
  try {
    await ElMessageBox.confirm(t('mcp.confirmApprove', { tool: row.tool_name }), t('mcp.confirmTitle'), { type: 'warning' })
    const res = await approvePending(row.id)
    ElMessage.success(t('mcp.msgApproved'))
    loadPending()
    if (res.data?.result) console.log('执行结果', res.data.result)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(t('mcp.msgApproveFail'))
  }
}

async function doReject(row) {
  try {
    await ElMessageBox.confirm(t('mcp.confirmReject', { tool: row.tool_name }), t('mcp.tip'), { type: 'warning' })
    await rejectPending(row.id)
    ElMessage.success(t('mcp.msgRejected'))
    loadPending()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(t('mcp.msgRejectFail'))
  }
}

onMounted(() => {
  loadTools()
  loadPending()
  // 15s 轮询刷新待确认列表（人工审批时效性强）；1s 驱动倒计时
  pollTimer = setInterval(() => {
    if (activeTab.value === 'pending' && !pendingLoading.value) loadPending()
  }, 15000)
  tickTimer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (tickTimer) clearInterval(tickTimer)
})
</script>

<style scoped lang="scss">
.mcp-console { padding: 0 4px; }
.filter-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
}
.mono {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  color: #606266;
}
.countdown {
  margin-top: 2px;
  color: #e6a23c;
  &.expired { color: #909399; }
}
.tool-summary {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}
.summary-card {
  flex: 1;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 8px;
  text-align: center;
}
.summary-num {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
  &.text-success { color: #67c23a; }
  &.text-danger { color: #f56c6c; }
}
.summary-label {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.detail-section {
  margin-bottom: 20px;
}
.detail-head {
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.detail-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #606266;
}
.example-block {
  padding: 10px 12px;
  margin: 0 0 8px;
  overflow-x: auto;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  line-height: 1.6;
  color: #303133;
  background: #f5f7fa;
  border-radius: 6px;
}
.anno-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.config-block {
  max-height: 320px;
  margin-bottom: 10px;
}
</style>
