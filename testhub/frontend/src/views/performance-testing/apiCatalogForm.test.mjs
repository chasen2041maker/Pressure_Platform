import test from 'node:test'
import assert from 'node:assert/strict'
import { allCatalogPages, mergeSelected, requestGate, typedRows, typedObject, sameJsonValue } from './apiCatalogForm.mjs'

test('all filtered pages are fetched in order and selection remains unique across pages', async () => {
  const calls = []
  const result = await allCatalogPages(async params => {
    calls.push(params)
    return { data: { results: Array.from({ length: params.page === 1 ? 50 : 7 }, (_, i) => ({ id: (params.page - 1) * 50 + i + 1 })), count: 57, next: params.page === 1 ? 'next' : null, version: { version: 1 } } }
  }, { tag: 'read', ready: 'true' })
  assert.deepEqual(calls.map(call => call.page), [1, 2])
  assert.ok(calls.every(call => call.ready === 'true' && call.tag === 'read'))
  assert.deepEqual(mergeSelected([57, 999], result.results), [57, 999, ...Array.from({ length: 56 }, (_, i) => i + 1)])
})

test('mixed catalog versions and failed later pages never return a partial all-selection', async () => {
  await assert.rejects(allCatalogPages(async ({ page }) => ({ data: { results: [{ id: page }], count: 2, next: page === 1 ? 'next' : null, version: { version: page } } })), error => error.response.status === 409)
  await assert.rejects(allCatalogPages(async ({ page }) => { if (page === 2) throw new Error('offline'); return { data: { results: [{ id: 1 }], count: 2, next: 'next' } } }), /offline/)
})

test('invalidated all-selection does not publish old project results', async () => {
  const gate = requestGate(), ticket = gate.begin()
  const result = await allCatalogPages(async () => { gate.invalidate(); return { data: { results: [{ id: 1 }], next: null } } }, {}, () => gate.current(ticket))
  assert.equal(result, null)
})

test('unmodified defaults preserve zero, false, arrays and objects after the shared KV editor drops extra row metadata', () => {
  const original = { zero: 0, no: false, list: [1, false], object: { x: 0 } }
  const rows = typedRows(original).map(({ key, value, enabled }) => ({ key, value, enabled }))
  assert.deepEqual(typedObject(rows, original), original)
  rows[0].value = '77'
  assert.deepEqual(typedObject(rows, original), { ...original, zero: '77' })
  rows[1].enabled = false
  assert.equal(Object.hasOwn(typedObject(rows, original), 'no'), false)
})


test('retry1: JSON comparison ignores object key order but retains nested types and array order', () => {
  assert.equal(sameJsonValue({ id: 49, params: { zero: 0, flag: false }, items: [0, false] }, { items: [0, false], params: { flag: false, zero: 0 }, id: 49 }), true)
  for (const value of [{ zero: '0', flag: false }, { zero: 0, flag: 'false' }, { zero: 0 }]) assert.equal(sameJsonValue({ zero: 0, flag: false }, value), false)
  assert.equal(sameJsonValue([0, false], [false, 0]), false)
})
