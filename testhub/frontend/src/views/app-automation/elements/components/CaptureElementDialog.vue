<template>
  <el-dialog
    v-model="dialogVisible"
    :title="$t('appAutomation.element.capture.title')"
    width="94vw"
    top="4vh"
    @close="handleClose"
  >
    <div class="capture-container">
      <!-- 左侧：截图画布 -->
      <div class="capture-left">
        <div
          v-if="capturedImage"
          ref="imageWrapper"
          class="image-wrapper"
          @mousedown="handleMouseDown"
          @mousemove="handleMouseMove"
          @mouseup="handleMouseUp"
          @mouseleave="handleMouseUp"
        >
          <img
            ref="imageRef"
            :src="capturedImage"
            @load="handleImageLoad"
            class="capture-image"
          />
          <!-- 选区框 -->
          <div
            v-if="selection"
            class="selection-box"
            :style="selectionStyle"
            @mousedown.stop="handleSelectionMouseDown"
          >
            <button class="selection-close" @click.stop="clearSelection">×</button>
            <div class="selection-info">{{ selectionInfo }}</div>
            <!-- 8个调整手柄 -->
            <span
              v-for="handle in resizeHandles"
              :key="handle"
              class="resize-handle"
              :class="`resize-handle-${handle}`"
              @mousedown.stop="handleResizeStart(handle, $event)"
            ></span>
          </div>
        </div>
        <div v-else class="empty-state">
          <el-empty :description="$t('appAutomation.element.capture.emptyState')" />
        </div>
      </div>

      <!-- 右侧：配置表单 -->
      <div class="capture-right">
        <el-form :model="formData" ref="formRef" label-width="110px" size="small">
          <!-- 设备选择和截图 -->
          <el-form-item :label="$t('appAutomation.element.capture.selectDevice')">
            <el-select v-model="selectedDevice" :placeholder="$t('appAutomation.element.capture.selectDevicePlaceholder')" style="width: 100%" :loading="devicesLoading">
              <el-option
                v-for="device in devices"
                :key="device.id"
                :label="device.device_id"
                :value="device.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item>
            <el-button type="primary" :loading="capturing" :disabled="!selectedDevice" @click="captureScreen">
              {{ $t('appAutomation.element.capture.captureFromDevice') }}
            </el-button>
          </el-form-item>

          <!-- Region和Pos值（根据元素类型显示） -->
          <el-form-item :label="$t('appAutomation.element.capture.regionValue')" v-if="formData.element_type === 'region'">
            <el-input v-model="regionValue" readonly :placeholder="$t('appAutomation.element.capture.dragToSelectTip')" />
          </el-form-item>

          <el-form-item :label="$t('appAutomation.element.capture.posValue')" v-if="formData.element_type === 'pos'">
            <el-input v-model="posValue" readonly :placeholder="$t('appAutomation.element.capture.clickToSelectTip')" />
          </el-form-item>

          <el-divider content-position="left">{{ $t('appAutomation.element.capture.elementInfo') }}</el-divider>

          <!-- 元素名称 -->
          <el-form-item :label="$t('appAutomation.element.capture.elementName')" required>
            <el-input v-model="formData.name" :placeholder="$t('appAutomation.element.capture.elementNamePlaceholder')" />
          </el-form-item>

          <!-- 所属项目 -->
          <el-form-item :label="$t('appAutomation.element.capture.project')">
            <el-select v-model="formData.project" :placeholder="$t('appAutomation.common.selectProject')" clearable filterable style="width: 100%">
              <el-option v-for="p in projectList" :key="p.id" :label="p.name" :value="p.id" />
            </el-select>
          </el-form-item>

          <!-- 元素类型 -->
          <el-form-item :label="$t('appAutomation.element.capture.elementType')" required>
            <el-radio-group v-model="formData.element_type">
              <el-radio value="image">{{ $t('appAutomation.element.capture.imageElement') }}</el-radio>
              <el-radio value="pos">{{ $t('appAutomation.element.capture.posElement') }}</el-radio>
              <el-radio value="region">{{ $t('appAutomation.element.capture.regionElement') }}</el-radio>
            </el-radio-group>
          </el-form-item>

          <!-- 标签 -->
          <el-form-item :label="$t('appAutomation.element.capture.tags')">
            <el-select v-model="formData.tags" multiple filterable allow-create :placeholder="$t('appAutomation.element.capture.tagsPlaceholder')" style="width: 100%">
              <el-option :label="$t('appAutomation.element.capture.exampleTag')" value="登录" />
            </el-select>
            <div style="color: #909399; font-size: 12px; margin-top: 5px;">
              💡 {{ $t('appAutomation.element.capture.tagsTip') }}
            </div>
          </el-form-item>

          <!-- 图片类型特有配置 -->
          <template v-if="formData.element_type === 'image'">
            <el-divider content-position="left">{{ $t('appAutomation.element.capture.imageConfig') }}</el-divider>

            <!-- 图片分类 -->
            <el-form-item :label="$t('appAutomation.element.capture.imageCategory')" required>
              <div style="display: flex; gap: 10px;">
                <el-select
                  v-model="formData.image_category"
                  :placeholder="$t('appAutomation.element.capture.selectCategory')"
                  filterable
                  style="flex: 1;"
                >
                  <el-option
                    v-for="cat in imageCategories"
                    :key="cat.name || cat"
                    :label="cat.name || cat"
                    :value="cat.name || cat"
                  >
                    <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                      <span>{{ cat.name || cat }}</span>
                      <el-button
                        v-if="(cat.name || cat) !== 'common'"
                        type="danger"
                        size="small"
                        link
                        :icon="Delete"
                        @click.stop="handleDeleteCategory(cat.name || cat)"
                        :title="$t('appAutomation.element.capture.deleteCategory')"
                        style="padding: 0; margin-left: 8px;"
                      />
                    </div>
                  </el-option>
                </el-select>
                <el-button
                  type="primary"
                  :icon="Plus"
                  @click="showCreateCategoryDialog"
                  :title="$t('appAutomation.element.capture.createCategoryTitle')"
                />
              </div>
              <div style="color: #909399; font-size: 12px; margin-top: 5px;">
                💡 {{ $t('appAutomation.element.capture.imageCategoryTip') }}
              </div>
            </el-form-item>

            <el-form-item :label="$t('appAutomation.element.capture.templateFileName')" required>
              <el-input v-model="templateFileName" :placeholder="$t('appAutomation.element.capture.templateFileNamePlaceholder')" />
              <div style="color: #909399; font-size: 12px; margin-top: 5px;">
                💡 {{ $t('appAutomation.element.capture.templateFileNameTip') }}
              </div>
            </el-form-item>

            <!-- 当前保存路径 -->
            <el-form-item :label="$t('appAutomation.element.capture.savePath')">
              <el-input :value="imageSavePath" readonly>
                <template #prepend>
                  <el-icon><FolderOpened /></el-icon>
                </template>
              </el-input>
            </el-form-item>

            <el-form-item :label="$t('appAutomation.element.capture.threshold')">
              <el-slider v-model="formData.config.image_threshold" :min="0.5" :max="1.0" :step="0.05" show-input />
              <div style="color: #909399; font-size: 12px; margin-top: 5px;">
                💡 {{ $t('appAutomation.element.capture.thresholdTip') }}
              </div>
            </el-form-item>

            <el-form-item :label="$t('appAutomation.element.capture.colorMode')">
              <el-switch
                v-model="formData.config.rgb"
                :active-text="$t('appAutomation.element.capture.rgbColor')"
                :inactive-text="$t('appAutomation.element.capture.grayscale')"
              />
              <div style="color: #909399; font-size: 12px; margin-top: 5px;">
                💡 {{ $t('appAutomation.element.capture.colorModeTip') }}
              </div>
            </el-form-item>
          </template>
        </el-form>
      </div>
    </div>

    <template #footer>
      <el-button @click="handleClose">{{ $t('appAutomation.common.cancel') }}</el-button>
      <el-button type="primary" @click="handleSubmit" :loading="submitting" :disabled="!canSave">
        {{ $t('appAutomation.element.capture.saveElement') }}
      </el-button>
    </template>
  </el-dialog>

  <!-- 创建图片分类对话框 -->
  <el-dialog
    v-model="createCategoryVisible"
    :title="$t('appAutomation.element.capture.createCategoryDialog')"
    width="400px"
  >
    <el-form>
      <el-form-item :label="$t('appAutomation.element.capture.categoryName')">
        <el-input
          v-model="newCategoryName"
          :placeholder="$t('appAutomation.element.capture.categoryNamePlaceholder')"
          @keyup.enter="handleCreateCategory"
        />
        <div style="color: #909399; font-size: 12px; margin-top: 5px;">
          💡 {{ $t('appAutomation.element.capture.categoryNameTip') }}
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="createCategoryVisible = false">{{ $t('appAutomation.common.cancel') }}</el-button>
      <el-button type="primary" @click="handleCreateCategory" :loading="creatingCategory">{{ $t('appAutomation.element.capture.create') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import type { PropType } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

interface ProjectItem {
  id: number
  name: string
}
import { FolderOpened, Plus, Delete } from '@element-plus/icons-vue'
import {
  getDeviceList,
  captureDeviceScreenshot,
  uploadAppElementImage,
  createAppElement,
  getAppImageCategories,
  createAppImageCategory,
  deleteAppImageCategory
} from '@/api/app-automation'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectList: { type: Array as PropType<ProjectItem[]>, default: () => [] }
})

