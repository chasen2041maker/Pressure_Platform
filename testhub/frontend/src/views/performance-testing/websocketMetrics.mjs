const count = value => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null
const metric = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
export const hasWebsocketSteps = steps => Array.isArray(steps) && steps.some(step => step.enabled !== false && step.protocol === 'WEBSOCKET')

export function websocketConnectionCounts(websocket) {
  const connections = websocket?.connections
  return Object.fromEntries(['current', 'peak', 'unclosed'].map(key => [key, connections?.observed === true ? count(connections[key]) : null]))
}

export function websocketCommandRows(websocket, steps = []) {
  const stats = Array.isArray(websocket?.command_metrics) ? websocket.command_metrics : []
  const used = new Set()
  const rows = []
  for (const step of steps.filter(step => step.enabled !== false && step.protocol === 'WEBSOCKET')) {
    for (const command of step.websocket_commands || []) {
      if (!Number.isInteger(command.index) || command.index < 0) continue
      const matches = stats.map((stat, index) => ({ stat, index })).filter(({ stat }) => String(stat.step_id) === String(step.id) && stat.command_index === command.index)
      const match = matches.length === 1 ? matches[0] : null
      if (match) used.add(match.index)
      rows.push({ stepId: step.id, commandIndex: command.index, stepName: step.name, name: command.name, action: command.action,
        latencyKind: command.kind === 'event' ? 'event_wait' : 'command', stat: match?.stat || {} })
    }
  }
  stats.forEach((stat, index) => {
    if (!used.has(index)) rows.push({ stepId: stat.step_id, commandIndex: stat.command_index, stepName: null, name: null, action: null,
      latencyKind: ['event_wait', 'command'].includes(stat.latency_kind) ? stat.latency_kind : null, stat })
  })
  return rows.map(({ stat, ...row }, index) => ({ ...row, key: `${row.stepId}:${row.commandIndex}:${index}`,
    ...Object.fromEntries(['total', 'success', 'failed'].map(key => [key, count(stat[key])])),
    ...Object.fromEntries(['avg_rt', 'p95_rt', 'p99_rt', 'error_rate', 'tps'].map(key => [key, count(stat.total) > 0 ? metric(stat[key]) : null]))
  }))
}
