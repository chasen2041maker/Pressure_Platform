export const clone = value => JSON.parse(JSON.stringify(value))

export function sameJsonValue(left, right) {
  if (Object.is(left, right)) return true
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object'
    || Array.isArray(left) !== Array.isArray(right)) return false
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length
    && keys.every(key => Object.hasOwn(right, key) && sameJsonValue(left[key], right[key]))
}

export function requestGate() {
  let sequence = 0
  return { begin: () => ++sequence, current: ticket => ticket === sequence, invalidate: () => ++sequence }
}

export function catalogError(error, t) {
  const status = error?.response?.status
  if ([403, 404, 409].includes(status)) return t(`performanceTesting.catalog.error${status}`)
  const message = error?.response?.data?.error || error?.response?.data?.detail
  return typeof message === 'string' ? message : t('performanceTesting.catalog.errorNetwork')
}

// A complete selection is published only after every page from one version succeeds.
export async function allCatalogPages(fetchPage, params = {}, isCurrent = () => true) {
  const rows = []
  let page = 1
  let version
  let count
  while (page <= 100) {
    const { data } = await fetchPage({ ...params, page, page_size: 200 })
    if (!isCurrent()) return null
    const currentVersion = data.version?.version ?? null
    if (page === 1) { version = currentVersion; count = data.count }
    if (version !== currentVersion || count !== data.count) throw { response: { status: 409 } }
    rows.push(...data.results)
    if (!data.next) return { results: rows, version: data.version, count: data.count }
    if (!data.results.length || page >= 100) throw new Error('Invalid catalog pagination')
    page++
  }
  throw new Error('Invalid catalog pagination')
}

export function mergeSelected(current, rows) {
  const ids = [...new Set([...current, ...rows.map(row => row.id)])]
  if (ids.length > 2000) throw new Error('selection_limit')
  return ids
}

export function typedRows(value) {
  return Object.entries(value || {}).map(([key, original]) => ({ key, enabled: true,
    value: typeof original === 'object' ? JSON.stringify(original) : String(original),
    _original: clone(original), _display: typeof original === 'object' ? JSON.stringify(original) : String(original) }))
}

export function typedObject(rows, original = {}) {
  return Object.fromEntries((rows || []).filter(row => row.enabled !== false && row.key?.trim())
    .map(row => [row.key.trim(), Object.hasOwn(original, row.key) && row.value === (typeof original[row.key] === 'object' ? JSON.stringify(original[row.key]) : String(original[row.key]))
      ? clone(original[row.key]) : Object.hasOwn(row, '_original') && row.value === row._display ? clone(row._original) : row.value]))
}
export function catalogProtocolLabel(protocol) {
  const names = { HTTP: 'HTTP', WEBSOCKET: 'WebSocket', SSE: 'SSE' }
  return Object.hasOwn(names, protocol) ? names[protocol] : null
}