const emit = defineEmits(['update:modelValue', 'success'])

const dialogVisible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

// 状态
const formRef = ref(null)
const imageRef = ref(null)
const imageWrapper = ref(null)
const submitting = ref(false)

// 设备相关
const devices = ref([])
const devicesLoading = ref(false)
const selectedDevice = ref(null)
const capturing = ref(false)
const capturedImage = ref('')

// 截图选区
const selection = ref(null)
const selecting = ref(false)
const startPoint = ref(null)
const action = ref(null) // 'create', 'move', 'resize'
const resizeHandle = ref(null)
const moveOffset = ref(null)
const imageSize = ref({ width: 0, height: 0 })

// 调整手柄列表
const resizeHandles = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']

// 表单数据
const formData = reactive({
  name: '',
  element_type: 'image',
  image_category: 'common',
  project: null,
  tags: [],
  config: {
    image_threshold: 0.7,
    rgb: false,
    x: 0,
    y: 0,
    x1: 0,
    y1: 0,
    x2: 0,
    y2: 0,
    image_path: '',
    file_hash: ''
  }
})

const templateFileName = ref('')
const imageCategories = ref([])
const createCategoryVisible = ref(false)
const newCategoryName = ref('')
const creatingCategory = ref(false)

// 计算属性
const regionValue = computed(() => {
  if (formData.config.x1 && formData.config.y1 && formData.config.x2 && formData.config.y2) {
    return `${formData.config.x1},${formData.config.y1},${formData.config.x2},${formData.config.y2}`
  }
  return ''
})

