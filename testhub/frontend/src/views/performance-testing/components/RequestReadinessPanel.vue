<template>
  <section class="readiness-panel" aria-live="polite">
    <div class="heading">
      <strong>{{ t('performanceTesting.catalog.preparation') }}</strong>
      <el-tag v-if="metadata.version" size="small" effect="plain">v{{ metadata.version }}</el-tag>
      <el-tag size="small" :type="stale ? 'warning' : readiness?.ready ? 'success' : 'warning'">
        {{ t(`performanceTesting.catalog.${stale ? 'stale' : readiness?.ready ? 'ready' : 'needsPreparation'}`) }}
      </el-tag>
    </div>
    <p class="hint">{{ t(`performanceTesting.catalog.${catalog ? 'contractOnly' : 'scenarioCheckHint'}`) }}</p>
    <p v-if="metadata.source_key" class="source">{{ metadata.source_key }} · {{ metadata.source_version || '' }}</p>
    <ul v-if="readiness?.gaps?.length" class="gaps">
      <li v-for="gap in readiness.gaps" :key="gap.code + gap.field"><code>{{ gap.field }}</code> — {{ gap.message }}</li>
    </ul>
    <el-collapse v-if="Object.keys(metadata).length">
      <el-collapse-item :title="t('performanceTesting.catalog.contractDetails')" name="schema">
        <div v-for="requirement in metadata.requirements || []" :key="requirement.field" class="requirement">
          <code>{{ requirement.field }}</code>
          <span>{{ requirement.provenance }}{{ requirement.required ? ' · ' + t('performanceTesting.catalog.required') : '' }}{{ requirement.resource ? ' · ' + t('performanceTesting.catalog.resource') : '' }}</span>
          <pre v-if="Object.hasOwn(requirement, 'suggested')">{{ format(requirement.suggested) }}</pre>
        </div>
        <h4>{{ t('performanceTesting.catalog.parameters') }}</h4><pre>{{ format(metadata.parameters || []) }}</pre>
        <h4>{{ t('performanceTesting.catalog.bodySchema') }}</h4><pre>{{ format(metadata.request_body || {}) }}</pre>
        <h4>{{ t('performanceTesting.catalog.authentication') }}</h4><pre>{{ format({ security: metadata.security, schemes: metadata.security_schemes }) }}</pre>
      </el-collapse-item>
      <el-collapse-item v-if="editable" :title="t('performanceTesting.catalog.confirmPreparation')" name="confirmation">
        <p class="hint">{{ t('performanceTesting.catalog.confirmHint') }}</p>
        <el-checkbox v-for="field in confirmable" :key="field" :model-value="(preparation.confirmed_fields || []).includes(field)" @change="value => confirm(field, value)">
          {{ t('performanceTesting.catalog.confirmField', { field }) }}
        </el-checkbox>
        <el-checkbox v-if="Object.keys(metadata.request_body || {}).length" :model-value="!!preparation.body_reviewed" @change="value => emit('update:preparation', { ...preparation, body_reviewed: value })">
          {{ t('performanceTesting.catalog.bodyReviewed') }}
        </el-checkbox>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
const props = defineProps({
  metadata: { type: Object, default: () => ({}) }, readiness: { type: Object, default: null },
  preparation: { type: Object, default: () => ({}) }, stale: Boolean, catalog: Boolean, editable: Boolean
})
const emit = defineEmits(['update:preparation'])
const { t } = useI18n()
const confirmable = computed(() => [...new Set([
  ...(props.metadata.requirements || []).filter(item => item.provenance === 'example' || item.resource).map(item => item.field),
  ...(props.metadata.gaps || []).filter(item => item.code === 'server_path_choice').map(item => item.field)
])])
const format = value => JSON.stringify(value, null, 2)
function confirm(field, selected) {
  const values = new Set(props.preparation.confirmed_fields || [])
  if (selected) values.add(field)
  else values.delete(field)
  emit('update:preparation', { ...props.preparation, confirmed_fields: [...values] })
}
</script>

<style scoped>
.readiness-panel { border: 1px solid var(--el-border-color); border-radius: 6px; padding: 12px; margin-bottom: 14px; min-width: 0; }
.heading { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
.source, .gaps { overflow-wrap: anywhere; font-size: 13px; }
.gaps { padding-left: 20px; line-height: 1.7; }
.requirement { display: grid; gap: 4px; margin: 10px 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; max-height: 260px; overflow: auto; background: var(--el-fill-color-light); padding: 8px; }
:deep(.el-checkbox) { height: auto; display: flex; margin: 8px 0; }
:deep(.el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; }
</style>
