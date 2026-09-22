export function nullableLineOptions(data, preserveMissing) {
  if (!preserveMissing) return { data, showSymbol: false }
  const valid = value => typeof value === 'number' && Number.isFinite(value)
  return {
    data, step: false, connectNulls: false, showSymbol: true, showAllSymbol: true,
    symbolSize: (_, { dataIndex: i }) => valid(data[i]) && !valid(data[i - 1]) && !valid(data[i + 1]) ? 6 : 0
  }
}
