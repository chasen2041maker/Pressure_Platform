<template>
  <div class="environment-fields">
    <div class="section-head">
      <h3>{{ t('performanceTesting.environment.headers') }}</h3>
      <el-button size="small" @click="headerRows.push({ key: '', value: '' })">{{ t('performanceTesting.environment.addHeader') }}</el-button>
    </div>
    <p class="field-tip">{{ t('performanceTesting.environment.secretTip') }}</p>
    <el-table :data="headerRows" border size="small">
      <el-table-column :label="t('performanceTesting.environment.headerName')" min-width="160">
        <template #default="{ row }"><el-input v-model="row.key" :aria-label="t('performanceTesting.environment.headerName')" @input="emitHeaders" /></template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.editor.variableValue')" min-width="230">
        <template #default="{ row }"><el-input v-model="row.value" :type="sensitiveHeader(row.key) ? 'password' : 'text'" :aria-label="t('performanceTesting.editor.variableValue')" @input="emitHeaders" /></template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.common.actions')" width="80">
        <template #default="{ $index }"><el-button link type="danger" @click="removeHeader($index)">{{ t('performanceTesting.common.delete') }}</el-button></template>
      </el-table-column>
    </el-table>
    <el-alert v-if="duplicateHeader || blankHeader" class="field-alert" type="error" :closable="false" :title="t('performanceTesting.environment.duplicateHeader')" />
    <div class="section-head">
      <h3>{{ t('performanceTesting.editor.variables') }}</h3>
      <el-button size="small" @click="addVariable">{{ t('performanceTesting.editor.addRow') }}</el-button>
    </div>
    <p class="field-tip">{{ t('performanceTesting.environment.variableTip') }}</p>
    <el-alert v-if="!project" class="field-alert" type="info" :closable="false" :title="t('performanceTesting.environment.globalVariableTip')" />
    <el-alert v-if="variableRows.some(row => !supported(row))" class="field-alert" type="warning" :closable="false" :title="t('performanceTesting.environment.unsupportedVariables')" />
    <el-table :data="variableRows" border size="small">
      <el-table-column :label="t('performanceTesting.editor.variableName')" min-width="150">
        <template #default="{ row, $index }"><el-input :model-value="row.name" :aria-label="t('performanceTesting.editor.variableName')" @update:model-value="value => updateVariable($index, 'name', value)" /></template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.editor.variableType')" width="130">
        <template #default="{ row, $index }">
          <el-select :model-value="row.type || 'CONSTANT'" :aria-label="t('performanceTesting.editor.variableType')" @update:model-value="value => updateVariable($index, 'type', value)">
            <el-option :label="t('performanceTesting.environment.constantType')" value="CONSTANT" />
            <el-option :label="t('performanceTesting.environment.csvType')" value="CSV" :disabled="!project" />
            <el-option v-if="!supported(row)" :label="row.type" :value="row.type" disabled />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.editor.variableValue')" min-width="250">
        <template #default="{ row, $index }">
          <el-input v-if="!row.type || row.type === 'CONSTANT'" :model-value="row.value" :type="row.secret ? 'password' : 'text'" :aria-label="t('performanceTesting.editor.variableValue')" @update:model-value="value => updateVariable($index, 'value', value)" />
          <div v-else-if="row.type === 'CSV'" class="csv-fields">
            <el-select :model-value="row.data_file_id" :loading="filesLoading" :disabled="!project || filesLoading || filesError" :placeholder="t('performanceTesting.editor.varCsv')" :aria-label="t('performanceTesting.editor.varCsv')" @update:model-value="value => updateVariable($index, 'data_file_id', value)">
              <el-option v-for="file in files" :key="file.id" :label="file.name" :value="file.id" />
            </el-select>
            <el-select :model-value="row.column" filterable allow-create default-first-option :placeholder="t('performanceTesting.environment.csvColumn')" :aria-label="t('performanceTesting.environment.csvColumn')" @update:model-value="value => updateVariable($index, 'column', value)">
              <el-option v-for="column in columnsOf(row.data_file_id)" :key="column" :label="column" :value="column" />
            </el-select>
            <span v-if="!filesLoading && !filesError && row.data_file_id && !files.some(file => file.id === row.data_file_id)" class="field-warning">{{ t('performanceTesting.environment.csvUnavailable', { id: row.data_file_id }) }}</span>
          </div>
          <pre v-else class="preserved-variable">{{ JSON.stringify(publicVariable(row), null, 2) }}</pre>
        </template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.environment.secret')" width="80">
        <template #default="{ row, $index }"><el-switch :model-value="!!row.secret" :aria-label="t('performanceTesting.environment.secret')" @update:model-value="value => updateVariable($index, 'secret', value)" /></template>
      </el-table-column>
      <el-table-column :label="t('performanceTesting.common.actions')" width="80">
        <template #default="{ $index }"><el-button link type="danger" @click="removeVariable($index)">{{ t('performanceTesting.common.delete') }}</el-button></template>
      </el-table-column>
    </el-table>
    <el-alert v-if="filesError" class="field-alert" type="error" :closable="false" :title="t('performanceTesting.environment.filesLoadFailed')">
      <el-button link type="primary" @click="loadFiles">{{ t('performanceTesting.common.retry') }}</el-button>
    </el-alert>
  </div>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { getPerfDataFiles } from '@/api/performance-testing'
