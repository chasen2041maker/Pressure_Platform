const reserved = new Set(['vu_id', 'iteration', 'base_url', 'baseUrl', '__proto__', 'prototype', 'constructor'])
const validName = name => /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(name) && !reserved.has(name)

export function mappingIssue(rows, columns) {
  if (!rows.length) return 'mappingRequired'
  if (rows.some(row => !validName(row.name) || !columns.includes(row.column))) return 'mappingInvalid'
  if (new Set(rows.map(row => row.name)).size !== rows.length) return 'mappingNameDuplicate'
  if (new Set(rows.map(row => row.column)).size !== rows.length) return 'mappingColumnDuplicate'
  return ''
}

export function suggestedMapping(columns) {
  return columns.map(column => ({ name: validName(column) ? column : '', column }))
}

export function buildAccountImport(form, file) {
  const data = new FormData()
  data.append('project', String(form.project))
  data.append('name', form.name.trim())
  data.append('file', file)
  data.append('identity_column', form.identity_column)
  data.append('group_column', form.group_column || '')
  data.append('field_mapping', JSON.stringify(Object.fromEntries(form.mapping.map(row => [row.name, row.column]))))
  return data
}

const errorReasons = {
  duplicate_identity: 'duplicateIdentity', empty_row: 'emptyRow', irregular_row: 'irregularRow',
  repeated_header_row: 'repeatedHeader', duplicate_header: 'duplicateHeader', invalid_header: 'invalidHeader',
  invalid_csv_quote: 'csvQuote', unclosed_csv_quote: 'csvQuote', invalid_csv: 'invalidCsv',
  duplicate_key: 'duplicateKey', invalid_json: 'invalidJson', expected_nonempty_object_array: 'objectArray',
  invalid_number: 'invalidValue', invalid_value: 'invalidValue', empty_or_control_value: 'emptyValue',
  missing_or_extra_columns: 'missingColumns', invalid_utf8: 'utf8', no_accounts: 'noAccounts',
  empty_or_too_large: 'fileSize', too_large: 'fileSize', file_required_or_too_large: 'fileSize',
  file_required: 'fileRequired', format_must_be_csv_or_json: 'fileFormat',
  invalid_column_selection: 'columnSelection', missing_identity_column: 'identityRequired',
  missing_group_column: 'groupMissing', mapping_required: 'mappingRequired', invalid_mapping: 'mappingInvalid',
  duplicate_mapping: 'mappingColumnDuplicate', invalid_name: 'nameRequired', project_required: 'projectRequired'
}

export function accountImportErrors(error, columns, t) {
  const at = (key, params) => t(`performanceTesting.accountPool.${key}`, params)
  const errors = error?.response?.data?.errors
  if (Array.isArray(errors) && errors.length) return errors.map(item => {
    const row = Number(item.row)
    const index = Number(item.field)
    const field = Number.isInteger(index) && index > 0
      ? columns[index - 1] || at('columnNumber', { number: index })
      : at(['name', 'project', 'identity_column', 'group_column', 'field_mapping'].includes(item.field)
        ? `fields.${item.field}` : 'fileConfiguration')
    return at('locatedError', { location: row > 0 ? at('rowNumber', { number: row }) : at('fileConfiguration'),
      field, reason: at(errorReasons[item.code] || 'invalidData') })
  })
  const status = error?.response?.status
  return [at(status === 403 ? 'permissionDenied' : status === 404 ? 'notFound' : status === 409 ? 'referenced' : 'requestFailed')]
}

export function bindingState({ versionId, group = '', version, concurrency = 1, variableNames = [] }) {
  const invalid = Boolean(versionId) && (!version || version.id !== versionId || (group && !version.groups?.some(item => item.id === group)))
  const conflict = Boolean(versionId && version && Object.keys(version.field_mapping || {}).some(name => variableNames.includes(name)))
  const capacity = invalid || !versionId ? 0 : Number(group ? version.groups.find(item => item.id === group).count : version.row_count)
  return { invalid: Boolean(invalid), conflict, capacity,
    debugBlocked: Boolean(versionId) && (Boolean(invalid) || conflict || !(capacity >= 1)),
    executeBlocked: Boolean(versionId) && (Boolean(invalid) || conflict || !(capacity >= Number(concurrency))) }
}
