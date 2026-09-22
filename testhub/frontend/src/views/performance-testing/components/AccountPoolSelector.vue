<template>
  <section class="account-selector" aria-live="polite">
    <h3>{{ at('selection') }}</h3>
    <el-alert v-if="engine !== 'K6' && (modelValue || group)" type="warning" :closable="false" :title="at('legacyBlocked')" />
    <el-alert v-else-if="error" type="error" :closable="false" :title="at('loadFailed')" />
    <el-alert v-else-if="state.invalid" type="error" :closable="false" :title="at('invalidSelection')" />
    <el-alert v-else-if="state.conflict" type="error" :closable="false" :title="at('conflict')" />
    <p v-if="loading">{{ at('loading') }}</p>
    <el-form label-position="top">
      <el-form-item :label="at('pool')">
        <el-select :model-value="poolId" clearable filterable :placeholder="at('noPool')" :disabled="disabled || engine !== 'K6' || loading || !!error || !project" @change="selectPool">
          <el-option v-for="pool in pools" :key="pool.id" :value="pool.id" :label="pool.name" />
        </el-select>
        <p v-if="!loading && !error && !pools.length" class="tip">{{ at('noPools') }}</p>
      </el-form-item>
      <el-form-item v-if="poolId || modelValue" :label="at('version')">
        <el-select :model-value="modelValue" :disabled="disabled || engine !== 'K6' || loading || !!error" @change="selectVersion">
          <el-option v-for="version in versions" :key="version.id" :value="version.id" :label="`v${version.version} · ${at('count')} ${version.row_count}`" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="selectedVersion?.groups?.length" :label="at('group')">
        <el-select :model-value="group || ''" :placeholder="at('allGroups')" :disabled="disabled || engine !== 'K6' || loading || !!error" @change="value => emit('update:group', value)">
          <el-option value="" :label="at('allGroups')" />
          <el-option v-for="item in selectedVersion.groups" :key="item.id" :value="item.id" :label="`${item.label} · ${item.count}`" />
        </el-select>
        <p v-if="!group" class="tip">{{ at('allGroups') }}</p>
        <p class="tip">{{ at('groupTip') }}</p>
      </el-form-item>
    </el-form>
    <template v-if="modelValue && !loading && !error && !state.invalid">
      <p>{{ at('capacity', { count: state.capacity, vus: concurrency }) }}</p>
      <p class="tip">{{ at('verifiedTip') }}</p>
      <el-alert v-if="state.capacity < 1" type="error" :closable="false" :title="at('noCapacity')" />
      <el-alert v-else-if="state.executeBlocked && !state.conflict" type="warning" :closable="false" :title="at('capacityExceeded', { count: state.capacity, vus: concurrency })" />
      <div class="mapping-summary"><el-tag v-for="(column, name) in selectedVersion.field_mapping" :key="name" effect="plain">{{ name }} ← {{ column }}</el-tag></div>
      <p class="tip">{{ at('versionNotice') }}</p>
    </template>
    <p v-if="hasLegacyCsv" class="tip">{{ at('oldCsv') }}</p>
    <div class="actions">
      <el-button link type="primary" :loading="loading" :disabled="disabled" @click="reload">{{ at('refresh') }}</el-button>
      <el-button v-if="poolId || modelValue || group" link type="danger" :disabled="disabled" @click="clear">{{ at('clearSelection') }}</el-button>
      <el-button link type="primary" @click="router.push({ path: '/performance-testing/account-pools', query: { project } })">{{ at('manage') }}</el-button>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getPerfAccountPools, getPerfAccountPoolVersions, getPerfAccountPoolVersion } from '@/api/performance-testing'
import { latestRequestGate } from '../environmentForm.mjs'
import { bindingState } from '../accountPoolForm.mjs'

const props = defineProps({ project: { type: Number, default: null }, modelValue: { type: Number, default: null },
  group: { type: String, default: '' }, engine: { type: String, default: 'K6' }, concurrency: { type: Number, default: 1 },
  variableNames: { type: Array, default: () => [] }, hasLegacyCsv: Boolean, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'update:group', 'state'])
