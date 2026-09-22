export function observedMetrics(summary = {}, k6 = false) {
  const result = { ...summary }
  if (k6 && !(Number(summary.business_total ?? summary.total_requests) > 0)) {
    for (const key of ['avg_rt', 'p90_rt', 'p95_rt', 'p99_rt', 'max_rt', 'error_rate']) result[key] = null
  }
  return result
}
export function metricValue(value, digits = 2) {
  return value == null || value === '' || !Number.isFinite(Number(value)) ? '未采集' : Number(value).toFixed(digits)
}
export function slaScope(row) {
  return row.scope === 'step' ? `步骤 #${row.step_id} · ${row.step_name || ''}` : '全局业务'
}
export function slaResult(row) {
  return row.passed == null ? '未评估' : row.passed ? '通过' : '未通过'
}
export const SLA_REASONS = {
  sample_data_incomplete: '采样持久化失败，部分时序数据缺失；原始请求统计保留，整体 SLA 未评估',
  execution_incomplete: '执行已停止、失败或未完整结束', iterations_incomplete: '未完成指定用户轮次',
  requests_incomplete: '存在未完成 HTTP 请求', no_business_requests: '没有已完成业务请求',
  no_step_requests: '目标步骤没有已完成请求', missing_metric: '指标未采集', unknown_step: '步骤已删除、禁用或不是业务步骤', invalid_config: 'SLA 配置无效'
}
