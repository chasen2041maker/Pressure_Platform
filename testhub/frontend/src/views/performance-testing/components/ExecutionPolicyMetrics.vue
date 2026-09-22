<template>
  <el-card v-if="rows.length" shadow="never" class="policy-metrics">
    <template #header>专项执行范围与实际参与</template>
    <p>配置来自本次冻结快照。固定轮数取轮数与配额的较小值，按时长执行以配额为上限；最小间隔可能减少实际次数。跳过、依赖阻断和空轮次不计入请求数、TPS 或成功率。</p>
    <el-alert v-if="metrics?.groups_truncated" type="warning" :closable="false" title="策略采样超出大小上限，仅展示已保存的组。" />
    <el-table :data="rows" border size="small">
      <el-table-column label="执行组 / VU 范围" min-width="170"><template #default="{ row }">组 {{ row.group_index }} · {{ row.vu_start }}–{{ row.vu_end }}</template></el-table-column>
      <el-table-column label="每用户上限 / 间隔" min-width="155"><template #default="{ row }">{{ row.max_runs_per_vu }} 次 / {{ row.min_interval_ms }} ms</template></el-table-column>
      <el-table-column v-for="field in fields" :key="field.key" :label="field.label" min-width="105" align="right"><template #default="{ row }">{{ display(row[field.key]) }}</template></el-table-column>
    </el-table>
    <p>跳过次数是有界累计采样，异常停止可能缺少最后一批；未采集保持未知，不补成功或延迟 0。</p>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ metrics: { type: Object, default: null }, steps: { type: Array, default: () => [] } })
const fields = [
  { key: 'planned_participants', label: '计划参与 VU' }, { key: 'participants', label: '实际参与 VU' },
  { key: 'uncovered_participants', label: '尚未参与 VU' },
  { key: 'attempt_limit', label: '计划次数上限' }, { key: 'started', label: '已开始组' },
  { key: 'success', label: '成功组' }, { key: 'failed', label: '失败组' }, { key: 'incomplete', label: '未完成组' },
  { key: 'executed_steps', label: '实际执行步骤' }, { key: 'blocked_steps', label: '依赖阻断步骤' },
  { key: 'not_participant', label: '范围跳过' },
  { key: 'quota', label: '额度跳过' }, { key: 'interval', label: '间隔跳过' }
]
const rows = computed(() => {
  const frozen = new Map()
  for (const step of props.steps) {
    const p = step.execution_policy
    if (Number.isInteger(p?.group_index) && !frozen.has(p.group_index)) frozen.set(p.group_index, p)
  }
  const observed = new Map((props.metrics?.groups || []).map(row => [row.group_index, row]))
  return [...frozen.values()].map(p => ({ ...observed.get(p.group_index), ...p }))
})
const display = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '未采集'
</script>

<style scoped>
.policy-metrics { margin: 16px 0; }
p { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
</style>
