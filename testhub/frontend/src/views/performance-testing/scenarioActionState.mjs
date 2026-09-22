export function getScenarioActionState({ engine, proxy, capabilities, actionsReady, engineStatusLoading }) {
  const isK6 = engine === 'K6'
  const enabled = id => !engineStatusLoading
    && capabilities?.items?.some(item => item.id === id && item.enabled === true) === true
  const proxyBlocked = isK6 && Boolean(proxy) && !enabled('proxy')
  const ready = Boolean(actionsReady) && !engineStatusLoading && !proxyBlocked
  return {
    canDebug: ready && (!isK6 || enabled('debug')),
    canExecute: ready && (!isK6 || enabled('stop')),
    proxyBlocked
  }
}
