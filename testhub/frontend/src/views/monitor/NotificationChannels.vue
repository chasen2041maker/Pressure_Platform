<template>
  <div class="monitor-channels">
    <div class="page-header">
      <div class="page-title">
        <el-icon><Connection /></el-icon>
        <span>{{ t('monitor.channels.title') }}</span>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">
        {{ t('monitor.channels.add') }}
      </el-button>
    </div>
    <div class="page-subtitle">{{ t('monitor.channels.subtitle') }}</div>

    <el-table v-loading="loading" :data="channels" border stripe class="tbl">
      <el-table-column prop="name" :label="t('monitor.channels.col.name')" min-width="160" />
      <el-table-column :label="t('monitor.channels.col.type')" width="120">
        <template #default="{ row }">
          <el-tag>{{ t('monitor.channels.type.' + row.type) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('monitor.channels.col.enabled')" width="100">
        <template #default="{ row }">
          <el-switch
            v-model="row.enabled"
            :active-text="t('monitor.channels.enabledText')"
            @change="(v) => toggleEnabled(row, v)"
          />
        </template>
      </el-table-column>
      <el-table-column :label="t('monitor.channels.col.config')" min-width="220">
        <template #default="{ row }">
          <span class="cfg-summary">{{ configSummary(row.config) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" :label="t('monitor.channels.col.createdAt')" width="180" :formatter="formatDateTime" />
      <el-table-column :label="t('monitor.channels.col.actions')" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" :icon="Promotion" :loading="row._testing"
                     @click="testChannel(row)">{{ t('monitor.channels.test') }}</el-button>
          <el-button link type="primary" :icon="Edit" @click="openEdit(row)">{{ t('monitor.channels.edit') }}</el-button>
          <el-button link type="danger" :icon="Delete" @click="remove(row)" :title="t('monitor.channels.deleteSuccess')" />
        </template>
      </el-table-column>
      <template #empty>{{ t('monitor.channels.empty') }}</template>
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
    <el-dialog v-model="dialogVisible" :title="editingId ? t('monitor.channels.edit') : t('monitor.channels.add')"
               width="560px" @closed="resetForm">
      <el-form :model="form" label-width="120px">
        <el-form-item :label="t('monitor.channels.form.name')" required>
          <el-input v-model="form.name" :placeholder="t('monitor.channels.form.name')" />
        </el-form-item>
        <el-form-item :label="t('monitor.channels.form.channelType')" required>
          <el-select v-model="form.type" :disabled="!!editingId" @change="onTypeChange">
            <el-option v-for="k in channelTypes" :key="k" :label="t('monitor.channels.type.' + k)" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('monitor.channels.form.enabled')">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <!-- 钉钉 -->
        <template v-if="form.type === 'DINGTALK'">
          <el-alert :title="t('monitor.channels.form.dingtalkHint')" type="info" :closable="false" show-icon />
          <el-form-item :label="t('monitor.channels.form.webhookUrl')" required>
            <el-input v-model="form.config.webhook_url" placeholder="https://oapi.dingtalk.com/robot/send?access_token=xxx" />
          </el-form-item>
          <el-form-item :label="t('monitor.channels.form.secret')">
            <el-input v-model="form.config.secret" placeholder="SECxxxx（可留空）" show-password />
          </el-form-item>
          <el-form-item :label="t('monitor.channels.form.atAll')">
            <el-switch v-model="form.config.at_all" />
          </el-form-item>
        </template>

        <!-- 企业微信 -->
        <template v-else-if="form.type === 'WECOM'">
          <el-alert :title="t('monitor.channels.form.wecomHint')" type="info" :closable="false" show-icon />
          <el-form-item :label="t('monitor.channels.form.webhookUrl')" required>
            <el-input v-model="form.config.webhook_url" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" />
          </el-form-item>
          <el-form-item label="@所有人">
            <el-switch v-model="form.config.at_all" active-text="开启" inactive-text="关闭" />
            <div class="hint">开启后发送告警时将 @全体成员，确保关键通知触达所有人</div>
          </el-form-item>
          <el-form-item :label="t('monitor.channels.form.mentionedList')">
            <el-input v-model="mentionedText" placeholder="zhangsan,lisi（额外 @指定成员，@all 模式下也生效）" />
          </el-form-item>
        </template>

        <!-- 邮件 -->
        <template v-else-if="form.type === 'EMAIL'">
          <el-alert :title="t('monitor.channels.form.emailHint')" type="info" :closable="false" show-icon />
          <el-form-item :label="t('monitor.channels.form.smtpHost')">
            <el-input v-model="form.config.host" placeholder="smtp.example.com" />
          </el-form-item>
          <el-form-item :label="t('monitor.channels.form.smtpPort')">
            <el-input-number v-model="form.config.port" :min="1" :max="65535" controls-position="right" />
          </el-form-item>
          <el-form-item :label="t('monitor.channels.form.smtpUser')">
            <el-input v-model="form.config.username" />
          </el-form-item>
          <el-form-item :label="t('monitor.channels.form.smtpPassword')">
            <el-input v-model="form.config.password" show-password />
          </el-form-item>
          <el-form-item :label="t('monitor.channels.form.useSsl')">
            <el-switch v-model="form.config.use_ssl" />
          </el-form-item>
          <el-form-item :label="t('monitor.channels.form.receivers')">
            <el-input v-model="receiversText" placeholder="a@x.com,b@y.com" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">{{ t('monitor.channels.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ t('monitor.channels.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { Plus, Promotion, Edit, Delete, Connection } from '@element-plus/icons-vue'
import { getChannels, getChannel, createChannel, updateChannel, deleteChannel, testChannel as testChannelApi } from '@/api/monitor'
import {
  CHANNEL_TYPES, defaultConfig, configSummary, normalizeEditConfig, buildSavePayload, isCiphertext,
} from './channelForm.mjs'

const { t } = useI18n()
const channelTypes = CHANNEL_TYPES

const channels = ref([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)

const form = reactive({ name: '', type: 'DINGTALK', enabled: true, config: defaultConfig('DINGTALK') })
const mentionedText = ref('')
const receiversText = ref('')

function resetForm() {
  form.name = ''
  form.type = 'DINGTALK'
  form.enabled = true
  Object.assign(form.config, defaultConfig('DINGTALK'))
  mentionedText.value = ''
  receiversText.value = ''
  editingId.value = null
}

function onTypeChange(type) {
  // 替换而非合并：清除旧类型的残留字段，避免脏数据随提交发出
  Object.keys(form.config).forEach((k) => { if (!(k in defaultConfig(type))) delete form.config[k] })
  Object.assign(form.config, defaultConfig(type))
  mentionedText.value = ''
  receiversText.value = ''
}

async function load() {
  loading.value = true
  try {
    const res = await getChannels({ page: page.value, page_size: pageSize.value })
    channels.value = res.data.results || res.data || []
    total.value = res.data.count ?? channels.value.length
  } finally {
    loading.value = false
  }
}

function onPageChange() {
  load()
}

function onSizeChange(sz) {
  pageSize.value = sz
  page.value = 1
  load()
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

async function openEdit(row) {
  editingId.value = row.id
  // 先调 retrieve 取明文配置（含 webhook_url / secret / password 等敏感字段）
  let cfg = {}
  try {
    const res = await getChannel(row.id)
    cfg = res.data.config || {}
  } catch {
    // fallback 用行数据
    cfg = row.config || {}
  }
  form.name = row.name || cfg.name || ''
  form.type = row.type
  form.enabled = row.enabled
  // 规整为可编辑值：密文 / null 字段清空为 ''，提示用户重填，杜绝二次加密
  form.config = normalizeEditConfig(cfg, row.type)
  mentionedText.value = Array.isArray(cfg.mentioned_list) ? cfg.mentioned_list.join(',') : (cfg.mentioned_list || '')
  receiversText.value = Array.isArray(cfg.receivers) ? cfg.receivers.join(',') : (cfg.receivers || '')
  // 若原配置含失效密文，提醒用户必须重新填写相关字段
  if (isCiphertext(cfg.webhook_url) || isCiphertext(cfg.secret) || isCiphertext(cfg.password)) {
    ElMessage.warning(t('monitor.channels.configExpiredHint'))
  }
  dialogVisible.value = true
}

async function toggleEnabled(row, val) {
  try {
    await updateChannel(row.id, { enabled: val })
    ElMessage.success(t('monitor.channels.enabledText') + (val ? '' : ' / ' + t('monitor.channels.disabledText')))
  } catch (e) {
    row.enabled = !val
    ElMessage.error('更新失败')
  }
}

async function save() {
  if (!form.name.trim()) { ElMessage.warning(t('monitor.channels.form.name')); return }
  // 只携带该类型允许的字段，杜绝跨类型残留（如切换类型后残留的 webhook/secret）
  const payload = buildSavePayload(form, { mentionedText: mentionedText.value, receiversText: receiversText.value })

  saving.value = true
  try {
    if (editingId.value) await updateChannel(editingId.value, payload)
    else await createChannel(payload)
    ElMessage.success(editingId.value ? t('monitor.channels.edit') + ' ✓' : t('monitor.channels.add') + ' ✓')
    dialogVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error((e.response?.data?.detail) || '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(t('monitor.channels.deleteConfirm'), { type: 'warning' })
  } catch { return }
  try {
    await deleteChannel(row.id)
    ElMessage.success(t('monitor.channels.deleteSuccess'))
    await load()
  } catch (e) {
    ElMessage.error('删除失败')
  }
}

/** ISO 时间字���串 → YYYY-MM-DD HH:mm:ss */
function formatDateTime(row, column, cellValue) {
  if (!cellValue) return '-'
  const d = new Date(cellValue)
  if (isNaN(d.getTime())) return cellValue
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function testChannel(row) {
  row._testing = true
  try {
    const res = await testChannelApi(row.id)
    if (res.data.success) ElMessage.success(t('monitor.channels.testSuccess'))
    else ElMessage.warning(t('monitor.channels.testFail') + (res.data.detail || ''))
  } catch (e) {
    ElMessage.error(t('monitor.channels.testFail') + ((e.response?.data?.detail) || '网络错误'))
  } finally {
    row._testing = false
  }
}

onMounted(load)
</script>

<style lang="scss" scoped>
.monitor-channels { padding: 16px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { display: flex; align-items: center; font-size: 20px; font-weight: 600; color: #1f2d3d;
  .el-icon { margin-right: 8px; color: #1890ff; font-size: 22px; } }
.page-subtitle { color: #8c8c8c; font-size: 13px; margin: 4px 0 16px; }
.tbl { margin-top: 8px; }
.pager { display: flex; justify-content: flex-end; margin-top: 16px; }
.cfg-summary { color: #595959; font-size: 13px; word-break: break-all; }
</style>
