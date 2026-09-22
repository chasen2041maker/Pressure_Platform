<template>
  <div class="app-settings">
    <el-card>
      <template #header>
        <div class="card-header">
          <span><el-icon><Setting /></el-icon> {{ $t('appAutomation.settings.title') }}</span>
        </div>
      </template>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="120px"
        style="max-width: 600px"
      >
        <el-form-item :label="$t('appAutomation.settings.adbPath')" prop="adb_path">
          <el-input
            v-model="form.adb_path"
            :placeholder="$t('appAutomation.settings.adbPathPlaceholder')"
            clearable
          >
            <template #prepend>
              <el-icon><FolderOpened /></el-icon>
            </template>
          </el-input>
          <div class="form-item-tip">
            <el-text size="small" type="info">
              {{ $t('appAutomation.settings.adbPathTip') }}
            </el-text>
          </div>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="handleSave" :loading="saving">
            <el-icon><Check /></el-icon>
            {{ $t('appAutomation.settings.saveConfig') }}
          </el-button>
          <el-button @click="handleReset">
            <el-icon><RefreshLeft /></el-icon>
            {{ $t('appAutomation.common.reset') }}
          </el-button>
        </el-form-item>
      </el-form>

      <el-divider />

      <div class="config-info">
        <el-descriptions :title="$t('appAutomation.settings.currentConfig')" :column="1" border>
          <el-descriptions-item :label="$t('appAutomation.settings.adbPath')">
            <el-tag>{{ currentConfig.adb_path || 'adb' }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="$t('appAutomation.common.updateTime')">
            {{ formatTime(currentConfig.updated_at) }}
          </el-descriptions-item>
          <el-descriptions-item :label="$t('appAutomation.common.createTime')">
            {{ formatTime(currentConfig.created_at) }}
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { Setting, FolderOpened, Check, RefreshLeft } from '@element-plus/icons-vue'
import { getAppConfig, updateAppConfig } from '@/api/app-automation'
import { formatDateTime } from '@/utils/app-automation-helpers'

const { t } = useI18n()

const formRef = ref(null)
const saving = ref(false)

const form = reactive({
  adb_path: 'adb'
})

const currentConfig = reactive({
  adb_path: '',
  created_at: '',
  updated_at: ''
})

const rules = computed(() => ({
  adb_path: [
    { required: true, message: t('appAutomation.settings.rules.adbPathRequired'), trigger: 'blur' }
  ]
}))

// 加载配置
const loadConfig = async () => {
  try {
    const res = await getAppConfig()
    if (res.data.success && res.data.data) {
      Object.assign(form, res.data.data)
      Object.assign(currentConfig, res.data.data)
    }
  } catch (error) {
    console.error('加载配置失败:', error)
    ElMessage.error(t('appAutomation.settings.messages.loadFailed'))
  }
}

// 保存配置
const handleSave = async () => {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
    saving.value = true

    const res = await updateAppConfig(form)
    if (res.data.success) {
      ElMessage.success(t('appAutomation.settings.messages.saveSuccess'))
      await loadConfig()
    } else {
      ElMessage.error(res.data.message || t('appAutomation.settings.messages.saveFailed'))
    }
  } catch (error) {
    if (error !== false) { // 不是表单验证错误
      console.error('保存配置失败:', error)
      ElMessage.error(t('appAutomation.settings.messages.saveFailed'))
    }
  } finally {
    saving.value = false
  }
}

// 重置表单
const handleReset = () => {
  Object.assign(form, currentConfig)
}

const formatTime = formatDateTime

onMounted(() => {
  loadConfig()
})
</script>

<style scoped lang="scss">
.app-settings {
  padding: 20px;

  .card-header {
    display: flex;
    align-items: center;
    font-weight: bold;
    
    span {
      display: flex;
      align-items: center;
      gap: 8px;
    }
  }

  .form-item-tip {
    margin-top: 8px;
  }

  .config-info {
    margin-top: 20px;
  }
}
</style>
