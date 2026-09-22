<template>
  <section class="recovery-editor" :aria-label="rt('title')">
    <h3>{{ rt('title') }}</h3>
    <el-switch :model-value="enabled" :disabled="disabled" :active-text="rt('enabled')" @change="toggle" />
    <p class="recovery-note">{{ rt('configNotice') }}</p>
    <template v-if="enabled">
      <el-alert type="info" :closable="false" :title="rt('identityNotice')" />
      <p class="recovery-note">{{ rt('bindingNotice') }}</p>
      <el-form label-position="top" :disabled="disabled" class="recovery-grid">
        <el-form-item :label="rt('stock')"><el-input :model-value="modelValue.stock_code" placeholder="sh600000" maxlength="8" @update:model-value="value => field('stock_code', value)" /></el-form-item>
        <el-form-item :label="rt('group')"><el-select :model-value="modelValue.group_id" filterable @change="value => field('group_id', value)"><el-option v-for="group in groups" :key="group" :value="group" :label="group" /></el-select></el-form-item>
        <el-form-item v-for="role in recoveryRoles" :key="role" :label="rt(role)"><el-select :model-value="modelValue[role]" :placeholder="rt('selectStep')" filterable clearable @change="value => field(role, value || null)"><el-option v-for="step in options(role)" :key="step.id" :value="step.id" :label="`${step.name || step.method} · #${step.id}`" /></el-select></el-form-item>
        <el-form-item :label="rt('maxResources')"><el-input-number :model-value="modelValue.max_resources" :min="1" :max="1000" :precision="0" @update:model-value="value => field('max_resources', value)" /></el-form-item>
      </el-form>
      <el-alert v-if="errors.length" type="error" :closable="false" :title="rt('invalid')" :description="errors.map(error => rt(`error_${error}`)).join('；')" />
    </template>
  </section>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { recoveryRoles, recoveryEnabled, newRecoveryConfig, recoveryFormErrors, recoveryStepMatches } from '../reminderRecovery.mjs'
const props = defineProps({ modelValue: { type: Object, default: () => ({}) }, steps: { type: Array, default: () => [] }, engine: { type: String, default: 'K6' }, concurrency: { type: Number, default: 1 }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'validity'])
const { t } = useI18n()
const rt = key => t(`performanceTesting.recovery.${key}`)
const enabled = computed(() => recoveryEnabled(props.modelValue))
const errors = computed(() => recoveryFormErrors(props.modelValue, props.steps, props.engine, props.concurrency))
const groups = computed(() => [...new Set(props.steps.filter(step => step.enabled !== false && step.execution_policy?.max_runs_per_vu === 1).map(step => step.execution_policy.group_id).filter(Boolean))])
const options = role => props.steps.filter(step => recoveryStepMatches(step, role, props.modelValue.stock_code) && step.execution_policy?.group_id === props.modelValue.group_id)
watch(errors, value => emit('validity', !value.length), { immediate: true })
function toggle(value) { if (!props.disabled) emit('update:modelValue', value ? newRecoveryConfig() : {}) }
function field(key, value) { if (!props.disabled) emit('update:modelValue', { ...props.modelValue, [key]: value }) }
</script>

<style scoped>
.recovery-editor { margin: 24px 0; min-width: 0; }
.recovery-editor h3 { font-size: 16px; }
.recovery-note { font-size: 13px; color: var(--el-text-color-secondary); line-height: 1.7; overflow-wrap: anywhere; }
.recovery-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.el-select, .el-input-number { width: 100%; }
@media (max-width: 800px) { .recovery-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
