<template>
  <el-card shadow="never" class="sse-metrics">
    <template #header><strong>{{ t('performanceTesting.sse.metricsTitle') }}</strong></template>
    <el-alert type="info" :closable="false" show-icon :title="t('performanceTesting.sse.metricsNotice')" />
    <dl class="stream-counts"><div v-for="field in fields" :key="field"><dt>{{ t(`performanceTesting.sse.counts.${field}`) }}</dt><dd>{{ display(summary[field]) }}</dd></div></dl>
    <p>{{ t('performanceTesting.sse.events') }}: {{ display(summary.events) }} · {{ t('performanceTesting.sse.bytes') }}: {{ display(summary.bytes) }}</p>
    <dl class="stream-counts"><div v-for="kind in ['firstEvent', 'completion']" :key="kind"><dt>{{ t(`performanceTesting.sse.${kind}Latency`) }} · {{ t('performanceTesting.sse.average') }}</dt><dd>{{ display(summary[kind].avg_ms, 2) }} ms</dd><span class="sse-note">n={{ display(summary[kind].count) }}</span></div></dl>
    <el-alert v-if="metrics?.streams_truncated" type="warning" :closable="false" :title="t('performanceTesting.sse.truncated')" />
    <el-table :data="rows" border size="small" :empty-text="t('performanceTesting.sse.noStreams')">
      <el-table-column :label="t('performanceTesting.sse.stream')" min-width="180"><template #default="{ row }">{{ row.name || t('performanceTesting.sse.unknown') }} · #{{ row.stepId }}</template></el-table-column>
      <el-table-column v-for="field in fields" :key="field" :label="t(`performanceTesting.sse.counts.${field}`)" min-width="100" align="right"><template #default="{ row }">{{ display(row[field]) }}</template></el-table-column>
      <el-table-column v-for="kind in ['firstEvent', 'completion']" :key="kind" :label="t(`performanceTesting.sse.${kind}Latency`)" min-width="220"><template #default="{ row }">
        {{ t('performanceTesting.sse.average') }} {{ display(row[kind].avg_ms, 2) }} ms
        <div class="sse-note">{{ t('performanceTesting.sse.range') }} {{ display(row[kind].min_ms, 2) }}–{{ display(row[kind].max_ms, 2) }} ms · n={{ display(row[kind].count) }}</div>
      </template></el-table-column>
      <el-table-column :label="t('performanceTesting.sse.failures')" min-width="240"><template #default="{ row }">
        <div v-for="(error, index) in row.errors" :key="index">{{ t(`performanceTesting.sse.phases.${error.phase}`) }} · {{ reasonLabel(error.reason) }} × {{ display(error.count) }}</div>
        <span v-if="!row.errors.length">{{ t('performanceTesting.sse.noRecordedErrors') }}</span>
        <div v-for="(location, index) in row.diagnostics" :key="`location-${index}`" class="sse-note">{{ locationLabel(location) }} · {{ reasonLabel(location.reason) }} × {{ display(location.count) }}</div>
        <div v-if="row.diagnosticsTruncated" class="sse-note">{{ t('performanceTesting.sse.diagnosticsTruncated') }}</div>
      </template></el-table-column>
    </el-table>
    <p class="sse-note">{{ t('performanceTesting.sse.latencyNotice') }}</p>
    <p class="sse-note">{{ t('performanceTesting.sse.diagnosticsNotice') }}</p>
    <details v-if="summary.errors.length"><summary>{{ t('performanceTesting.sse.failures') }}</summary><p v-for="(error, index) in summary.errors" :key="index">{{ t(`performanceTesting.sse.phases.${error.phase}`) }} · {{ reasonLabel(error.reason) }} × {{ display(error.count) }}</p></details>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { sseSummary, sseStreamRows } from '../sseMetrics.mjs'
const props = defineProps({ metrics: { type: Object, default: null }, steps: { type: Array, default: () => [] } })
const { t } = useI18n()
const fields = ['started', 'completed', 'success', 'failed', 'incomplete']
const summary = computed(() => sseSummary(props.metrics))
const rows = computed(() => sseStreamRows(props.metrics, props.steps))
const display = (value, digits = 0) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : t('performanceTesting.sse.unknown')
const reasonLabel = reason => reason === 'unknown' ? t('performanceTesting.sse.unknown') : reason
const locationLabel = location => [t(`performanceTesting.sse.scopes.${location.scope}`), ...['event_index', 'rule_index', 'condition_index'].filter(key => location[key] != null).map(key => `${t(`performanceTesting.sse.positions.${key}`)} ${location[key]}`)].join(' · ')
</script>

<style scoped>
.sse-metrics { margin: 16px 0; }
.stream-counts { display: flex; flex-wrap: wrap; gap: 16px 40px; }
.stream-counts dt, .sse-note { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.stream-counts dd { margin: 6px 0; font-size: 24px; font-weight: 600; }
</style>
