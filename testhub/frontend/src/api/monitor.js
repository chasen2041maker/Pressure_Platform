import request from '@/utils/api'

// 看板聚合
export function getDashboard() {
  return request({ url: '/monitor/dashboard/', method: 'get' })
}

// 调度器在线状态
export function getSchedulerStatus() {
  return request({ url: '/monitor/dashboard/scheduler/', method: 'get' })
}

// 监控目标
export function getTargets(params) {
  return request({ url: '/monitor/targets/', method: 'get', params })
}
export function getTarget(id) {
  return request({ url: `/monitor/targets/${id}/`, method: 'get' })
}
export function createTarget(data) {
  return request({ url: '/monitor/targets/', method: 'post', data })
}
export function updateTarget(id, data) {
  return request({ url: `/monitor/targets/${id}/`, method: 'patch', data })
}
export function deleteTarget(id) {
  return request({ url: `/monitor/targets/${id}/`, method: 'delete' })
}
export function checkTargetNow(id) {
  return request({ url: `/monitor/targets/${id}/check_now/`, method: 'post' })
}
export function debugTest(data) {
  return request({ url: '/monitor/targets/debug_test/', method: 'post', data })
}

// 探测记录
export function getChecks(params) {
  return request({ url: '/monitor/checks/', method: 'get', params })
}

// 告警记录
export function getAlerts(params) {
  return request({ url: '/monitor/alerts/', method: 'get', params })
}
export function acknowledgeAlert(id) {
  return request({ url: `/monitor/alerts/${id}/acknowledge/`, method: 'post' })
}
export function resolveAlert(id) {
  return request({ url: `/monitor/alerts/${id}/resolve/`, method: 'post' })
}

// 通知渠道
export function getChannels(params) {
  return request({ url: '/monitor/channels/', method: 'get', params })
}
export function getChannel(id) {
  return request({ url: `/monitor/channels/${id}/`, method: 'get' })
}
export function createChannel(data) {
  return request({ url: '/monitor/channels/', method: 'post', data })
}
export function updateChannel(id, data) {
  return request({ url: `/monitor/channels/${id}/`, method: 'patch', data })
}
export function deleteChannel(id) {
  return request({ url: `/monitor/channels/${id}/`, method: 'delete' })
}
export function testChannel(id) {
  return request({ url: `/monitor/channels/${id}/test/`, method: 'post' })
}
