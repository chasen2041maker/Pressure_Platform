import request from '@/utils/api'

export const getPerfAccountPools = params => request({ url: '/perf-testing/account-pools/', method: 'get', params })
export const getPerfAccountPool = id => request({ url: `/perf-testing/account-pools/${id}/`, method: 'get' })
export const inspectPerfAccountPool = data => request({ url: '/perf-testing/account-pools/inspect/', method: 'post', data, headers: { 'Content-Type': 'multipart/form-data' } })
export const createPerfAccountPool = data => request({ url: '/perf-testing/account-pools/', method: 'post', data, headers: { 'Content-Type': 'multipart/form-data' } })
export const getPerfAccountPoolVersions = id => request({ url: `/perf-testing/account-pools/${id}/versions/`, method: 'get' })
export const createPerfAccountPoolVersion = (id, data) => request({ url: `/perf-testing/account-pools/${id}/versions/`, method: 'post', data, headers: { 'Content-Type': 'multipart/form-data' } })
export const getPerfAccountPoolVersion = id => request({ url: `/perf-testing/account-pool-versions/${id}/`, method: 'get' })
export const previewPerfAccountPoolVersion = id => request({ url: `/perf-testing/account-pool-versions/${id}/preview/`, method: 'get' })
export const getPerfAccountPoolUsage = id => request({ url: `/perf-testing/account-pool-versions/${id}/usage/`, method: 'get' })
export const deletePerfAccountPool = id => request({ url: `/perf-testing/account-pools/${id}/`, method: 'delete' })
export const deletePerfAccountPoolVersion = id => request({ url: `/perf-testing/account-pool-versions/${id}/`, method: 'delete' })

export function getPerfEnvironmentPermissions() {
  return request({ url: '/perf-testing/environments/permissions/', method: 'get' })
}
export function getPerfEnvironments(params) {
  return request({ url: '/perf-testing/environments/', method: 'get', params })
}
export function getPerfEnvironment(id) {
  return request({ url: `/perf-testing/environments/${id}/`, method: 'get' })
}
export function createPerfEnvironment(data) {
  return request({ url: '/perf-testing/environments/', method: 'post', data })
}
export function updatePerfEnvironment(id, data) {
  return request({ url: `/perf-testing/environments/${id}/`, method: 'patch', data })
}
export function deletePerfEnvironment(id) {
  return request({ url: `/perf-testing/environments/${id}/`, method: 'delete' })
}

// ==================== 引擎与能力 ====================
// 前端据此置灰不可用引擎，并决定实时通道走 WebSocket 还是轮询降级
export function getEngineStatus() {
  return request({ url: '/perf-testing/engines/status/', method: 'get' })
}

// ==================== 压测项目 ====================
export function getPerfProjects(params) {
  return request({ url: '/perf-testing/projects/', method: 'get', params })
}
export function getPerfProject(id) {
  return request({ url: `/perf-testing/projects/${id}/`, method: 'get' })
}

export const getPerfScenarioDefaults = id => request({ url: `/perf-testing/projects/${id}/scenario-defaults/`, method: 'get' })
export function createPerfProject(data) {
  return request({ url: '/perf-testing/projects/', method: 'post', data })
}
export function updatePerfProject(id, data) {
  return request({ url: `/perf-testing/projects/${id}/`, method: 'patch', data })
}
export function deletePerfProject(id) {
  return request({ url: `/perf-testing/projects/${id}/`, method: 'delete' })
}
export function getPerfProjectStatistics(id) {
  return request({ url: `/perf-testing/projects/${id}/statistics/`, method: 'get' })
}

// ==================== 压测场景 ====================
export function getPerfScenarios(params) {
  return request({ url: '/perf-testing/scenarios/', method: 'get', params })
}
export function getPerfScenario(id) {
  return request({ url: `/perf-testing/scenarios/${id}/`, method: 'get' })
}
export function createPerfScenario(data) {
  return request({ url: '/perf-testing/scenarios/', method: 'post', data })
}
export function updatePerfScenario(id, data) {
  return request({ url: `/perf-testing/scenarios/${id}/`, method: 'patch', data })
}
export function deletePerfScenario(id) {
  return request({ url: `/perf-testing/scenarios/${id}/`, method: 'delete' })
}
// 整表覆盖式保存步骤，避免逐条增删改带来的顺序错乱
export function savePerfScenarioSteps(id, steps) {
  return request({ url: `/perf-testing/scenarios/${id}/save-steps/`, method: 'post', data: { steps } })
}
export function duplicatePerfScenario(id, data) {
  return request({ url: `/perf-testing/scenarios/${id}/duplicate/`, method: 'post', data })
}
export function importStepsFromApi(id, data) {
  return request({ url: `/perf-testing/scenarios/${id}/import-from-api/`, method: 'post', data })
}
export function preflightPerfScenario(id, data) {
  return request({ url: `/perf-testing/scenarios/${id}/preflight/`, method: 'post', data })
}
export function executePerfScenario(id, data) {
  return request({ url: `/perf-testing/scenarios/${id}/execute/`, method: 'post', data })
}
export function debugPerfScenario(id, data) {
  return request({ url: `/perf-testing/scenarios/${id}/debug/`, method: 'post', data })
}
export function getScenarioExecutionHistory(id, params) {
  return request({ url: `/perf-testing/scenarios/${id}/execution-history/`, method: 'get', params })
}

