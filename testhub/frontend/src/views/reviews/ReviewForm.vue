<template>
  <div class="page-container">
    <div class="page-header">
      <h1 class="page-title">{{ isEdit ? $t('reviewForm.editTitle') : $t('reviewForm.createTitle') }}</h1>
      <div>
        <el-button @click="$router.back()">{{ $t('reviewForm.back') }}</el-button>
        <el-button type="primary" @click="saveReview" :loading="saving">{{ $t('reviewForm.save') }}</el-button>
      </div>
    </div>

    <div class="form-container">
      <el-form :model="form" :rules="rules" ref="formRef" label-width="120px">
        <el-row :gutter="24">
          <el-col :span="12">
            <el-form-item :label="$t('reviewForm.reviewTitle')" prop="title">
              <el-input v-model="form.title" :placeholder="$t('reviewForm.reviewTitlePlaceholder')" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item :label="$t('reviewForm.associatedProject')" prop="projects">
              <el-select
                v-model="form.projects"
                multiple
                :placeholder="$t('reviewForm.selectProject')"
                @change="onProjectChange"
              >
                <el-option
                  v-for="project in projects"
                  :key="project.id"
                  :label="project.name"
                  :value="project.id"
                />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="24">
          <el-col :span="12">
            <el-form-item :label="$t('reviewForm.priority')" prop="priority">
              <el-select v-model="form.priority" :placeholder="$t('reviewForm.selectPriority')">
                <el-option :label="$t('reviewForm.priorityLow')" value="low" />
                <el-option :label="$t('reviewForm.priorityMedium')" value="medium" />
                <el-option :label="$t('reviewForm.priorityHigh')" value="high" />
                <el-option :label="$t('reviewForm.priorityUrgent')" value="urgent" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item :label="$t('reviewForm.deadline')" prop="deadline">
              <el-date-picker
                v-model="form.deadline"
                type="datetime"
                :placeholder="$t('reviewForm.deadlinePlaceholder')"
                format="YYYY-MM-DD HH:mm"
                value-format="YYYY-MM-DD HH:mm:ss"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item :label="$t('reviewForm.description')">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            :placeholder="$t('reviewForm.descriptionPlaceholder')"
          />
        </el-form-item>

        <el-form-item :label="$t('reviewForm.selectTestcases')" prop="testcases">
          <div class="testcase-selector">
            <div class="search-bar">
              <el-button
                type="primary"
                plain
                :disabled="!form.projects || form.projects.length === 0"
                @click="showTestcaseSelector"
              >
                {{ $t('reviewForm.selectTestcasesBtn') }}
              </el-button>
              <span class="testcase-selection-tip">
                {{
                  selectedTestcases.length > 0
                    ? $t('reviewForm.selectedTestcasesCount', { count: selectedTestcases.length })
                    : $t('reviewForm.noTestcasesSelected')
                }}
              </span>
              <el-button
                v-if="selectedTestcases.length > 0"
                link
                type="primary"
                @click="showTestcaseSelector"
              >
                {{ $t('reviewForm.modifyTestcasesSelection') }}
              </el-button>
            </div>
          </div>
        </el-form-item>

        <el-form-item :label="$t('reviewForm.reviewers')" prop="reviewers">
          <el-select
            v-model="form.reviewers"
            multiple
            :placeholder="$t('reviewForm.selectReviewers')"
            @change="onReviewersChange"
          >
            <el-option
              v-for="user in projectUsers"
              :key="user.id"
              :label="user.username"
              :value="user.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item :label="$t('reviewForm.reviewTemplate')">
          <el-select v-model="form.template" :placeholder="$t('reviewForm.selectTemplate')" @change="applyTemplate">
            <el-option
              v-for="template in templates"
              :key="template.id"
              :label="template.name"
              :value="template.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
    </div>

    <!-- 用例选择对话框 -->
    <el-dialog v-model="testcaseSelectorVisible" :title="$t('reviewForm.testcaseSelectorTitle')" :close-on-click-modal="false" width="800px">
      <div class="testcase-selector-content">
        <el-input
          v-model="testcaseSearchInDialog"
          :placeholder="$t('reviewForm.searchTestcases')"
          @input="onTestcaseSearch"
          clearable
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-table
          ref="testcaseTableRef"
          :data="testcases"
          :row-key="row => row.id"
          v-loading="testcaseLoading"
          @select="onTestcaseSelect"
          @select-all="onTestcaseSelectAll"
          max-height="400"
          class="testcase-table"
        >
          <el-table-column type="selection" width="55" reserve-selection />
          <el-table-column prop="title" :label="$t('reviewForm.testcaseTitle')" min-width="200" show-overflow-tooltip />
          <el-table-column prop="test_type" :label="$t('reviewForm.testType')" width="120" />
          <el-table-column prop="priority" :label="$t('reviewForm.priority')" width="100">
            <template #default="{ row }">
              <el-tag :class="`priority-tag ${row.priority}`">{{ getPriorityText(row.priority) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('reviewForm.author')" width="120">
            <template #default="{ row }">
              {{ row.author?.username }}
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-container">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50, 100]"
            :total="total"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="handlePageChange"
            @size-change="handleSizeChange"
          />
        </div>
      </div>
      <template #footer>
        <el-button @click="testcaseSelectorVisible = false">{{ $t('reviewForm.cancel') }}</el-button>
        <el-button type="primary" @click="confirmTestcaseSelection">{{ $t('reviewForm.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { debounce } from 'lodash-es'
import api from '@/utils/api'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const isEdit = computed(() => !!route.params.id)

const formRef = ref()
const saving = ref(false)
const testcaseSelectorVisible = ref(false)
const testcaseSearchInDialog = ref('')

const projects = ref([])
const projectUsers = ref([])
const templates = ref([])
const testcases = ref([])              // 当前页用例数据
const selectedTestcases = ref([])      // 跨页累计选中的用例（完整对象）
const testcaseTableRef = ref()

// 用例弹框分页状态
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const testcaseLoading = ref(false)

// 按 id 查已选用例，便于跨页判定与回填勾选
const isSelected = (id) => selectedTestcases.value.some(tc => tc.id === id)

const form = reactive({
  title: '',
  description: '',
  projects: [],
  priority: 'medium',
  deadline: '',
  testcases: [],
  reviewers: [],
  template: ''
})

const rules = computed(() => ({
  title: [{ required: true, message: t('reviewForm.titleRequired'), trigger: 'blur' }],
  projects: [{ required: true, message: t('reviewForm.projectRequired'), trigger: 'change' }],
  deadline: [{ required: true, message: t('reviewForm.deadlineRequired'), trigger: 'change' }],
  testcases: [{ required: true, message: t('reviewForm.testcasesRequired'), trigger: 'change' }],
  reviewers: [{ required: true, message: t('reviewForm.reviewersRequired'), trigger: 'change' }]
}))

const fetchProjects = async () => {
  try {
    const response = await api.get('/projects/')
    projects.value = response.data.results || response.data
  } catch (error) {
    ElMessage.error(t('reviewForm.fetchProjectsFailed'))
  }
}

const fetchProjectUsers = async () => {
  try {
    const response = await api.get('/auth/users/')
    projectUsers.value = response.data.results || response.data || []
    console.log('All users:', projectUsers.value)
  } catch (error) {
    console.error('Fetch users failed:', error)
    ElMessage.error(t('reviewForm.fetchUsersFailed'))
    projectUsers.value = []
  }
}

const fetchTestcases = async (projectIds) => {
  // 未选项目则清空
  if (!projectIds || projectIds.length === 0) {
    testcases.value = []
    total.value = 0
    return
  }

  testcaseLoading.value = true
  try {
    const response = await api.get('/testcases/', {
      params: {
        project: projectIds.join(','),
        page: currentPage.value,
        page_size: pageSize.value,
        search: testcaseSearchInDialog.value || undefined
      }
    })
    testcases.value = response.data.results || []
    total.value = response.data.count || 0
    // 数据回来后回填本页中已勾选的行
    await nextTick()
    restoreTableSelection()
  } catch (error) {
    console.error('Fetch testcases failed:', error)
  } finally {
    testcaseLoading.value = false
  }
}

// 回填当前页中已选中的行（跨页保留勾选状态）
const restoreTableSelection = () => {
  const table = testcaseTableRef.value
  if (!table) return
  testcases.value.forEach(tc => {
    if (isSelected(tc.id)) {
      table.toggleRowSelection(tc, true)
    }
  })
}

// 分页
const handlePageChange = (val) => {
  currentPage.value = val
  fetchTestcases(form.projects)
}

const handleSizeChange = (val) => {
  pageSize.value = val
  currentPage.value = 1
  fetchTestcases(form.projects)
}

// 搜索（防抖，回到第 1 页）
const onTestcaseSearch = debounce(() => {
  currentPage.value = 1
  fetchTestcases(form.projects)
}, 300)

// 跨页勾选维护：单行勾选/取消
const onTestcaseSelect = (selection, row) => {
  if (selection.includes(row)) {
    if (!isSelected(row.id)) selectedTestcases.value.push(row)
  } else {
    selectedTestcases.value = selectedTestcases.value.filter(tc => tc.id !== row.id)
  }
}

// 跨页勾选维护：本页全选/全不选
const onTestcaseSelectAll = (selection) => {
  const selectedIds = new Set(selection.map(tc => tc.id))
  // 先把本页里、但已不在 selection 的移除
  selectedTestcases.value = selectedTestcases.value.filter(
    tc => selectedIds.has(tc.id) || !testcases.value.some(t => t.id === tc.id)
  )
  // 再把本页里新选中的补上
  selection.forEach(tc => {
    if (!isSelected(tc.id)) selectedTestcases.value.push(tc)
  })
}

const fetchTemplates = async (projectIds) => {
  try {
    // 如果没有选择项目，清空模板列表
    if (!projectIds || projectIds.length === 0) {
      templates.value = []
      return
    }

    // 获取所有选中项目的模板
    const promises = projectIds.map(projectId =>
      api.get('/reviews/review-templates/', { params: { project: projectId } })
    )

    const responses = await Promise.all(promises)
    const allTemplates = []

    responses.forEach(response => {
      const temps = response.data.results || response.data || []
      allTemplates.push(...temps)
    })

    // 去重（基于模板ID）
    const uniqueTemplates = allTemplates.filter((template, index, self) =>
      index === self.findIndex(t => t.id === template.id)
    )

    templates.value = uniqueTemplates
  } catch (error) {
    console.error('Fetch templates failed:', error)
  }
}

const onProjectChange = (projectIds) => {
  // 项目变化时重置分页与已选用例
  currentPage.value = 1
  testcaseSearchInDialog.value = ''
  selectedTestcases.value = []

  if (projectIds && projectIds.length > 0) {
    fetchTestcases(projectIds)
    fetchTemplates(projectIds)
  } else {
    // 如果没有选择项目，清空相关数据
    testcases.value = []
    total.value = 0
    templates.value = []
  }

  // 清空相关选择
  form.reviewers = []
  form.testcases = []
}

const showTestcaseSelector = () => {
  if (!form.projects || form.projects.length === 0) {
    ElMessage.warning(t('reviewForm.selectProjectFirst'))
    return
  }
  testcaseSelectorVisible.value = true
  // 打开弹框时回到第 1 页并加载（保留已选中的用例，用于回填勾选）
  currentPage.value = 1
  fetchTestcases(form.projects)
}

const confirmTestcaseSelection = () => {
  // selectedTestcases 在勾选时已实时维护，直接提交
  form.testcases = selectedTestcases.value.map(tc => tc.id)
  testcaseSelectorVisible.value = false
}

const onReviewersChange = () => {
  // 可以在这里添加评审人员变更的逻辑
}

const applyTemplate = async (templateId) => {
  if (!templateId) return

  try {
    const template = templates.value.find(t => t.id === templateId)
    if (template) {
      // 应用模板的默认评审人员
      form.reviewers = template.default_reviewers.map(u => u.id)
    }
  } catch (error) {
    console.error('Apply template failed:', error)
  }
}

const saveReview = async () => {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
    saving.value = true

    // 截止日期预处理：未选则传 null（字段可空）；有值则转 ISO-8601 格式，
    // 避免 "YYYY-MM-DD HH:mm:ss"（空格分隔）被后端 DateTimeField 判为格式错误
    let deadline = null
    if (form.deadline) {
      deadline = form.deadline.replace(' ', 'T')
    }

    const data = {
      ...form,
      testcases: form.testcases,
      reviewers: form.reviewers,
      deadline,
      // 确保包含 template 字段
      template: form.template || null
    }

    if (isEdit.value) {
      await api.put(`/reviews/reviews/${route.params.id}/`, data)
      ElMessage.success(t('reviewForm.updateSuccess'))
    } else {
      await api.post('/reviews/reviews/', data)
      ElMessage.success(t('reviewForm.createSuccess'))
    }

    router.push('/ai-generation/reviews')

  } catch (error) {
    if (error.response?.data) {
      const errors = Object.values(error.response.data).flat()
      ElMessage.error(errors[0] || t('reviewForm.saveFailed'))
    } else {
      ElMessage.error(t('reviewForm.saveFailed'))
    }
  } finally {
    saving.value = false
  }
}

const getPriorityText = (priority) => {
  const textMap = {
    low: t('reviewForm.priorityLow'),
    medium: t('reviewForm.priorityMedium'),
    high: t('reviewForm.priorityHigh'),
    critical: t('reviewForm.priorityUrgent')
  }
  return textMap[priority] || priority
}

const findMatchingTemplate = (review, templateList) => {
  if (!templateList || templateList.length === 0) return null

  // 获取评审的项目ID列表和评审人ID列表
  const reviewProjectIds = review.projects.map(p => p.id).sort()
  const reviewReviewerIds = review.assignments.map(a => a.reviewer.id).sort()

  let bestMatch = null
  let bestScore = 0

  for (const template of templateList) {
    let score = 0

    // 检查项目匹配度
    const templateProjectIds = (template.project || []).map(p => p.id).sort()
    const projectIntersection = reviewProjectIds.filter(id => templateProjectIds.includes(id))
    if (projectIntersection.length > 0) {
      score += projectIntersection.length * 2 // 项目匹配权重更高
    }

    // 检查默认评审人匹配度
    const templateReviewerIds = (template.default_reviewers || []).map(r => r.id).sort()
    const reviewerIntersection = reviewReviewerIds.filter(id => templateReviewerIds.includes(id))
    if (reviewerIntersection.length > 0) {
      score += reviewerIntersection.length // 评审人匹配
    }

    // 如果有更高的匹配分数，更新最佳匹配
    if (score > bestScore) {
      bestScore = score
      bestMatch = template
    }
  }

  // 只有当匹配分数大于0时才返回匹配的模板
  return bestScore > 0 ? bestMatch : null
}

const fetchReviewData = async (reviewId) => {
  try {
    const response = await api.get(`/reviews/reviews/${reviewId}/`)
    const review = response.data

    // 填充表单数据
    form.title = review.title
    form.description = review.description
    form.projects = review.projects.map(p => p.id)
    form.priority = review.priority
    form.deadline = review.deadline
    form.reviewers = review.assignments.map(a => a.reviewer.id)

    // 处理测试用例
    selectedTestcases.value = review.testcases
    form.testcases = review.testcases.map(tc => tc.id)

    // 加载项目相关数据
    if (form.projects.length > 0) {
      await fetchTestcases(form.projects)
      await fetchTemplates(form.projects)

      // 在模板加载完成后，尝试找到匹配的模板
      const matchingTemplate = findMatchingTemplate(review, templates.value)
      if (matchingTemplate) {
        form.template = matchingTemplate.id
      }
    }

  } catch (error) {
    console.error('Fetch review data failed:', error)
    ElMessage.error(t('reviewForm.fetchReviewFailed'))
    router.push('/ai-generation/reviews')
  }
}

onMounted(async () => {
  await fetchProjects()
  fetchProjectUsers() // 页面加载时就获取所有用户

  if (isEdit.value) {
    // 如果是编辑模式，加载现有数据
    fetchReviewData(route.params.id)
  } else {
    // 创建模式，检查是否有模板参数
    const templateId = route.query.template
    if (templateId) {
      try {
        // 获取模板详情
        const response = await api.get(`/reviews/review-templates/${templateId}/`)
        const template = response.data

        // 设置模板ID到表单
        form.template = parseInt(templateId)

        // 自动填充项目
        if (template.project && template.project.length > 0) {
          form.projects = template.project.map(p => p.id)

          // 触发项目变更，加载对应的用例和模板
          await onProjectChange(form.projects)

          // 应用模板的默认评审人
          if (template.default_reviewers && template.default_reviewers.length > 0) {
            form.reviewers = template.default_reviewers.map(u => u.id)
          }
        }
      } catch (error) {
        console.error('加载模板失败:', error)
      }
    }
  }
})
</script>

<style lang="scss" scoped>
.testcase-selector {
  .search-bar {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .testcase-selection-tip {
    color: #606266;
    font-size: 14px;
  }
}

.testcase-selector-content {
  .el-input {
    margin-bottom: 16px;
  }

  .pagination-container {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    margin-top: 16px;
    padding-top: 4px;
  }
}

.priority-tag {
  &.low { color: #67c23a; }
  &.medium { color: #e6a23c; }
  &.high { color: #f56c6c; }
  &.critical { color: #f56c6c; font-weight: bold; }
}
</style>
