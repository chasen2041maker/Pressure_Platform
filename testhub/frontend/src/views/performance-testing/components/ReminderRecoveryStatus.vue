<template>
  <el-card shadow="never" class="recovery-status">
    <template #header><div class="recovery-heading"><strong>{{ rt('statusTitle') }}</strong><el-button :disabled="loading" :loading="loading" size="small" @click="refresh">{{ rt('refresh') }}</el-button></div></template>
    <el-alert :title="rt(`state_${observation.state}`)" :type="tone" :closable="false" show-icon />
    <template v-if="observation.state !== 'DISABLED'">
      <p class="recovery-note">{{ rt('metricsNotice') }}</p>
      <dl class="recovery-counts"><div v-for="field in recoveryCountFields" :key="field"><dt>{{ rt(`count_${field}`) }}</dt><dd>{{ display(observation.counts[field]) }}</dd></div></dl>
      <p class="recovery-note">{{ rt('unknownNotice') }}</p>
    </template>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { recoveryCountFields, recoveryObservation } from '../reminderRecovery.mjs'
const props = defineProps({ value: { type: Object, default: null }, loading: Boolean })
const emit = defineEmits(['refresh'])
const { t } = useI18n()
const rt = key => t(`performanceTesting.recovery.${key}`)
const observation = computed(() => recoveryObservation(props.value))
const tone = computed(() => ['CORRUPT', 'CONFLICT'].includes(observation.value.state) ? 'error' : observation.value.state === 'RECOVERED' ? 'success' : ['UNKNOWN', 'PENDING', 'PREPARING'].includes(observation.value.state) ? 'warning' : 'info')
const display = value => Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString() : rt('unknown')
function refresh() { if (!props.loading) emit('refresh') }
</script>

<style scoped>
.recovery-status { margin: 16px 0; }
.recovery-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.recovery-counts { display: flex; flex-wrap: wrap; gap: 16px 32px; }
.recovery-counts dt, .recovery-note { font-size: 13px; line-height: 1.7; color: var(--el-text-color-secondary); }
.recovery-counts dd { margin: 6px 0; font-size: 24px; font-weight: 600; }
</style>
