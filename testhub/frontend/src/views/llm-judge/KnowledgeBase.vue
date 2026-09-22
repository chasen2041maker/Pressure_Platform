<template>
  <div class="judge-kb">
    <el-card shadow="never" class="page-card">
      <template #header>
        <div class="card-header">
          <div>
            <h2 class="page-title">{{ $t('llmJudge.kb.title') }}</h2>
            <p class="page-desc">{{ $t('llmJudge.kb.desc') }}</p>
          </div>
          <div class="header-actions">
            <el-select
              v-if="kbList.length"
              v-model="currentKbId"
              :placeholder="$t('llmJudge.kb.selectKb')"
              style="width: 220px"
              @change="handleKbChange"
            >
              <el-option
                v-for="k in kbList"
                :key="k.id"
                :label="k.name + (k.is_default ? '（默认）' : '')"
                :value="k.id"
              />
            </el-select>
            <el-button type="primary" :icon="Plus" @click="openKbDialog()">
              {{ $t('llmJudge.kb.newKb') }}
            </el-button>
            <el-button
              v-if="currentKbId"
              type="danger"
              :icon="Delete"
              @click="handleDeleteKb"
            >
              {{ $t('llmJudge.kb.deleteKb') }}
            </el-button>
            <el-button
              v-if="currentKbId"
              :icon="Edit"
              @click="handleEditKb"
            >
              {{ $t('llmJudge.kb.editKb') }}
            </el-button>
            <el-button
              v-if="currentKbId"
              :icon="Upload"
              @click="handleExport"
            >
              {{ $t('llmJudge.kb.exportBtn') }}
            </el-button>
          </div>
        </div>
      </template>

      <!-- KB 概览统计 -->
      <div v-if="currentKb" class="kb-overview">
        <div class="overview-grid">
          <div class="ov-item">
            <div class="ov-label">{{ $t('llmJudge.kb.stats.companies') }}</div>
            <div class="ov-value">{{ currentKb.company_count || 0 }}</div>
          </div>
          <div class="ov-item">
            <div class="ov-label">{{ $t('llmJudge.kb.stats.periods') }}</div>
            <div class="ov-value">{{ currentKb.period_count || 0 }}</div>
          </div>
          <div class="ov-item">
            <div class="ov-label">{{ $t('llmJudge.kb.stats.metrics') }}</div>
            <div class="ov-value">{{ currentKb.metric_count || 0 }}</div>
          </div>
          <div class="ov-item">
            <div class="ov-label">{{ $t('llmJudge.kb.stats.values') }}</div>
            <div class="ov-value">{{ currentKb.value_count || 0 }}</div>
          </div>
        </div>
        <el-alert v-if="currentKb.description" :title="currentKb.description" type="info" :closable="false" show-icon />
      </div>
      <el-empty v-else :description="$t('llmJudge.common.noData') + '，请先创建知识库'" />

      <el-tabs v-if="currentKbId" v-model="activeTab" class="kb-tabs">
        <!-- 主体管理 -->
        <el-tab-pane :label="$t('llmJudge.kb.tabCompanies')" name="companies">
          <div class="tab-toolbar">
            <el-input v-model="kwCompany" :placeholder="$t('llmJudge.common.search')" clearable style="width:220px" />
            <el-button type="primary" :icon="Plus" @click="openCompanyDialog()">{{ $t('llmJudge.common.add') }}{{ $t('llmJudge.kb.companyName') }}</el-button>
          </div>
          <el-table :data="pagedCompanies" border stripe size="small">
            <el-table-column type="index" label="#" width="60" />
            <el-table-column prop="name" :label="$t('llmJudge.kb.companyName')" min-width="140" />
            <el-table-column prop="aliases" :label="$t('llmJudge.common.aliases')" min-width="220">
              <template #default="{ row }">
                <el-tag v-for="a in (row.aliases || [])" :key="a" size="small" style="margin: 2px">{{ a }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="metric_count" :label="$t('llmJudge.kb.stats.values')" width="100" />
            <el-table-column :label="$t('llmJudge.common.operation')" width="160" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openCompanyDialog(row)">{{ $t('llmJudge.common.edit') }}</el-button>
                <el-button link type="danger" size="small" @click="removeCompany(row)">{{ $t('llmJudge.common.delete') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="companies.length > pageSize"
            class="tab-paginator"
            v-model:current-page="pCompany"
            :page-size="pageSize"
            :total="companies.length"
            layout="prev, pager, next, total"
            background
            small
          />
        </el-tab-pane>

        <!-- 报告期管理 -->
        <el-tab-pane :label="$t('llmJudge.kb.tabPeriods')" name="periods">
          <div class="tab-toolbar">
            <el-input v-model="kwPeriod" :placeholder="$t('llmJudge.common.search')" clearable style="width:220px" />
            <el-button type="primary" :icon="Plus" @click="openPeriodDialog()">{{ $t('llmJudge.common.add') }}{{ $t('llmJudge.kb.periodName') }}</el-button>
          </div>
          <el-table :data="pagedPeriods" border stripe size="small">
            <el-table-column type="index" label="#" width="60" />
            <el-table-column prop="name" :label="$t('llmJudge.kb.periodName')" min-width="160" />
            <el-table-column prop="aliases" :label="$t('llmJudge.common.aliases')" min-width="280">
              <template #default="{ row }">
                <el-tag v-for="a in (row.aliases || [])" :key="a" size="small" style="margin: 2px">{{ a }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('llmJudge.common.operation')" width="160" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openPeriodDialog(row)">{{ $t('llmJudge.common.edit') }}</el-button>
                <el-button link type="danger" size="small" @click="removePeriod(row)">{{ $t('llmJudge.common.delete') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="periods.length > pageSize"
            class="tab-paginator"
            v-model:current-page="pPeriod"
            :page-size="pageSize"
            :total="periods.length"
            layout="prev, pager, next, total"
            background
            small
          />
        </el-tab-pane>

        <!-- 指标管理 -->
        <el-tab-pane :label="$t('llmJudge.kb.tabMetrics')" name="metrics">
          <div class="tab-toolbar">
            <el-input v-model="kwMetric" :placeholder="$t('llmJudge.common.search')" clearable style="width:220px" />
            <el-button type="primary" :icon="Plus" @click="openMetricDialog()">{{ $t('llmJudge.common.add') }}{{ $t('llmJudge.kb.metricName') }}</el-button>
          </div>
          <el-table :data="pagedMetrics" border stripe size="small">
            <el-table-column type="index" label="#" width="60" />
            <el-table-column prop="name" :label="$t('llmJudge.kb.metricName')" min-width="140" />
            <el-table-column prop="aliases" :label="$t('llmJudge.common.aliases')" min-width="240">
              <template #default="{ row }">
                <el-tag v-for="a in (row.aliases || [])" :key="a" size="small" style="margin: 2px">{{ a }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="default_unit" :label="$t('llmJudge.kb.metricDefaultUnit')" width="100" />
            <el-table-column prop="default_tolerance" :label="$t('llmJudge.kb.metricDefaultTolerance')" width="110" />
            <el-table-column :label="$t('llmJudge.common.operation')" width="160" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openMetricDialog(row)">{{ $t('llmJudge.common.edit') }}</el-button>
                <el-button link type="danger" size="small" @click="removeMetric(row)">{{ $t('llmJudge.common.delete') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="metrics.length > pageSize"
            class="tab-paginator"
            v-model:current-page="pMetric"
            :page-size="pageSize"
            :total="metrics.length"
            layout="prev, pager, next, total"
            background
            small
          />
        </el-tab-pane>

        <!-- 数值维护 -->
        <el-tab-pane :label="$t('llmJudge.kb.tabValues')" name="values">
          <div class="tab-toolbar">
            <el-select v-model="fCompany" :placeholder="$t('llmJudge.kb.valueCompany')" clearable style="width:160px">
              <el-option v-for="c in companies" :key="c.id" :label="c.name" :value="c.id" />
            </el-select>
            <el-select v-model="fPeriod" :placeholder="$t('llmJudge.kb.valuePeriod')" clearable style="width:160px">
              <el-option v-for="p in periods" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
            <el-select v-model="fMetric" :placeholder="$t('llmJudge.kb.valueMetric')" clearable style="width:160px">
              <el-option v-for="m in metrics" :key="m.id" :label="m.name" :value="m.id" />
            </el-select>
            <el-button type="primary" :icon="Plus" @click="openValueDialog()">{{ $t('llmJudge.common.add') }}数值</el-button>
          </div>
          <el-table :data="pagedValues" border stripe size="small">
            <el-table-column type="index" label="#" width="60" />
            <el-table-column prop="company_name" :label="$t('llmJudge.kb.valueCompany')" min-width="130" />
            <el-table-column prop="period_name" :label="$t('llmJudge.kb.valuePeriod')" min-width="120" />
            <el-table-column prop="metric_name" :label="$t('llmJudge.kb.valueMetric')" min-width="120" />
            <el-table-column prop="value" :label="$t('llmJudge.kb.valueNumber')" width="110">
              <template #default="{ row }"><b>{{ row.value }}</b></template>
            </el-table-column>
            <el-table-column prop="unit" :label="$t('llmJudge.kb.valueUnit')" width="90" />
            <el-table-column prop="tolerance" :label="$t('llmJudge.kb.valueTolerance')" width="100" />
            <el-table-column :label="$t('llmJudge.common.operation')" width="160" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openValueDialog(row)">{{ $t('llmJudge.common.edit') }}</el-button>
                <el-button link type="danger" size="small" @click="removeValue(row)">{{ $t('llmJudge.common.delete') }}</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="values.length > pageSize"
            class="tab-paginator"
            v-model:current-page="pValue"
            :page-size="pageSize"
            :total="values.length"
            layout="prev, pager, next, total"
            background
            small
          />
        </el-tab-pane>

        <!-- 文本解析导入 -->
        <el-tab-pane :label="$t('llmJudge.kb.tabParse')" name="parse">
          <el-form :model="parseForm" label-position="top">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12">
                <el-form-item :label="$t('llmJudge.kb.parseForceCompany')">
                  <el-select v-model="parseForm.company" :placeholder="$t('llmJudge.common.all')" clearable filterable style="width:100%">
                    <el-option v-for="c in companies" :key="c.id" :label="c.name" :value="c.name" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12">
                <el-form-item :label="$t('llmJudge.kb.parseForcePeriod')">
                  <el-select v-model="parseForm.period" :placeholder="$t('llmJudge.common.all')" clearable filterable style="width:100%">
                    <el-option v-for="p in periods" :key="p.id" :label="p.name" :value="p.name" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
            <el-form-item :label="$t('llmJudge.kb.parseTextLabel')">
              <el-input
                v-model="parseForm.text"
                type="textarea"
                :rows="8"
                :placeholder="$t('llmJudge.kb.parseTextPlaceholder')"
              />
            </el-form-item>
            <div class="form-actions">
              <el-button type="primary" :icon="MagicStick" :loading="parsing" @click="handleParse">{{ $t('llmJudge.kb.parseBtn') }}</el-button>
              <el-button @click="parseForm.text = ''; parseResult = null">{{ $t('llmJudge.batch.clearInput') }}</el-button>
            </div>
          </el-form>

          <el-alert
            v-if="parseResult && parseResult.parsed.hints && parseResult.parsed.hints.length"
            type="warning"
            :closable="false"
            show-icon
            :title="$t('llmJudge.kb.parseHints')"
            style="margin-top:12px"
          >
            <ul class="hint-list">
              <li v-for="(h, i) in parseResult.parsed.hints" :key="i">{{ h }}</li>
            </ul>
          </el-alert>

          <el-card v-if="parseResult" shadow="never" class="preview-card">
            <template #header>
              <div class="preview-card-header">
                <h3 class="section-title">{{ $t('llmJudge.kb.parsePreview') }}</h3>
                <el-button
                  type="success"
                  :icon="Check"
                  :loading="importing"
                  :disabled="!parseResult.parsed.values.length"
                  @click="handleImport"
                >
                  {{ $t('llmJudge.kb.parseImport') }}
                </el-button>
              </div>
            </template>

            <el-descriptions :column="4" border size="small">
              <el-descriptions-item :label="$t('llmJudge.kb.parsePreviewCompanies')">
                <el-tag v-for="c in parseResult.parsed.companies" :key="c.name" type="primary" size="small" style="margin:2px">{{ c.name }}</el-tag>
                <span v-if="!parseResult.parsed.companies.length" class="muted">—</span>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('llmJudge.kb.parsePreviewPeriods')">
                <el-tag v-for="p in parseResult.parsed.periods" :key="p.name" size="small" style="margin:2px">{{ p.name }}</el-tag>
                <span v-if="!parseResult.parsed.periods.length" class="muted">—</span>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('llmJudge.kb.parsePreviewMetrics')">
                <el-tag v-for="m in parseResult.parsed.metrics" :key="m.name" type="warning" size="small" style="margin:2px">{{ m.name }}</el-tag>
                <span v-if="!parseResult.parsed.metrics.length" class="muted">—</span>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('llmJudge.kb.parsePreviewValues')">
                <b class="num-badge">{{ parseResult.parsed.values.length }}</b>
              </el-descriptions-item>
            </el-descriptions>

            <el-table :data="parseResult.parsed.values.slice(0, 50)" border stripe size="small" style="margin-top:16px" max-height="360">
              <el-table-column type="index" label="#" width="55" />
              <el-table-column prop="company" :label="$t('llmJudge.kb.valueCompany')" min-width="120" />
              <el-table-column prop="period" :label="$t('llmJudge.kb.valuePeriod')" min-width="110" />
              <el-table-column prop="metric" :label="$t('llmJudge.kb.valueMetric')" min-width="120" />
              <el-table-column prop="value" :label="$t('llmJudge.kb.valueNumber')" width="100">
                <template #default="{ row }"><b>{{ row.value }}</b></template>
              </el-table-column>
              <el-table-column prop="unit" :label="$t('llmJudge.kb.valueUnit')" width="80" />
              <el-table-column prop="tolerance" :label="$t('llmJudge.kb.valueTolerance')" width="90" />
            </el-table>
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 新建/编辑知识库 Dialog -->
    <el-dialog v-model="kbDialogVisible" width="520px" destroy-on-close>
      <template #title>{{ kbForm.id ? $t('llmJudge.kb.editKb') : $t('llmJudge.kb.newKb') }}</template>
      <el-form :model="kbForm" label-width="100px">
        <el-form-item :label="$t('llmJudge.common.name')">
          <el-input v-model="kbForm.name" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.common.domain')">
          <el-select v-model="kbForm.domain" style="width:100%">
            <el-option
              v-for="(label, key) in kbDomains"
              :key="key"
              :label="label"
              :value="key"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.description')">
          <el-input v-model="kbForm.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.isDefault')">
          <el-switch v-model="kbForm.is_default" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="kbDialogVisible = false">{{ $t('llmJudge.common.cancel') }}</el-button>
        <el-button type="primary" @click="saveKB">{{ $t('llmJudge.common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 主体 Dialog -->
    <el-dialog v-model="companyDialogVisible" width="520px" destroy-on-close>
      <template #title>{{ companyForm.id ? $t('llmJudge.common.edit') : $t('llmJudge.common.add') }}{{ $t('llmJudge.kb.companyName') }}</template>
      <el-form :model="companyForm" label-width="100px">
        <el-form-item :label="$t('llmJudge.kb.companyName')">
          <el-input v-model="companyForm.name" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.common.aliases')">
          <el-select
            v-model="companyForm.aliases"
            multiple
            filterable
            allow-create
            default-first-option
            style="width:100%"
            :placeholder="$t('llmJudge.kb.companyAliasesTip')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="companyDialogVisible = false">{{ $t('llmJudge.common.cancel') }}</el-button>
        <el-button type="primary" @click="saveCompany">{{ $t('llmJudge.common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 期间 Dialog -->
    <el-dialog v-model="periodDialogVisible" width="520px" destroy-on-close>
      <template #title>{{ periodForm.id ? $t('llmJudge.common.edit') : $t('llmJudge.common.add') }}{{ $t('llmJudge.kb.periodName') }}</template>
      <el-form :model="periodForm" label-width="100px">
        <el-form-item :label="$t('llmJudge.kb.periodName')">
          <el-input v-model="periodForm.name" placeholder="如 2024年报" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.common.aliases')">
          <el-select
            v-model="periodForm.aliases"
            multiple
            filterable
            allow-create
            default-first-option
            style="width:100%"
            :placeholder="$t('llmJudge.kb.periodAliasesTip')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="periodDialogVisible = false">{{ $t('llmJudge.common.cancel') }}</el-button>
        <el-button type="primary" @click="savePeriod">{{ $t('llmJudge.common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 指标 Dialog -->
    <el-dialog v-model="metricDialogVisible" width="520px" destroy-on-close>
      <template #title>{{ metricForm.id ? $t('llmJudge.common.edit') : $t('llmJudge.common.add') }}{{ $t('llmJudge.kb.metricName') }}</template>
      <el-form :model="metricForm" label-width="100px">
        <el-form-item :label="$t('llmJudge.kb.metricName')">
          <el-input v-model="metricForm.name" placeholder="如 营业收入" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.common.aliases')">
          <el-select
            v-model="metricForm.aliases"
            multiple
            filterable
            allow-create
            default-first-option
            style="width:100%"
            :placeholder="$t('llmJudge.kb.metricAliasesTip')"
          />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.metricDefaultUnit')">
          <el-input v-model="metricForm.default_unit" placeholder="如 亿 / %" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.metricDefaultTolerance')">
          <el-input-number v-model="metricForm.default_tolerance" :min="0" :step="0.5" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="metricDialogVisible = false">{{ $t('llmJudge.common.cancel') }}</el-button>
        <el-button type="primary" @click="saveMetric">{{ $t('llmJudge.common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 数值 Dialog -->
    <el-dialog v-model="valueDialogVisible" width="520px" destroy-on-close>
      <template #title>{{ valueForm.id ? $t('llmJudge.common.edit') : $t('llmJudge.common.add') }}数值</template>
      <el-form :model="valueForm" label-width="100px">
        <el-form-item :label="$t('llmJudge.kb.valueCompany')">
          <el-select v-model="valueForm.company" filterable style="width:100%">
            <el-option v-for="c in companies" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.valuePeriod')">
          <el-select v-model="valueForm.period" filterable style="width:100%">
            <el-option v-for="p in periods" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.valueMetric')">
          <el-select v-model="valueForm.metric" filterable style="width:100%">
            <el-option v-for="m in metrics" :key="m.id" :label="m.name" :value="m.id" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.valueNumber')">
          <el-input-number v-model="valueForm.value" :step="0.01" style="width:100%" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.valueUnit')">
          <el-input v-model="valueForm.unit" placeholder="如 亿 / %" />
        </el-form-item>
        <el-form-item :label="$t('llmJudge.kb.valueTolerance')">
          <el-input-number v-model="valueForm.tolerance" :min="0" :step="0.5" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="valueDialogVisible = false">{{ $t('llmJudge.common.cancel') }}</el-button>
        <el-button type="primary" @click="saveValue">{{ $t('llmJudge.common.save') }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Check, MagicStick, Upload, Edit, Delete } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import {
  getKBList, createKB, updateKB, deleteKB, exportKB, parseKBText, importKB,
  getKBCompanyList, createKBCompany, updateKBCompany, deleteKBCompany,
  getKBPeriodList, createKBPeriod, updateKBPeriod, deleteKBPeriod,
  getKBMetricList, createKBMetric, updateKBMetric, deleteKBMetric,
  getKBValueList, createKBValue, updateKBValue, deleteKBValue,
} from '@/api/llm-judge'

const { t } = useI18n()
const pageSize = 15
const kbDomains = { finance: t('llmJudge.rubric.domains.finance'), qa: t('llmJudge.rubric.domains.qa'), customer_service: t('llmJudge.rubric.domains.customer_service'), custom: t('llmJudge.rubric.domains.custom') }

// =============== KB 列表 & 当前选择 ===============
const kbList = ref([])
const currentKbId = ref(null)
const currentKb = ref(null)
const activeTab = ref('companies')

const loadKBs = async () => {
  try {
    const { data } = await getKBList({ page_size: 100 })
    kbList.value = (data.results || data) || []
    if (kbList.value.length && !currentKbId.value) {
      const def = kbList.value.find(k => k.is_default) || kbList.value[0]
      currentKbId.value = def.id
      currentKb.value = def
    } else if (currentKbId.value) {
      currentKb.value = kbList.value.find(k => k.id === currentKbId.value) || currentKb.value
    }
  } catch (_) { /* ignore */ }
}

const handleKbChange = async () => {
  currentKb.value = kbList.value.find(k => k.id === currentKbId.value) || null
  await Promise.all([loadCompanies(), loadPeriods(), loadMetrics(), loadValues()])
}

// =============== CRUD KB ===============
const kbDialogVisible = ref(false)
const kbForm = reactive({ id: null, name: '', domain: 'finance', description: '', is_default: false })

const resetKbForm = () => {
  kbForm.id = null
  kbForm.name = ''
  kbForm.domain = 'finance'
  kbForm.description = ''
  kbForm.is_default = false
}

const openKbDialog = () => {
  resetKbForm()
  kbDialogVisible.value = true
}

const saveKB = async () => {
  if (!kbForm.name) return ElMessage.warning('请填写名称')
  try {
    if (kbForm.id) {
      await updateKB(kbForm.id, kbForm)
    } else {
      await createKB(kbForm)
    }
    ElMessage.success(t('llmJudge.common.success'))
    kbDialogVisible.value = false
    await loadKBs()
    resetKbForm()
  } catch (e) {
    ElMessage.error(e?.response?.data?.error || e?.message || t('llmJudge.common.failure'))
  }
}

const handleEditKb = () => {
  if (!currentKb.value) return
  kbForm.id = currentKb.value.id
  kbForm.name = currentKb.value.name
  kbForm.domain = currentKb.value.domain
  kbForm.description = currentKb.value.description || ''
  kbForm.is_default = !!currentKb.value.is_default
  kbDialogVisible.value = true
}

const handleDeleteKb = async () => {
  if (!currentKb.value) return
  try {
    await ElMessageBox.confirm(
      currentKb.value.is_default
        ? '当前是默认知识库，删除后系统将自动选择同领域其他知识库作为默认。确认删除？'
        : t('llmJudge.kb.deleteConfirm'),
      t('llmJudge.common.confirm'),
      { type: 'warning' }
    )
  } catch (_) { return }
  try {
    await deleteKB(currentKb.value.id)
    const deletedId = currentKb.value.id
    ElMessage.success('删除成功')
    await loadKBs()
    // 如果删掉的是当前 ID，切到 kbList 第一条或 null
    if (currentKbId.value === deletedId) {
      if (kbList.value.length) {
        const def = kbList.value.find(k => k.is_default) || kbList.value[0]
        currentKbId.value = def.id
        currentKb.value = def
      } else {
        currentKbId.value = null
        currentKb.value = null
      }
      await Promise.all([loadCompanies(), loadPeriods(), loadMetrics(), loadValues()])
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.error || e?.message || t('llmJudge.common.failure'))
  }
}

const handleExport = async () => {
  try {
    await exportKB(currentKbId.value)
    ElMessage.success(t('llmJudge.kb.exportOk') + '；' + t('llmJudge.kb.exportTip'))
    await loadKBs()
  } catch (e) {
    ElMessage.error(t('llmJudge.common.failure'))
  }
}

// =============== 主体 ===============
const companies = ref([])
const kwCompany = ref('')
const pCompany = ref(1)
const filteredCompanies = computed(() => companies.value.filter(c => !kwCompany.value || c.name.includes(kwCompany.value) || (c.aliases || []).some(a => a.includes(kwCompany.value))))
const pagedCompanies = computed(() => filteredCompanies.value.slice((pCompany.value - 1) * pageSize, pCompany.value * pageSize))

const companyDialogVisible = ref(false)
const companyForm = reactive({ id: null, name: '', aliases: [] })

const loadCompanies = async () => {
  if (!currentKbId.value) return
  try {
    const { data } = await getKBCompanyList({ kb: currentKbId.value, page_size: 500 })
    companies.value = (data.results || data) || []
  } catch (_) { companies.value = [] }
}
const openCompanyDialog = (row = null) => {
  if (row) {
    companyForm.id = row.id; companyForm.name = row.name; companyForm.aliases = [...(row.aliases || [])]
  } else {
    companyForm.id = null; companyForm.name = ''; companyForm.aliases = []
  }
  companyDialogVisible.value = true
}
const saveCompany = async () => {
  if (!companyForm.name) return ElMessage.warning('请填写名称')
  try {
    const payload = { kb: currentKbId.value, name: companyForm.name, aliases: companyForm.aliases }
    if (companyForm.id) await updateKBCompany(companyForm.id, payload)
    else await createKBCompany(payload)
    ElMessage.success(t('llmJudge.common.success'))
    companyDialogVisible.value = false
    await loadCompanies()
  } catch (_) { ElMessage.error(t('llmJudge.common.failure')) }
}
const removeCompany = async (row) => {
  try {
    await ElMessageBox.confirm(t('llmJudge.kb.deleteConfirm'), t('llmJudge.common.confirm'), { type: 'warning' })
    await deleteKBCompany(row.id)
    ElMessage.success(t('llmJudge.common.success'))
    await loadCompanies(); await loadValues()
  } catch (_) { /* cancel */ }
}

// =============== 期间 ===============
const periods = ref([])
const kwPeriod = ref('')
const pPeriod = ref(1)
const filteredPeriods = computed(() => periods.value.filter(p => !kwPeriod.value || p.name.includes(kwPeriod.value) || (p.aliases || []).some(a => a.includes(kwPeriod.value))))
const pagedPeriods = computed(() => filteredPeriods.value.slice((pPeriod.value - 1) * pageSize, pPeriod.value * pageSize))
const periodDialogVisible = ref(false)
const periodForm = reactive({ id: null, name: '', aliases: [] })
const loadPeriods = async () => {
  if (!currentKbId.value) return
  try {
    const { data } = await getKBPeriodList({ kb: currentKbId.value, page_size: 500 })
    periods.value = (data.results || data) || []
  } catch (_) { periods.value = [] }
}
const openPeriodDialog = (row = null) => {
  if (row) { periodForm.id = row.id; periodForm.name = row.name; periodForm.aliases = [...(row.aliases || [])] }
  else { periodForm.id = null; periodForm.name = ''; periodForm.aliases = [] }
  periodDialogVisible.value = true
}
const savePeriod = async () => {
  if (!periodForm.name) return ElMessage.warning('请填写报告期')
  try {
    const payload = { kb: currentKbId.value, name: periodForm.name, aliases: periodForm.aliases }
    if (periodForm.id) await updateKBPeriod(periodForm.id, payload)
    else await createKBPeriod(payload)
    ElMessage.success(t('llmJudge.common.success'))
    periodDialogVisible.value = false
    await loadPeriods()
  } catch (_) { ElMessage.error(t('llmJudge.common.failure')) }
}
const removePeriod = async (row) => {
  try {
    await ElMessageBox.confirm(t('llmJudge.kb.deleteConfirm'), t('llmJudge.common.confirm'), { type: 'warning' })
    await deleteKBPeriod(row.id)
    ElMessage.success(t('llmJudge.common.success'))
    await loadPeriods(); await loadValues()
  } catch (_) { /* cancel */ }
}

// =============== 指标 ===============
const metrics = ref([])
const kwMetric = ref('')
const pMetric = ref(1)
const filteredMetrics = computed(() => metrics.value.filter(m => !kwMetric.value || m.name.includes(kwMetric.value) || (m.aliases || []).some(a => a.includes(kwMetric.value))))
const pagedMetrics = computed(() => filteredMetrics.value.slice((pMetric.value - 1) * pageSize, pMetric.value * pageSize))
const metricDialogVisible = ref(false)
const metricForm = reactive({ id: null, name: '', aliases: [], default_unit: '', default_tolerance: 5 })
const loadMetrics = async () => {
  if (!currentKbId.value) return
  try {
    const { data } = await getKBMetricList({ kb: currentKbId.value, page_size: 500 })
    metrics.value = (data.results || data) || []
  } catch (_) { metrics.value = [] }
}
const openMetricDialog = (row = null) => {
  if (row) {
    metricForm.id = row.id; metricForm.name = row.name; metricForm.aliases = [...(row.aliases || [])]
    metricForm.default_unit = row.default_unit || ''; metricForm.default_tolerance = row.default_tolerance ?? 5
  } else {
    metricForm.id = null; metricForm.name = ''; metricForm.aliases = []
    metricForm.default_unit = ''; metricForm.default_tolerance = 5
  }
  metricDialogVisible.value = true
}
const saveMetric = async () => {
  if (!metricForm.name) return ElMessage.warning('请填写指标名')
  try {
    const payload = {
      kb: currentKbId.value, name: metricForm.name, aliases: metricForm.aliases,
      default_unit: metricForm.default_unit, default_tolerance: Number(metricForm.default_tolerance) || 5
    }
    if (metricForm.id) await updateKBMetric(metricForm.id, payload)
    else await createKBMetric(payload)
    ElMessage.success(t('llmJudge.common.success'))
    metricDialogVisible.value = false
    await loadMetrics()
  } catch (_) { ElMessage.error(t('llmJudge.common.failure')) }
}
const removeMetric = async (row) => {
  try {
    await ElMessageBox.confirm(t('llmJudge.kb.deleteConfirm'), t('llmJudge.common.confirm'), { type: 'warning' })
    await deleteKBMetric(row.id)
    ElMessage.success(t('llmJudge.common.success'))
    await loadMetrics(); await loadValues()
  } catch (_) { /* cancel */ }
}

// =============== 数值 ===============
const values = ref([])
const fCompany = ref(null); const fPeriod = ref(null); const fMetric = ref(null)
const pValue = ref(1)
const filteredValues = computed(() => values.value.filter(v => {
  if (fCompany.value && v.company !== fCompany.value) return false
  if (fPeriod.value && v.period !== fPeriod.value) return false
  if (fMetric.value && v.metric !== fMetric.value) return false
  return true
}))
const pagedValues = computed(() => filteredValues.value.slice((pValue.value - 1) * pageSize, pValue.value * pageSize))
const valueDialogVisible = ref(false)
const valueForm = reactive({ id: null, company: null, period: null, metric: null, value: 0, unit: '', tolerance: 5 })
const loadValues = async () => {
  if (!currentKbId.value) return
  try {
    const { data } = await getKBValueList({ 'company__kb': currentKbId.value, page_size: 2000 })
    values.value = (data.results || data) || []
  } catch (_) { values.value = [] }
}
const openValueDialog = (row = null) => {
  if (row) {
    valueForm.id = row.id; valueForm.company = row.company; valueForm.period = row.period
    valueForm.metric = row.metric; valueForm.value = row.value
    valueForm.unit = row.unit || ''; valueForm.tolerance = row.tolerance ?? 5
  } else {
    valueForm.id = null; valueForm.company = fCompany.value || null
    valueForm.period = fPeriod.value || null; valueForm.metric = fMetric.value || null
    valueForm.value = 0; valueForm.unit = ''; valueForm.tolerance = 5
  }
  valueDialogVisible.value = true
}
const saveValue = async () => {
  if (!(valueForm.company && valueForm.period && valueForm.metric)) {
    return ElMessage.warning('请完整选择主体/报告期/指标')
  }
  try {
    const payload = {
      company: valueForm.company, period: valueForm.period, metric: valueForm.metric,
      value: Number(valueForm.value) || 0, unit: valueForm.unit || '',
      tolerance: Number(valueForm.tolerance) || 5,
    }
    if (valueForm.id) await updateKBValue(valueForm.id, payload)
    else await createKBValue(payload)
    ElMessage.success(t('llmJudge.common.success'))
    valueDialogVisible.value = false
    await loadValues()
    await loadKBs()
  } catch (_) { ElMessage.error(t('llmJudge.common.failure')) }
}
const removeValue = async (row) => {
  try {
    await ElMessageBox.confirm(t('llmJudge.kb.deleteConfirm'), t('llmJudge.common.confirm'), { type: 'warning' })
    await deleteKBValue(row.id)
    ElMessage.success(t('llmJudge.common.success'))
    await loadValues(); await loadKBs()
  } catch (_) { /* cancel */ }
}

// =============== 文本解析 ===============
const parseForm = reactive({ text: '', company: '', period: '' })
const parseResult = ref(null)
const parsing = ref(false)
const importing = ref(false)

const handleParse = async () => {
  if (!parseForm.text.trim()) return ElMessage.warning('请粘贴文本')
  parsing.value = true
  try {
    const { data } = await parseKBText({
      kb: currentKbId.value,
      text: parseForm.text,
      company: parseForm.company || undefined,
      period: parseForm.period || undefined,
    })
    parseResult.value = data
    if (!data.parsed.values.length) ElMessage.warning(t('llmJudge.kb.parseNoValue'))
  } catch (_) {
    ElMessage.error('解析失败')
  } finally { parsing.value = false }
}

const handleImport = async () => {
  if (!parseResult.value?.parsed?.values?.length) return
  importing.value = true
  try {
    const { data } = await importKB({
      kb: currentKbId.value,
      ...parseResult.value.parsed,
    })
    const s = data.stats || {}
    ElMessage.success(
      t('llmJudge.kb.parseImportStats', {
        cc: s.company_created || 0, cu: s.company_updated || 0,
        pc: s.period_created || 0, pu: s.period_updated || 0,
        mc: s.metric_created || 0, mu: s.metric_updated || 0,
        vc: s.value_created || 0, vu: s.value_updated || 0,
      })
    )
    if ((s.errors || []).length) {
      ElMessageBox.alert(s.errors.slice(0, 20).join('\n'), '部分失败', { type: 'warning' })
    }
    parseResult.value = null
    await Promise.all([loadCompanies(), loadPeriods(), loadMetrics(), loadValues(), loadKBs()])
  } catch (_) { ElMessage.error(t('llmJudge.common.failure')) }
  finally { importing.value = false }
}

onMounted(async () => {
  await loadKBs()
  if (currentKbId.value) {
    await Promise.all([loadCompanies(), loadPeriods(), loadMetrics(), loadValues()])
  }
})
</script>

<style scoped lang="scss">
.judge-kb { display: flex; flex-direction: column; gap: 16px; }
.page-card {
  border-radius: 8px;
  :deep(.el-card__header) { padding: 16px 20px; }
  :deep(.el-card__body) { padding: 20px; }
}
.card-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.page-title { margin: 0; font-size: 18px; color: #303133; }
.page-desc { margin: 4px 0 0; font-size: 13px; color: #909399; }
.section-title { margin: 0; font-size: 16px; color: #303133; }
.header-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.kb-overview { margin-bottom: 16px; }
.overview-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 16px; margin-bottom: 16px;
  .ov-item {
    text-align: center; padding: 12px; border-radius: 8px;
    background: #f5f7fa;
    .ov-label { font-size: 12px; color: #909399; margin-bottom: 6px; }
    .ov-value { font-size: 24px; font-weight: 600; color: #409eff; }
  }
}
.kb-tabs { margin-top: 20px; }
.tab-toolbar {
  display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 12px;
}
.tab-paginator { margin-top: 12px; text-align: right; }
.preview-card { margin-top: 20px; }
.preview-card-header { display: flex; justify-content: space-between; align-items: center; }
.hint-list { margin: 0; padding-left: 18px; font-size: 12px; color: #e6a23c; }
.muted { color: #c0c4cc; }
.num-badge {
  display: inline-block; min-width: 40px; padding: 2px 10px; border-radius: 999px;
  background: #409eff; color: #fff; text-align: center; font-size: 14px; font-weight: 600;
}
.form-actions { display: flex; gap: 10px; }
</style>
