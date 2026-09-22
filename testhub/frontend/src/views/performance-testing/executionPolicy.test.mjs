import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'

async function component(name) {
  const text = await readFile(new URL(`./components/${name}.vue`, import.meta.url), 'utf8').catch(() => '')
  assert.ok(text, `${name} must exist`)
  const { descriptor } = parse(text)
  assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: name }).errors, [])
  const code = compileScript(descriptor, { id: name }).content
    .replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"];?/g, 'const {$1} = Vue;')
    .replace('export default', 'return')
  return new Function('Vue', code)(Vue)
}
const renderer = Vue.createRenderer({ createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag }),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {}, parentNode() {}, nextSibling() {} })
function mount(comp, props) {
  let state
  const wrapped = { ...comp, setup(p, context) { state = comp.setup(p, context); return state }, render: () => null }
  const target = {}; renderer.render(Vue.h(wrapped, props), target)
  return { state, close: () => renderer.render(null, target) }
}

test('policy editor enables explicit defaults, retains saved scope, and can restore historical behavior', async () => {
  let result
  const h = mount(await component('ExecutionPolicyEditor'), { modelValue: {}, setupStep: false,
    'onUpdate:modelValue': value => { result = value } })
  try {
    h.state.toggle(true)
    assert.deepEqual(result, { group_id: 'special', vu_start: 1, vu_end: 1, max_runs_per_vu: 1, min_interval_ms: 0 })
    h.state.toggle(false); assert.deepEqual(result, {})
  } finally { h.close() }
  const saved = { group_id: 'reminders', vu_start: 51, vu_end: 100, max_runs_per_vu: 1, min_interval_ms: 60000 }
  const edit = mount(await component('ExecutionPolicyEditor'), { modelValue: saved, 'onUpdate:modelValue': value => { result = value } })
  try { edit.state.change('min_interval_ms', 500); assert.deepEqual(result, { ...saved, min_interval_ms: 500 }); assert.equal(saved.min_interval_ms, 60000) } finally { edit.close() }
})

test('policy results use only frozen ranges, show missing observations as unknown, and keep blocked separate', async () => {
  const h = mount(await component('ExecutionPolicyMetrics'), { metrics: null,
    steps: [{ execution_policy: { group_index: 1, vu_start: 51, vu_end: 100, max_runs_per_vu: 1, min_interval_ms: 0 } }] })
  try {
    assert.equal(h.state.rows.value[0].vu_start, 51)
    assert.equal(h.state.display(h.state.rows.value[0].participants), '未采集')
    for (const key of ['planned_participants', 'attempt_limit', 'executed_steps', 'uncovered_participants']) {
      assert.ok(h.state.fields.some(field => field.key === key), `report must display ${key}`)
      assert.equal(h.state.display(h.state.rows.value[0][key]), '未采集')
    }
  } finally { h.close() }
})

test('policy results retain the frozen-round attempt limit and actual counters without recomputing quota', async () => {
  const policy = { group_index: 1, vu_start: 1, vu_end: 4, max_runs_per_vu: 10, min_interval_ms: 0 }
  const h = mount(await component('ExecutionPolicyMetrics'), {
    steps: [{ execution_policy: policy }, { execution_policy: policy }],
    metrics: { groups: [{ ...policy, planned_participants: 4, attempt_limit: 8, participants: 3,
      started: 6, completed: 5, executed_steps: 11, uncovered_participants: 1 }] }
  })
  try {
    assert.equal(h.state.rows.value.length, 1)
    const row = h.state.rows.value[0]
    for (const [key, value] of Object.entries({ planned_participants: 4, attempt_limit: 8,
      participants: 3, executed_steps: 11, uncovered_participants: 1 })) {
      assert.ok(h.state.fields.some(field => field.key === key), `report must display ${key}`)
      assert.equal(row[key], value)
      assert.equal(h.state.display(row[key]), String(value))
    }
  } finally { h.close() }
})
