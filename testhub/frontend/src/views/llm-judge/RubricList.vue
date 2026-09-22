<template>
  <div class="judge-rubrics">
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="card-header">
          <div>
            <h2 class="page-title">{{ $t('llmJudge.rubric.title') }}</h2>
            <p class="page-desc">{{ $t('llmJudge.rubric.desc') }}</p>
          </div>
          <el-button type="primary" @click="showCloneDialog">{{ $t('llmJudge.rubric.cloneFrom') }}</el-button>
        </div>
      </template>

      <el-table :data="rubrics" border stripe size="small" v-loading="loading" style="width:100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" :label="$t('llmJudge.rubric.name')" min-width="180" show-overflow-tooltip />
        <el-table-column :label="$t('llmJudge.rubric.domain')" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ domainText(row.domain) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="version" :label="$t('llmJudge.rubric.version')" width="80" />
        <el-table-column :label="$t('llmJudge.rubric.isDefault')" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small">{{ $t('llmJudge.rubric.isDefault') }}</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.rubric.dimensions')" width="80">
          <template #default="{ row }">{{ row.dimension_count }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.rubric.rules')" width="80">
          <template #default="{ row }">{{ row.rule_count }}</template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column :label="$t('llmJudge.common.operation')" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="showDetail(row)">{{ $t('llmJudge.common.viewDetail') }}</el-button>
            <el-button v-if="!row.is_default" link type="warning" size="small" @click="handleSetDefault(row)">{{ $t('llmJudge.common.setDefault') }}</el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">{{ $t('llmJudge.common.delete') }}</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination v-model:current-page="page" v-model:page-size="pageSize"
          :total="total" :page-sizes="[20, 50]" layout="total, prev, pager, next"
          @size-change="loadData" @current-change="loadData" />
      </div>
    </el-card>

    <!-- 详情对话框 -->
    <el-dialog v-model="detailVisible" :title="$t('llmJudge.rubric.detailTitle')" width="820px" top="5vh">
      <div v-if="detail">
        <el-descriptions :column="2" border size="small" class="detail-desc">
          <el-descriptions-item :label="$t('llmJudge.rubric.name')">{{ detail.name }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.rubric.domain')">{{ domainText(detail.domain) }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.rubric.version')">{{ detail.version }}</el-descriptions-item>
          <el-descriptions-item :label="$t('llmJudge.rubric.isDefault')">{{ detail.is_default ? '✔' : '—' }}</el-descriptions-item>
        </el-descriptions>

        <el-tabs v-model="activeTab" class="detail-tabs">
          <el-tab-pane :label="$t('llmJudge.rubric.dimensionsTab')" name="dims">
            <el-table :data="detail.dimensions" border size="small" style="width:100%">
              <el-table-column prop="dim_key" label="ID" width="160" />
              <el-table-column prop="name" :label="$t('llmJudge.rubric.name')" width="140" />
              <el-table-column prop="dim_type" label="类型" width="80" />
              <el-table-column :label="$t('llmJudge.rubric.weight')" width="80">
                <template #default="{ row }">{{ (row.weight * 100).toFixed(0) }}%</template>
              </el-table-column>
              <el-table-column :label="$t('llmJudge.rubric.veto')" width="80">
                <template #default="{ row }">{{ row.vetoable ? '✔' : '—' }}</template>
              </el-table-column>
              <el-table-column prop="anchor_text" label="评分锚点" min-width="240" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="$t('llmJudge.rubric.rulesTab')" name="rules">
            <el-table :data="detail.rules" border size="small" style="width:100%">
              <el-table-column prop="rule_key" label="ID" width="160" />
              <el-table-column prop="name" :label="$t('llmJudge.rubric.name')" width="160" />
              <el-table-column prop="severity" label="级别" width="80" />
              <el-table-column :label="$t('llmJudge.rubric.veto')" width="80">
                <template #default="{ row }">{{ row.is_veto ? '✔' : '—' }}</template>
              </el-table-column>
              <el-table-column :label="$t('llmJudge.rubric.enabled')" width="80">
                <template #default="{ row }">{{ row.enabled ? '✔' : '—' }}</template>
              </el-table-column>
              <el-table-column prop="fallback_mode" label="降级模式" width="100" />
            </el-table>
          </el-tab-pane>
          <el-tab-pane :label="$t('llmJudge.rubric.configTab')" name="config">
            <div class="config-block">
              <h4 class="cfg-title">评分权重</h4>
              <pre class="cfg-json">{{ fmtJSON(detail.scoring_weights) }}</pre>
              <h4 class="cfg-title">门禁配置</h4>
              <pre class="cfg-json">{{ fmtJSON(detail.gate_config) }}</pre>
              <h4 class="cfg-title">Judge 配置</h4>
              <pre class="cfg-json">{{ fmtJSON(detail.judge_config) }}</pre>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-dialog>

    <!-- 克隆预设对话框 -->
    <el-dialog v-model="cloneVisible" :title="$t('llmJudge.rubric.cloneFrom')" width="520px">
      <el-form :model="cloneForm" label-position="top">
        <el-form-item :label="$t('llmJudge.rubric.cloneFrom')">
          <el-select v-model="cloneForm.clone_from" placeholder="选择预设" style="width:100%">
            <el-option v-for="r in presets" :key="r.id"
              :label="`${r.name}（${domainText(r.domain)}）`" :value="r.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('llmJudge.rubric.name')">
          <el-input v-model="cloneForm.name" placeholder="新评分标准名称" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.rubric.domain')">
          <el-select v-model="cloneForm.domain" style="width:100%">
            <el-option v-for="k in domainKeys" :key="k" :label="domainText(k)" :value="k" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cloneVisible = false">{{ $t('llmJudge.common.cancel') }}</el-button>
        <el-button type="primary" :loading="cloning" @click="handleClone">{{ $t('llmJudge.common.confirm') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getRubricList, getRubricDetail, getRubricPresets, createRubric, deleteRubric, setDefaultRubric } from '@/api/llm-judge'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const loading = ref(false)
const rubrics = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(50)
const detailVisible = ref(false)
const detail = ref(null)
const activeTab = ref('dims')
const cloneVisible = ref(false)
const cloning = ref(false)
const presets = ref([])

const domainKeys = ['finance', 'qa', 'customer_service', 'custom']

const cloneForm = reactive({
  clone_from: null,
  name: '',
  domain: 'custom'
})

const loadData = async () => {
  loading.value = true
  try {
    const res = await getRubricList({ page: page.value, page_size: pageSize.value })
    rubrics.value = res.data.results || res.data
    total.value = res.data.count || rubrics.value.length
  } catch (e) { /* ignore */ } finally {
    loading.value = false
  }
}

const domainText = (k) => t(`llmJudge.rubric.domains.${k}`)

const showDetail = async (row) => {
  try {
    const res = await getRubricDetail(row.id)
    detail.value = res.data
    activeTab.value = 'dims'
    detailVisible.value = true
  } catch (e) { ElMessage.error(t('llmJudge.common.failure')) }
}

const handleSetDefault = async (row) => {
  try {
    await setDefaultRubric(row.id)
    ElMessage.success(t('llmJudge.common.success'))
    loadData()
  } catch (e) { ElMessage.error(t('llmJudge.common.failure')) }
}

const handleDelete = (row) => {
  ElMessageBox.confirm(t('llmJudge.rubric.deleteConfirm'), t('llmJudge.common.confirm'), {
    type: 'warning'
  }).then(async () => {
    try {
      await deleteRubric(row.id)
      ElMessage.success(t('llmJudge.common.success'))
      loadData()
    } catch (e) { ElMessage.error(t('llmJudge.common.failure')) }
  }).catch(() => {})
}

const showCloneDialog = async () => {
  cloneForm.clone_from = null
  cloneForm.name = ''
  cloneForm.domain = 'custom'
  try {
    const res = await getRubricPresets()
    presets.value = res.data
  } catch (e) { /* ignore */ }
  cloneVisible.value = true
}

const handleClone = async () => {
  if (!cloneForm.clone_from || !cloneForm.name.trim()) {
    ElMessage.warning(t('llmJudge.common.confirm'))
    return
  }
  cloning.value = true
  try {
    await createRubric({
      name: cloneForm.name,
      domain: cloneForm.domain,
      version: '1.0.0',
      is_active: true,
      scoring_weights: { rule: 0.4, llm: 0.6 },
      gate_config: { green_mean: 85, yellow_mean: 70, safety_pass_rate: 1.0, critical_success_rate: 0.95 },
      judge_config: { n_runs: 3, temperature: 0.0, judge_models: [] },
      clone_from: cloneForm.clone_from
    })
    ElMessage.success(t('llmJudge.common.success'))
    cloneVisible.value = false
    loadData()
  } catch (e) { ElMessage.error(t('llmJudge.common.failure')) } finally {
    cloning.value = false
  }
}

const fmtJSON = (obj) => obj ? JSON.stringify(obj, null, 2) : '{}'

onMounted(() => { loadData() })
</script>

<style scoped lang="scss">
.judge-rubrics {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-card {
  border-radius: 8px;
  :deep(.el-card__header) { padding: 16px 20px; }
  :deep(.el-card__body) { padding: 20px; }
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.page-title { margin: 0; font-size: 18px; color: #303133; }
.page-desc { margin: 4px 0 0; font-size: 13px; color: #909399; }
.pagination-wrap {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.detail-desc { margin-bottom: 16px; }
.detail-tabs { min-height: 300px; }
.config-block {
  max-height: 50vh;
  overflow-y: auto;
}
.cfg-title {
  font-size: 13px;
  color: #303133;
  margin: 12px 0 6px;
  padding-left: 8px;
  border-left: 3px solid #409eff;
  &:first-child { margin-top: 0; }
}
.cfg-json {
  margin: 0;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 12px;
  color: #303133;
  line-height: 1.5;
  overflow-x: auto;
}
</style>
