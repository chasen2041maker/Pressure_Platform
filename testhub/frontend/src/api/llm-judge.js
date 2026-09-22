import request from '@/utils/api'

// ========== Dashboard ==========
export function getJudgeDashboardStats(params = {}) {
  return request({
    url: '/llm-judge/dashboard/stats/',
    method: 'get',
    params
  })
}

// ========== 服务配置 ==========
export function getJudgeServiceConfig() {
  return request({
    url: '/llm-judge/config/service/',
    method: 'get'
  })
}

export function testJudgeServiceConnection() {
  return request({
    url: '/llm-judge/config/service/',
    method: 'post',
    timeout: 60000
  })
}

// ========== 单条评分 ==========
export function scoreSingle(data) {
  return request({
    url: '/llm-judge/judge/single/',
    method: 'post',
    data,
    timeout: 120000
  })
}

// ========== 批量评分 ==========
export function createBatchJudge(data) {
  return request({
    url: '/llm-judge/batches/',
    method: 'post',
    data,
    timeout: 60000
  })
}

export function getBatchList(params = {}) {
  return request({
    url: '/llm-judge/batches/',
    method: 'get',
    params
  })
}

export function getBatchDetail(id) {
  return request({
    url: `/llm-judge/batches/${id}/`,
    method: 'get'
  })
}

export function getBatchProgress(id) {
  return request({
    url: `/llm-judge/batches/${id}/progress/`,
    method: 'get'
  })
}

export function getBatchRecords(id) {
  return request({
    url: `/llm-judge/batches/${id}/records/`,
    method: 'get'
  })
}

export function pauseBatch(id) {
  return request({
    url: `/llm-judge/batches/${id}/pause/`,
    method: 'post'
  })
}

export function resumeBatch(id) {
  return request({
    url: `/llm-judge/batches/${id}/resume/`,
    method: 'post'
  })
}

// ========== 评分记录 ==========
export function getRecordList(params = {}) {
  return request({
    url: '/llm-judge/records/',
    method: 'get',
    params
  })
}

export function getRecordDetail(id) {
  return request({
    url: `/llm-judge/records/${id}/`,
    method: 'get'
  })
}

// ========== Rubric 评分标准 ==========
export function getRubricList(params = {}) {
  return request({
    url: '/llm-judge/rubrics/',
    method: 'get',
    params
  })
}

export function getRubricDetail(id) {
  return request({
    url: `/llm-judge/rubrics/${id}/`,
    method: 'get'
  })
}

export function createRubric(data) {
  return request({
    url: '/llm-judge/rubrics/',
    method: 'post',
    data
  })
}

export function updateRubric(id, data) {
  return request({
    url: `/llm-judge/rubrics/${id}/`,
    method: 'patch',
    data
  })
}

export function deleteRubric(id) {
  return request({
    url: `/llm-judge/rubrics/${id}/`,
    method: 'delete'
  })
}

export function setDefaultRubric(id) {
  return request({
    url: `/llm-judge/rubrics/${id}/set-default/`,
    method: 'post'
  })
}

export function getRubricPresets() {
  return request({
    url: '/llm-judge/rubrics/presets/',
    method: 'get'
  })
}

// ========== 知识库维护 ==========
export function getKBList(params = {}) {
  return request({
    url: '/llm-judge/kbs/',
    method: 'get',
    params
  })
}

export function getKBDetail(id) {
  return request({
    url: `/llm-judge/kbs/${id}/`,
    method: 'get'
  })
}

export function createKB(data) {
  return request({
    url: '/llm-judge/kbs/',
    method: 'post',
    data
  })
}

export function updateKB(id, data) {
  return request({
    url: `/llm-judge/kbs/${id}/`,
    method: 'patch',
    data
  })
}

export function deleteKB(id) {
  return request({
    url: `/llm-judge/kbs/${id}/`,
    method: 'delete'
  })
}

export function setKBDefault(id) {
  return request({
    url: `/llm-judge/kbs/${id}/set-default/`,
    method: 'post'
  })
}

export function exportKB(id) {
  return request({
    url: `/llm-judge/kbs/${id}/export/`,
    method: 'post'
  })
}

// 自然语言文本 → 结构化 KB（预览）
export function parseKBText(data) {
  return request({
    url: '/llm-judge/kbs/parse-text/',
    method: 'post',
    data
  })
}

// 确认导入结构化 KB 到 DB
export function importKB(data) {
  return request({
    url: '/llm-judge/kbs/import/',
    method: 'post',
    data
  })
}

// 主体（公司）CRUD
export function getKBCompanyList(params = {}) {
  return request({
    url: '/llm-judge/kb/companies/',
    method: 'get',
    params
  })
}
export function createKBCompany(data) {
  return request({ url: '/llm-judge/kb/companies/', method: 'post', data })
}
export function updateKBCompany(id, data) {
  return request({ url: `/llm-judge/kb/companies/${id}/`, method: 'patch', data })
}
export function deleteKBCompany(id) {
  return request({ url: `/llm-judge/kb/companies/${id}/`, method: 'delete' })
}

// 报告期 CRUD
export function getKBPeriodList(params = {}) {
  return request({ url: '/llm-judge/kb/periods/', method: 'get', params })
}
export function createKBPeriod(data) {
  return request({ url: '/llm-judge/kb/periods/', method: 'post', data })
}
export function updateKBPeriod(id, data) {
  return request({ url: `/llm-judge/kb/periods/${id}/`, method: 'patch', data })
}
export function deleteKBPeriod(id) {
  return request({ url: `/llm-judge/kb/periods/${id}/`, method: 'delete' })
}

// 指标 CRUD
export function getKBMetricList(params = {}) {
  return request({ url: '/llm-judge/kb/metrics/', method: 'get', params })
}
export function createKBMetric(data) {
  return request({ url: '/llm-judge/kb/metrics/', method: 'post', data })
}
export function updateKBMetric(id, data) {
  return request({ url: `/llm-judge/kb/metrics/${id}/`, method: 'patch', data })
}
export function deleteKBMetric(id) {
  return request({ url: `/llm-judge/kb/metrics/${id}/`, method: 'delete' })
}

// 指标数值 CRUD
export function getKBValueList(params = {}) {
  return request({ url: '/llm-judge/kb/values/', method: 'get', params })
}
export function createKBValue(data) {
  return request({ url: '/llm-judge/kb/values/', method: 'post', data })
}
export function updateKBValue(id, data) {
  return request({ url: `/llm-judge/kb/values/${id}/`, method: 'patch', data })
}
export function deleteKBValue(id) {
  return request({ url: `/llm-judge/kb/values/${id}/`, method: 'delete' })
}

// ========== 批量评分：文件上传 + 模板下载 ==========
export function uploadBatchFile(file, onProgress) {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: '/llm-judge/batch/upload/',
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000,
    onUploadProgress: onProgress
  })
}

export async function downloadBatchTemplate(format = 'csv') {
  // 通过 axios 请求 blob，自动携带认证头，再用 createObjectURL 触发下载
  const res = await request({
    url: '/llm-judge/batch/template/',
    method: 'get',
    params: { tpl: format },
    responseType: 'blob',
    timeout: 30000,
  })
  // 从 Content-Disposition 提取文件名
  const cd = res.headers['content-disposition'] || ''
  let filename = `judge_batch_template.${format}`
  const m = cd.match(/filename\*?=UTF-8''(.+?)(?:;|$)/i) || cd.match(/filename="?(.+?)"?(?:;|$)/i)
  if (m) filename = decodeURIComponent(m[1])
  const url = window.URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  window.URL.revokeObjectURL(url)
}
