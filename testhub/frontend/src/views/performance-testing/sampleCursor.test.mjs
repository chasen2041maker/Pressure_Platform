import test from 'node:test'
import assert from 'node:assert/strict'
import { createSampleCursor, drainSamplePages } from './sampleCursor.mjs'

test('same elapsed second preserves different IDs and duplicate responses are ignored', () => {
  const cursor = createSampleCursor()
  assert.equal(cursor.accept({ id: 1, ts_offset: 1 }), true)
  assert.equal(cursor.accept({ id: 2, ts_offset: 1 }), true)
  assert.equal(cursor.accept({ id: 1, ts_offset: 1 }), false)
})
test('all pages including final forced row drain before terminal status', async () => {
  const cursor = createSampleCursor(), received = [], requested = []
  const final = await drainSamplePages(async params => {
    requested.push(params.after_id)
    return params.after_id === 0
      ? { status: 'COMPLETED', samples: [{ id: 1, ts_offset: 1 }], next_after_id: 1, has_more: true }
      : { status: 'COMPLETED', samples: [{ id: 2, ts_offset: 1, engine_finished: true }], next_after_id: 2, has_more: false }
  }, cursor, row => received.push(row))
  assert.deepEqual(requested, [0, 1]); assert.deepEqual(received.map(row => row.id), [1, 2])
  assert.equal(final.status, 'COMPLETED'); assert.equal(cursor.afterId, 2)
  await drainSamplePages(async () => ({ samples: received, next_after_id: 2 }), cursor, () => assert.fail('duplicate'))
})
test('retry resumes from last successful page and reload can replay all rows', async () => {
  const cursor = createSampleCursor(), seen = []
  await assert.rejects(drainSamplePages(async params => {
    if (params.after_id) throw new Error('offline')
    return { samples: [{ id: 9 }], next_after_id: 9, has_more: true }
  }, cursor, row => seen.push(row.id)))
  assert.equal(cursor.afterId, 9)
  await drainSamplePages(async params => { assert.equal(params.after_id, 9); return { samples: [{ id: 10 }], next_after_id: 10 } }, cursor, row => seen.push(row.id))
  assert.deepEqual(seen, [9, 10]); assert.equal(createSampleCursor().afterId, 0)
})
test('nonadvancing pages fail rather than silently skip backlog or loop', async () => {
  await assert.rejects(drainSamplePages(async () => ({ samples: [], has_more: true, next_after_id: 0 }), createSampleCursor(), () => {}))
})

import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as presentation from './slaReport.mjs'
import * as interfaces from './k6InterfaceStats.mjs'

test('actual monitor uses HTTP row cursor, preserves active cards on empty polls, and drains terminal page', async () => {
  const descriptor = parse(await readFile(new URL('./ExecutionMonitor.vue', import.meta.url), 'utf8')).descriptor
  const requests = [], pushed = []
  let phase = 0
  const rows = [
    { id: 1, sample_seq: 1, ts_offset: 1, elapsed_seconds: 1.05, business_total: 3, total_requests: 3, http_total: 4, failed_requests: 1, tps: 3,
      throughput: { latest_bucket_start_ms: 1000, verified: true, provisional: true }, steps: [{ step_id: 11, total: 2, failed: 0 }, { step_id: 12, total: 1, failed: 1 }] },
    { id: 2, sample_seq: 2, ts_offset: 1, elapsed_seconds: 1.4, business_total: 4, total_requests: 4, http_total: 5, failed_requests: 1, tps: 1,
      throughput: { latest_bucket_start_ms: 2000, verified: true, provisional: false }, engine_finished: true, steps: [{ step_id: 11, total: 2, failed: 0 }, { step_id: 12, total: 2, failed: 1 }] }
  ]
  const api = {
    async getPerfRealtime(id, params) {
      requests.push(params.after_id)
      if (phase === 0) return { data: { status: 'RUNNING', summary: {}, samples: [rows[0]], next_after_id: 1 } }
      if (phase === 1) return { data: { status: 'RUNNING', summary: {}, samples: [], next_after_id: 1 } }
      return { data: { status: 'COMPLETED', summary: { business_total: 4, total_requests: 4, http_total: 5 },
        samples: params.after_id === 1 ? [rows[1]] : [], next_after_id: 2 } }
    },
    async getPerfExecution() { return { data: { status: 'COMPLETED', summary: { business_total: 4, http_total: 5 } } } },
    async getPerfRunLog() { return { data: {} } },
    async getPerfRequestStats() { return { data: [] } }
  }
  const deps = { ...presentation, ...interfaces, ...api, createSampleCursor, drainSamplePages,
    useRoute: () => ({ params: { id: 1 } }), useRouter: () => ({}), useI18n: () => ({ t: key => key }),
    ElMessage: { success() {}, warning() {} } }
  const source = compileScript(descriptor, { id: 'monitor-test' }).content
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g, (_, names) => `const {${names.replace(/\bas\b/g, ':')}} = Vue`)
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"][^'"]+['"]/g, (_, names) => `const {${names}} = deps`)
    .replace(/import\s+(\w+)\s+from\s+['"][^'"]+['"]/g, (_, name) => `const ${name} = {}`)
    .replace('export default', 'return')
  const component = new Function('Vue', 'deps', source)({ ...Vue, onMounted() {}, onBeforeUnmount() {} }, deps)
  const state = component.setup({}, { expose() {} })
  state.execution.status = 'RUNNING'; state.execution.load_snapshot = { _engine: 'K6' }
  state.execution.steps_snapshot = [{ id: 11, name: 'same' }, { id: 12, name: 'same' }]
  state.chartRef.value = { push: row => pushed.push(row.id) }
  await state.pollOnce()
  assert.equal(state.displayedMetrics.value.http_total, 4)
  assert.equal(state.displayedMetrics.value.tps, 3)
  assert.deepEqual(state.k6InterfaceRows.value.map(row => [row.stepId, row.failed]), [[11, 0], [12, 1]])
  phase = 1; await state.pollOnce()
  assert.equal(state.latest.value.tps, 3); assert.equal(state.latest.value.throughput.latest_bucket_start_ms, 1000)
  phase = 2; await state.pollOnce()
  assert.deepEqual(requests, [0, 1, 1]); assert.deepEqual(pushed, [1, 2])
  assert.equal(state.latest.value.engine_finished, true)
  assert.equal(state.execution.status, 'COMPLETED')
  assert.equal(state.channel.value, 'closed')
})
