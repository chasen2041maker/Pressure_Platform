<template>
  <div class="policy-editor">
    <el-form-item label="专项执行">
      <el-switch :model-value="enabled" :disabled="setupStep && !enabled" @update:model-value="toggle" />
      <span class="policy-note">关闭时，所有用户每轮按顺序执行。</span>
    </el-form-item>
    <template v-if="enabled">
      <el-alert v-if="setupStep" type="error" :closable="false" title="前置步骤不能配置专项执行，请取消前置标记或关闭专项执行。" />
      <el-form-item label="执行组标识"><el-input :model-value="modelValue.group_id" maxlength="48" placeholder="例如 reminders_01" @update:model-value="value => change('group_id', value)" /></el-form-item>
      <div class="policy-fields">
        <el-form-item v-for="field in fields" :key="field.key" :label="field.label">
          <el-input-number :model-value="modelValue[field.key]" :min="field.min" :max="field.max" :precision="0" @update:model-value="value => change(field.key, value)" />
        </el-form-item>
      </div>
      <p class="policy-note">同组步骤必须连续且以上配置完全一致。VU 序号对应账号池原始行；组内开始一次就消耗一次额度，失败不会补发。组外请仅引用前置或固定变量。</p>
      <p class="policy-note">本组最多 {{ budget ?? '待补齐' }} 次尝试；每次包含本组全部步骤，认证重试另计。跳过和依赖阻断不算请求成功。</p>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ modelValue: { type: Object, default: () => ({}) }, setupStep: Boolean })
const emit = defineEmits(['update:modelValue'])
const enabled = computed(() => Object.keys(props.modelValue || {}).length > 0)
const fields = [
  { key: 'vu_start', label: '起始 VU', min: 1, max: 100000 },
  { key: 'vu_end', label: '结束 VU', min: 1, max: 100000 },
  { key: 'max_runs_per_vu', label: '每用户上限', min: 1, max: 1000000 },
  { key: 'min_interval_ms', label: '最小间隔 ms', min: 0, max: 7200000 }
]
const budget = computed(() => {
  const p = props.modelValue || {}
  return [p.vu_start, p.vu_end, p.max_runs_per_vu].every(Number.isInteger) && p.vu_end >= p.vu_start
    ? (p.vu_end - p.vu_start + 1) * p.max_runs_per_vu : null
})
function toggle(value) {
  if (value && props.setupStep) return
  emit('update:modelValue', value ? { group_id: 'special', vu_start: 1, vu_end: 1, max_runs_per_vu: 1, min_interval_ms: 0 } : {})
}
function change(key, value) { emit('update:modelValue', { ...props.modelValue, [key]: value }) }
</script>

<style scoped>
.policy-editor { margin: 12px 0 20px; padding: 16px; border: 1px solid var(--el-border-color); border-radius: 6px; }
.policy-fields { display: flex; flex-wrap: wrap; gap: 0 24px; }
.policy-note { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin-left: 12px; }
</style>