const posValue = computed(() => {
  if (formData.config.x && formData.config.y) {
    return `${formData.config.x},${formData.config.y}`
  }
  return ''
})

const selectionStyle = computed(() => {
  if (!selection.value) return {}
  const x1 = Math.min(selection.value.x1, selection.value.x2)
  const y1 = Math.min(selection.value.y1, selection.value.y2)
  const x2 = Math.max(selection.value.x1, selection.value.x2)
  const y2 = Math.max(selection.value.y1, selection.value.y2)
  return {
    left: `${x1}px`,
    top: `${y1}px`,
    width: `${x2 - x1}px`,
    height: `${y2 - y1}px`
  }
})

const selectionInfo = computed(() => {
  if (!selection.value) return ''
  const width = Math.abs(selection.value.x2 - selection.value.x1)
  const height = Math.abs(selection.value.y2 - selection.value.y1)
  return `${Math.round(width)} × ${Math.round(height)}`
})

// 计算图片保存路径
const imageSavePath = computed(() => {
  const imageCategory = formData.image_category || 'common'
  const filename = templateFileName.value || 'template.png'
  return `Template/${imageCategory}/${filename}`
})

// 是否可以保存
const canSave = computed(() => {
  if (!formData.name) return false
  if (formData.element_type === 'image') {
    return capturedImage.value && templateFileName.value && formData.image_category
  } else if (formData.element_type === 'pos') {
    return formData.config.x && formData.config.y
  } else if (formData.element_type === 'region') {
    return formData.config.x1 && formData.config.y1 && formData.config.x2 && formData.config.y2
  }
  return false
})

