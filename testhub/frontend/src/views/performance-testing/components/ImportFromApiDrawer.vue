<template>
  <el-drawer :model-value="modelValue" :title="t('performanceTesting.catalog.selectTitle')" size="min(680px, 96vw)" :close-on-click-modal="!submitting" :close-on-press-escape="!submitting" :show-close="!submitting" @update:model-value="value => emit('update:modelValue', value)">
    <el-checkbox v-model="usePrepared" :disabled="submitting">{{ t('performanceTesting.apiPool.reusePassed') }}</el-checkbox>
    <p class="hint">{{ t(`performanceTesting.apiPool.${usePrepared ? 'reuseHint' : 'rawHint'}`) }}</p>
    <div :inert="submitting"><ProjectApiCatalog v-if="modelValue && projectId" :project-id="projectId" v-model:selected-ids="selectedIds" selectable :use-prepared="usePrepared" @selection-state="selectionState = $event" @manage="emit('manage')" /></div>
    <el-alert v-if="error" type="error" :title="error" :closable="false" />
    <template #footer>
      <div class="footer">
        <el-checkbox v-if="!usePrepared" v-model="asSetup" :disabled="submitting">{{ t('performanceTesting.importApi.asSetup') }}</el-checkbox>
        <span>{{ t('performanceTesting.importApi.selected', { count: selectedIds.length }) }}</span>
        <el-button type="primary" :disabled="!selectedIds.length" :loading="submitting" @click="confirm">{{ t('performanceTesting.catalog.addToScenario') }}</el-button>
      </div>
    </template>
  </el-drawer>
</template>
<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { importStepsFromApi } from '@/api/performance-testing'
import ProjectApiCatalog from './ProjectApiCatalog.vue'
import { catalogError, requestGate } from '../apiCatalogForm.mjs'
const props = defineProps({ modelValue: Boolean, scenarioId: { type: [Number, String], default: null }, projectId: { type: [Number, String], default: null } })
const emit = defineEmits(['update:modelValue', 'imported', 'manage'])
const { t } = useI18n()
const selectedIds = ref([]), asSetup = ref(false), submitting = ref(false), error = ref('')
const usePrepared = ref(true), selectionState = ref(null), gate = requestGate()
watch(() => [props.modelValue, props.projectId, props.scenarioId], () => {
  gate.invalidate(); selectedIds.value = []; selectionState.value = null; asSetup.value = false; error.value = ''; submitting.value = false
}, { flush: 'sync' })
watch(usePrepared, () => { gate.invalidate(); selectedIds.value = []; selectionState.value = null; asSetup.value = false; error.value = ''; submitting.value = false }, { flush: 'sync' })
async function confirm() {
  if (!props.modelValue || !props.scenarioId || !selectedIds.value.length || submitting.value) return
  const scenario = props.scenarioId, project = props.projectId, ticket = gate.begin()
  const current = () => gate.current(ticket) && props.modelValue && scenario === props.scenarioId && project === props.projectId
  const payload = { request_ids: [...selectedIds.value], as_setup: asSetup.value }
  submitting.value = true; error.value = ''
  try {
    if (usePrepared.value) {
      const snapshot = selectionState.value
      const selected = payload.request_ids.map(id => snapshot?.rows.find(row => row.id === id))
      if (!snapshot?.version || selected.some(row => !row || row._catalogVersion !== snapshot.version || row.prepared?.status !== 'passed' || !row.prepared.source_key || !row.prepared.revision)) throw { response: { status: 409 } }
      Object.assign(payload, { use_prepared: true, expected_catalog_version: snapshot.version,
        expected_prepared_revisions: Object.fromEntries(selected.map(row => [row.prepared.source_key, row.prepared.revision])) })
    }
    const { data } = await importStepsFromApi(scenario, payload)
    if (!current()) return
    emit('imported', data)
    emit('update:modelValue', false)
    ElMessage.success(t('performanceTesting.importApi.importSuccess', { count: data.imported }))
  } catch (e) { if (current()) error.value = e?.response?.status === 409 ? t('performanceTesting.apiPool.importConflict') : catalogError(e, t) }
  finally { if (gate.current(ticket)) submitting.value = false }
}
onBeforeUnmount(() => gate.invalidate())
</script>
<style scoped>
.footer { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
</style>
