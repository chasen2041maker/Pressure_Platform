<template>
  <section class="sse-editor">
    <el-alert type="info" :closable="false" show-icon :title="t('performanceTesting.sse.editorNotice')" />
    <div class="sse-presets">
      <el-button v-for="kind in presets" :key="kind" :disabled="disabled" @click="applyTemplate(kind)">{{ t(`performanceTesting.sse.presets.${kind}`) }}</el-button>
    </div>
    <p class="sse-note">{{ t('performanceTesting.sse.templateNotice') }}</p>
    <div class="sse-bounds">
      <el-form-item v-for="(bounds, field) in sseBounds" :key="field" :label="t(`performanceTesting.sse.bounds.${field}`)" label-width="150px">
        <el-input-number :model-value="parsed.config?.[field]" :disabled="disabled || !parsed.config" :min="bounds[0]" :max="bounds[1]" :precision="0" @update:model-value="value => updateBound(field, value)" />
      </el-form-item>
    </div>
    <label class="sse-label" :for="editorId">{{ t('performanceTesting.sse.config') }}</label>
    <el-input :id="editorId" :model-value="draftText" :disabled="disabled" type="textarea" :rows="20" spellcheck="false" :aria-label="t('performanceTesting.sse.config')" @update:model-value="updateDraft" />
    <el-alert v-if="parsed.errors.length" type="error" :closable="false" :title="t('performanceTesting.sse.invalid')">
      <ul><li v-for="error in parsed.errors" :key="`${error.path}:${error.code}`"><code>{{ error.path }}</code> · {{ t(`performanceTesting.sse.errors.${error.code}`) }}</li></ul>
    </el-alert>
    <p class="sse-note">{{ t('performanceTesting.sse.validationNotice') }}</p>
  </section>
</template>

<script setup>
import { computed, getCurrentInstance } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { sseBounds, sseTemplate, parseSseDraft } from '../sseStepForm.mjs'
const props = defineProps({ modelValue: { type: Object, default: () => ({}) }, draft: { type: String, default: undefined }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'update:draft'])
const { t } = useI18n()
const editorId = `sse-config-${getCurrentInstance().uid}`
const presets = ['CHAT', 'MESSAGE_SPEECH', 'CONTENT_SPEECH']
const draftText = computed(() => props.draft ?? JSON.stringify(props.modelValue, null, 2))
const parsed = computed(() => parseSseDraft(draftText.value))
function updateDraft(value) {
  if (props.disabled) return
  emit('update:draft', value)
  const result = parseSseDraft(value)
  if (result.config) emit('update:modelValue', result.config)
}
function updateBound(field, value) {
  if (!parsed.value.config) return
  updateDraft(JSON.stringify({ ...parsed.value.config, [field]: value }, null, 2))
}
async function applyTemplate(kind) {
  if (props.disabled) return
  try {
    await ElMessageBox.confirm(t('performanceTesting.sse.replaceNotice'), t('performanceTesting.sse.config'), { type: 'warning' })
    updateDraft(JSON.stringify(sseTemplate(kind), null, 2))
  } catch { /* Keep the current draft on cancellation. */ }
}
</script>

<style scoped>
.sse-presets, .sse-bounds { display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
.sse-presets .el-button { margin-left: 0; }
.sse-note { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.7; }
.sse-label { display: block; margin-bottom: 8px; font-weight: 600; }
.sse-editor :deep(textarea) { font-family: var(--el-font-family-monospace, monospace); }
.sse-editor .el-alert { margin-top: 12px; }
</style>
