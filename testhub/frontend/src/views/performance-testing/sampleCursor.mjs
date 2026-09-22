/** Row IDs are transport identity; rounded elapsed seconds are display only. */
export function createSampleCursor() {
  let afterId = 0
  const seen = new Set()
  return {
    get afterId() { return afterId },
    accept(sample) {
      const id = sample?.id
      if (!Number.isSafeInteger(id) || id <= 0) return true
      if (seen.has(id)) return false
      seen.add(id)
      return true
    },
    advance(page) {
      const next = page?.next_after_id
      if (Number.isSafeInteger(next) && next >= afterId) afterId = next
    }
  }
}

export async function drainSamplePages(fetchPage, cursor, consume) {
  let page
  do {
    const previous = cursor.afterId
    page = await fetchPage({ after_id: previous })
    for (const sample of page.samples || []) if (cursor.accept(sample)) consume(sample)
    cursor.advance(page)
    if (page.has_more && cursor.afterId <= previous) throw new Error('采样分页游标未前进，请重试')
  } while (page.has_more)
  return page
}
