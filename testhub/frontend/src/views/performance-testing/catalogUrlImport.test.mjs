import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as catalog from './apiCatalogForm.mjs'
import zh from '../../locales/lang/zh-cn/performance-testing.js'

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const settle = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const source = url => ({ url, document_url: url.replace('/swagger/#/', '/swagger/doc.json'), checked_at: '2026-09-17T05:00:00Z' })
const preview = (url, overrides = {}) => ({ data: { operation_count: 196, content_hash: 'new', current_version: { version: 2, content_hash: 'old' },
  diff: { added: ['GET /new'], removed: [], changed: ['GET /items'] }, source: source(url), preview_token: 'signed-preview', ...overrides } })
const listing = saved => ({ data: { results: [], count: 0, next: null, version: { version: 2 }, tags: [], source: saved || {} } })
const text = node => node ? [node.text || '', ...(node.children || []).map(text)].join('') : ''
const nodes = node => node ? [node, ...(node.children || []).flatMap(nodes)] : []
const t = (key, values = {}) => {
  const value = key.split('.').slice(1).reduce((current, part) => current?.[part], zh) || key
  return value.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? `{${name}}`)
}
const renderer = Vue.createRenderer({
  createElement: tag => ({ tag, props: {}, children: [] }), createText: value => ({ text: value }), createComment: () => ({ text: '' }),
  insert(child, parent, anchor) { if (child.parent) { const old = child.parent.children.indexOf(child); if (old >= 0) child.parent.children.splice(old, 1) } child.parent = parent; const index = parent.children.indexOf(anchor); parent.children.splice(index < 0 ? parent.children.length : index, 0, child) },
  remove(child) { const index = child.parent?.children.indexOf(child) ?? -1; if (index >= 0) child.parent.children.splice(index, 1); child.parent = null },
  setText(node, value) { node.text = value }, setElementText(node, value) { node.children = [{ text: value }] },
  patchProp(node, key, _, value) { node.props[key] = value }, parentNode: node => node.parent,
  nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1]
})

