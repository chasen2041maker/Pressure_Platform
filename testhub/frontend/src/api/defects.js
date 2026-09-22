import request from '@/utils/api'

const baseUrl = '/defects/defects/'

export function getDefects(params) {
  return request({
    url: baseUrl,
    method: 'get',
    params
  })
}

export function getDefect(id) {
  return request({
    url: `${baseUrl}${id}/`,
    method: 'get'
  })
}

export function createDefect(data) {
  return request({
    url: baseUrl,
    method: 'post',
    data
  })
}

export function updateDefect(id, data) {
  return request({
    url: `${baseUrl}${id}/`,
    method: 'patch',
    data
  })
}

export function deleteDefect(id) {
  return request({
    url: `${baseUrl}${id}/`,
    method: 'delete'
  })
}

export function assignDefect(id, data) {
  return request({
    url: `${baseUrl}${id}/assign/`,
    method: 'post',
    data
  })
}

export function resolveDefect(id, data) {
  return request({
    url: `${baseUrl}${id}/resolve/`,
    method: 'post',
    data
  })
}

export function startProgressDefect(id, data) {
  return request({
    url: `${baseUrl}${id}/start-progress/`,
    method: 'post',
    data
  })
}

export function verifyDefect(id, data) {
  return request({
    url: `${baseUrl}${id}/verify/`,
    method: 'post',
    data
  })
}

export function rejectDefect(id, data) {
  return request({
    url: `${baseUrl}${id}/reject/`,
    method: 'post',
    data
  })
}

export function reopenDefect(id, data) {
  return request({
    url: `${baseUrl}${id}/reopen/`,
    method: 'post',
    data
  })
}

export function closeDefect(id, data) {
  return request({
    url: `${baseUrl}${id}/close/`,
    method: 'post',
    data
  })
}

export function addDefectComment(id, data) {
  return request({
    url: `${baseUrl}${id}/comments/`,
    method: 'post',
    data
  })
}

export function uploadDefectAttachment(id, formData) {
  return request({
    url: `${baseUrl}${id}/attachments/`,
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}

export function bulkActionDefects(data) {
  return request({
    url: `${baseUrl}bulk-action/`,
    method: 'post',
    data
  })
}

export function getDefectSummary(params) {
  return request({
    url: `${baseUrl}summary/`,
    method: 'get',
    params
  })
}

export function getDefectTrend(params) {
  return request({
    url: `${baseUrl}trend/`,
    method: 'get',
    params
  })
}

export function exportDefects(params) {
  return request({
    url: `${baseUrl}export/`,
    method: 'get',
    params,
    responseType: 'blob'
  })
}

export function exportDefectReport(params) {
  return request({
    url: `${baseUrl}export-report/`,
    method: 'get',
    params,
    responseType: 'blob'
  })
}
