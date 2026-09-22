<template>
  <section class="auth-editor" :aria-label="at('title')">
    <h3>{{ at('title') }}</h3>
    <el-switch :model-value="!!modelValue.mode" :disabled="disabled" :active-text="at('enabled')" @change="toggle" />
    <p class="tip">{{ at('notice') }}</p>
    <template v-if="modelValue.mode">
      <el-alert v-if="sourceLoading || sourceError" :type="sourceError ? 'error' : 'info'" :closable="false" :title="at(sourceError ? 'sourceError' : 'sourceLoading')" />
      <p v-else-if="!sources.length" class="tip">{{ at('noSources') }}</p>
      <div class="sources"><el-tag v-for="source in sources" :key="source.name" effect="plain">{{ source.name }} ← {{ at(source.source) }} / {{ source.column }}{{ source.identity ? ` · ${at('identityColumn')}` : '' }}</el-tag></div>
      <el-form label-position="top" :disabled="disabled">
        <div class="auth-grid">
          <el-form-item :label="at('mode')"><el-select :model-value="modelValue.mode" @change="setMode"><el-option value="LOGIN" :label="at('LOGIN')" /><el-option value="STATIC" :label="at('STATIC')" /></el-select></el-form-item>
          <el-form-item :label="at('transport')"><el-select :model-value="modelValue.transport" @change="setTransport"><el-option value="BEARER" label="Bearer Token" /><el-option value="COOKIE" label="Cookie" /></el-select></el-form-item>
          <el-form-item :label="at('identity')"><el-select :model-value="identity" clearable filterable :placeholder="at('autoIdentity')" @change="value => emit('update:identity', value || '')"><el-option v-for="source in sources" :key="source.name" :value="source.name" :label="source.name" /></el-select></el-form-item>
          <el-form-item v-if="modelValue.transport === 'BEARER'" :label="at('tokenVariable')"><el-select :model-value="modelValue.access_token_variable" filterable allow-create default-first-option @change="value => field('access_token_variable', value)"><el-option v-for="name in variableNames" :key="name" :value="name" :label="name" /></el-select></el-form-item>
          <el-form-item v-if="modelValue.transport === 'COOKIE'" :label="at('cookieName')"><el-input :model-value="modelValue.cookie_name" @update:model-value="value => field('cookie_name', value)" /></el-form-item>
          <el-form-item v-if="modelValue.transport === 'COOKIE' && modelValue.mode === 'STATIC'" :label="at('cookieVariable')"><el-select :model-value="modelValue.cookie_variable" filterable @change="value => field('cookie_variable', value)"><el-option v-for="source in sources" :key="source.name" :value="source.name" :label="source.name" /></el-select></el-form-item>
        </div>
        <el-form-item :label="at('refresh')"><el-switch :model-value="!!modelValue.refresh" @change="refresh" /></el-form-item>
        <div v-if="modelValue.refresh" class="auth-grid">
          <el-form-item :label="at('refreshVariable')"><el-input :model-value="modelValue.refresh_token_variable" @update:model-value="value => field('refresh_token_variable', value)" /></el-form-item>
          <el-form-item :label="at('expiryVariable')"><el-input :model-value="modelValue.expires_in_variable || ''" :placeholder="at('optional')" @update:model-value="value => optionalField('expires_in_variable', value)" /></el-form-item>
          <el-form-item :label="at('skew')"><el-input-number :model-value="modelValue.expiry_skew_seconds || 0" :min="0" :max="300" @update:model-value="value => field('expiry_skew_seconds', value)" /></el-form-item>
          <el-form-item :label="at('statuses')"><el-checkbox :model-value="true" disabled>401</el-checkbox><el-checkbox :model-value="modelValue.refresh_on_status?.includes(403)" @change="value => field('refresh_on_status', value ? [401, 403] : [401])">403</el-checkbox></el-form-item>
        </div>
        <div class="auth-grid">
          <el-form-item :label="at('attempts')"><el-input-number :model-value="modelValue.max_attempts || 1" :min="1" :max="3" @update:model-value="value => field('max_attempts', value)" /></el-form-item>
          <el-form-item :label="at('retryDelay')"><el-input-number :model-value="modelValue.retry_delay_ms || 0" :min="0" :max="5000" @update:model-value="value => field('retry_delay_ms', value)" /></el-form-item>
        </div>
        <p class="tip">{{ at('bindingTip') }}</p>
        <fieldset v-for="phase in phases" :key="phase">
          <legend>{{ at(phase === 'refresh' ? 'refreshRequest' : phase) }} · {{ at('separateCount') }}</legend>
          <div class="auth-grid">
            <el-form-item :label="at('method')"><el-select :model-value="modelValue[phase].method" @change="value => stepField(phase, 'method', value)"><el-option v-for="method in methods" :key="method" :value="method" :label="method" /></el-select></el-form-item>
            <el-form-item :label="at('path')"><el-input :model-value="modelValue[phase].url" placeholder="/auth/login" @update:model-value="value => stepField(phase, 'url', value)" /></el-form-item>
          </div>
          <el-form-item :label="at('bodyType')"><el-select :model-value="modelValue[phase].body_type" @change="value => bodyType(phase, value)"><el-option value="JSON" label="JSON" /><el-option value="NONE" :label="at('noBody')" /></el-select></el-form-item>
          <el-form-item v-if="modelValue[phase].body_type === 'JSON'" :label="at('body')">
            <div v-if="modelValue[phase].body === MASK" class="saved"><span>{{ at('kept') }}</span><el-button link type="primary" @click="replace(phase, 'body')">{{ at('replace') }}</el-button></div>
            <el-input v-else type="textarea" :rows="4" :model-value="modelValue[phase].body" :aria-label="`${at(phase)} ${at('body')}`" @update:model-value="value => stepField(phase, 'body', value)" />
            <el-button v-if="replacing[`${phase}.body`]" link @click="cancel(phase, 'body')">{{ at('cancelReplace') }}</el-button>
          </el-form-item>
          <el-form-item :label="at('headers')">
            <div v-if="hasMaskedHeaders(phase)" class="saved"><span>{{ at('kept') }} · {{ Object.keys(modelValue[phase].headers).join(', ') }}</span><el-button link type="primary" @click="replace(phase, 'headers')">{{ at('replaceAll') }}</el-button></div>
            <el-input v-else type="textarea" :rows="2" :model-value="headerDrafts[phase] ?? JSON.stringify(modelValue[phase].headers || {}, null, 2)" placeholder="{}" @update:model-value="value => headers(phase, value)" />
            <el-button v-if="replacing[`${phase}.headers`]" link @click="cancel(phase, 'headers')">{{ at('cancelReplace') }}</el-button>
          </el-form-item>
          <h4>{{ at('extractors') }}</h4><p class="tip">{{ at('extractTip') }}</p>
          <div v-for="(rule, index) in modelValue[phase].extractors" :key="index" class="rule-row">
            <el-input :model-value="rule.name" :aria-label="at('outputVariable')" :placeholder="at('outputVariable')" @update:model-value="value => ruleField(phase, 'extractors', index, 'name', value)" />
            <el-input :model-value="rule.expr || rule.json_path" aria-label="JSONPath" placeholder="$.data.token" @update:model-value="value => ruleField(phase, 'extractors', index, 'expr', value)" />
            <el-button @click="removeRule(phase, 'extractors', index)">{{ at('remove') }}</el-button>
          </div>
          <el-button size="small" @click="addRule(phase, 'extractors')">{{ at('addExtractor') }}</el-button>
          <h4>{{ at('assertions') }}</h4>
          <div v-for="(rule, index) in modelValue[phase].assertions" :key="index" class="assert-row">
            <el-select :model-value="rule.type" @change="value => ruleField(phase, 'assertions', index, 'type', value)"><el-option value="STATUS_CODE" :label="at('statusCode')" /><el-option value="JSON_PATH" label="JSONPath" /></el-select>
            <el-input v-if="rule.type !== 'STATUS_CODE'" :model-value="rule.expr || rule.json_path" aria-label="JSONPath" placeholder="$.code" @update:model-value="value => ruleField(phase, 'assertions', index, 'expr', value)" />
            <span v-if="rule.expected === MASK" class="saved">{{ at('kept') }}<el-button link @click="replaceExpected(phase, index)">{{ at('replace') }}</el-button></span>
            <el-input v-else :model-value="String(rule.expected ?? '')" :aria-label="at('expected')" :placeholder="at('expected')" @update:model-value="value => ruleField(phase, 'assertions', index, 'expected', value)" />
            <el-button v-if="replacing[`${phase}.expected.${index}`]" link @click="cancelExpected(phase, index)">{{ at('cancelReplace') }}</el-button>
            <el-button @click="removeRule(phase, 'assertions', index)">{{ at('remove') }}</el-button>
          </div>
          <el-button size="small" @click="addRule(phase, 'assertions')">{{ at('addAssertion') }}</el-button>
        </fieldset>
      </el-form>
      <el-alert v-if="errors.length" type="error" :closable="false" :title="at('invalid')" :description="errors.map(error => at(error.split(':')[1])).join('；')" />
    </template>
  </section>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { MASK, cloneAuth, newAuthProfile, authRequest, setRefresh, authFormErrors } from '../authProfileForm.mjs'
