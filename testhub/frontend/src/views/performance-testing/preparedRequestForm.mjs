const clone = value => JSON.parse(JSON.stringify(value))
const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const pointer = value => value.replace(/~1/g, '/').replace(/~0/g, '~')
const safeKey = key => !['__proto__', 'prototype', 'constructor'].includes(key)

function pathMatch(operation, url) {
  const template = operation.request_path || operation.path || ''
  const names = [...template.matchAll(/\{([^{}]+)\}/g)].map(match => match[1])
  let pattern = escape(template)
  for (const name of names) pattern = pattern.replace(escape(`{${name}}`), '([^/?]*)')
  return { template, names, match: String(url || '').match(new RegExp(pattern + '(?=\\?|$)')) }
}

export function readField(request, operation, field) {
  const [location, ...parts] = field.split('/')
  const name = parts.join('/')
  if (location === 'query') return request.params?.[name]
  if (location === 'header') return Object.entries(request.headers || {}).find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1]
  if (location === 'path') {
    const { names, match } = pathMatch(operation, request.url)
    const value = match?.[names.indexOf(name) + 1]
    if (value == null || value === `{${name}}`) return ''
    try { return decodeURIComponent(value) } catch { return value }
  }
  if (location === 'body') {
    try {
      let value = JSON.parse(request.body || '{}')
      for (const part of parts) value = value?.[pointer(part)]
      return value
    } catch { return undefined }
  }
}

export function scalarFields(operation, request) {
  const fields = new Map()
  const add = (field, schema = {}, extra = {}) => {
    if (schema.type === 'file' || ['binary', 'byte'].includes(schema.format)) return
    if (schema.type === 'object' || schema.type === 'array' || schema.properties || field.includes('/*')) return
    const existing = fields.get(field) || {}
    fields.set(field, { field, type: schema.type || existing.type || 'string', ...existing, ...extra })
  }
  for (const param of operation.parameters || []) if (['path', 'query', 'header'].includes(param.in)) {
    add(`${param.in}/${param.name}`, param.schema || param, { required: param.in === 'path' || !!param.required, description: param.description || '' })
  }
  const bodySchema = Object.values(operation.request_body?.content || {})[0]?.schema
  const walk = (schema, field, required, depth = 0) => {
    if (!schema || depth > 20) return
    if (field === 'body' && (schema.oneOf || schema.anyOf)) {
      add(field, { type: 'json' }, { required, description: schema.description || '' })
      return
    }
    if (schema.properties) {
      for (const [key, child] of Object.entries(schema.properties)) walk(child, `${field}/${key.replace(/~/g, '~0').replace(/\//g, '~1')}`, required && (schema.required || []).includes(key), depth + 1)
    } else add(field, schema, { required, description: schema.description || '' })
  }
  walk(bodySchema, 'body', true)
  for (const item of operation.requirements || []) {
    if (!/^(path|query|header|body)\//.test(item.field) || item.kind === 'file' || (item.suggested !== null && typeof item.suggested === 'object')) continue
    add(item.field, {}, item)
  }
  for (const [location, values] of [['query', request.params], ['header', request.headers]]) {
    for (const [key, value] of Object.entries(values || {})) if (value === null || typeof value !== 'object') add(`${location}/${key}`, { type: typeof value === 'boolean' ? 'boolean' : typeof value === 'number' ? 'number' : 'string' })
  }
  return [...fields.values()]
}

export function writeField(request, operation, field, input) {
  const result = clone(request), [location, ...parts] = field.field.split('/'), name = parts.join('/')
  if (parts.some(part => !safeKey(pointer(part)))) throw new Error('invalid_field')
  let value = input
  if (typeof input === 'string' && input.trim() && !/\{\{|\$\{/.test(input) && input !== '******') {
    if (field.type === 'boolean' && ['true', 'false'].includes(input)) value = input === 'true'
    else if (['integer', 'number'].includes(field.type) && Number.isFinite(Number(input))) value = Number(input)
  }
  if (location === 'query') result.params = { ...(result.params || {}), [name]: value }
  if (location === 'header') {
    const key = Object.keys(result.headers || {}).find(key => key.toLowerCase() === name.toLowerCase()) || name
    result.headers = { ...(result.headers || {}), [key]: String(value ?? '') }
  }
  if (location === 'path') {
    const { template, names, match } = pathMatch(operation, result.url)
    if (!match || !names.includes(name)) throw new Error('path_shape_changed')
    const replacement = template.replace(/\{([^{}]+)\}/g, (_, key) => key === name
      ? /\{\{|\$\{/.test(String(value)) ? String(value) : encodeURIComponent(String(value ?? '')) : match[names.indexOf(key) + 1])
    result.url = result.url.slice(0, match.index) + replacement + result.url.slice(match.index + match[0].length)
  }
  if (location === 'body') {
    // 整体 JSON 保留输入草稿，保存时校验；不能再把正文编码成字符串。
    if (!parts.length && field.type === 'json') {
      result.body_type = 'JSON'; result.body = String(input ?? '')
      return result
    }
    let body = JSON.parse(result.body || '{}')
    if (!parts.length) body = value
    else {
      if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('invalid_body')
      let target = body
      parts.forEach((part, index) => {
        const key = pointer(part)
        if (index === parts.length - 1) target[key] = value
        else { if (target[key] == null) target[key] = {}; if (typeof target[key] !== 'object') throw new Error('invalid_body'); target = target[key] }
      })
    }
    result.body_type = request.body_type === 'FORM' ? 'FORM' : 'JSON'; result.body = JSON.stringify(body, null, 2)
  }
  return result
}

export function checkScalarTypes(request, operation) {
  for (const field of scalarFields(operation, request)) {
    if (field.type === 'json') {
      if (!operation.request_body?.required && request.body_type === 'NONE'
        && !request.body && !request.files?.length) continue
      try { JSON.parse(request.body) } catch { throw new Error(field.field) }
      continue
    }
    const value = readField(request, operation, field.field)
    if (value == null || value === '' || value === '******' || /\{\{|\$\{/.test(String(value))) continue
    if (field.field.startsWith('path/') || field.field.startsWith('header/')) continue
    if (field.type === 'integer' && !Number.isInteger(value)) throw new Error(field.field)
    if (field.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) throw new Error(field.field)
    if (field.type === 'boolean' && typeof value !== 'boolean') throw new Error(field.field)
  }
}