// 加载设备列表
const loadDevices = async () => {
  devicesLoading.value = true
  try {
    const { data } = await getDeviceList()
    devices.value = data.results || []
  } catch (error) {
    console.error('加载设备列表失败:', error)
    ElMessage.error(t('appAutomation.element.capture.messages.loadDevicesFailed'))
  } finally {
    devicesLoading.value = false
  }
}

// 从设备截图
const captureScreen = async () => {
  if (!selectedDevice.value) {
    ElMessage.warning(t('appAutomation.element.capture.messages.selectDeviceRequired'))
    return
  }

  capturing.value = true
  try {
    const { data } = await captureDeviceScreenshot(selectedDevice.value)

    if (data.success && data.data) {
      capturedImage.value = data.data.content || data.content || ''
      if (!capturedImage.value) {
        throw new Error(t('appAutomation.element.capture.messages.emptyScreenshot'))
      }
      ElMessage.success(t('appAutomation.element.capture.messages.captureSuccess'))
    } else {
      ElMessage.error(data.message || t('appAutomation.element.capture.messages.captureFailed'))
    }
  } catch (error) {
    console.error('截图失败:', error)
    ElMessage.error(t('appAutomation.element.capture.messages.captureFailed'))
  } finally {
    capturing.value = false
  }
}

// 图片加载完成
const handleImageLoad = () => {
  if (imageRef.value) {
    imageSize.value = {
      width: imageRef.value.naturalWidth || imageRef.value.width,
      height: imageRef.value.naturalHeight || imageRef.value.height
    }
  }
}

// 获取图片容器位置
const getImageRect = () => {
  if (!imageWrapper.value || !imageRef.value) return null
  return imageWrapper.value.getBoundingClientRect()
}

// 将选区坐标转换为实际图片坐标
const getSelectionInNatural = () => {
  if (!selection.value || !imageRef.value) return null
  const scaleX = imageSize.value.width / imageRef.value.clientWidth
  const scaleY = imageSize.value.height / imageRef.value.clientHeight
  const x1 = Math.min(selection.value.x1, selection.value.x2)
  const y1 = Math.min(selection.value.y1, selection.value.y2)
  const x2 = Math.max(selection.value.x1, selection.value.x2)
  const y2 = Math.max(selection.value.y1, selection.value.y2)
  return {
    x1: Math.round(x1 * scaleX),
    y1: Math.round(y1 * scaleY),
    x2: Math.round(x2 * scaleX),
    y2: Math.round(y2 * scaleY)
  }
}

// 更新配置值
const updateSelectionValues = () => {
  const natural = getSelectionInNatural()
  if (natural) {
    formData.config.x1 = natural.x1
    formData.config.y1 = natural.y1
    formData.config.x2 = natural.x2
    formData.config.y2 = natural.y2
  }
}

// 鼠标事件处理
const handleMouseDown = (e) => {
  if (!capturedImage.value || !imageWrapper.value) return
  const rect = getImageRect()
  if (!rect) return
  const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width))
  const y = Math.max(0, Math.min(e.clientY - rect.top, rect.height))
  selecting.value = true
  startPoint.value = { x, y }
  action.value = 'create'
  selection.value = { x1: x, y1: y, x2: x, y2: y }
  e.preventDefault()
}