const props = defineProps({ modelValue: { type: Object, default: () => ({}) }, identity: { type: String, default: '' }, sources: { type: Array, default: () => [] }, savedRevision: { type: Number, default: 0 }, disabled: Boolean, sourceLoading: Boolean, sourceError: Boolean })
const emit = defineEmits(['update:modelValue', 'update:identity', 'validity', 'edit'])
const { t } = useI18n()
const at = key => t(`performanceTesting.auth.${key}`)
const methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const replacing = reactive({}), headerDrafts = reactive({}), headerErrors = reactive({})
const phases = computed(() => ['login', 'refresh'].filter(phase => props.modelValue[phase]))
const variableNames = computed(() => [...new Set([...props.sources.map(s => s.name), 'access_token', ...phases.value.flatMap(phase => (props.modelValue[phase].extractors || []).map(r => r.name)).filter(Boolean)])])
const errors = computed(() => !props.modelValue.mode ? [] : [...authFormErrors(props.modelValue), ...Object.keys(headerErrors).filter(key => headerErrors[key]).map(key => `${key}:headersJson`)])
watch(errors, value => emit('validity', value.length === 0), { immediate: true })
watch(() => props.savedRevision, () => {
  for (const state of [replacing, headerDrafts, headerErrors]) Object.keys(state).forEach(key => delete state[key])
})
watch(() => props.modelValue, value => {
  for (const phase of ['login', 'refresh']) {
    if (!value[phase]) {
      Object.keys(replacing).filter(key => key.startsWith(`${phase}.`)).forEach(key => delete replacing[key])
      delete headerDrafts[phase]; delete headerErrors[phase]
    }
    Object.keys(replacing).filter(key => key.startsWith(`${phase}.expected.`)).forEach(key => { if (!value[phase]?.assertions[Number(key.split('.').at(-1))]) delete replacing[key] })
  }
}, { deep: true })
function update(change) { if (props.disabled) return; const next = cloneAuth(props.modelValue); change(next); emit('update:modelValue', next) }
function toggle(enabled) { if (!props.disabled) { Object.keys(headerErrors).forEach(key => delete headerErrors[key]); Object.keys(headerDrafts).forEach(key => delete headerDrafts[key]); emit('update:modelValue', enabled ? newAuthProfile(props.identity || props.sources.find(s => s.identity)?.name || props.sources[0]?.name) : {}) } }
function field(key, value) { update(next => { next[key] = value }) }
function optionalField(key, value) { update(next => { if (value) next[key] = value; else delete next[key] }) }
function stepField(phase, key, value) { update(next => { next[phase][key] = value }) }
function setMode(value) { update(next => { next.mode = value; if (value === 'STATIC') delete next.login; else { next.login ||= authRequest('login', props.identity || props.sources.find(s => s.identity)?.name); if (next.transport === 'COOKIE') next.login.extractors = next.login.extractors.filter(rule => rule.name !== 'access_token' || rule.expr !== '$.data.token') } }) }
function setTransport(value) { update(next => { next.transport = value; if (value === 'COOKIE') { next.cookie_name ||= 'sid'; next.cookie_variable ||= props.sources.find(s => /cookie/i.test(s.name))?.name || 'session_cookie' } else next.access_token_variable ||= 'access_token'; for (const phase of ['login', 'refresh']) { if (!next[phase]) continue; if (value === 'COOKIE') next[phase].extractors = next[phase].extractors.filter(rule => rule.name !== 'access_token' || rule.expr !== '$.data.token'); else if (!next[phase].extractors.some(rule => rule.name === next.access_token_variable)) next[phase].extractors.push({ type: 'JSON_PATH', name: next.access_token_variable, expr: '$.data.token' }) } }) }
function refresh(enabled) { if (!props.disabled) emit('update:modelValue', setRefresh(props.modelValue, enabled, props.identity)) }
function bodyType(phase, value) { update(next => { next[phase].body_type = value; next[phase].body = value === 'NONE' ? '' : '{}' }) }
function hasMaskedHeaders(phase) { return Object.values(props.modelValue[phase].headers || {}).includes(MASK) }
function replace(phase, fieldName) { if (props.disabled) return; replacing[`${phase}.${fieldName}`] = cloneAuth(props.modelValue[phase][fieldName]); stepField(phase, fieldName, fieldName === 'body' ? '{}' : {}) }
function cancel(phase, fieldName) {
  if (props.disabled || !Object.hasOwn(replacing, `${phase}.${fieldName}`)) return
  stepField(phase, fieldName, replacing[`${phase}.${fieldName}`])
  delete replacing[`${phase}.${fieldName}`]
  if (fieldName === 'headers') { delete headerDrafts[phase]; delete headerErrors[phase] }
}
function headers(phase, value) { if (props.disabled) return; emit('edit'); headerDrafts[phase] = value; try { const parsed = JSON.parse(value); if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object' || Object.values(parsed).some(v => typeof v !== 'string' || v === MASK)) throw new Error(); headerErrors[phase] = false; stepField(phase, 'headers', parsed) } catch { headerErrors[phase] = true } }
function ruleField(phase, type, index, key, value) { update(next => { if (type === 'assertions' && ['type', 'expr'].includes(key) && next[phase][type][index].expected === MASK) next[phase][type][index].expected = ''; next[phase][type][index][key] = value; if (key === 'expr') delete next[phase][type][index].json_path }) }
function addRule(phase, type) { update(next => next[phase][type].push(type === 'extractors' ? { name: '', type: 'JSON_PATH', expr: '$.data.' } : { type: 'JSON_PATH', expr: '$.code', expected: '' })) }
function removeRule(phase, type, index) { update(next => next[phase][type].splice(index, 1)); Object.keys(replacing).filter(key => key.startsWith(`${phase}.expected.`)).forEach(key => delete replacing[key]) }
function replaceExpected(phase, index) { replacing[`${phase}.expected.${index}`] = true; ruleField(phase, 'assertions', index, 'expected', '') }
function cancelExpected(phase, index) { if (props.disabled || !replacing[`${phase}.expected.${index}`]) return; ruleField(phase, 'assertions', index, 'expected', MASK); delete replacing[`${phase}.expected.${index}`] }
</script>

<style scoped>
.auth-editor { min-width: 0; margin: 20px 0; }
h3 { font-size: 16px; } h4 { margin: 12px 0 6px; }
.tip { font-size: 12px; line-height: 1.7; color: var(--el-text-color-secondary); overflow-wrap: anywhere; }
.auth-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
fieldset { min-width: 0; border: 1px solid var(--el-border-color); border-radius: 6px; margin: 16px 0; padding: 12px; }
legend { font-size: 14px; } .el-select, .el-input-number { width: 100%; }
.sources, .saved { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.sources { margin: 12px 0; } .sources .el-tag { height: auto; white-space: normal; overflow-wrap: anywhere; }
.rule-row, .assert-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.rule-row > .el-input, .assert-row > .el-input, .assert-row > .el-select { flex: 1 1 160px; min-width: 0; }
@media (max-width: 800px) { .auth-grid { grid-template-columns: minmax(0, 1fr); } fieldset { padding: 8px; } }
</style>