import { latestRequestGate, publicVariable } from '../environmentForm.mjs'

const props = defineProps({ headers: { type: Object, default: () => ({}) }, variables: { type: Array, default: () => [] }, project: { type: Number, default: null }, copyState: { type: Object, default: null } })
const emit = defineEmits(['update:headers', 'update:variables', 'invalid'])
const { t } = useI18n()
const headerRows = ref([])
const variableRows = computed(() => props.copyState ? props.copyState.variables.map(row => row.value) : props.variables)
const files = ref([])
const filesLoading = ref(false)
const filesError = ref(false)
const fileRequests = latestRequestGate()
const duplicateHeader = computed(() => {
  const names = headerRows.value.map(row => row.key.trim().toLowerCase()).filter(Boolean)
  return new Set(names).size !== names.length
})
const blankHeader = computed(() => headerRows.value.some(row => !row.key.trim() && row.value))
watch([duplicateHeader, blankHeader], () => emit('invalid', duplicateHeader.value || blankHeader.value), { immediate: true })
function toHeaders() { return Object.fromEntries(headerRows.value.filter(row => row.key.trim()).map(row => [row.key.trim(), row.value])) }
function emitHeaders() { emit('update:headers', toHeaders()) }
function removeHeader(index) { headerRows.value.splice(index, 1); emitHeaders() }
watch(() => props.headers, value => {
  if (props.copyState) { headerRows.value = props.copyState.headers; return }
  if (JSON.stringify(value || {}) !== JSON.stringify(toHeaders())) headerRows.value = Object.entries(value || {}).map(([key, item]) => ({ key, value: item }))
}, { immediate: true, deep: true })
const supported = row => !row.type || ['CONSTANT', 'CSV'].includes(row.type)
const sensitiveHeader = name => /authorization|cookie|token|apikey|secret|password|credential/i.test(name.replace(/[-_]/g, ''))
function updateVariable(index, key, value) {
  const copiedRows = props.copyState?.variables
  if (copiedRows) {
    copiedRows[index].value = { ...copiedRows[index].value, [key]: value }
    emit('update:variables', copiedRows.map(row => row.value))
    return
  }
  emit('update:variables', props.variables.map((row, i) => i === index ? { ...row, [key]: value } : row))
}
function addVariable() {
  const variable = { name: '', type: 'CONSTANT', value: '', secret: false }
  const copiedRows = props.copyState?.variables
  if (copiedRows) {
    copiedRows.push({ value: variable, requiredFields: [] })
    emit('update:variables', copiedRows.map(row => row.value))
  } else emit('update:variables', [...props.variables, variable])
}
function removeVariable(index) {
  const copiedRows = props.copyState?.variables
  if (copiedRows) {
    copiedRows.splice(index, 1)
    emit('update:variables', copiedRows.map(row => row.value))
  } else emit('update:variables', props.variables.filter((_, i) => i !== index))
}
function columnsOf(id) { return files.value.find(file => file.id === id)?.columns || [] }
async function loadFiles() {
  const request = fileRequests.begin()
  files.value = []
  filesError.value = false
  filesLoading.value = Boolean(props.project)
  if (!props.project) return
  try {
    const { data } = await getPerfDataFiles({ project: props.project, file_type: 'CSV', page_size: 0 })
    if (fileRequests.isCurrent(request)) files.value = data.results || data || []
  } catch {
    if (fileRequests.isCurrent(request)) filesError.value = true
  } finally {
    if (fileRequests.isCurrent(request)) filesLoading.value = false
  }
}
watch(() => props.project, loadFiles, { immediate: true })
onBeforeUnmount(() => fileRequests.begin())
</script>

<style scoped>
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 18px; }
.section-head h3 { font-size: 14px; margin: 0; }
.field-tip { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.7; }
.field-alert { margin: 12px 0; }
.csv-fields { display: flex; flex-wrap: wrap; gap: 6px; }
.csv-fields .el-select { min-width: 110px; flex: 1; }
.field-warning { color: var(--el-color-warning); font-size: 12px; }
.preserved-variable { max-width: 360px; overflow: auto; margin: 0; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
