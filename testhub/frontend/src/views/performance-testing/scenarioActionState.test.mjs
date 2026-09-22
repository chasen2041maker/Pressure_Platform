import test from 'node:test'
import assert from 'node:assert/strict'
import { getScenarioActionState as actionState } from './scenarioActionState.mjs'

function readyK6(proxy = '') {
  return {
    engine: 'K6', proxy, actionsReady: true, engineStatusLoading: false,
    capabilities: { items: [
      { id: 'debug', enabled: true }, { id: 'stop', enabled: true },
      { id: 'proxy', enabled: false }
    ] }
  }
}

test('a saved unavailable proxy blocks debug and execution without changing the saved value', async () => {
  for (const reason of ['docker_proxy_unsupported', 'not_validated']) {
    const input = readyK6('http://old-proxy.invalid:8080')
    input.capabilities.items[2].reason_code = reason
    const before = structuredClone(input)
    assert.deepEqual(await actionState(input), { canDebug: false, canExecute: false, proxyBlocked: true })
    assert.deepEqual(input, before)
  }
})

test('explicitly clearing a saved proxy restores available actions and whitespace remains blocked', async () => {
  const input = readyK6('   ')
  assert.equal((await actionState(input)).proxyBlocked, true)
  input.proxy = ''
  assert.deepEqual(await actionState(input), { canDebug: true, canExecute: true, proxyBlocked: false })
})

test('loading or missing capability information cannot open k6 execution', async () => {
  for (const override of [{ actionsReady: false }, { engineStatusLoading: true }, { capabilities: null }]) {
    const state = await actionState({ ...readyK6(), ...override })
    assert.equal(state.canDebug, false)
    assert.equal(state.canExecute, false)
  }
})

test('other engines keep their proxy behavior and a future enabled k6 proxy is respected', async () => {
  for (const engine of ['BUILTIN', 'LOCUST', 'JMETER']) {
    const state = await actionState({ ...readyK6('http://old-proxy.invalid:8080'), engine })
    assert.deepEqual(state, { canDebug: true, canExecute: true, proxyBlocked: false })
  }
  const enabled = readyK6('http://old-proxy.invalid:8080')
  enabled.capabilities.items[2].enabled = true
  assert.deepEqual(await actionState(enabled), { canDebug: true, canExecute: true, proxyBlocked: false })
})