// ==================== 场景步骤（单步维护，批量场景用 save-steps） ====================
export function getPerfSteps(params) {
  return request({ url: '/perf-testing/steps/', method: 'get', params })
}
export function createPerfStep(data) {
  return request({ url: '/perf-testing/steps/', method: 'post', data })
}
export function updatePerfStep(id, data) {
  return request({ url: `/perf-testing/steps/${id}/`, method: 'patch', data })
}
export function deletePerfStep(id) {
  return request({ url: `/perf-testing/steps/${id}/`, method: 'delete' })
}

// ==================== 执行记录 ====================
export function getPerfExecutions(params) {
  return request({ url: '/perf-testing/executions/', method: 'get', params })
}
export function getPerfExecution(id) {
  return request({ url: `/perf-testing/executions/${id}/`, method: 'get' })
}
export function deletePerfExecution(id) {
  return request({ url: `/perf-testing/executions/${id}/`, method: 'delete' })
}
export function stopPerfExecution(id) {
  return request({ url: `/perf-testing/executions/${id}/stop/`, method: 'post' })
}
// 新客户端传 { after_id } 按行增量分页；数值 since 保留原时间偏移协议。
export function getPerfRealtime(id, since = -1) {
  return request({ url: `/perf-testing/executions/${id}/realtime/`, method: 'get', params: typeof since === 'object' ? since : { since } })
}
// 报告用：服务端降采样到 max_points 个点，避免长压测点数过多拖垮图表
// 返回 { total, returned, samples: [...] }
export function getPerfSamples(id, points = 1000) {
  return request({ url: `/perf-testing/executions/${id}/samples/`, method: 'get', params: { max_points: points } })
}
export function getPerfRequestStats(id) {
  return request({ url: `/perf-testing/executions/${id}/request-stats/`, method: 'get' })
}
export function generatePerfReport(id) {
  return request({ url: `/perf-testing/executions/${id}/generate-report/`, method: 'post' })
}
export function getPerfRunLog(id, params) {
  return request({ url: `/perf-testing/executions/${id}/run-log/`, method: 'get', params })
}
export function comparePerfExecutions(ids) {
  return request({ url: '/perf-testing/executions/compare/', method: 'get', params: { ids: ids.join(',') } })
}

// ==================== 对照报告（持久化 + AI 分析） ====================
export function getPerfComparisonReports(params) {
  return request({ url: '/perf-testing/comparison-reports/', method: 'get', params })
}
export function getPerfComparisonReport(id) {
  return request({ url: `/perf-testing/comparison-reports/${id}/`, method: 'get' })
}
export function createPerfComparisonReport(data) {
  return request({ url: '/perf-testing/comparison-reports/', method: 'post', data })
}
export function deletePerfComparisonReport(id) {
  return request({ url: `/perf-testing/comparison-reports/${id}/`, method: 'delete' })
}
export function getPerfDashboard(params) {
  return request({ url: '/perf-testing/executions/dashboard/', method: 'get', params })
}
export function reapStalePerfExecutions() {
  return request({ url: '/perf-testing/executions/reap-stale/', method: 'post' })
}
// 报告 HTML 与原始 CSV 分享直链：可附加 ?token= 让未登录用户也能访问
export function getPerfReportUrl(id, token) {
  const base = `/api/perf-testing/executions/${id}/report/`
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}
export function getPerfRawDownloadUrl(id, token) {
  const base = `/api/perf-testing/executions/${id}/download-raw/`
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}
// 生成/重置分享直链；expiresInDays=null 表示永不过期
export function generatePerfShareLink(id, expiresInDays = null) {
  return request({
    url: `/perf-testing/executions/${id}/share-link/`,
    method: 'post',
    data: { expires_in_days: expiresInDays }
  })
}
// 撤销分享直链
export function revokePerfShareLink(id) {
  return request({ url: `/perf-testing/executions/${id}/revoke-share-link/`, method: 'post' })
}

// ==================== 性能基线 ====================
export function getPerfBaselines(params) {
  return request({ url: '/perf-testing/baselines/', method: 'get', params })
}
export function createPerfBaseline(data) {
  return request({ url: '/perf-testing/baselines/', method: 'post', data })
}
export function updatePerfBaseline(id, data) {
  return request({ url: `/perf-testing/baselines/${id}/`, method: 'patch', data })
}
export function deletePerfBaseline(id) {
  return request({ url: `/perf-testing/baselines/${id}/`, method: 'delete' })
}
export function setBaselineFromExecution(data) {
  return request({ url: '/perf-testing/baselines/set-from-execution/', method: 'post', data })
}
export function compareWithBaseline(params) {
  return request({ url: '/perf-testing/baselines/compare/', method: 'get', params })
}

