import request from '@/utils/api'

// 工具目录：汇总 + 全量工具列表（元数据/参数 schema/近 7 天统计）
export function getMcpTools() {
  return request({ url: '/mcp/tools/', method: 'get' })
}

// 单工具详情（含完整描述）
export function getMcpToolDetail(name) {
  return request({ url: `/mcp/tools/${name}/`, method: 'get' })
}

// 接入配置：协议端点 + 本人长效 API-Key（客户端配置直接复制）
export function getMcpConfig() {
  return request({ url: '/mcp/config/', method: 'get' })
}

// MCP 调用日志（分页，支持 tool 过滤）
export function getCallLogs(params) {
  return request({ url: '/mcp/logs/', method: 'get', params })
}

// 待确认操作列表（分页，支持 status 过滤）
export function getPendingList(params) {
  return request({ url: '/mcp/pending/', method: 'get', params })
}

// 批准待确认操作
export function approvePending(id) {
  return request({ url: `/mcp/pending/${id}/approve/`, method: 'post' })
}

// 拒绝待确认操作
export function rejectPending(id) {
  return request({ url: `/mcp/pending/${id}/reject/`, method: 'post' })
}
