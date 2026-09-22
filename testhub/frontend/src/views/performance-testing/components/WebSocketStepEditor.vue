<template>
  <section class="websocket-editor" :aria-label="t('performanceTesting.websocket.title')">
    <el-alert type="info" :closable="false" show-icon :title="t('performanceTesting.websocket.executionNotice')" />
    <p class="form-tip">{{ t('performanceTesting.websocket.scopeNotice') }}</p>
    <div class="template-actions">
      <el-button :disabled="disabled" @click="applyTemplate">{{ t('performanceTesting.websocket.readonlyTemplate') }}</el-button>
      <el-button v-for="kind in ['home', 'quotes', 'reminder']" :key="kind" :disabled="disabled" @click="applyPushTemplate(kind)">{{ t(`performanceTesting.websocket.pushTemplates.${kind}`) }}</el-button>
      <span>{{ t('performanceTesting.websocket.tokenReference') }} <code>{{ tokenReference }}</code></span>
    </div>
    <el-row :gutter="12">
      <el-col v-for="(bounds, name) in websocketTimers" :key="name" :xs="24" :sm="12" :xl="8">
        <el-form-item :label="t(`performanceTesting.websocket.${name}`)" label-width="auto">
          <el-input-number :model-value="parsed.config?.[name]" :min="bounds[0]" :max="bounds[1]" :step="1000"
            :disabled="disabled || !parsed.config" :aria-label="t(`performanceTesting.websocket.${name}`)"
            @update:model-value="value => updateTimer(name, value)" />
        </el-form-item>
      </el-col>
    </el-row>
    <el-form-item :label="t('performanceTesting.websocket.configJson')" label-position="top" label-width="auto">
      <el-input :model-value="draft" :disabled="disabled" type="textarea" :rows="22" class="config-json" spellcheck="false"
        :aria-label="t('performanceTesting.websocket.configJson')" @update:model-value="updateDraft" />
    </el-form-item>
    <p class="form-tip">{{ t('performanceTesting.websocket.jsonNotice') }}</p>
    <el-alert v-if="parsed.errors.length" type="error" :closable="false" show-icon :title="t('performanceTesting.websocket.invalid')">
      <ul class="config-errors"><li v-for="(error, index) in parsed.errors" :key="index"><code>{{ error.path }}</code>: {{ t(`performanceTesting.websocket.errors.${error.code}`) }}</li></ul>
    </el-alert>
    <template v-if="parsed.config">
      <h4>{{ t('performanceTesting.websocket.commandOrder') }}</h4>
      <ol class="command-list">
        <li v-for="(command, index) in parsed.config.commands" :key="index">
          <span>{{ command.name }} <code>{{ command.event_type || command.request?.action }}</code></span>
          <div>
            <el-button size="small" :disabled="disabled || index === 0" :aria-label="t('performanceTesting.websocket.moveUp', { name: command.name })" @click="moveCommand(index, -1)">{{ t('performanceTesting.websocket.up') }}</el-button>
            <el-button size="small" :disabled="disabled || index === parsed.config.commands.length - 1" :aria-label="t('performanceTesting.websocket.moveDown', { name: command.name })" @click="moveCommand(index, 1)">{{ t('performanceTesting.websocket.down') }}</el-button>
          </div>
        </li>
      </ol>
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { websocketTemplate, websocketPushTemplate, websocketTimers, parseWebsocketDraft } from '../websocketStepForm.mjs'

const props = defineProps({ modelValue: { type: Object, default: () => ({}) }, draft: { type: String, default: undefined }, accessVariable: { type: String, default: 'access_token' }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'update:draft'])
const { t } = useI18n()
const draft = computed(() => props.draft ?? JSON.stringify(props.modelValue, null, 2))
const tokenReference = computed(() => `{{${props.accessVariable || 'access_token'}}}`)
const parsed = computed(() => parseWebsocketDraft(draft.value, props.accessVariable))
let mounted = true
onBeforeUnmount(() => { mounted = false })
function updateDraft(value) {
  if (!mounted || props.disabled) return
  emit('update:draft', value)
  const result = parseWebsocketDraft(value, props.accessVariable)
  if (result.config) emit('update:modelValue', result.config)
}
function updateTimer(name, value) {
  if (parsed.value.config) updateDraft(JSON.stringify({ ...parsed.value.config, [name]: value }, null, 2))
}
function moveCommand(index, offset) {
  const config = parsed.value.config
  if (!config) return
  const commands = [...config.commands]
  ;[commands[index], commands[index + offset]] = [commands[index + offset], commands[index]]
  updateDraft(JSON.stringify({ ...config, commands }, null, 2))
}
async function applyTemplate() {
  if (!mounted || props.disabled) return
  try {
    await ElMessageBox.confirm(t('performanceTesting.websocket.replaceTemplate'), t('performanceTesting.websocket.readonlyTemplate'), { type: 'warning' })
    updateDraft(JSON.stringify(websocketTemplate(props.accessVariable), null, 2))
  } catch { /* Keep the current draft when replacement is cancelled. */ }
}
async function applyPushTemplate(kind) {
  if (!mounted || props.disabled) return
  try {
    await ElMessageBox.confirm(t('performanceTesting.websocket.replaceTemplate'), t(`performanceTesting.websocket.pushTemplates.${kind}`), { type: 'warning' })
    updateDraft(JSON.stringify(websocketPushTemplate(kind, props.accessVariable), null, 2))
  } catch { /* Keep the current draft when replacement is cancelled. */ }
}
</script>

<style scoped>
.websocket-editor { min-width: 0; margin: 12px 0; }
.template-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 16px 0; }
.form-tip { color: var(--el-text-color-secondary); line-height: 1.6; font-size: 13px; }
.config-json :deep(textarea) { font-family: ui-monospace, Consolas, monospace; }
.config-errors { margin: 6px 0; padding-left: 20px; overflow-wrap: anywhere; }
.command-list { padding-left: 24px; }
.command-list li { padding: 8px 0; }
.command-list li > span { display: inline-block; margin: 0 12px 8px 0; }
.command-list li > div { display: inline-flex; }
code { overflow-wrap: anywhere; }
</style>