const handleMouseMove = (e) => {
  if (!selecting.value || !selection.value) return
  if (!imageWrapper.value) return
  const rect = getImageRect()
  if (!rect) return
  const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width))
  const y = Math.max(0, Math.min(e.clientY - rect.top, rect.height))
  
  if (action.value === 'create' && startPoint.value) {
    selection.value = { x1: startPoint.value.x, y1: startPoint.value.y, x2: x, y2: y }
  } else if (action.value === 'move' && moveOffset.value) {
    const width = Math.abs(selection.value.x2 - selection.value.x1)
    const height = Math.abs(selection.value.y2 - selection.value.y1)
    const left = Math.max(0, Math.min(x - moveOffset.value.x, rect.width - width))
    const top = Math.max(0, Math.min(y - moveOffset.value.y, rect.height - height))
    selection.value = { x1: left, y1: top, x2: left + width, y2: top + height }
  } else if (action.value === 'resize' && resizeHandle.value) {
    selection.value = resizeSelection(selection.value, resizeHandle.value, x, y, rect)
  }
  e.preventDefault()
}

const handleMouseUp = () => {
  if (selecting.value) {
    if (action.value === 'create' && selection.value) {
      const width = Math.abs(selection.value.x2 - selection.value.x1)
      const height = Math.abs(selection.value.y2 - selection.value.y1)
      if (width < 5 && height < 5) {
        // 单击设置坐标
        if (imageRef.value) {
          const scaleX = imageSize.value.width / imageRef.value.clientWidth
          const scaleY = imageSize.value.height / imageRef.value.clientHeight
          formData.config.x = Math.round(selection.value.x1 * scaleX)
          formData.config.y = Math.round(selection.value.y1 * scaleY)
        }
        selection.value = null
      } else {
        updateSelectionValues()
      }
    } else if (action.value === 'move' || action.value === 'resize') {
      updateSelectionValues()
    }
    selecting.value = false
    startPoint.value = null
    action.value = null
    resizeHandle.value = null
    moveOffset.value = null
  }
}

const handleSelectionMouseDown = (e) => {
  if (!imageWrapper.value) return
  const rect = getImageRect()
  if (!rect || !selection.value) return
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  const x1 = Math.min(selection.value.x1, selection.value.x2)
  const y1 = Math.min(selection.value.y1, selection.value.y2)
  selecting.value = true
  action.value = 'move'
  moveOffset.value = { x: x - x1, y: y - y1 }
  e.preventDefault()
}

const handleResizeStart = (handle, e) => {
  if (!imageWrapper.value) return
  selecting.value = true
  action.value = 'resize'
  resizeHandle.value = handle
  e.preventDefault()
}

const resizeSelection = (sel, handle, x, y, rect) => {
  let { x1, y1, x2, y2 } = sel
  const clampX = Math.max(0, Math.min(x, rect.width))
  const clampY = Math.max(0, Math.min(y, rect.height))
  if (handle.includes('n')) y1 = clampY
  if (handle.includes('s')) y2 = clampY
  if (handle.includes('w')) x1 = clampX
  if (handle.includes('e')) x2 = clampX
  return { x1, y1, x2, y2 }
}

const clearSelection = () => {
  selection.value = null
  action.value = null
  resizeHandle.value = null
  moveOffset.value = null
  formData.config.x1 = 0
  formData.config.y1 = 0
  formData.config.x2 = 0
  formData.config.y2 = 0
}

