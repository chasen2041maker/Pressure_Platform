<template>
  <div>
    <el-alert :closable="false" type="info" title="全局与接口规则独立判定。响应时间单位 ms、失败率单位 %、速率为已完成业务请求/秒。0% 表示不允许失败；时延或速率填 0 表示不设置。" />
    <h3>全局业务阈值</h3>
    <div class="threshold-grid">
      <el-form-item v-for="metric in metrics" :key="metric.key" :label="metric.label">
        <el-input-number v-model="model.thresholds[metric.key]" :min="0" :max="metric.max" :precision="2" controls-position="right" @change="changed" />
      </el-form-item>
    </div>
    <h3>按接口验收阈值</h3>
    <p>只可选择已保存且启用的业务步骤。修改步骤名称不改变规则归属；删除或禁用前请先移除对应规则并保存。</p>
    <div v-for="(rule, index) in model.step_thresholds" :key="index" class="step-rule">
      <el-select v-model="rule.step_id" placeholder="选择业务步骤（稳定 ID）" style="width:100%" @change="changed">
        <el-option v-for="step in selectable" :key="step.id" :value="step.id" :label="`#${step.id} ${step.method} ${step.name} · ${step.url}`" :disabled="model.step_thresholds.some((r, n) => n !== index && r.step_id === step.id)" />
      </el-select>
      <div class="threshold-grid">
        <el-form-item v-for="metric in metrics" :key="metric.key" :label="metric.label">
          <el-input-number v-model="rule.thresholds[metric.key]" :min="0" :max="metric.max" :precision="2" controls-position="right" @change="changed" />
        </el-form-item>
      </div>
      <el-button type="danger" plain @click="model.step_thresholds.splice(index, 1); changed()">移除此接口规则</el-button>
    </div>
    <el-button :disabled="!selectable.length" @click="model.step_thresholds.push({ step_id: null, thresholds: {} }); changed()">添加接口 SLA</el-button>
    <el-empty v-if="!selectable.length" description="请先保存业务步骤，再设置接口 SLA" :image-size="50" />
    <el-form-item label="最少观察延迟（秒）" class="delay-field">
      <el-input-number v-model="model.abort_delay" :min="0" :max="86400" @change="changed" />
    </el-form-item>
    <p>自动中止使用累计请求指标。同一规则在观察延迟结束后持续超限达到下方窗口秒数才中止；采样空缺会重置连续时间。</p>
  </div>
</template>
<script setup>
import { computed } from 'vue'
const props = defineProps({ modelValue: { type: Object, required: true }, steps: { type: Array, default: () => [] } })
const emit = defineEmits(['change'])
const model = computed(() => props.modelValue)
const selectable = computed(() => props.steps.filter(s => Number.isInteger(s.id) && s.enabled !== false && !s.is_setup))
const metrics = [
  { key: 'avg_response_time', label: '平均响应上限（ms）' }, { key: 'p90_response_time', label: 'P90 上限（ms）' },
  { key: 'p95_response_time', label: 'P95 上限（ms）' }, { key: 'p99_response_time', label: 'P99 上限（ms）' },
  { key: 'error_rate', label: '失败率上限（%）', max: 100 }, { key: 'min_tps', label: '最低业务 RPS' }
]
const changed = () => emit('change')
</script>
<style scoped>
.threshold-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:8px; margin-top:16px; }
.step-rule { border:1px solid var(--el-border-color); border-radius:6px; padding:12px; margin-bottom:12px; }
p { color:var(--el-text-color-secondary); line-height:1.6; }
.delay-field { margin-top:20px; }
</style>