async function harness(overrides = {}, initial = {}) {
  const calls = [], messages = [], imported = []
  const api = {
    getPerfApiCatalog: async () => listing(),
    previewPerfApiCatalog: async (id, data) => { calls.push(['preview', id, Object.fromEntries(data)]); return preview(data.get('source_url')) },
    importPerfApiCatalog: async (id, data) => { calls.push(['import', id, Object.fromEntries(data)]); return { data: { changed: true, source: source(data.get('source_url')) } } },
    ...overrides
  }
  const dependencies = { vue: Vue, '../apiCatalogForm.mjs': catalog, '@/api/performance-testing': api,
    'vue-i18n': { useI18n: () => ({ t }) }, 'element-plus': { ElMessage: { success: message => messages.push(message) } },
    './ApiPoolPreparation.vue': { default: { render: () => null } },
    './RequestReadinessPanel.vue': { default: { render: () => null } } }
  const descriptor = parse(await readFile(new URL('./components/ProjectApiCatalog.vue', import.meta.url), 'utf8')).descriptor
  const script = compileScript(descriptor, { id: 'catalog-url-test' })
  const template = compileTemplate({ source: descriptor.template.content, id: 'catalog-url-test', compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  const modules = []
  const imports = code => code.replace(/import\s+([\s\S]*?)\s+from\s+(['"])([^'"]+)\2\s*;?/g, (_, binding, quote, name) => {
    const index = modules.push(dependencies[name] || {}) - 1
    return binding.trim().startsWith('{') ? `const ${binding.replace(/\bas\b/g, ':')} = __modules[${index}];\n` : `const ${binding} = __modules[${index}].default;\n`
  })
  const component = new Function('__modules', imports(script.content).replace('export default', 'return'))(modules)
  const render = new Function('__modules', imports(template.code).replace('export function render', 'return function render'))(modules)
  const wrap = (tag, extra = () => ({})) => ({ inheritAttrs: false, setup: (_, context) => () => Vue.h(tag, { ...context.attrs, ...extra(context.attrs) }, context.slots.default?.()) })
  const stubs = {
    ElButton: wrap('button', attrs => ({ disabled: attrs.disabled || attrs.loading })),
    ElInput: { inheritAttrs: false, setup: (_, { attrs }) => () => Vue.h('input', { ...attrs, value: attrs.modelValue, onInput: event => attrs['onUpdate:modelValue']?.(event.target.value) }) },
    ElRadioGroup: { setup: (_, context) => { Vue.provide('radio', context); return () => Vue.h('div', context.slots.default?.()) } },
    ElRadioButton: { setup: (_, { attrs, slots }) => { const group = Vue.inject('radio'); return () => Vue.h('button', { disabled: group.attrs.disabled,
      onClick: () => group.attrs['onUpdate:modelValue'](attrs.label ?? attrs.value) }, slots.default?.()) } },
    ElAlert: { setup: (_, { attrs, slots }) => () => Vue.h('div', { role: 'alert' }, [attrs.title, slots.default?.()]) },
    ElCollapseItem: { setup: (_, { attrs, slots }) => () => Vue.h('section', [attrs.title, slots.default?.()]) }
  }
  for (const name of ['ElCollapse', 'ElSelect', 'ElOption', 'ElCheckbox', 'ElTag', 'ElEmpty', 'ElPagination', 'ElDrawer']) stubs[name] = wrap('div')
  const props = Vue.reactive({ projectId: 7, selectable: false, ...initial })
  const rendered = { ...component, render }
  const app = renderer.createApp({ render: () => Vue.h(rendered, { ...props, onImported: value => imported.push(value) }) })
  for (const [name, stub] of Object.entries(stubs)) app.component(name, stub)
  app.directive('loading', {})
  const root = { children: [] }; app.mount(root); await settle()
  return { root, props, calls, messages, imported,
    button(label) { const result = nodes(root).find(node => node.tag === 'button' && text(node).trim() === label); assert.ok(result, `Button exists: ${label}`); return result },
    async click(label) { const button = this.button(label); if (button.props.disabled) return false; await button.props.onClick(); await settle(); return true },
    async input(value) { const input = nodes(root).find(node => node.tag === 'input' && node.props['aria-label'] === t('performanceTesting.catalog.sourceUrl')); assert.ok(input, 'URL input is rendered'); input.props.onInput({ target: { value } }); await settle() },
    url() { return nodes(root).find(node => node.tag === 'input' && node.props['aria-label'] === t('performanceTesting.catalog.sourceUrl'))?.props.value },
    async project(id) { props.projectId = id; await settle() },
    unmount() { app.unmount() }
  }
}

const url = 'http://192.168.56.10:30300/swagger/#/'

test('URL mode previews the Swagger page before importing its bound token and current version', async () => {
  const h = await harness()
  try {
    assert.equal(h.url(), '')
    await h.input(url); await h.click('获取并预览')
    assert.deepEqual(h.calls, [['preview', 7, { source_url: url }]])
    assert.match(text(h.root), /swagger\/doc\.json/)
    assert.match(text(h.root), /新增 \(1\)/)
    assert.match(text(h.root), /变化 \(1\)/)
    await h.click('确认导入此契约')
    assert.deepEqual(h.calls[1], ['import', 7, { source_url: url, preview_token: 'signed-preview', expected_version: '2' }])
    assert.equal(h.imported.length, 1)
  } finally { h.unmount() }
})

test('saved source hydrates after reload and checks updates without inventing a new version', async () => {
  const h = await harness({ getPerfApiCatalog: async () => listing(source(url)), previewPerfApiCatalog: async () => preview(url,
    { content_hash: 'old', diff: { added: [], removed: [], changed: [] } }), importPerfApiCatalog: async () => ({ data: { changed: false, source: source(url) } }) })
  try {
    assert.equal(h.url(), url); await h.click('检查更新')
    assert.match(text(h.root), /契约内容未变化/)
    assert.match(text(h.root), /检查时间/)
    await h.click('确认导入此契约')
    assert.deepEqual(h.messages, ['契约相同，接口库保持当前版本'])
    assert.equal(h.imported[0].changed, false)
  } finally { h.unmount() }
})

test('confirmed canonical source keeps refresh and document metadata for the entered Swagger fragment URL', async () => {
  for (const entered of [url, 'HTTP://EXAMPLE.INVALID:80/swagger/#/']) {
    const canonical = new URL(entered); canonical.hash = ''
    const normalized = { url: canonical.href, document_url: `${canonical.href}doc.json`, checked_at: '2026-09-17T05:00:00Z' }
    let saved = {}
    const h = await harness({ getPerfApiCatalog: async () => listing(saved),
      previewPerfApiCatalog: async () => preview(entered, { source: normalized }),
      importPerfApiCatalog: async () => { saved = normalized; return { data: { changed: true, source: normalized } } } })
    try {
      await h.input(entered); await h.click('获取并预览'); await h.click('确认导入此契约')
      assert.equal(h.url(), entered, 'confirmation does not mutate the draft or its request gate')
      assert.equal(h.button('检查更新').props.disabled, false)
      assert.ok(text(h.root).includes(normalized.document_url))
      await h.click('检查更新'); assert.ok(h.button('确认导入此契约'))
    } finally { h.unmount() }
  }
})

test('initial source response cannot replace an already edited URL', async () => {
  const pending = deferred()
  const h = await harness({ getPerfApiCatalog: () => pending.promise })
  try { await h.input('https://example.invalid/openapi.yaml'); pending.resolve(listing(source(url))); await settle(); assert.equal(h.url(), 'https://example.invalid/openapi.yaml') }
  finally { h.unmount() }
})

for (const transition of ['input', 'mode', 'project', 'unmount']) test(`late URL preview is discarded after ${transition}`, async () => {
  const pending = deferred()
  const h = await harness({ previewPerfApiCatalog: () => pending.promise })
  try {
    await h.input(url); const task = h.click('获取并预览'); await settle()
    if (transition === 'input') { await h.input('https://example.invalid/next.yaml'); await h.input(url) }
    if (transition === 'mode') { await h.click('上传文件'); await h.click('Swagger 链接') }
    if (transition === 'project') await h.project(8)
    if (transition === 'unmount') h.unmount()
    pending.resolve(preview(url)); await task
    assert.equal(h.imported.length, 0)
    assert.equal(nodes(h.root).some(node => node.tag === 'button' && text(node).includes('确认导入')), false)
  } finally { if (transition !== 'unmount') h.unmount() }
})

test('409 clears URL confirmation and demands a fresh preview token', async () => {
  let imports = 0
  const h = await harness({ importPerfApiCatalog: async () => { imports++; throw { response: { status: 409 } } } })
  try {
    await h.input(url); await h.click('获取并预览'); await h.click('确认导入此契约')
    assert.equal(imports, 1); assert.match(text(h.root), /重新预览/)
    assert.equal(nodes(h.root).some(node => node.tag === 'button' && text(node).includes('确认导入')), false)
    await h.click('获取并预览'); assert.ok(h.button('确认导入此契约'))
  } finally { h.unmount() }
})

test('late import success and failure cannot modify another project or show an old toast', async () => {
  for (const fail of [false, true]) {
    const pending = deferred(); let called
    const h = await harness({ importPerfApiCatalog: (project, data) => { called = [project, Object.fromEntries(data)]; return pending.promise } })
    try {
      await h.input(url); await h.click('获取并预览'); const task = h.click('确认导入此契约'); await settle()
      await h.project(8)
      if (fail) pending.reject({ response: { status: 409 } }); else pending.resolve({ data: { changed: true, source: source(url) } })
      await task
      assert.equal(called[0], 7); assert.equal(called[1].source_url, url)
      assert.equal(h.url(), ''); assert.deepEqual(h.messages, []); assert.deepEqual(h.imported, [])
      assert.doesNotMatch(text(h.root), /重新预览/)
    } finally { h.unmount() }
  }
})

test('file mode preserves preview/import FormData and never sends URL fields', async () => {
  const file = new File(['{}'], 'openapi.json'), calls = []
  const h = await harness({ previewPerfApiCatalog: async (_, data) => { calls.push(Object.fromEntries(data)); return preview(url) },
    importPerfApiCatalog: async (_, data) => { calls.push(Object.fromEntries(data)); return { data: { changed: true, source: {} } } } }, { initialFile: file })
  try {
    assert.equal(h.url(), undefined)
    await h.click('确认导入此契约')
    assert.equal(calls.length, 2); assert.equal(calls[0].file.name, 'openapi.json')
    assert.deepEqual(Object.keys(calls[1]).sort(), ['expected_version', 'file'])
    await h.click('Swagger 链接'); assert.equal(h.url(), '')
  } finally { h.unmount() }
})

test('invalid URL stays local with actionable feedback', async () => {
  const h = await harness()
  try { await h.input('javascript:alert(1)'); await h.click('获取并预览'); assert.equal(h.calls.length, 0); assert.match(text(h.root), /HTTP.*HTTPS/) }
  finally { h.unmount() }
})

test('late preview error cannot erase the newer input preview', async () => {
  const pending = deferred(); let requests = 0
  const next = 'https://example.invalid/openapi.yaml'
  const h = await harness({ previewPerfApiCatalog: (_, data) => ++requests === 1 ? pending.promise : Promise.resolve(preview(data.get('source_url'))) })
  try {
    await h.input(url); const old = h.click('获取并预览'); await settle()
    await h.input(next); await h.click('获取并预览')
    pending.reject({ response: { status: 403 } }); await old
    assert.ok(h.button('确认导入此契约')); assert.match(text(h.root), /example.invalid\/openapi.yaml/)
    assert.doesNotMatch(text(h.root), /没有此项目/)
  } finally { h.unmount() }
})

test('URL confirmation requires a returned preview token', async () => {
  const h = await harness({ previewPerfApiCatalog: async () => preview(url, { preview_token: '' }) })
  try {
    await h.input(url); await h.click('获取并预览')
    assert.match(text(h.root), /预览凭据缺失/)
    assert.equal(nodes(h.root).some(node => node.tag === 'button' && text(node).includes('确认导入')), false)
    assert.equal(h.calls.length, 0)
  } finally { h.unmount() }
})

test('confirmed file import clears an existing saved URL without sending it with the file', async () => {
  let saved = source(url), submitted
  const h = await harness({ getPerfApiCatalog: async () => listing(saved), previewPerfApiCatalog: async () => preview(url),
    importPerfApiCatalog: async (_, data) => { submitted = Object.fromEntries(data); saved = {}; return { data: { changed: false, source: {} } } } })
  try {
    assert.equal(h.url(), url); await h.click('上传文件')
    const input = nodes(h.root).find(node => node.tag === 'input' && node.props.type === 'file')
    input.props.onChange({ target: { files: [new File(['{}'], 'openapi.yaml')] } }); await settle()
    await h.click('预览契约变化'); await h.click('确认导入此契约')
    assert.deepEqual(Object.keys(submitted).sort(), ['expected_version', 'file'])
    await h.click('Swagger 链接'); assert.equal(h.url(), '')
    assert.doesNotMatch(text(h.root), /swagger\/doc.json/)
  } finally { h.unmount() }
})
