<template>
  <div class="monitor-targets">
    <div class="page-header">
      <div class="page-title">
        <el-icon><Monitor /></el-icon>
        <span>{{ t('monitor.targets.title') }}</span>
        <el-tag :type="schedTagType" effect="light" size="small" class="sched-badge">
          <span class="sched-dot" :class="'sched-dot-' + scheduler"></span>
          {{ t('monitor.targets.scheduler.label') }} · {{ t('monitor.targets.scheduler.' + scheduler) }}
        </el-tag>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">
        {{ t('monitor.targets.add') }}
      </el-button>
    </div>
    <div class="page-subtitle">{{ t('monitor.targets.subtitle') }}</div>

    <el-card class="filter-card" shadow="never">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="搜索">
          <el-input v-model="filterSearch" placeholder="按目标名称模糊搜索" clearable style="width:240px"
                    @input="onSearchInput" @clear="onSearchClear">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </el-form-item>
        <el-form-item :label="t('monitor.targets.typeLabel')">
          <el-select v-model="filterType" :placeholder="t('monitor.targets.allTypes')" clearable style="width:160px" @change="onFilterChange">
            <el-option :label="t('monitor.targets.allTypes')" value="" />
            <el-option v-for="k in targetTypes" :key="k" :label="t('monitor.targets.type.' + k)" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('monitor.targets.col.status')">
          <el-select v-model="filterStatus" :placeholder="t('monitor.targets.allStatus')" clearable style="width:120px" @change="onFilterChange">
            <el-option :label="t('monitor.targets.allStatus')" value="" />
            <el-option label="UP" value="UP" />
            <el-option label="DOWN" value="DOWN" />
            <el-option label="UNKNOWN" value="UNKNOWN" />
          </el-select>
        </el-form-item>
      </el-form>
    </el-card>

    <el-table v-loading="loading" :data="targets" border stripe class="tbl">
      <el-table-column prop="name" :label="t('monitor.targets.col.name')" min-width="160" />
      <el-table-column :label="t('monitor.targets.col.type')" width="120">
        <template #default="{ row }">
          <el-tag>{{ t('monitor.targets.type.' + row.type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('monitor.targets.col.status')" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'UP' ? 'success' : row.status === 'DOWN' ? 'danger' : 'info'">
            {{ t('monitor.targets.status.' + row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('monitor.targets.col.enabled')" width="90">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="(v) => toggleEnabled(row, v)" />
        </template>
      </el-table-column>
      <el-table-column prop="interval_seconds" :label="t('monitor.targets.col.interval')" width="110" />
      <el-table-column prop="alert_threshold" :label="t('monitor.targets.col.alertThreshold')" width="100" />
      <el-table-column prop="alert_repeat_interval" :label="t('monitor.targets.col.repeatInterval')" width="110" />
      <el-table-column prop="manual_alert_cooldown" :label="t('monitor.targets.col.manualCooldown')" width="110" />
      <el-table-column prop="last_check_at" :label="t('monitor.targets.col.lastCheck')" min-width="170" :formatter="formatDateTime" />
      <el-table-column :label="t('monitor.targets.col.actions')" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :icon="VideoPlay" :loading="row._checking"
                     @click="checkNow(row)">{{ t('monitor.targets.checkNow') }}</el-button>
          <el-button link type="primary" :icon="Edit" @click="openEdit(row)">{{ t('monitor.targets.edit') }}</el-button>
          <el-button link type="danger" :icon="Delete" :title="t('monitor.targets.deleteSuccess')" @click="remove(row)" />
        </template>
      </el-table-column>
      <template #empty>{{ t('monitor.targets.empty') }}</template>
    </el-table>

    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </div>

    <!-- 新增 / 编辑 对话框 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? t('monitor.targets.edit') : t('monitor.targets.add')"
               width="680px" @closed="resetForm">
      <el-form :model="form" label-width="130px">
        <el-divider content-position="left">{{ t('monitor.targets.common') }}</el-divider>
        <el-form-item :label="t('monitor.targets.nameLabel')" required>
          <el-input v-model="form.name" :placeholder="t('monitor.targets.nameLabel')" />
        </el-form-item>
        <el-form-item :label="t('monitor.targets.typeLabel')" required>
          <el-select v-model="form.type" @change="onTypeChange">
            <el-option v-for="k in targetTypes" :key="k" :label="t('monitor.targets.type.' + k)" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item :label="form.type === 'LOGIN' ? '登录地址' : t('monitor.targets.urlLabel')">
          <el-input v-model="form.url" :placeholder="form.type === 'LOGIN' ? 'https://example.com/api/login（登录接口完整地址）' : 'https://... 或留空（配置内覆盖）'" />
          <div class="hint" v-if="form.type === 'LOGIN'">登录可用性的请求目标地址。完整 URL 直接使用；若非完整 URL，将与探测配置中的「登录路径」拼接。</div>
        </el-form-item>
        <el-form-item :label="t('monitor.targets.method')">
          <el-select v-model="form.method" style="width:140px">
            <el-option v-for="m in methods" :key="m" :label="m" :value="m" />
          </el-select>
          <div class="hint" v-if="form.type === 'LOGIN'">登录请求的 HTTP 方法，默认 POST。可在探测配置的请求体中自定义字段名。</div>
        </el-form-item>
        <el-form-item :label="t('monitor.targets.hostLabel')">
          <el-input v-model="form.host" :placeholder="form.type === 'LOGIN' ? 'example.com（如 URL 已含主机则留空）' : 'host（DOCKER/SL651 用）'" />
        </el-form-item>
        <el-form-item :label="t('monitor.targets.portLabel')">
          <el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
        <el-form-item :label="t('monitor.targets.intervalLabel')">
          <el-input-number v-model="form.interval_seconds" :min="10" :max="86400" controls-position="right" />
        </el-form-item>
        <el-form-item :label="t('monitor.targets.thresholdLabel')">
          <el-input-number v-model="form.alert_threshold" :min="1" :max="100" controls-position="right" />
        </el-form-item>
        <el-form-item :label="t('monitor.targets.repeatIntervalLabel')" :title="t('monitor.targets.repeatIntervalHint')">
          <el-input-number v-model="form.alert_repeat_interval" :min="1" :max="1440" controls-position="right" />
          <span class="field-suffix">{{ t('monitor.targets.minutes') }}</span>
        </el-form-item>
        <el-form-item :label="t('monitor.targets.manualCooldownLabel')" :title="t('monitor.targets.manualCooldownHint')">
          <el-input-number v-model="form.manual_alert_cooldown" :min="0" :max="1440" controls-position="right" />
          <span class="field-suffix">{{ t('monitor.targets.minutes') }}</span>
        </el-form-item>
        <el-form-item :label="t('monitor.targets.enabledLabel')">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item :label="t('monitor.targets.primaryChannels')">
          <el-select v-model="form.primary_channels" multiple filterable placeholder="可选"
                     style="width:100%">
            <el-option v-for="c in channels" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
          <div class="hint">{{ t('monitor.targets.primaryChannelsHint') }}</div>
        </el-form-item>
        <el-form-item :label="t('monitor.targets.secondaryChannels')">
          <el-select v-model="form.secondary_channels" multiple filterable placeholder="可选"
                     style="width:100%">
            <el-option v-for="c in channels" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
          <div class="hint">{{ t('monitor.targets.secondaryChannelsHint') }}</div>
        </el-form-item>

        <el-divider content-position="left">{{ t('monitor.targets.configLabel') }}</el-divider>
        <ConfigForm ref="configFormRef" v-model="configData" :type="form.type"
          :url="form.url" :method="form.method" :host="form.host" :port="form.port" />
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('monitor.targets.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('monitor.targets.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { Plus, VideoPlay, Edit, Delete, Monitor, Search } from '@element-plus/icons-vue'
import ConfigForm from './MonitorTargetConfigForm.vue'
import {
  getTargets, getTarget, createTarget, updateTarget, deleteTarget,
  checkTargetNow, getChannels, getSchedulerStatus,
} from '@/api/monitor'

const { t } = useI18n()
const targetTypes = ['LOGIN', 'HTTP', 'ONLINE', 'DOCKER', 'SL651']
const methods = ['GET', 'POST', 'PUT', 'DELETE']

const targets = ref([])
const channels = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const configData = ref({})
const configFormRef = ref(null)
const filterType = ref('')
const filterStatus = ref('')
const filterSearch = ref('')
let searchTimer = null

const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

const form = reactive({
  name: '', type: 'HTTP', url: '', method: 'GET', host: '', port: null,
  interval_seconds: 60, alert_threshold: 3,
  alert_repeat_interval: 30, manual_alert_cooldown: 5,
  enabled: true, primary_channels: [], secondary_channels: [],
})

function configTemplate(type) {
  if (type === 'LOGIN') return {
    endpoint: '/api/login', username: 'monitor',
    password: '******', is_base64: false, token_path: 'obj.token', uid_path: 'obj.id', timeout: 15,
    assertions_json: '',
  }
  if (type === 'HTTP') return {
    url: 'https://example.com/health', method: 'GET', headers: {},
    check_type: 'http_status', expected_status: 200, expect_contains: '', timeout: 15,
  }
  if (type === 'ONLINE') return {
    login: { login_url: 'https://example.com/api/login', username: 'u', password: '******' },
    base_url: 'https://example.com', statistics: { endpoint: '/api/statistics', method: 'POST' },
    labelname: '', warning_threshold: 90, timeout: 15,
  }
  if (type === 'DOCKER') return {
    host: '172.16.0.10', port: 2375, tls: false, containers: ['my-container'], max_restart: 3, timeout: 10,
  }
  // SL651
  return {
    host: '172.16.0.20', port: 10000, connect_timeout: 15, rw_timeout: 15, ack_wait_timeout: 15,
    frame: '7E7E0000...', db: {
      host: '', port: 3306, user: '', password: '', db: '', table: '',
      time_field: 'time', where_clause: '', status_data_max_lag: 3600, status_field: '', online_value: '1',
    },
  }
}

function resetForm() {
  form.name = ''; form.type = 'HTTP'; form.url = ''; form.method = 'GET'
  form.host = ''; form.port = null; form.interval_seconds = 60; form.alert_threshold = 3
  form.alert_repeat_interval = 30; form.manual_alert_cooldown = 5
  form.enabled = true; form.primary_channels = []; form.secondary_channels = []
  configData.value = configTemplate('HTTP')
  editingId.value = null
}

function onTypeChange(type) {
  // 创建模式：重置为模板；编辑模式：保持已有配置
  if (!editingId.value) {
    configData.value = configTemplate(type)
    nextTick(() => {
      if (configFormRef.value) configFormRef.value.switchToForm()
    })
  }
}

async function load() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filterType.value) params.type = filterType.value
    if (filterStatus.value) params.status = filterStatus.value
    if (filterSearch.value) params.search = filterSearch.value
    const res = await getTargets(params)
    targets.value = res.data.results || res.data || []
    total.value = res.data.count ?? targets.value.length
  } finally { loading.value = false }
}

function onFilterChange() {
  page.value = 1
  load()
}

function onSearchInput() {
  page.value = 1
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => load(), 300)
}

function onSearchClear() {
  filterSearch.value = ''
  page.value = 1
  load()
}

function onPageChange() {
  load()
}

/** ISO 时间字符串 → YYYY-MM-DD HH:mm:ss */
function formatDateTime(row, column, cellValue) {
  if (!cellValue) return '-'
  const d = new Date(cellValue)
  if (isNaN(d.getTime())) return cellValue
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function onSizeChange(sz) {
  pageSize.value = sz
  page.value = 1
  load()
}

async function loadChannels() {
  try {
    const res = await getChannels({ page_size: 200 })
    channels.value = res.data.results || res.data || []
  } catch { channels.value = [] }
}

function fillForm(d) {
  if (!d) return
  form.name = d.name; form.type = d.type; form.url = d.url || ''; form.method = d.method || 'GET'
  form.host = d.host || ''; form.port = d.port; form.interval_seconds = d.interval_seconds
  form.alert_threshold = d.alert_threshold; form.enabled = d.enabled
  form.alert_repeat_interval = d.alert_repeat_interval ?? 30
  form.manual_alert_cooldown = d.manual_alert_cooldown ?? 5
  form.primary_channels = Array.isArray(d.primary_channels) ? [...d.primary_channels] : []
  form.secondary_channels = Array.isArray(d.secondary_channels) ? [...d.secondary_channels] : []
  const cfg = d.check_config && Object.keys(d.check_config || {}).length ? d.check_config : configTemplate(d.type)
  configData.value = { ...cfg }
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

async function openEdit(row) {
  editingId.value = row.id
  // 先用列表行即时回填，避免弹窗空窗
  fillForm(row)
  dialogVisible.value = true
  // 再用详情接口取数据库最新完整记录刷新（兜底列表字段缺失/分页裁剪）
  try {
    const res = await getTarget(row.id)
    fillForm(res.data)
  } catch {
    // 详情失败则保留列表行回填的结果
  }
}

async function toggleEnabled(row, val) {
  try { await updateTarget(row.id, { enabled: val }); ElMessage.success('OK') }
  catch (e) { row.enabled = !val; ElMessage.error('更新失败') }
}

async function save() {
  if (!form.name.trim()) { ElMessage.warning(t('monitor.targets.nameLabel')); return }
  const payload = {
    name: form.name, type: form.type, url: form.url, method: form.method,
    host: form.host, port: form.port, interval_seconds: form.interval_seconds,
    alert_threshold: form.alert_threshold,
    alert_repeat_interval: form.alert_repeat_interval,
    manual_alert_cooldown: form.manual_alert_cooldown,
    enabled: form.enabled,
    primary_channels: form.primary_channels,
    secondary_channels: form.secondary_channels,
    check_config: { ...configData.value },
  }
  saving.value = true
  try {
    if (editingId.value) await updateTarget(editingId.value, payload)
    else await createTarget(payload)
    ElMessage.success(editingId.value ? t('monitor.targets.updateSuccess') : t('monitor.targets.createSuccess'))
    dialogVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error((e.response?.data?.detail) || '保存失败')
  } finally { saving.value = false }
}

async function remove(row) {
  try { await ElMessageBox.confirm(t('monitor.targets.deleteConfirm'), { type: 'warning' }) }
  catch { return }
  try { await deleteTarget(row.id); ElMessage.success(t('monitor.targets.deleteSuccess')); await load() }
  catch { ElMessage.error('删除失败') }
}

async function checkNow(row) {
  row._checking = true
  try {
    const res = await checkTargetNow(row.id)
    const st = res.data.status
    const label = `${row.name}：${t('monitor.targets.status.' + st)}`
    if (st === 'UP') {
      ElMessage.success(label)
    } else if (st === 'DOWN') {
      // 异常必须红色，不能用绿色成功提示
      ElMessage.error(label)
    } else {
      ElMessage.warning(label)
    }
    await load()
  } catch (e) {
    ElMessage.error('检测失败')
  } finally { row._checking = false }
}

// 调度器在线状态（Redis 心跳）
const scheduler = ref('unknown')
const schedTagType = computed(() =>
  scheduler.value === 'online' ? 'success' : scheduler.value === 'offline' ? 'info' : 'warning'
)
function loadSchedulerStatus() {
  getSchedulerStatus().then(r => { scheduler.value = r.data.status }).catch(() => {})
}
let schedTimer = null

onMounted(() => {
  load()
  loadChannels()
  loadSchedulerStatus()
  schedTimer = setInterval(loadSchedulerStatus, 30000)
})
onUnmounted(() => { if (schedTimer) clearInterval(schedTimer) })
</script>

<style lang="scss" scoped>
.monitor-targets { padding: 16px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { display: flex; align-items: center; font-size: 20px; font-weight: 600; color: #1f2d3d;
  .el-icon { margin-right: 8px; color: #1890ff; font-size: 22px; } }
.sched-badge { display: inline-flex; align-items: center; gap: 5px; margin-left: 12px; }
.sched-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #bbb; }
.sched-dot-online { background: #52c41a; }
.sched-dot-offline { background: #bfbfbf; }
.sched-dot-unknown { background: #faad14; }
.page-subtitle { color: #8c8c8c; font-size: 13px; margin: 4px 0 16px; }
.filter-card { margin-bottom: 16px; }
.tbl { margin-top: 8px; }
.pager { display: flex; justify-content: flex-end; margin-top: 16px; }
.hint { color: #8c8c8c; font-size: 12px; margin-top: 4px; }
</style>
