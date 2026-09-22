<template>
  <div class="judge-batch">
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="card-header">
          <div>
            <h2 class="page-title">{{ $t('llmJudge.batch.title') }}</h2>
            <p class="page-desc">{{ $t('llmJudge.batch.desc') }}</p>
          </div>
        </div>
      </template>

      <el-form :model="form" label-position="top" class="batch-form">
        <el-row :gutter="16">
          <el-col :xs="24" :sm="12">
            <el-form-item :label="$t('llmJudge.batch.batchName')">
              <el-input v-model="form.name" :placeholder="$t('llmJudge.batch.batchNamePlaceholder')" clearable />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item :label="$t('llmJudge.batch.selectRubric')">
              <el-select v-model="form.rubric" :placeholder="$t('llmJudge.batch.useDefault')" clearable style="width:100%">
                <el-option v-for="r in rubrics" :key="r.id" :label="r.name + (r.is_default ? '（默认）' : '')" :value="r.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <!-- 用例输入 Tab：文本 / 上传文件 -->
        <el-tabs v-model="inputTab" class="input-tabs">
          <el-tab-pane :label="$t('llmJudge.batch.casesInput')" name="text">
            <div class="input-tip">
              <el-icon><InfoFilled /></el-icon>
              <span>{{ $t('llmJudge.batch.casesInputTip') }}</span>
            </div>
            <el-input
              v-model="form.casesText"
              type="textarea"
              :rows="8"
              :placeholder="$t('llmJudge.batch.casesInputPlaceholder')"
            />
          </el-tab-pane>

          <el-tab-pane :label="$t('llmJudge.batch.fileUpload')" name="file">
            <div class="file-upload-wrap">
              <div class="input-tip">
                <el-icon><InfoFilled /></el-icon>
                <span>{{ $t('llmJudge.batch.fileUploadTip') }}</span>
              </div>

              <div class="upload-actions">
                <el-upload
                  ref="uploadRef"
                  :auto-upload="false"
                  :show-file-list="false"
                  :on-change="handleFileChange"
                  accept=".csv,.xlsx,.xlsm,.txt,.md"
                  :limit="1"
                  drag
                  class="batch-uploader"
                >
                  <div class="uploader-inner">
                    <el-icon class="uploader-icon"><UploadFilled /></el-icon>
                    <div class="uploader-text">
                      <div class="uploader-title">
                        <el-button type="primary" :icon="FolderAdd">{{ $t('llmJudge.batch.fileSelect') }}</el-button>
                        <span style="margin-left:10px;color:#606266;">CSV / XLSX / TXT</span>
                      </div>
                      <div class="uploader-hint" style="color:#909399;margin-top:6px;">
                        拖拽文件到此处，或点击选择文件（最大 10MB，最多 5000 条）
                      </div>
                    </div>
                  </div>
                </el-upload>

                <div class="upload-side">
                  <div class="tpl-label">{{ $t('llmJudge.batch.templateDownload') }}：</div>
                  <div class="tpl-buttons">
                    <el-button link type="primary" @click="downloadTpl('csv')">CSV</el-button>
                    <el-button link type="primary" @click="downloadTpl('xlsx')">XLSX</el-button>
                    <el-button link type="primary" @click="downloadTpl('txt')">TXT</el-button>
                  </div>
                  <el-alert
                    v-if="parsedFile"
                    :title="$t('llmJudge.batch.parsedOk', { n: parsedFile.valid_rows }) + (parsedFile.errors.length ? '，' + $t('llmJudge.batch.parsedErrors', { n: parsedFile.errors.length }) : '')"
                    :type="parsedFile.errors.length ? 'warning' : 'success'"
                    :closable="false"
                    show-icon
                    style="margin-top:10px"
                  />
                  <el-button
                    v-if="parsedFile && parsedFile.valid_rows"
                    type="success"
                    plain
                    :icon="Check"
                    @click="applyParsed"
                    style="margin-top:10px;width:100%"
                  >
                    {{ $t('llmJudge.batch.useFileCases') }}（{{ parsedFile.valid_rows }} {{ $t('llmJudge.common.rows') }}）
                  </el-button>
                </div>
              </div>

              <!-- 解析错误详情 -->
              <el-alert
                v-if="parsedFile && parsedFile.errors.length"
                type="warning"
                :closable="false"
                show-icon
                style="margin-top:12px"
              >
                <template #title>解析提示（{{ parsedFile.errors.length }} 条）</template>
                <ul class="parse-error-list">
                  <li v-for="(e, i) in parsedFile.errors.slice(0, 30)" :key="i">{{ e }}</li>
                  <li v-if="parsedFile.errors.length > 30" class="muted">……及另外 {{ parsedFile.errors.length - 30 }} 条</li>
                </ul>
              </el-alert>

              <!-- 预览表 -->
              <div v-if="parsedFile && parsedFile.preview && parsedFile.preview.length" class="parsed-preview">
                <div class="preview-title">
                  <span>{{ $t('llmJudge.batch.parsedPreview') }}</span>
                  <el-tag type="info" size="small">共 {{ parsedFile.total_rows }} 行，有效 {{ parsedFile.valid_rows }} 条</el-tag>
                </div>
                <el-table :data="parsedFile.preview" border stripe size="small" max-height="300">
                  <el-table-column type="index" label="#" width="50" />
                  <el-table-column :label="$t('llmJudge.common.question')" min-width="240" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.question }}</template>
                  </el-table-column>
                  <el-table-column :label="$t('llmJudge.common.answer')" min-width="260" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.answer }}</template>
                  </el-table-column>
                  <el-table-column :label="$t('llmJudge.common.groundTruth')" min-width="160" show-overflow-tooltip>
                    <template #default="{ row }">
                      <span v-if="row.ground_truth">{{ typeof row.ground_truth === 'string' ? row.ground_truth : (row.ground_truth.text || 'JSON') }}</span>
                      <el-tag v-else size="small" type="success" effect="plain">自动匹配</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column :label="$t('llmJudge.common.autoGt')" width="100">
                    <template #default="{ row }">
                      <el-tag v-if="row.auto_gt" size="small" type="success">是</el-tag>
                      <el-tag v-else size="small">否</el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>

        <div class="form-actions">
          <el-button type="primary" :loading="submitting" :disabled="!hasCases()" @click="handleSubmit">
            {{ $t('llmJudge.batch.startBatch') }}
          </el-button>
          <el-button @click="handleClear">{{ $t('llmJudge.batch.clearInput') }}</el-button>
          <span class="cases-count">当前用例数：<b>{{ getCaseCount() }}</b></span>
        </div>
      </el-form>
    </el-card>

    <!-- 进度区 -->
    <el-card v-if="currentBatch" shadow="never" class="page-card">
      <template #header>
        <div class="card-header">
          <h3 class="section-title">{{ $t('llmJudge.batch.progressTitle') }}</h3>
          <el-tag :type="statusTagType(currentBatch.status)" effect="dark" size="small">
            {{ statusText(currentBatch.status) }}
          </el-tag>
        </div>
      </template>
      <div class="progress-wrap">
        <el-progress :percentage="currentBatch.progress || 0" :status="progressStatus" :stroke-width="20" :text-inside="true" striped striped-flow />
        <div class="progress-meta">
          <span>{{ $t('llmJudge.common.scored') }}：{{ currentBatch.scored || 0 }} / {{ currentBatch.total || 0 }}</span>
          <span class="progress-tip">{{ $t('llmJudge.batch.progressTip') }}</span>
        </div>
      </div>

      <!-- 汇总 -->
      <div v-if="currentBatch.status === 'completed'" class="summary-grid">
        <div class="summary-item">
          <div class="summary-label">{{ $t('llmJudge.common.meanScore') }}</div>
          <div class="summary-value score">{{ fmtNum(currentBatch.mean_score) }}</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">{{ $t('llmJudge.batch.stdDev') }}</div>
          <div class="summary-value">{{ fmtNum(currentBatch.std_dev) }}</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">{{ $t('llmJudge.batch.safetyPassRate') }}</div>
          <div class="summary-value">{{ fmtPct(currentBatch.safety_pass_rate) }}</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">{{ $t('llmJudge.batch.criticalSuccessRate') }}</div>
          <div class="summary-value">{{ fmtPct(currentBatch.critical_success_rate) }}</div>
        </div>
        <div class="summary-item">
          <div class="summary-label">{{ $t('llmJudge.common.gateZone') }}</div>
          <div class="summary-value">
            <el-tag :type="zoneTagType(currentBatch.gate_zone)" effect="dark" size="small">
              {{ zoneText(currentBatch.gate_zone) }}
            </el-tag>
          </div>
        </div>
        <div class="summary-item">
          <div class="summary-label">{{ $t('llmJudge.gate.blocked') }}</div>
          <div class="summary-value">
            <el-tag :type="currentBatch.blocked ? 'danger' : 'success'" effect="plain" size="small">
              {{ currentBatch.blocked ? $t('llmJudge.gate.blocked') : $t('llmJudge.common.success') }}
            </el-tag>
          </div>
        </div>
      </div>

      <div v-if="currentBatch.status === 'completed'" class="view-records-btn">
        <el-button type="primary" plain @click="loadRecords(currentBatch.id)">
          {{ $t('llmJudge.batch.viewRecords') }}
        </el-button>
      </div>
    </el-card>

    <!-- 明细 -->
    <el-card v-if="records.length" shadow="never" class="page-card">
      <template #header>
        <h3 class="section-title">{{ $t('llmJudge.batch.recordsTitle') }}</h3>
      </template>
      <el-table :data="records" border stripe size="small" style="width:100%">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column :label="$t('llmJudge.common.question')" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.question }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.score')" width="90">
          <template #default="{ row }">
            <span :class="['score-num', scoreClass(row.final_score)]">{{ fmtNum(row.final_score) }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.label')" width="100">
          <template #default="{ row }">
            <el-tag :type="labelTagType(row.overall_label)" size="small">{{ labelText(row.overall_label) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.gateZone')" width="100">
          <template #default="{ row }">
            <el-tag :type="zoneTagType(row.gate_zone)" size="small" effect="plain">{{ zoneText(row.gate_zone) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.vetoed')" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.vetoed" type="danger" size="small">{{ $t('llmJudge.common.vetoed') }}</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.latency')" width="90">
          <template #default="{ row }">{{ row.latency_ms ? row.latency_ms + 'ms' : '—' }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.operation')" width="100" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="showRecordDetail(row)">明细</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 批次历史 -->
    <el-card shadow="never" class="page-card">
      <template #header>
        <h3 class="section-title">{{ $t('llmJudge.batch.batchHistory') }}</h3>
      </template>
      <el-table :data="batchHistory" border stripe size="small" style="width:100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="name" :label="$t('llmJudge.batch.batchName')" min-width="160" show-overflow-tooltip />
        <el-table-column prop="rubric_name" :label="$t('llmJudge.common.rubric')" width="140" show-overflow-tooltip />
        <el-table-column :label="$t('llmJudge.common.status')" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.progress')" width="120">
          <template #default="{ row }">{{ row.scored || 0 }} / {{ row.total || 0 }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.meanScore')" width="100">
          <template #default="{ row }">{{ fmtNum(row.mean_score) }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.gateZone')" width="100">
          <template #default="{ row }">
            <el-tag :type="zoneTagType(row.gate_zone)" size="small" effect="plain">{{ zoneText(row.gate_zone) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.createdAt')" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column :label="$t('llmJudge.common.operation')" width="200" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'running' || row.status === 'pending' || row.status === 'partial'"
              link type="warning" size="small"
              @click="handlePause(row)">{{ $t('llmJudge.common.pause') }}</el-button>
            <el-button
              v-if="row.status === 'paused' || row.status === 'partial' || row.status === 'failed'"
              link type="primary" size="small"
              @click="handleResume(row)">{{ $t('llmJudge.common.resume') }}</el-button>
            <el-button link type="primary" size="small" @click="viewBatch(row)">{{ $t('llmJudge.common.viewDetail') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 单条执行明细 Drawer -->
    <el-drawer v-model="recordDetailVisible" :title="$t('llmJudge.batch.recordDetailTitle')" size="640px" direction="rtl">
      <div v-if="currentRecord" class="record-detail">
        <el-descriptions :column="2" border size="small" title="基础信息">
          <el-descriptions-item label="请求ID" :span="2">
            <code class="mono">{{ currentRecord.request_id || '-' }}</code>
          </el-descriptions-item>
          <el-descriptions-item label="评分标准">
            {{ currentRecord.rubric_name || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="模型">
            {{ currentRecord.judge_model || '-' }}
            <el-tag v-if="currentRecord.cache_hit" size="small" type="success" effect="plain" style="margin-left:6px;">缓存命中</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="耗时">
            {{ currentRecord.latency_ms ? currentRecord.latency_ms + ' ms' : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="自动匹配GT">
            <el-tag v-if="currentRecord.auto_gt" type="success" size="small">是</el-tag>
            <span v-else>否</span>
          </el-descriptions-item>
          <el-descriptions-item label="门禁分区">
            <el-tag :type="zoneTagType(currentRecord.gate_zone)" size="small" effect="plain">
              {{ zoneText(currentRecord.gate_zone) }}
            </el-tag>
            <el-tag v-if="currentRecord.blocked" size="small" type="danger" effect="plain" style="margin-left:6px;">拦截</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="评分拆分" :span="2">
            <div class="score-split">
              <div><span>规则分</span><b>{{ fmtNum(currentRecord.rule_score) }}</b></div>
              <div><span>LLM分</span><b>{{ fmtNum(currentRecord.llm_score) }}</b></div>
              <div><span>最终分</span><b class="score-num" :class="scoreClass(currentRecord.final_score)">{{ fmtNum(currentRecord.final_score) }}</b></div>
            </div>
          </el-descriptions-item>
          <el-descriptions-item label="问题" :span="2">
            <div class="block-text">{{ currentRecord.question }}</div>
          </el-descriptions-item>
          <el-descriptions-item label="答案" :span="2">
            <div class="block-text">{{ currentRecord.answer }}</div>
          </el-descriptions-item>
          <el-descriptions-item label="参考答案(GroundTruth)" :span="2">
            <template v-if="currentRecord.ground_truth">
              <div v-if="typeof currentRecord.ground_truth === 'string'" class="block-text">{{ currentRecord.ground_truth }}</div>
              <div v-else>
                <div v-if="currentRecord.ground_truth.text" class="block-text">{{ currentRecord.ground_truth.text }}</div>
                <el-table v-if="Array.isArray(currentRecord.ground_truth.values)" :data="currentRecord.ground_truth.values" size="small" border>
                  <el-table-column prop="label" label="指标" min-width="200" show-overflow-tooltip />
                  <el-table-column prop="value" label="值" width="120" />
                  <el-table-column prop="unit" label="单位" width="90" />
                  <el-table-column prop="tolerance" label="容差" width="90" />
                </el-table>
              </div>
            </template>
            <span v-else class="muted">（无）</span>
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="currentRecord.verdict_reasoning" class="detail-block">
          <div class="detail-title">LLM 评判理由 (CoT)</div>
          <div class="block-text reasoning">{{ currentRecord.verdict_reasoning }}</div>
        </div>

        <div v-if="currentRecord.verdict_dimensions && currentRecord.verdict_dimensions.length" class="detail-block">
          <div class="detail-title">逐维度评分</div>
          <el-table :data="currentRecord.verdict_dimensions" size="small" border>
            <el-table-column prop="id" label="维度ID" width="120" />
            <el-table-column prop="name" label="维度名" width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.name || row.id }}</template>
            </el-table-column>
            <el-table-column prop="score" label="得分" width="90">
              <template #default="{ row }">
                <span :class="['score-num', scoreClass(normDim(row.score))]">{{ row.score }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="weight" label="权重" width="80">
              <template #default="{ row }">{{ row.weight != null ? row.weight : '-' }}</template>
            </el-table-column>
            <el-table-column prop="reasoning" label="维度理由" show-overflow-tooltip>
              <template #default="{ row }">{{ row.reasoning || '-' }}</template>
            </el-table-column>
          </el-table>
        </div>

        <div v-if="currentRecord.rule_findings && currentRecord.rule_findings.length" class="detail-block">
          <div class="detail-title">规则引擎命中</div>
          <el-table :data="currentRecord.rule_findings" size="small" border>
            <el-table-column prop="rule" label="规则" width="140" show-overflow-tooltip />
            <el-table-column label="严重度" width="100">
              <template #default="{ row }">
                <el-tag :type="row.severity === 'critical' ? 'danger' : (row.severity === 'warn' ? 'warning' : 'info')" size="small">
                  {{ row.severity === 'critical' ? '严重/否决' : (row.severity === 'warn' ? '警告' : '提示') }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="message" label="命中描述" show-overflow-tooltip />
            <el-table-column label="详细" width="140">
              <template #default="{ row }">
                <span v-if="row.detail && typeof row.detail === 'object'">
                  {{ JSON.stringify(row.detail).slice(0,60) }}{{ JSON.stringify(row.detail).length > 60 ? '…' : '' }}
                </span>
                <span v-else>{{ row.detail || '-' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div v-if="currentRecord.vetoed && currentRecord.veto_reasons && currentRecord.veto_reasons.length" class="detail-block">
          <div class="detail-title">否决原因</div>
          <ul class="veto-list">
            <li v-for="(r,i) in currentRecord.veto_reasons" :key="i">{{ r }}</li>
          </ul>
        </div>

        <div v-if="currentRecord.error_message" class="detail-block">
          <div class="detail-title text-danger">错误信息</div>
          <div class="block-text err-text">{{ currentRecord.error_message }}</div>
        </div>

        <div class="detail-meta">
          <span>创建时间：{{ fmtTime(currentRecord.created_at) }}</span>
          <span v-if="currentRecord.batch_id" style="margin-left:16px;">批次ID：#{{ currentRecord.batch_id }}</span>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { InfoFilled, UploadFilled, FolderAdd, Check } from '@element-plus/icons-vue'
import {
  createBatchJudge, getBatchProgress, getBatchRecords, getBatchList, getRubricList,
  uploadBatchFile, downloadBatchTemplate, pauseBatch, resumeBatch,
} from '@/api/llm-judge'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const submitting = ref(false)
const rubrics = ref([])
const currentBatch = ref(null)
const records = ref([])
const batchHistory = ref([])
const recordDetailVisible = ref(false)
const currentRecord = ref(null)
let pollTimer = null

const form = reactive({
  name: '',
  rubric: null,
  casesText: ''
})

// 文件上传相关
const inputTab = ref('text')
const uploadRef = ref(null)
const parsedFile = ref(null) // 解析结果：{ filename, total_rows, valid_rows, errors, preview, cases }
const fileCases = ref([])

const loadRubrics = async () => {
  try {
    const res = await getRubricList({ page_size: 100 })
    rubrics.value = res.data.results || res.data
  } catch (e) { /* ignore */ }
}

const loadHistory = async () => {
  try {
    const res = await getBatchList({ page_size: 20 })
    batchHistory.value = res.data.results || res.data
  } catch (e) { /* ignore */ }
}

const parseTextCases = () => {
  const lines = form.casesText.split('\n').map(l => l.trim()).filter(Boolean)
  const cases = []
  for (const line of lines) {
    const idx = line.indexOf('|||')
    if (idx === -1) continue
    const question = line.slice(0, idx).trim()
    const answer = line.slice(idx + 3).trim()
    if (question && answer) cases.push({ question, answer, auto_gt: true })
  }
  return cases
}

const hasCases = () => {
  if (inputTab.value === 'text') return !!form.casesText.trim()
  if (inputTab.value === 'file') return !!(parsedFile.value && parsedFile.value.valid_rows)
  return false
}

const getCaseCount = () => {
  if (inputTab.value === 'text') return parseTextCases().length
  if (inputTab.value === 'file') return (parsedFile.value ? parsedFile.value.valid_rows : 0)
  return 0
}

const getCurrentCases = () => {
  if (inputTab.value === 'file' && fileCases.value.length) return fileCases.value
  if (inputTab.value === 'file' && parsedFile.value) return parsedFile.value.cases || []
  return parseTextCases()
}

// 处理上传文件
const handleFileChange = async (uploadFile) => {
  const file = uploadFile.raw || uploadFile
  if (!file) return
  // 大小检查
  if (file.size > 10 * 1024 * 1024) {
    return ElMessage.error('文件大小超过 10MB 限制')
  }
  try {
    const res = await uploadBatchFile(file)
    parsedFile.value = res.data
    fileCases.value = (res.data && res.data.cases) || []
    if (res.data && res.data.valid_rows) {
      ElMessage.success(t('llmJudge.batch.parsedOk', { n: res.data.valid_rows }))
    } else if (res.data && res.data.errors && res.data.errors.length) {
      ElMessage.warning(res.data.errors[0])
    } else {
      ElMessage.warning(t('llmJudge.batch.parsedNoRow'))
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.error || '文件解析失败')
  }
}

// 确认使用解析结果（同时把文本区也填充，避免用户切 Tab 丢失）
const applyParsed = () => {
  if (!parsedFile.value || !parsedFile.value.valid_rows) return
  fileCases.value = (parsedFile.value.cases || []).slice()
  const textLines = fileCases.value.map(c => {
    const gt = c.ground_truth ? (' ||| ' + (typeof c.ground_truth === 'string' ? c.ground_truth : (c.ground_truth.text || ''))) : ''
    return `${c.question} ||| ${c.answer}${gt}`
  })
  if (!form.casesText.trim()) form.casesText = textLines.join('\n')
  ElMessage.success(t('llmJudge.batch.useFileCasesOk', { n: fileCases.value.length }))
}

const downloadTpl = async (format) => {
  try {
    await downloadBatchTemplate(format)
  } catch (e) {
    ElMessage.error('模板下载失败：' + (e?.message || ''))
  }
}

const handleSubmit = async () => {
  const cases = getCurrentCases()
  if (!cases.length) {
    ElMessage.warning('请输入或上传用例')
    return
  }
  submitting.value = true
  records.value = []
  try {
    const payload = { cases }
    if (form.name) payload.name = form.name
    if (form.rubric) payload.rubric = form.rubric
    const res = await createBatchJudge(payload)
    currentBatch.value = res.data
    ElMessage.success(t('llmJudge.common.success'))
    startPolling(res.data.id)
    loadHistory()
  } catch (e) {
    ElMessage.error(e?.response?.data?.error || e?.message || t('llmJudge.batch.errorTip'))
  } finally {
    submitting.value = false
  }
}

const pollOnce = async (id) => {
  try {
    const res = await getBatchProgress(id)
    currentBatch.value = { ...currentBatch.value, ...res.data }
    if (['completed', 'failed', 'partial'].includes(res.data.status)) {
      stopPolling()
      loadHistory()
      if (res.data.status !== 'failed') loadRecords(id)
      return
    }
  } catch (e) { stopPolling() }
}

const startPolling = (id) => {
  stopPolling()
  // 立即拉取一次，避免用户等 1.5s 才看到进度更新
  pollOnce(id)
  pollTimer = setInterval(() => pollOnce(id), 1500)
}

const stopPolling = () => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

const loadRecords = async (id) => {
  try {
    const res = await getBatchRecords(id)
    records.value = res.data
  } catch (e) { /* ignore */ }
}

const viewBatch = async (row) => {
  currentBatch.value = { ...row }
  records.value = []
  if (row.status === 'running' || row.status === 'pending') {
    startPolling(row.id)
  } else if (row.status === 'completed' || row.status === 'partial' || row.status === 'paused') {
    loadRecords(row.id)
  }
}

const handlePause = async (row) => {
  try {
    const { data } = await pauseBatch(row.id)
    Object.assign(row, data)
    if (currentBatch.value && currentBatch.value.id === row.id) {
      Object.assign(currentBatch.value, data)
    }
    ElMessage.success(t('llmJudge.batch.paused'))
  } catch (e) {
    ElMessage.error(e?.response?.data?.error || e?.message || '暂停失败')
  }
}

const handleResume = async (row) => {
  try {
    const { data } = await resumeBatch(row.id)
    Object.assign(row, data)
    if (currentBatch.value && currentBatch.value.id === row.id) {
      Object.assign(currentBatch.value, data)
    }
    // 进入 running 后开始轮询
    if (data.status === 'running') {
      startPolling(row.id)
    }
    ElMessage.success(t('llmJudge.batch.resumed'))
  } catch (e) {
    ElMessage.error(e?.response?.data?.error || e?.message || '继续失败')
  }
}

const showRecordDetail = (row) => {
  currentRecord.value = { ...row }
  recordDetailVisible.value = true
}
// LLM 维度分是 1-5（judge_engine 输出），前端按 5→100 线性映射用于 scoreClass 颜色
const normDim = (s) => {
  const n = Number(s)
  if (isNaN(n)) return 0
  return Math.max(0, Math.min(100, (n - 1) / 4 * 100))
}

const handleClear = () => {
  form.name = ''
  form.rubric = null
  form.casesText = ''
  parsedFile.value = null
  fileCases.value = []
  inputTab.value = 'text'
  if (uploadRef.value) uploadRef.value.clearFiles()
}

const fmtNum = (v) => (v === null || v === undefined) ? '—' : Number(v).toFixed(1)
const fmtPct = (v) => (v === null || v === undefined) ? '—' : (Number(v) * 100).toFixed(1) + '%'
const fmtTime = (v) => v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '—'

const labelText = (k) => t(`llmJudge.labels.${k}`)
const zoneText = (k) => k ? t(`llmJudge.gate.${k}`) : '—'
const statusText = (k) => t(`llmJudge.batch.${k}`)
const labelTagType = (k) => ({ excellent: 'success', acceptable: '', needs_improvement: 'warning', critical_failure: 'danger' }[k] || 'info')
const zoneTagType = (k) => ({ green: 'success', yellow: 'warning', red: 'danger' }[k] || 'info')
const statusTagType = (k) => ({ completed: 'success', failed: 'danger', running: 'primary', pending: 'info', partial: 'warning', paused: 'warning' }[k] || 'info')
const scoreClass = (s) => s >= 85 ? 'high' : s >= 70 ? 'mid' : 'low'

const progressStatus = () => {
  if (!currentBatch.value) return ''
  if (currentBatch.value.status === 'completed') return 'success'
  if (currentBatch.value.status === 'failed') return 'exception'
  if (currentBatch.value.status === 'paused') return 'warning'
  return ''
}

onMounted(() => {
  loadRubrics()
  loadHistory()
})
onUnmounted(() => { stopPolling() })
</script>

<style scoped lang="scss">
.judge-batch { display: flex; flex-direction: column; gap: 16px; }
.page-card {
  border-radius: 8px;
  :deep(.el-card__header) { padding: 16px 20px; }
  :deep(.el-card__body) { padding: 20px; }
}
.card-header { display: flex; justify-content: space-between; align-items: center; }
.page-title { margin: 0; font-size: 18px; color: #303133; }
.page-desc { margin: 4px 0 0; font-size: 13px; color: #909399; }
.section-title { margin: 0; font-size: 16px; color: #303133; }
.input-tip {
  display: flex; align-items: center; gap: 4px;
  font-size: 12px; color: #909399; margin-bottom: 6px;
}
.input-tabs {
  :deep(.el-tabs__nav-wrap::after) { background-color: #ebeef5; }
}
.form-actions {
  display: flex; gap: 12px; align-items: center; margin-top: 16px;
  .cases-count { margin-left: auto; color: #606266; font-size: 13px; }
}
.progress-wrap {
  .progress-meta {
    display: flex; justify-content: space-between;
    margin-top: 10px; font-size: 13px; color: #606266;
    .progress-tip { color: #909399; }
  }
}
.summary-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px; margin-top: 20px; padding-top: 20px;
  border-top: 1px dashed #ebeef5;
}
.summary-item {
  text-align: center;
  .summary-label { font-size: 12px; color: #909399; margin-bottom: 6px; }
  .summary-value { font-size: 22px; font-weight: 600; color: #303133; }
  .summary-value.score { color: #409eff; }
}
.view-records-btn { margin-top: 16px; text-align: center; }
.score-num {
  font-weight: 600;
  &.high { color: #67c23a; }
  &.mid { color: #e6a23c; }
  &.low { color: #f56c6c; }
}

/* 文件上传 */
.file-upload-wrap { }
.upload-actions {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(260px, 1fr);
  gap: 16px;
  align-items: start;
  @media (max-width: 768px) { grid-template-columns: 1fr; }
}
.batch-uploader {
  :deep(.el-upload-dragger) {
    padding: 24px 20px; border-radius: 10px;
    border: 1px dashed #dcdfe6; background: #fafbfc;
    transition: all .2s;
    &:hover { border-color: #409eff; background: #ecf5ff; }
  }
}
.uploader-inner {
  display: flex; gap: 16px; align-items: center; justify-content: center;
}
.uploader-icon { font-size: 48px; color: #409eff; }
.uploader-text { text-align: left; }
.uploader-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.uploader-hint { font-size: 12px; }

.upload-side {
  padding: 14px; border: 1px solid #ebeef5; border-radius: 8px; background: #fafbfc;
  .tpl-label { font-size: 13px; color: #606266; margin-bottom: 6px; }
  .tpl-buttons { display: flex; gap: 8px; }
}
.parse-error-list {
  margin: 6px 0 0; padding-left: 18px;
  font-size: 12px; color: #b88230; line-height: 1.6;
  li + li { margin-top: 2px; }
  .muted { color: #909399; }
}
.parsed-preview {
  margin-top: 16px;
  .preview-title {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px;
    font-weight: 600; color: #303133;
  }
}

.record-detail {
  padding: 0 4px;
  .detail-block { margin-top: 18px; }
  .detail-title {
    font-weight: 600; color: #303133; margin-bottom: 8px; font-size: 14px;
    &.text-danger { color: #f56c6c; }
  }
  .block-text {
    white-space: pre-wrap; word-break: break-word; line-height: 1.6;
    background: #fafbfc; border: 1px solid #ebeef5; border-radius: 6px;
    padding: 10px 12px; color: #303133;
    &.reasoning { background: #f5f9ff; border-color: #d9ecff; color: #1b3d6b; }
    &.err-text { background: #fef0f0; border-color: #fde2e2; color: #a80011; }
  }
  .score-split {
    display: flex; gap: 24px; align-items: center;
    div { display: flex; flex-direction: column; align-items: center; gap: 4px;
      span { color: #909399; font-size: 12px; }
      b { font-size: 18px; }
    }
  }
  .veto-list { margin: 0; padding-left: 20px;
    li { color: #a80011; line-height: 1.8; }
  }
  .detail-meta { margin-top: 20px; color: #909399; font-size: 12px; text-align: right; }
  .muted { color: #909399; }
  .mono { font-family: Menlo, Consolas, monospace; background: #f5f7fa; padding: 2px 6px; border-radius: 4px; }
}
</style>