// 提交表单
const handleSubmit = async () => {
  if (!formData.name) {
    ElMessage.warning(t('appAutomation.element.capture.messages.elementNameRequired'))
    return
  }

  if (formData.element_type === 'image') {
    if (!capturedImage.value) {
      ElMessage.warning(t('appAutomation.element.capture.messages.captureRequired'))
      return
    }
    if (!templateFileName.value) {
      ElMessage.warning(t('appAutomation.element.capture.messages.templateFileNameRequired'))
      return
    }
    if (!formData.image_category) {
      ElMessage.warning(t('appAutomation.element.capture.messages.imageCategoryRequired'))
      return
    }
  } else if (formData.element_type === 'pos') {
    if (!formData.config.x || !formData.config.y) {
      ElMessage.warning(t('appAutomation.element.capture.messages.coordinateRequired'))
      return
    }
  } else if (formData.element_type === 'region') {
    if (!formData.config.x1 || !formData.config.y1 || !formData.config.x2 || !formData.config.y2) {
      ElMessage.warning(t('appAutomation.element.capture.messages.regionRequired'))
      return
    }
  }

  submitting.value = true

  try {
    if (formData.element_type === 'image' && capturedImage.value) {
      let imageBlob

      // 裁剪图片
      if (selection.value && imageRef.value) {
        const img = imageRef.value
        const sel = selection.value
        const scaleX = imageSize.value.width / img.clientWidth
        const scaleY = imageSize.value.height / img.clientHeight
        
        // 计算裁剪区域
        const x1 = Math.min(sel.x1, sel.x2)
        const y1 = Math.min(sel.y1, sel.y2)
        const x2 = Math.max(sel.x1, sel.x2)
        const y2 = Math.max(sel.y1, sel.y2)
        const width = x2 - x1
        const height = y2 - y1
        
        // 转换为实际图片坐标
        const cropX = Math.round(x1 * scaleX)
        const cropY = Math.round(y1 * scaleY)
        const cropWidth = Math.round(width * scaleX)
        const cropHeight = Math.round(height * scaleY)

        const canvas = document.createElement('canvas')
        canvas.width = cropWidth
        canvas.height = cropHeight
        const ctx = canvas.getContext('2d')

        if (ctx) {
          ctx.drawImage(img, cropX, cropY, cropWidth, cropHeight, 0, 0, cropWidth, cropHeight)
          imageBlob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'))
        }
      } else {
        const base64Data = capturedImage.value.split(',')[1]
        imageBlob = base64ToBlob(base64Data, 'image/png')
      }

      if (!imageBlob) {
        ElMessage.error(t('appAutomation.element.capture.messages.imageProcessFailed'))
        submitting.value = false
        return
      }

      const file = new File([imageBlob], templateFileName.value, { type: 'image/png' })

      try {
        const { data: uploadData } = await uploadAppElementImage(
          file,
          formData.image_category || 'common'
        )

        if (uploadData.success) {
          formData.config.image_path = uploadData.data.image_path
          formData.config.file_hash = uploadData.data.file_hash
          ElMessage.success(t('appAutomation.element.capture.messages.imageUploaded', { path: uploadData.data.image_path }))
        } else {
          // 显示详细的错误信息
          let errorMessage = uploadData.message || t('appAutomation.element.capture.messages.uploadImageFailed')

          if (uploadData.detail) {
            errorMessage += `\n\n${uploadData.detail}`
          }
          if (uploadData.suggestion) {
            errorMessage += `\n\n💡 ${t('appAutomation.element.capture.messages.suggestion')}：${uploadData.suggestion}`
          }

          if (uploadData.data?.existing_element) {
            const existing = uploadData.data.existing_element
            errorMessage += `\n\n${t('appAutomation.element.capture.messages.existingElement', { name: existing.name, id: existing.id })}`
            if (existing.image_path) {
              errorMessage += `\n${t('appAutomation.element.capture.messages.filePath')}${existing.image_path}`
            }
          }

          ElMessage.error({
            message: errorMessage,
            duration: 8000,
            showClose: true
          })
          submitting.value = false
          return
        }
      } catch (uploadError) {
        console.error('图片上传异常:', uploadError)
        let errorMessage = t('appAutomation.element.capture.messages.uploadImageFailed')

        if (uploadError.response?.data) {
          const data = uploadError.response.data
          errorMessage = data.message || data.detail || errorMessage
        } else if (uploadError.message) {
          errorMessage += `: ${uploadError.message}`
        }

        ElMessage.error({
          message: errorMessage,
          duration: 5000,
          showClose: true
        })
        submitting.value = false
        return
      }
    }

    // 准备提交数据
    const submitData = {
      name: formData.name,
      element_type: formData.element_type,
      project: formData.project || null,
      tags: formData.tags,
      config: {
        ...formData.config,
        image_category: formData.image_category || 'common'
      }
    }

    // DRF ModelViewSet 的 create 方法直接返回序列化数据，没有 success 字段
    await createAppElement(submitData)
    ElMessage.success(t('appAutomation.common.createSuccess'))
    emit('success')
    handleClose()
  } catch (error) {
    console.error('创建失败:', error)

    // 显示详细的错误信息
    let errorMessage = t('appAutomation.element.capture.messages.createFailed')

    if (error.response?.data) {
      const data = error.response.data
      if (data.message) {
        errorMessage = data.message
      } else if (data.detail) {
        errorMessage = data.detail
      } else if (data.config) {
        const configErrors = data.config
        if (Array.isArray(configErrors)) {
          errorMessage = t('appAutomation.element.capture.messages.configError', { detail: configErrors.join(', ') })
        } else if (typeof configErrors === 'object') {
          errorMessage = t('appAutomation.element.capture.messages.configError', { detail: JSON.stringify(configErrors) })
        }
      }
      errorMessage += t('appAutomation.element.capture.messages.statusCode', { code: error.response.status })
    } else if (error.request) {
      errorMessage = t('appAutomation.element.capture.messages.networkError')
    } else if (error.message) {
      errorMessage = t('appAutomation.element.capture.messages.errorWithMessage', { message: error.message })
    }

    ElMessage.error({
      message: errorMessage,
      duration: 8000,
      showClose: true
    })
  } finally {
    submitting.value = false
  }
}

