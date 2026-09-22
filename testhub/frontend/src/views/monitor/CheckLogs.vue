<template>
  <div class="check-logs">
    <div class="page-header">
      <div>
        <h2 class="page-title">{{ $t('monitor.checkLogs.title') }}</h2>
        <p class="page-subtitle">{{ $t('monitor.checkLogs.subtitle') }}</p>
      </div>
      <el-button type="primary" :icon="Refresh" :loading="loading" @click="fetchLogs">
        {{ $t('monitor.dashboard.refresh') }}
      </el-button>
    </div>

    <el-card class="filter-card" shadow="never">
      <el-form :inline="true" @submit.prevent>
        <el-form-item :label="$t('monitor.checkLogs.filterTarget')">
          <el-select
            v-model="filters.target"
            :placeholder="$t('monitor.checkLogs.allTargets')"
            clearable
            style="width: 220px"
            @change="onFilterChange"
          >
            <el-option :label="$t('monitor.checkLogs.allTargets')" value="" />
            <el-option
              v-for="t in targets"
              :key="t.id"
              :label="t.name"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('monitor.checkLogs.filterStatus')">
          <el-select
            v-model="filters.status"
            :placeholder="$t('monitor.checkLogs.allStatus')"
            clearable
            style="width: 160px"
            @change="onFilterChange"
          >
            <el-option :label="$t('monitor.checkLogs.allStatus')" value="" />
            <el-option label="UP" value="UP" />
            <el-option label="DOWN" value="DOWN" />
            <el-option label="UNKNOWN" value="UNKNOWN" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('monitor.checkLogs.filterDate')">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="~"
            :start-placeholder="$t('monitor.checkLogs.dateStart')"
            :end-placeholder="$t('monitor.checkLogs.dateEnd')"
            value-format="YYYY-MM-DD"
            style="width: 280px"
            @change="onFilterChange"
          />
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="list"
        row-key="id"
        default-expand-all
        stripe
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="detail-box">
              <h4>{{ $t('monitor.checkLogs.detailTitle') }}</h4>
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item :label="$t('monitor.checkLogs.fieldLatency')">
                  {{ row.latency_ms ?? '-' }}
                </el-descriptions-item>
                <el-descriptions-item :label="$t('monitor.checkLogs.fieldHttp')">
                  {{ row.http_status ?? '-' }}
                </el-descriptions-item>
                <el-descriptions-item :label="$t('monitor.checkLogs.fieldTriggered')">
                  {{ row.triggered_alert ? $t('monitor.checkLogs.yes') : $t('monitor.checkLogs.no') }}
                </el-descriptions-item>
                <el-descriptions-item :label="$t('monitor.checkLogs.fieldError')">
                  <span class="err-text">{{ row.error_message || '-' }}</span>
                </el-descriptions-item>
                <el-descriptions-item :label="$t('monitor.checkLogs.fieldDetail')" :span="2">
                  <pre class="detail-pre">{{ prettyDetail(row.detail) }}</pre>
                </el-descriptions-item>
              </el-descriptions>
            </div>
          </template>
        </el-table-column>

        <el-table-column
          :label="$t('monitor.checkLogs.col.target')"
          prop="target_name"
          min-width="160"
        />
        <el-table-column :label="$t('monitor.checkLogs.col.type')" prop="type" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">
              {{ $t('monitor.dashboard.type.' + row.type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitor.checkLogs.col.status')" prop="status" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">
              {{ $t('monitor.dashboard.status.' + row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitor.checkLogs.col.latency')" prop="latency_ms" width="110">
          <template #default="{ row }">
            {{ row.latency_ms != null ? row.latency_ms + ' ms' : '-' }}
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitor.checkLogs.col.http')" prop="http_status" width="90">
          <template #default="{ row }">{{ row.http_status ?? '-' }}</template>
        </el-table-column>
        <el-table-column :label="$t('monitor.checkLogs.col.message')" prop="error_message" min-width="200">
          <template #default="{ row }">
            <span class="msg-cell">{{ row.error_message || (row.status === 'UP' ? 'OK' : '-') }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitor.checkLogs.col.alert')" prop="triggered_alert" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.triggered_alert" type="danger" size="small">ALERT</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitor.checkLogs.col.time')" prop="checked_at" width="180">
          <template #default="{ row }">{{ formatTime(row.checked_at) }}</template>
        </el-table-column>

        <template #empty>
          <span>{{ $t('monitor.checkLogs.empty') }}</span>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :page-sizes="[5, 10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          background
          @current-change="fetchLogs"
          @size-change="onSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getChecks, getTargets } from '@/api/monitor'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(5)
const targets = ref([])
const filters = ref({ target: '', status: '' })
const dateRange = ref(null)

const statusType = (s) => {
  if (s === 'UP') return 'success'
  if (s === 'DOWN') return 'danger'
  return 'info'
}

const formatTime = (v) => {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

const prettyDetail = (d) => {
  if (!d) return '-'
  try {
    return JSON.stringify(d, null, 2)
  } catch {
    return String(d)
  }
}

const fetchTargets = async () => {
  try {
    const res = await getTargets({ page_size: 200 })
    targets.value = res.data.results || res.data || []
  } catch {
    targets.value = []
  }
}

const fetchLogs = async () => {
  loading.value = true
  try {
    const params = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (filters.value.target) params.target = filters.value.target
    if (filters.value.status) params.status = filters.value.status
    if (dateRange.value && dateRange.value.length === 2) {
      params.start_time = dateRange.value[0]
      params.end_time = dateRange.value[1]
    }
    const res = await getChecks(params)
    list.value = res.data.results || res.data || []
    total.value = res.data.count ?? list.value.length
  } catch {
    list.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

const onFilterChange = () => {
  page.value = 1
  fetchLogs()
}

const onSizeChange = (sz) => {
  pageSize.value = sz
  page.value = 1
  fetchLogs()
}

onMounted(() => {
  fetchTargets()
  fetchLogs()
})
</script>

<style scoped lang="scss">
.check-logs {
  padding: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}

.page-subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: #909399;
}

.filter-card {
  margin-bottom: 16px;
}

.msg-cell {
  color: #606266;
  word-break: break-all;
}

.detail-box {
  padding: 8px 24px;

  h4 {
    margin: 0 0 12px;
    font-size: 14px;
    color: #303133;
  }
}

.err-text {
  color: #f56c6c;
  word-break: break-all;
}

.detail-pre {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
  background: #f7f8fa;
  padding: 10px;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
