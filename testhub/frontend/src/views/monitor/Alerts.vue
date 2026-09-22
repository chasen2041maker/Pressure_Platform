<template>
  <div class="alerts">
    <div class="page-header">
      <div>
        <h2 class="page-title">{{ $t('monitor.alerts.title') }}</h2>
        <p class="page-subtitle">{{ $t('monitor.alerts.subtitle') }}</p>
      </div>
      <el-button type="primary" :icon="Refresh" :loading="loading" @click="fetchAlerts">
        {{ $t('monitor.dashboard.refresh') }}
      </el-button>
    </div>

    <el-card class="filter-card" shadow="never">
      <el-form :inline="true" @submit.prevent>
        <el-form-item :label="$t('monitor.alerts.filterStatus')">
          <el-select
            v-model="filters.status"
            :placeholder="$t('monitor.alerts.allStatus')"
            clearable
            style="width: 160px"
            @change="onFilterChange"
          >
            <el-option :label="$t('monitor.alerts.allStatus')" value="" />
            <el-option :label="$t('monitor.alerts.status.FIRING')" value="FIRING" />
            <el-option :label="$t('monitor.alerts.status.ACKED')" value="ACKED" />
            <el-option :label="$t('monitor.alerts.status.RESOLVED')" value="RESOLVED" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('monitor.alerts.filterDate')">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="~"
            :start-placeholder="$t('monitor.alerts.dateStart')"
            :end-placeholder="$t('monitor.alerts.dateEnd')"
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
        stripe
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="timeline-box">
              <h4>{{ $t('monitor.alerts.timelineTitle') }}</h4>
              <el-empty v-if="!row.send_detail || !row.send_detail.length" :description="$t('monitor.alerts.timelineEmpty')" />
              <el-table v-else :data="row.send_detail" size="small" border>
                <el-table-column :label="$t('monitor.alerts.sendType')" width="90">
                  <template #default="{ row: s }">
                    <el-tag :type="s.recovered ? 'success' : 'danger'" size="small">
                      {{ s.recovered ? $t('monitor.alerts.sendRecover') : $t('monitor.alerts.sendAlert') }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('monitor.alerts.sendLevel')" width="90">
                  <template #default="{ row: s }">
                    <el-tag v-if="s.level === 'primary'" type="primary" size="small" effect="plain">
                      {{ $t('monitor.alerts.levelPrimary') }}
                    </el-tag>
                    <el-tag v-else-if="s.level === 'secondary'" type="warning" size="small" effect="plain">
                      {{ $t('monitor.alerts.levelSecondary') }}
                      <el-tooltip v-if="s.fallback" :content="$t('monitor.alerts.fallbackTip')" placement="top">
                        <el-icon class="fallback-icon"><Switch /></el-icon>
                      </el-tooltip>
                    </el-tag>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('monitor.alerts.channel')" prop="channel" min-width="140" />
                <el-table-column :label="$t('monitor.alerts.channelType')" prop="type" width="100" />
                <el-table-column :label="$t('monitor.alerts.sendResult')" width="90">
                  <template #default="{ row: s }">
                    <el-tag :type="s.ok ? 'success' : 'danger'" size="small">
                      {{ s.ok ? $t('monitor.alerts.sendOk') : $t('monitor.alerts.sendFail') }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('monitor.alerts.sendDetail')" prop="detail" min-width="160">
                  <template #default="{ row: s }"><span class="err-text">{{ s.detail }}</span></template>
                </el-table-column>
                <el-table-column :label="$t('monitor.alerts.sendTime')" width="170">
                  <template #default="{ row: s }">{{ formatTime(s.sent_at) }}</template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>

        <el-table-column :label="$t('monitor.alerts.col.target')" prop="target_name" min-width="160" />
        <el-table-column :label="$t('monitor.alerts.col.level')" prop="level" width="100">
          <template #default="{ row }">
            <el-tag :type="row.level === 'CRITICAL' ? 'danger' : 'warning'" size="small">
              {{ $t('monitor.alerts.level.' + row.level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitor.alerts.col.status')" prop="status" width="110">
          <template #default="{ row }">
            <el-tag :type="alertStatusType(row.status)" size="small">
              {{ $t('monitor.alerts.status.' + row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitor.alerts.col.message')" prop="message" min-width="220">
          <template #default="{ row }"><span class="msg-cell">{{ row.message }}</span></template>
        </el-table-column>
        <el-table-column :label="$t('monitor.alerts.col.firstAt')" prop="first_triggered_at" width="170">
          <template #default="{ row }">{{ formatTime(row.first_triggered_at) }}</template>
        </el-table-column>
        <el-table-column :label="$t('monitor.alerts.col.lastAt')" prop="last_triggered_at" width="170">
          <template #default="{ row }">{{ formatTime(row.last_triggered_at) }}</template>
        </el-table-column>
        <el-table-column :label="$t('monitor.alerts.col.ackBy')" prop="acknowledged_by_username" width="120">
          <template #default="{ row }">{{ row.acknowledged_by_username || '-' }}</template>
        </el-table-column>
        <el-table-column :label="$t('monitor.alerts.col.actions')" width="170" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'FIRING'"
              type="warning" size="small" @click="onAck(row)"
            >{{ $t('monitor.alerts.ack') }}</el-button>
            <el-button
              v-if="row.status === 'FIRING' || row.status === 'ACKED'"
              type="success" size="small" @click="onResolve(row)"
            >{{ $t('monitor.alerts.resolve') }}</el-button>
            <span v-if="row.status === 'RESOLVED'" class="resolved-tag">{{ $t('monitor.alerts.resolved') }}</span>
          </template>
        </el-table-column>

        <template #empty>
          <span>{{ $t('monitor.alerts.empty') }}</span>
        </template>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          background
          @current-change="fetchAlerts"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Switch } from '@element-plus/icons-vue'
import { getAlerts, acknowledgeAlert, resolveAlert } from '@/api/monitor'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = ref({ status: '' })
const dateRange = ref(null)

const alertStatusType = (s) => {
  if (s === 'FIRING') return 'danger'
  if (s === 'ACKED') return 'warning'
  return 'success'
}

const formatTime = (v) => {
  if (!v) return '-'
  return String(v).replace('T', ' ').slice(0, 19)
}

const fetchAlerts = async () => {
  loading.value = true
  try {
    const params = { page: page.value, page_size: pageSize.value }
    if (filters.value.status) params.status = filters.value.status
    if (dateRange.value && dateRange.value.length === 2) {
      params.start_time = dateRange.value[0]
      params.end_time = dateRange.value[1]
    }
    const res = await getAlerts(params)
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
  fetchAlerts()
}

const onAck = async (row) => {
  try {
    await ElMessageBox.confirm(row.message, $t('monitor.alerts.ackConfirm'), {
      confirmButtonText: $t('monitor.alerts.ack'),
      cancelButtonText: $t('monitor.dashboard.refresh') && '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await acknowledgeAlert(row.id)
    ElMessage.success($t('monitor.alerts.opSuccess'))
    fetchAlerts()
  } catch {
    /* 错误提示由拦截器统一处理 */
  }
}

const onResolve = async (row) => {
  try {
    await resolveAlert(row.id)
    ElMessage.success($t('monitor.alerts.opSuccess'))
    fetchAlerts()
  } catch {
    /* 错误提示由拦截器统一处理 */
  }
}

onMounted(() => {
  fetchAlerts()
})
</script>

<style scoped lang="scss">
.alerts {
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

.err-text {
  color: #f56c6c;
  word-break: break-all;
}

.timeline-box {
  padding: 8px 24px;

  h4 {
    margin: 0 0 12px;
    font-size: 14px;
    color: #303133;
  }
}

.fallback-icon {
  margin-left: 2px;
  font-size: 12px;
  vertical-align: middle;
}

.resolved-tag {
  color: #67c23a;
  font-size: 13px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
