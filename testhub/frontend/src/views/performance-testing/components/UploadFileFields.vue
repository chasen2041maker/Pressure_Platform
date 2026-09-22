<template>
  <section class="upload-fields" :aria-label="t('performanceTesting.editor.files')">
    <el-alert v-if="error" type="error" :title="error" :closable="false" />
    <el-form-item v-for="(row, index) in modelValue" :key="index" :label="row.field">
      <el-select :model-value="row.file_id" :loading="loading" :disabled="disabled || loading || !!error"
        :aria-label="`${t('performanceTesting.editor.fileSelect')} ${row.field}`"
        :placeholder="t('performanceTesting.editor.filePlaceholder')" @update:model-value="id => selectFile(index, id)">
        <el-option v-for="file in files" :key="file.id" :value="file.id" :label="file.name" />
      </el-select>
      <span v-if="row.file_id && !loading && !files.some(file => file.id === row.file_id)" class="missing-file">{{ t('performanceTesting.editor.fileUnavailable') }}</span>
    </el-form-item>
    <label class="upload-control">{{ t('performanceTesting.editor.fileUpload') }}
      <input type="file" :disabled="disabled || loading || uploading" :aria-label="t('performanceTesting.editor.fileUpload')" @change="upload" />
    </label>
    <el-button v-if="error" :disabled="loading || uploading" @click="load">{{ t('performanceTesting.common.retry') }}</el-button>
  </section>
</template>

<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { getPerfDataFiles, uploadPerfUploadFile } from '@/api/performance-testing'
import { requestGate } from '../apiCatalogForm.mjs'
const props = defineProps({ projectId: { type: [Number, String], required: true }, modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue', 'busy'])
const { t } = useI18n()
const files = ref([]), loading = ref(false), uploading = ref(false), error = ref('')
const gate = requestGate()
function selectFile(index, id) {
  if (props.disabled || loading.value || uploading.value || !files.value.some(file => file.id === id)) return
  emit('update:modelValue', props.modelValue.map((row, i) => i === index ? { ...row, file_id: id } : { ...row }))
}
async function load() {
  const ticket = gate.begin(), project = props.projectId
  files.value = []; error.value = ''; loading.value = true
  try {
    const result = []; let page = 1, more = true
    while (more) {
      const { data } = await getPerfDataFiles({ project, file_type: 'UPLOAD', page_size: 200, page })
      if (!gate.current(ticket)) return
      result.push(...(data.results || data)); more = !!data.next; page++
      if (page > 100 && more) throw new Error('limit')
    }
    files.value = result
  } catch { if (gate.current(ticket)) error.value = t('performanceTesting.editor.fileLoadError') }
  finally { if (gate.current(ticket)) loading.value = false }
}
async function upload(event) {
  const file = event.target.files?.[0], project = props.projectId
  event.target.value = ''
  if (!file || props.disabled || loading.value || uploading.value) return
  if (!file.size || file.size > 20 * 1024 * 1024) { error.value = t('performanceTesting.editor.fileSizeError'); return }
  const ticket = gate.begin(); uploading.value = true; emit('busy', true); error.value = ''
  try {
    const { data } = await uploadPerfUploadFile({ project, file, name: file.name })
    if (!gate.current(ticket) || project !== props.projectId) return
    files.value = [...files.value.filter(item => item.id !== data.id), data]
    if (props.modelValue.length === 1) emit('update:modelValue', [{ ...props.modelValue[0], file_id: data.id }])
  } catch { if (gate.current(ticket)) error.value = t('performanceTesting.editor.fileLoadError') }
  finally { if (gate.current(ticket)) { uploading.value = false; emit('busy', false) } }
}
watch(() => props.projectId, () => { uploading.value = false; emit('busy', false); load() }, { immediate: true })
onBeforeUnmount(() => { gate.invalidate(); emit('busy', false) })
</script>

<style scoped>
.upload-fields { margin: 12px 0; }
.upload-control { display: flex; flex-wrap: wrap; gap: 8px; }
.missing-file { margin-left: 8px; color: var(--el-color-danger); }
</style>
