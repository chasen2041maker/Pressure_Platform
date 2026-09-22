export const severityOptions = [
  { label: '阻塞', value: 'blocker', type: 'danger' },
  { label: '严重', value: 'critical', type: 'danger' },
  { label: '一般', value: 'major', type: 'warning' },
  { label: '轻微', value: 'minor', type: 'info' },
  { label: '建议', value: 'suggestion', type: 'success' }
]

export const priorityOptions = [
  { label: 'P0', value: 'p0', type: 'danger' },
  { label: 'P1', value: 'p1', type: 'warning' },
  { label: 'P2', value: 'p2', type: 'primary' },
  { label: 'P3', value: 'p3', type: 'info' }
]

export const statusOptions = [
  { label: '新建', value: 'new', type: 'info' },
  { label: '已指派', value: 'assigned', type: 'primary' },
  { label: '修复中', value: 'in_progress', type: 'warning' },
  { label: '待回归', value: 'resolved', type: 'warning' },
  { label: '回归通过', value: 'verified', type: 'success' },
  { label: '已驳回', value: 'rejected', type: 'danger' },
  { label: '重新打开', value: 'reopened', type: 'danger' },
  { label: '已关闭', value: 'closed', type: 'info' }
]

export const defectTypeOptions = [
  { label: '功能缺陷', value: 'functional' },
  { label: '界面缺陷', value: 'ui' },
  { label: '兼容性缺陷', value: 'compatibility' },
  { label: '性能缺陷', value: 'performance' },
  { label: '安全缺陷', value: 'security' },
  { label: '数据缺陷', value: 'data' },
  { label: '其他', value: 'other' }
]

export const sourceOptions = [
  { label: '手工测试', value: 'manual' },
  { label: 'API 测试', value: 'api_testing' },
  { label: 'UI 自动化', value: 'ui_automation' },
  { label: 'APP 自动化', value: 'app_automation' },
  { label: '生产反馈', value: 'production' }
]

export function findOption(options, value) {
  return options.find(item => item.value === value)
}

export function getOptionLabel(options, value) {
  return findOption(options, value)?.label || value || '-'
}

export function getOptionType(options, value) {
  return findOption(options, value)?.type || 'info'
}
