<template>
  <el-card shadow="never" class="evidence-card">
    <template #header>执行结论与运行来源</template>
    <p>执行状态：{{ statusLabel }}；SLA：{{ slaLabel }}。执行完成和性能达标分别判定。</p>
    <template v-if="summary.timed_load">
      <p>配置发压 {{ metricValue(summary.timed_load.configured_seconds) }} 秒；最多收尾 {{ metricValue(summary.timed_load.drain_limit_seconds) }} 秒；引擎运行 {{ metricValue(summary.timed_load.engine_seconds) }} 秒。</p>
      <p>{{ summary.timed_load.notice }}</p>
    </template>
    <el-alert v-if="summary.setup_incomplete_vus" :title="`${summary.setup_incomplete_vus} 个用户在发压到期时尚未完成全部前置步骤`" type="error" :closable="false" />
    <el-alert v-if="summary.completion_reason" :title="SLA_REASONS[summary.completion_reason] || summary.completion_reason" type="warning" :closable="false" />
    <el-alert v-if="summary.sla_abort" title="SLA 持续超限触发自动中止；部分结果保留，整体未评估" type="warning" :closable="false" />
    <p v-if="summary.sla_abort">中止原因：{{ summary.sla_abort.reason }}；触发证据：{{ metricValue(summary.sla_abort.elapsed_seconds ?? summary.sla_abort.ts_offset) }} 秒</p>
    <details class="source-details">
      <summary>运行来源与版本 <span>查看环境、账号池及接口快照</span></summary>
      <el-table v-if="abortRows.length" :data="abortRows" size="small">
        <el-table-column label="中止范围" min-width="180"><template #default="{ row }">{{ slaScope(row) }}</template></el-table-column>
        <el-table-column prop="label" label="指标" /><el-table-column prop="threshold" label="阈值" />
        <el-table-column label="触发实测"><template #default="{ row }">{{ metricValue(row.actual) }}</template></el-table-column>
      </el-table>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="计划 / 实际峰值 VU">{{ execution.load_snapshot?.concurrency ?? execution.load_snapshot?.vus ?? '未采集' }} / {{ summary.max_concurrency ?? '未采集' }}</el-descriptions-item>
        <el-descriptions-item label="场景 / 执行快照">#{{ evidence.scenario_id }} · {{ evidence.scenario_name || '历史名称未冻结' }} / #{{ execution.id }}</el-descriptions-item>
        <el-descriptions-item label="k6 / 适配器">{{ evidence.k6_version || '未采集' }} / {{ evidence.adapter_version || '未采集' }}</el-descriptions-item>
        <el-descriptions-item label="环境版本">{{ evidence.environments?.length ? JSON.stringify(evidence.environments) : '未选择或历史未采集' }}</el-descriptions-item>
        <el-descriptions-item label="账号池版本">{{ Object.keys(evidence.account_pool || {}).length ? JSON.stringify(evidence.account_pool) : '未选择或历史未采集' }}</el-descriptions-item>
        <el-descriptions-item label="接口来源版本">{{ evidence.interfaces?.length ? JSON.stringify(evidence.interfaces) : '历史未采集' }}</el-descriptions-item>
        <el-descriptions-item label="runner 版本">{{ Object.keys(evidence.runner || {}).length ? JSON.stringify(evidence.runner) : '未采集' }}</el-descriptions-item>
        <el-descriptions-item label="本次冻结 SLA">{{ evidence.sla_config ? JSON.stringify(evidence.sla_config) : '历史未采集；不会以当前场景配置代替' }}</el-descriptions-item>
        <el-descriptions-item label="服务端资源">未采集</el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="summary.throughput_notice" :title="summary.throughput_notice" type="info" :closable="false" />
      <p>指标来源：TestHub 累计请求事件；P95/P99 为累计直方图估算。停止或缺失数据不代表达标。逐接口只采集已完成次数；逐接口发起/未完成次数未采集，全局计数见汇总。</p>
    </details>
  </el-card>
</template>
<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SLA_REASONS, slaScope, metricValue } from '../slaReport.mjs'
const props = defineProps({ execution: { type: Object, required: true } })
const { t } = useI18n()
const summary = computed(() => props.execution.summary || {})
const evidence = computed(() => props.execution.report_evidence || {})
const statusLabel = computed(() => t('performanceTesting.status.' + props.execution.status))
const slaLabel = computed(() => t('performanceTesting.sla.' + (props.execution.sla_result || 'NOT_EVALUATED')))
const abortRows = computed(() => summary.value.sla_abort?.details || summary.value.sla_abort?.rules || [])
</script>
<style scoped>
.evidence-card { margin:16px 0; min-width:0; }
p { margin:8px 0; line-height:1.6; overflow-wrap:anywhere; }
.source-details { margin-top:12px; }
.source-details > summary { cursor:pointer; padding:12px 0; color:var(--el-text-color-primary); font-weight:600; }
.source-details > summary span { margin-left:8px; font-size:12px; font-weight:400; color:var(--el-text-color-secondary); }
:deep(.el-descriptions__table) { table-layout:fixed; width:100%; }
:deep(.el-descriptions__label) { width:140px; white-space:nowrap; word-break:keep-all; }
:deep(.el-descriptions__content) { overflow-wrap:anywhere; word-break:break-word; line-height:1.6; }
:deep(.el-alert) { margin-top:8px; }
@media (max-width:600px) {
  :deep(.el-descriptions__label) { width:124px; }
  .source-details > summary span { display:block; margin-left:18px; margin-top:4px; }
}
</style>