const base64ToBlob = (base64, type = 'image/png') => {
  const byteCharacters = atob(base64)
  const byteNumbers = new Array(byteCharacters.length)
  for (let i = 0; i < byteCharacters.length; i++) {
    byteNumbers[i] = byteCharacters.charCodeAt(i)
  }
  const byteArray = new Uint8Array(byteNumbers)
  return new Blob([byteArray], { type })
}

const handleClose = () => {
  emit('update:modelValue', false)
  Object.assign(formData, {
    name: '',
    element_type: 'image',
    image_category: 'common',
    project: null,
    tags: [],
    config: {
      image_threshold: 0.7,
      rgb: false,
      x: 0,
      y: 0,
      x1: 0,
      y1: 0,
      x2: 0,
      y2: 0,
      image_path: '',
      file_hash: ''
    }
  })
  templateFileName.value = ''
  capturedImage.value = ''
  selection.value = null
  action.value = null
}

// 加载图片分类列表
const loadImageCategories = async () => {
  try {
    const { data } = await getAppImageCategories()
    if (data.success && Array.isArray(data.data)) {
      // 后端返回的是对象数组 [{name: 'common', path: 'common'}]
      // 转换为字符串数组以兼容现有代码
      imageCategories.value = data.data.map(cat => cat.name || cat)
    }
  } catch (error) {
    console.error('加载图片分类失败:', error)
    imageCategories.value = ['common']
  }
}

// 显示创建分类对话框
const showCreateCategoryDialog = () => {
  newCategoryName.value = ''
  createCategoryVisible.value = true
}

