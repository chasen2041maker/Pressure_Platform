<template>
  <div class="page-container">
    <div class="page-header">
      <div>
        <h1 class="page-title">{{ defect.title || '缺陷详情' }}</h1>
        <div class="sub-title">{{ defect.code || `#${defect.id || '-'}` }}</div>
      </div>
      <div class="header-actions">
        <el-button @click="router.push('/defects/list')">
          <el-icon><Back /></el-icon>
          返回列表
        </el-button>
        <el-button type="primary" @click="router.push(`/defects/${route.params.id}/edit`)">
          <el-icon><Edit /></el-icon>
          编辑
        </el-button>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="16">
        <el-card shadow="never" v-loading="loading">
          <template #header>
            <div class="card-header">
              <span>基本信息</span>
              <el-tag :type="getOptionType(statusOptions, defect.status)">{{ getOptionLabel(statusOptions, defect.status) }}</el-tag>
            </div>
          </template>

          <el-descriptions :column="2" border>
            <el-descriptions-item label="严重级别">
              <el-tag :type="getOptionType(severityOptions, defect.severity)">{{ getOptionLabel(severityOptions, defect.severity) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="优先级">
              <el-tag :type="getOptionType(priorityOptions, defect.priority)">{{ getOptionLabel(priorityOptions, defect.priority) }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="所属项目">{{ getName(defect.project) }}</el-descriptions-item>
            <el-descriptions-item label="版本">{{ getName(defect.version) }}</el-descriptions-item>
            <el-descriptions-item label="模块">{{ defect.module || '-' }}</el-descriptions-item>
            <el-descriptions-item label="处理人">{{ getName(defect.assignee) }}</el-descriptions-item>
            <el-descriptions-item label="提交人">{{ getName(defect.reporter) }}</el-descriptions-item>
            <el-descriptions-item label="创建时间">{{ formatDate(defect.created_at) }}</el-descriptions-item>
          </el-descriptions>

          <el-divider />

          <section class="detail-section">
            <h3>缺陷描述</h3>
            <p>{{ defect.description || '-' }}</p>
          </section>
          <section class="detail-section">
            <h3>复现步骤</h3>
            <p>{{ defect.reproduce_steps || '-' }}</p>
          </section>
          <el-row :gutter="16">
            <el-col :xs="24" :md="12">
              <section class="detail-section">
                <h3>预期结果</h3>
                <p>{{ defect.expected_result || '-' }}</p>
              </section>
            </el-col>
            <el-col :xs="24" :md="12">
              <section class="detail-section">
                <h3>实际结果</h3>
                <p>{{ defect.actual_result || '-' }}</p>
              </section>
            </el-col>
          </el-row>
        </el-card>

        <el-card shadow="never" class="content-card">
          <template #header>
            <div class="card-header">
              <span>评论</span>
              <el-button type="primary" size="small" :loading="submittingComment" @click="submitComment">发表评论</el-button>
            </div>
          </template>
          <el-input
            v-model="commentContent"
            type="textarea"
            :rows="3"
            maxlength="1000"
            show-word-limit
            placeholder="请输入评论内容"
          />
          <div v-if="comments.length" class="comment-list">
            <div v-for="comment in comments" :key="comment.id" class="comment-item">
              <div class="item-meta">
                <strong>{{ getName(comment.author) }}</strong>
                <span>{{ formatDate(comment.created_at) }}</span>
              </div>
              <p>{{ comment.content }}</p>
            </div>
          </div>
          <el-empty v-else description="暂无评论" />
        </el-card>

        <el-card shadow="never" class="content-card">
          <template #header>
            <div class="card-header">
              <span>附件</span>
              <el-upload
                :show-file-list="false"
                :auto-upload="false"
                :on-change="uploadAttachment"
              >
                <el-button size="small" :loading="uploadingAttachment">
                  <el-icon><Upload /></el-icon>
                  上传附件
                </el-button>
              </el-upload>
            </div>
          </template>
          <div v-if="attachments.length" class="attachment-list">
            <div v-for="attachment in attachments" :key="attachment.id" class="attachment-item">
              <el-link :href="attachment.file" type="primary" target="_blank">{{ attachment.name }}</el-link>
              <span>{{ getName(attachment.uploaded_by) }} · {{ formatDate(attachment.uploaded_at) }}</span>
            </div>
          </div>
          <el-empty v-else description="暂无附件" />
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="8">
        <el-card shadow="never" class="side-card">
          <template #header>状态操作</template>
          <div class="action-grid">
            <el-button @click="openAction('assign')">指派</el-button>
            <el-button type="primary" @click="openAction('startProgress')">开始修复</el-button>
            <el-button @click="openAction('resolve')">提交修复</el-button>
            <el-button type="success" @click="openAction('verify')">回归通过</el-button>
            <el-button type="warning" @click="openAction('reopen')">重新打开</el-button>
            <el-button type="danger" @click="openAction('reject')">驳回</el-button>
            <el-button type="info" @click="openAction('close')">关闭</el-button>
          </div>
        </el-card>

        <el-card shadow="never" class="side-card">
          <template #header>流转历史</template>
          <el-timeline v-if="transitions.length">
            <el-timeline-item
              v-for="item in transitions"
              :key="item.id || item.created_at"
              :timestamp="formatDate(item.created_at)"
            >
              <div class="transition-row">
                <strong class="transition-operator">{{ getName(item.operator) }}</strong>
                <span class="transition-status">
                  <template v-if="item.from_status && item.from_status !== item.to_status">{{ getOptionLabel(statusOptions, item.from_status) }} →</template>
                  {{ getOptionLabel(statusOptions, item.to_status) }}
                </span>
                <el-tag v-if="item.target_user" size="small" type="primary" effect="plain" class="transition-target">
                  指派给 {{ getName(item.target_user) }}
                </el-tag>
              </div>
              <div v-if="item.comment" class="timeline-comment">{{ item.comment }}</div>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-else description="暂无流转记录" />
        </el-card>
      </el-col>
    </el-row>

    <el-dialog v-model="actionDialogVisible" :title="actionTitle" width="520px">
      <el-form label-width="90px">
        <el-form-item v-if="currentAction === 'assign'" label="处理人">
          <el-select v-model="actionForm.assignee_id" filterable clearable placeholder="请选择处理人" class="full-width">
            <el-option v-for="user in users" :key="user.id" :label="user.username || user.name" :value="user.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="actionForm.comment" type="textarea" :rows="4" placeholder="请输入流转备注" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="actionDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submittingAction" @click="submitAction">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, Edit, Upload } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import api from '@/utils/api'
import {
  addDefectComment,
  assignDefect,
  closeDefect,
  getDefect,
  rejectDefect,
  reopenDefect,
  resolveDefect,
  startProgressDefect,
  uploadDefectAttachment,
  verifyDefect
} from '@/api/defects'
import {
  getOptionLabel,
  getOptionType,
  priorityOptions,
  severityOptions,
  statusOptions
} from './options'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const defect = ref({})
const transitions = ref([])
const comments = ref([])
const attachments = ref([])
const users = ref([])
const actionDialogVisible = ref(false)
const submittingAction = ref(false)
const submittingComment = ref(false)
const uploadingAttachment = ref(false)
const currentAction = ref('')
const commentContent = ref('')
const maxAttachmentSize = 10 * 1024 * 1024
const allowedAttachmentExtensions = ['.txt', '.log', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.doc', '.docx', '.xls', '.xlsx', '.zip']
const actionForm = reactive({
  assignee_id: '',
  comment: ''
})

const actionMap = {
  assign: { title: '指派缺陷', handler: assignDefect },
  startProgress: { title: '开始修复', handler: startProgressDefect },
  resolve: { title: '提交修复', handler: resolveDefect },
  verify: { title: '回归通过', handler: verifyDefect },
  reject: { title: '驳回缺陷', handler: rejectDefect },
  reopen: { title: '重新打开', handler: reopenDefect },
  close: { title: '关闭缺陷', handler: closeDefect }
}

const actionTitle = computed(() => actionMap[currentAction.value]?.title || '状态操作')

const getName = (value) => {
  if (!value) return '-'
  if (typeof value === 'string' || typeof value === 'number') return value
  return value.name || value.username || value.title || value.id || '-'
}

const formatDate = (value) => value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-'

const normalizeList = (data) => data?.results || data || []

const fetchUsers = async () => {
  try {
    const response = await api.get('/users/')
    users.value = normalizeList(response.data)
  } catch (error) {
    users.value = []
  }
}

const loadDefect = async () => {
  loading.value = true
  try {
    const response = await getDefect(route.params.id)
    defect.value = response.data || {}
    transitions.value = response.data?.transitions || response.data?.transition_records || []
    comments.value = response.data?.comments || []
    attachments.value = response.data?.attachments || []
  } catch (error) {
    defect.value = {}
    transitions.value = []
    comments.value = []
    attachments.value = []
    ElMessage.error('加载缺陷详情失败')
  } finally {
    loading.value = false
  }
}

const openAction = (action) => {
  currentAction.value = action
  actionForm.assignee_id = defect.value.assignee?.id || ''
  actionForm.comment = ''
  actionDialogVisible.value = true
}

const submitAction = async () => {
  const action = actionMap[currentAction.value]
  if (!action) return
  submittingAction.value = true
  try {
    const payload = { comment: actionForm.comment }
    if (currentAction.value === 'assign') {
      if (!actionForm.assignee_id) {
        ElMessage.warning('请选择处理人')
        return
      }
      payload.assignee_id = actionForm.assignee_id
    }
    await action.handler(route.params.id, payload)
    ElMessage.success('状态操作成功')
    actionDialogVisible.value = false
    loadDefect()
  } catch (error) {
    ElMessage.error(error?.response?.data?.error || '状态操作失败')
  } finally {
    submittingAction.value = false
  }
}

const submitComment = async () => {
  const content = commentContent.value.trim()
  if (!content) {
    ElMessage.warning('请输入评论内容')
    return
  }
  submittingComment.value = true
  try {
    await addDefectComment(route.params.id, { content })
    ElMessage.success('评论已发布')
    commentContent.value = ''
    loadDefect()
  } catch (error) {
    ElMessage.error('评论发布失败')
  } finally {
    submittingComment.value = false
  }
}

const uploadAttachment = async (uploadFile) => {
  if (!uploadFile?.raw) return
  const fileName = uploadFile.name || uploadFile.raw.name || ''
  const ext = fileName.includes('.') ? fileName.slice(fileName.lastIndexOf('.')).toLowerCase() : ''
  if (!allowedAttachmentExtensions.includes(ext)) {
    ElMessage.warning(`仅支持 ${allowedAttachmentExtensions.join('、')} 格式的附件`)
    return
  }
  if (uploadFile.raw.size > maxAttachmentSize) {
    ElMessage.warning('附件大小不能超过 10MB')
    return
  }
  const formData = new FormData()
  formData.append('file', uploadFile.raw)
  formData.append('name', uploadFile.name)
  uploadingAttachment.value = true
  try {
    await uploadDefectAttachment(route.params.id, formData)
    ElMessage.success('附件已上传')
    loadDefect()
  } catch (error) {
    ElMessage.error('附件上传失败')
  } finally {
    uploadingAttachment.value = false
  }
}

onMounted(() => {
  fetchUsers()
  loadDefect()
})
</script>

<style lang="scss" scoped>
.sub-title {
  margin-top: 4px;
  color: #909399;
}

.header-actions,
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.card-header {
  justify-content: space-between;
}

.detail-section {
  margin-bottom: 18px;

  h3 {
    margin: 0 0 8px;
    font-size: 15px;
    color: #303133;
  }

  p {
    margin: 0;
    line-height: 1.7;
    white-space: pre-wrap;
    color: #606266;
  }
}

.side-card,
.content-card {
  margin-bottom: 16px;
}

.action-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;

  .el-button {
    margin-left: 0;
  }
}

.timeline-comment {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}

.transition-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.transition-operator {
  color: #303133;
}

.transition-status {
  color: #606266;
}

.comment-list,
.attachment-list {
  margin-top: 16px;
}

.comment-item,
.attachment-item {
  padding: 12px 0;
  border-bottom: 1px solid #ebeef5;

  &:last-child {
    border-bottom: 0;
    padding-bottom: 0;
  }
}

.item-meta,
.attachment-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #909399;
  font-size: 12px;
}

.comment-item p {
  margin: 8px 0 0;
  color: #303133;
  line-height: 1.7;
  white-space: pre-wrap;
}

.full-width {
  width: 100%;
}
</style>