// ==================== 数据文件 / 脚本文件 ====================
// CSV 参数化数据与 JMeter .jmx 脚本共用同一套文件管理接口，用 file_type 区分。
// 查询脚本：getPerfDataFiles({ project, file_type: 'JMX' })
export function getPerfDataFiles(params) {
  return request({ url: '/perf-testing/data-files/', method: 'get', params })
}
export function uploadPerfDataFile(formData) {
  return request({
    url: '/perf-testing/data-files/',
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}
/**
 * 上传 JMeter .jmx 脚本。
 * @param {{ project: number|string, file: File, name?: string }} payload
 */
export function uploadPerfJmxScript({ project, file, name }) {
  const formData = new FormData()
  formData.append('project', String(project))
  formData.append('file_type', 'JMX')
  formData.append('file', file)
  if (name) formData.append('name', name)
  return uploadPerfDataFile(formData)
}
/**
 * 上传压测步骤的 multipart 请求体文件（file_type=UPLOAD），不限扩展名。
 * 查询：getPerfDataFiles({ project, file_type: 'UPLOAD' })
 * @param {{ project: number|string, file: File, name?: string }} payload
 */
export function uploadPerfUploadFile({ project, file, name }) {
  const formData = new FormData()
  formData.append('project', String(project))
  formData.append('file_type', 'UPLOAD')
  formData.append('file', file)
  if (name) formData.append('name', name)
  return uploadPerfDataFile(formData)
}
export function deletePerfDataFile(id) {
  return request({ url: `/perf-testing/data-files/${id}/`, method: 'delete' })
}
export function previewPerfDataFile(id, params) {
  return request({ url: `/perf-testing/data-files/${id}/preview/`, method: 'get', params })
}

// ==================== 定时压测 ====================
export function getPerfScheduledTasks(params) {
  return request({ url: '/perf-testing/scheduled-tasks/', method: 'get', params })
}
export function createPerfScheduledTask(data) {
  return request({ url: '/perf-testing/scheduled-tasks/', method: 'post', data })
}
export function updatePerfScheduledTask(id, data) {
  return request({ url: `/perf-testing/scheduled-tasks/${id}/`, method: 'patch', data })
}
export function deletePerfScheduledTask(id) {
  return request({ url: `/perf-testing/scheduled-tasks/${id}/`, method: 'delete' })
}
export function togglePerfScheduledTask(id) {
  return request({ url: `/perf-testing/scheduled-tasks/${id}/toggle/`, method: 'post' })
}
export function runPerfScheduledTaskNow(id) {
  return request({ url: `/perf-testing/scheduled-tasks/${id}/run-now/`, method: 'post' })
}
export function getPerfScheduledTaskExecutions(id) {
  return request({ url: `/perf-testing/scheduled-tasks/${id}/executions/`, method: 'get' })
}
export const getPerfApiCatalog = (id, params) => request({ url: `/perf-testing/projects/${id}/api-catalog/`, method: 'get', params })
export const getPerfApiCatalogRequest = (id, requestId) => request({ url: `/perf-testing/projects/${id}/api-catalog/requests/${requestId}/`, method: 'get' })
export const previewPerfApiCatalog = (id, data) => request({ url: `/perf-testing/projects/${id}/api-catalog/preview/`, method: 'post', data, headers: { 'Content-Type': 'multipart/form-data' } })
export const importPerfApiCatalog = (id, data) => request({ url: `/perf-testing/projects/${id}/api-catalog/import/`, method: 'post', data, headers: { 'Content-Type': 'multipart/form-data' } })
export const getPerfScenarioReadiness = (id, params) => request({ url: `/perf-testing/scenarios/${id}/readiness/`, method: 'get', params })
export const getPerfScenarioCatalogDiff = (id, params) => request({ url: `/perf-testing/scenarios/${id}/catalog-diff/`, method: 'get', params })
export const updatePerfScenarioCatalog = (id, data) => request({ url: `/perf-testing/scenarios/${id}/catalog-update/`, method: 'post', data })
export const getPerfApiPoolConfig = id => request({ url: `/perf-testing/projects/${id}/api-pool/config/`, method: 'get' })
export const updatePerfApiPoolConfig = (id, data) => request({ url: `/perf-testing/projects/${id}/api-pool/config/`, method: 'put', data })
export const preparePerfApiPool = (id, data) => request({ url: `/perf-testing/projects/${id}/api-pool/prepare/`, method: 'post', data })
export const verifyPerfApiPool = (id, data) => request({ url: `/perf-testing/projects/${id}/api-pool/verify/`, method: 'post', data })
export const getPerfApiPoolVerification = (id, batchId) => request({ url: `/perf-testing/projects/${id}/api-pool/verification/${batchId}/`, method: 'get' })
export const getPerfPreparedRequest = (id, requestId) => request({ url: `/perf-testing/projects/${id}/api-pool/requests/${requestId}/`, method: 'get' })
export const updatePerfPreparedRequest = (id, requestId, data) => request({ url: `/perf-testing/projects/${id}/api-pool/requests/${requestId}/`, method: 'put', data })
