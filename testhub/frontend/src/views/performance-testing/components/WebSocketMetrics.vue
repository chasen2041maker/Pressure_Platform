<template>
  <el-card shadow="never" class="ws-metrics">
    <template #header><strong>{{ t('performanceTesting.websocket.metricsTitle') }}</strong></template>
    <el-alert type="info" :closable="false" show-icon :title="t('performanceTesting.websocket.metricsNotice')" />
    <dl class="connection-counts">
      <div v-for="(value, key) in connections" :key="key"><dt>{{ t(`performanceTesting.websocket.connections.${key}`) }}</dt><dd>{{ display(value) }}</dd></div>
    </dl>
    <p class="ws-note">{{ t('performanceTesting.websocket.connectionNotice') }}</p>
    <el-table :data="stages" border size="small">
      <el-table-column prop="label" :label="t('performanceTesting.websocket.stage')" min-width="100" />
      <el-table-column v-for="field in fields" :key="field" :label="t(`performanceTesting.websocket.counts.${field}`)" min-width="100" align="right">
        <template #default="{ row }">{{ display(row[field]) }}</template>
      </el-table-column>
    </el-table>
    <h4>{{ t('performanceTesting.websocket.commandRtt') }}</h4>
    <el-alert v-if="metrics?.commands_truncated" type="warning" :closable="false"
      :title="t('performanceTesting.websocket.truncated', { count: metrics.command_metrics?.length || 0, total: metrics.commands_total ?? '?' })" />
    <el-table :data="commands" border size="small" :empty-text="t('performanceTesting.websocket.noCommands')">
      <el-table-column :label="t('performanceTesting.websocket.command')" min-width="230"><template #default="{ row }">
        {{ row.stepName || `#${row.stepId}` }} · {{ row.commandIndex + 1 }} · {{ row.name || t('performanceTesting.websocket.unknownCommand') }}
        <div><code>{{ row.action || '—' }}</code></div>
      </template></el-table-column>
      <el-table-column :label="t('performanceTesting.websocket.protocolLatency')" min-width="130"><template #default="{ row }">{{ t(`performanceTesting.websocket.${row.latencyKind === 'event_wait' ? 'eventLatency' : row.latencyKind === 'command' ? 'commandLatency' : 'unknown'}`) }}</template></el-table-column>
      <el-table-column v-for="field in ['total', 'success', 'failed']" :key="field" :label="t(`performanceTesting.websocket.counts.${field}`)" min-width="105" align="right"><template #default="{ row }">{{ display(row[field]) }}</template></el-table-column>
      <el-table-column v-for="field in ['avg_rt', 'p95_rt', 'p99_rt']" :key="field" :label="t(`performanceTesting.websocket.${field}`)" min-width="145" align="right"><template #default="{ row }">{{ display(row[field], 2) }}</template></el-table-column>
    </el-table>
    <p class="ws-note">{{ t('performanceTesting.websocket.commandNotice') }}</p>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { websocketCommandRows, websocketConnectionCounts } from '../websocketMetrics.mjs'
const props = defineProps({ metrics: { type: Object, default: null }, steps: { type: Array, default: () => [] } })
const { t } = useI18n()
const fields = ['started', 'completed', 'success', 'failed', 'incomplete']
const stages = computed(() => ['sessions', 'connect', 'auth', 'commands', 'events', 'heartbeat'].map(key => ({ ...props.metrics?.[key], label: t(`performanceTesting.websocket.stages.${key}`) })))
const connections = computed(() => websocketConnectionCounts(props.metrics))
const commands = computed(() => websocketCommandRows(props.metrics, props.steps))
const display = (value, digits = 0) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : t('performanceTesting.websocket.unknown')
</script>

<style scoped>
.ws-metrics { margin: 16px 0; }
.connection-counts { display: flex; flex-wrap: wrap; gap: 16px 40px; }
.connection-counts dt, .ws-note { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.connection-counts dd { margin: 6px 0; font-size: 24px; font-weight: 600; }
code { overflow-wrap: anywhere; }
</style>
