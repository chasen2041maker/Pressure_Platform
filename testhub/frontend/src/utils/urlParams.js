/**
 * URL 查询参数解析工具
 * 用于从请求地址中拾取查询参数（?key=value&...），供接口管理、性能测试等模块复用
 */

/**
 * 解析 URL 中的查询串，返回基础地址、fragment 与参数列表
 * @param {string} url 完整请求地址（可含 ? 查询串与 # fragment）
 * @returns {{ baseUrl: string, fragment: string, pairs: Array<{key: string, value: string}> } | null}
 *          无查询串或解析失败时返回 null；参数值已做 URL 解码（如 %20 -> 空格）
 */
export function parseUrlQueryString(url) {
  if (!url || typeof url !== 'string') return null
  const qIndex = url.indexOf('?')
  if (qIndex < 0) return null

  // 分离查询串与可能存在的 fragment（#xxx）
  let queryString = url.slice(qIndex + 1)
  let fragment = ''
  const hashIndex = queryString.indexOf('#')
  if (hashIndex >= 0) {
    fragment = queryString.slice(hashIndex)
    queryString = queryString.slice(0, hashIndex)
  }
  if (!queryString) return null

  const pairs = []
  try {
    const search = new URLSearchParams(queryString)
    const keys = [...new Set([...search.keys()])]
    keys.forEach((key) => {
      if (!key) return
      search.getAll(key).forEach((value) => pairs.push({ key, value }))
    })
  } catch (e) {
    return null
  }
  if (!pairs.length) return null

  return {
    baseUrl: url.slice(0, qIndex),
    fragment,
    pairs
  }
}

/**
 * 将解析出的参数合并进现有的 key-value 行数组（KeyValueEditor 数据格式）
 * 同名参数更新值，新参数追加，用户手动填写的其他行原样保留
 * @param {Array} rows 现有参数行数组，行格式 { key, value, description?, enabled?, type? }
 * @param {Array<{key: string, value: string}>} pairs 解析出的参数
 * @returns {Array} 合并后的参数行数组
 */
export function mergeQueryPairsIntoRows(rows, pairs) {
  const merged = Array.isArray(rows)
    ? rows.filter(row => row && (row.key || row.value || row.description))
    : []
  ;(pairs || []).forEach(({ key, value }) => {
    const existed = merged.find(row => row.key === key)
    if (existed) {
      existed.value = value
      existed.enabled = existed.enabled !== false
    } else {
      merged.push({ key, value, description: '', enabled: true, type: 'text' })
    }
  })
  return merged
}