const router = useRouter()
const { t } = useI18n()
const at = (key, params) => t(`performanceTesting.accountPool.${key}`, params)
const pools = ref([])
const versions = ref([])
const poolId = ref(null)
const loading = ref(true)
const error = ref(false)
const requests = latestRequestGate()
const selectedVersion = computed(() => versions.value.find(version => version.id === props.modelValue) || null)
const state = computed(() => bindingState({ versionId: props.modelValue, version: selectedVersion.value, group: props.group,
  concurrency: props.concurrency, variableNames: props.variableNames }))
watch(() => [props.project, props.modelValue, props.group, poolId.value, loading.value, error.value, state.value], () => {
  emit('state', { ...state.value, project: props.project, versionId: props.modelValue, group: props.group, pending: Boolean(poolId.value && !props.modelValue),
    fieldMapping: { ...(selectedVersion.value?.field_mapping || {}) }, identityColumn: selectedVersion.value?.identity_column || '',
    mappedNames: Object.keys(selectedVersion.value?.field_mapping || {}), loading: loading.value, error: error.value })
}, { immediate: true, deep: true, flush: 'sync' })

async function reload() {
  const request = requests.begin()
  const project = props.project
  const requestedPool = poolId.value
  loading.value = true
  error.value = false
  versions.value = []
  pools.value = []
  poolId.value = requestedPool
  try {
    if (!project) return
    const { data } = await getPerfAccountPools({ project, page_size: 0 })
    if (!requests.isCurrent(request) || project !== props.project) return
    pools.value = (data.results || data).filter(pool => pool.project === project)
    if (props.modelValue) {
      const { data: version } = await getPerfAccountPoolVersion(props.modelValue)
      if (!requests.isCurrent(request) || project !== props.project) return
      if (!pools.value.some(pool => pool.id === version.pool)) return
      poolId.value = version.pool
      const history = await getPerfAccountPoolVersions(version.pool)
      if (!requests.isCurrent(request) || project !== props.project) return
      versions.value = history.data
    } else if (requestedPool && pools.value.some(pool => pool.id === requestedPool)) {
      poolId.value = requestedPool
      const history = await getPerfAccountPoolVersions(requestedPool)
      if (!requests.isCurrent(request) || project !== props.project) return
      versions.value = history.data
      emit('update:modelValue', history.data[0]?.id || null)
    }
  } catch {
    if (requests.isCurrent(request)) error.value = true
  } finally { if (requests.isCurrent(request)) loading.value = false }
}

function clear() {
  if (props.disabled) return
  requests.begin()
  poolId.value = null
  versions.value = []
  loading.value = false
  emit('update:modelValue', null)
  emit('update:group', '')
}
async function selectPool(id) {
  if (props.disabled || props.engine !== 'K6' || loading.value || error.value) return
  if (!id) { clear(); return }
  const request = requests.begin()
  poolId.value = id
  versions.value = []
  loading.value = true
  emit('update:modelValue', null)
  emit('update:group', '')
  try {
    const { data } = await getPerfAccountPoolVersions(id)
    if (!requests.isCurrent(request)) return
    versions.value = data
    emit('update:modelValue', data[0]?.id || null)
  } catch { if (requests.isCurrent(request)) error.value = true }
  finally { if (requests.isCurrent(request)) loading.value = false }
}
function selectVersion(id) {
  if (props.disabled || loading.value || error.value || props.engine !== 'K6') return
  emit('update:modelValue', id)
  emit('update:group', '')
}
watch(() => props.project, () => { poolId.value = null; reload() }, { immediate: true })
watch(() => props.modelValue, id => { if (id && !versions.value.some(version => version.id === id) && !loading.value) reload() })
onBeforeUnmount(() => requests.begin())
</script>

<style scoped>
.account-selector { margin: 12px 0; min-width: 0; }
h3 { font-size: 15px; margin: 0 0 12px; }
.tip { font-size: 12px; line-height: 1.7; color: var(--el-text-color-secondary); margin: 6px 0; width: 100%; }
.el-select { width: 100%; min-width: 0; }
.actions, .mapping-summary { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.mapping-summary .el-tag { max-width: 100%; height: auto; white-space: normal; overflow-wrap: anywhere; }
</style>