// 创建新分类
const handleCreateCategory = async () => {
  if (!newCategoryName.value.trim()) {
    ElMessage.warning(t('appAutomation.element.capture.messages.categoryNameRequired'))
    return
  }

  try {
    creatingCategory.value = true
    const { data } = await createAppImageCategory(newCategoryName.value.trim())

    if (data.success) {
      ElMessage.success(t('appAutomation.common.createSuccess'))
      // 刷新分类列表
      await loadImageCategories()
      // 自动选中新创建的分类
      formData.image_category = data.data.name
      // 关闭对话框
      createCategoryVisible.value = false
    } else {
      ElMessage.error(data.message || t('appAutomation.element.capture.messages.createFailed'))
    }
  } catch (error) {
    console.error('创建分类失败:', error)
    ElMessage.error(t('appAutomation.element.capture.messages.createFailed'))
  } finally {
    creatingCategory.value = false
  }
}

// 删除分类
const handleDeleteCategory = async (categoryName) => {
  try {
    await ElMessageBox.confirm(
      t('appAutomation.element.capture.messages.deleteCategoryConfirm', { name: categoryName }),
      t('appAutomation.common.confirmDelete'),
      {
        confirmButtonText: t('appAutomation.common.delete'),
        cancelButtonText: t('appAutomation.common.cancel'),
        type: 'warning',
      }
    )

    const { data } = await deleteAppImageCategory(categoryName)

    if (data.success) {
      ElMessage.success(t('appAutomation.common.deleteSuccess'))
      // 刷新分类列表
      await loadImageCategories()
      // 如果当前选中的分类被删除，切换到 common
      if (formData.image_category === categoryName) {
        formData.image_category = 'common'
      }
    } else {
      ElMessage.error(data.message || t('appAutomation.common.deleteFailed'))
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除分类失败:', error)
      ElMessage.error(t('appAutomation.common.deleteFailed'))
    }
  }
}

watch(() => props.modelValue, (val) => {
  if (val) {
    loadDevices()
    loadImageCategories()
  }
})
</script>

<style scoped>
.capture-container {
  display: flex;
  gap: 20px;
  height: calc(100vh - 200px);
}

.capture-left {
  flex: 1;
  min-width: 0;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;
  background: #f5f7fa;
  display: flex;
  align-items: center;
  justify-content: center;
}

.image-wrapper {
  position: relative;
  cursor: crosshair;
  display: inline-block;
  max-width: 100%;
  max-height: 100%;
}

.capture-image {
  max-width: 100%;
  max-height: calc(100vh - 220px);
  display: block;
  user-select: none;
  object-fit: contain;
}

.selection-box {
  position: absolute;
  border: 2px solid #409eff;
  background: rgba(64, 158, 255, 0.1);
  cursor: move;
  pointer-events: auto;
}

.selection-info {
  position: absolute;
  top: -25px;
  left: 0;
  background: #409eff;
  color: white;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 12px;
  white-space: nowrap;
  pointer-events: none;
}

.selection-close {
  position: absolute;
  top: -10px;
  right: -10px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #f56c6c;
  color: white;
  border: none;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  pointer-events: auto;
  z-index: 10;
}

.selection-close:hover {
  background: #f78989;
}

.resize-handle {
  position: absolute;
  width: 8px;
  height: 8px;
  background: #409eff;
  border: 1px solid white;
  border-radius: 50%;
  pointer-events: auto;
  z-index: 5;
}

.resize-handle-nw { top: -5px; left: -5px; cursor: nwse-resize; }
.resize-handle-n { top: -5px; left: 50%; transform: translateX(-50%); cursor: ns-resize; }
.resize-handle-ne { top: -5px; right: -5px; cursor: nesw-resize; }
.resize-handle-e { top: 50%; right: -5px; transform: translateY(-50%); cursor: ew-resize; }
.resize-handle-se { bottom: -5px; right: -5px; cursor: nwse-resize; }
.resize-handle-s { bottom: -5px; left: 50%; transform: translateX(-50%); cursor: ns-resize; }
.resize-handle-sw { bottom: -5px; left: -5px; cursor: nesw-resize; }
.resize-handle-w { top: 50%; left: -5px; transform: translateY(-50%); cursor: ew-resize; }

.capture-right {
  width: 400px;
  flex-shrink: 0;
  overflow-y: auto;
  padding-right: 10px;
}

.empty-state {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
