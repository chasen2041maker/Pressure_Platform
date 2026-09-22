<template>
  <div class="docs-center">
    <!-- 左侧文档列表 -->
    <div class="docs-sidebar">
      <div class="sidebar-header">
        <h2>{{ $t('docs.title') }}</h2>
        <el-input
          v-model="keyword"
          :placeholder="$t('docs.searchPlaceholder')"
          clearable
          size="small"
          :prefix-icon="Search"
        />
      </div>
      <el-scrollbar class="docs-list">
        <div
          v-for="doc in filteredDocs"
          :key="doc.name"
          class="doc-item"
          :class="{ active: currentDoc && currentDoc.name === doc.name }"
          @click="loadContent(doc)"
        >
          <div class="doc-title">{{ doc.title }}</div>
          <div class="doc-meta">{{ doc.name }}</div>
        </div>
        <el-empty v-if="!loading && filteredDocs.length === 0" :description="$t('docs.empty')" :image-size="60" />
      </el-scrollbar>
    </div>

    <!-- 右侧内容区 -->
    <div class="docs-content">
      <div v-if="contentLoading" class="content-loading" v-loading="true" />
      <div v-else-if="currentContent" class="markdown-body" v-html="renderedHtml" />
      <el-empty v-else :description="$t('docs.selectTip')" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { marked } from 'marked'
import api from '@/utils/api'

const { t } = useI18n()

const docs = ref([])
const keyword = ref('')
const loading = ref(false)
const currentDoc = ref(null)
const currentContent = ref('')
const contentLoading = ref(false)

const filteredDocs = computed(() => {
  if (!keyword.value) return docs.value
  const kw = keyword.value.toLowerCase()
  return docs.value.filter(
    (d) => d.title.toLowerCase().includes(kw) || d.name.toLowerCase().includes(kw)
  )
})

const renderedHtml = computed(() => {
  if (!currentContent.value) return ''
  return marked.parse(currentContent.value)
})

const fetchDocs = async () => {
  loading.value = true
  try {
    const res = await api.get('/core/docs/')
    docs.value = res.data.items || []
  } catch (e) {
    ElMessage.error(t('docs.loadFailed'))
  } finally {
    loading.value = false
  }
}

const loadContent = async (doc) => {
  currentDoc.value = doc
  contentLoading.value = true
  try {
    const res = await api.get('/core/docs/content/', { params: { name: doc.name } })
    currentContent.value = res.data.content || ''
  } catch (e) {
    ElMessage.error(t('docs.loadFailed'))
  } finally {
    contentLoading.value = false
  }
}

onMounted(() => {
  fetchDocs()
})
</script>

<style lang="scss" scoped>
.docs-center {
  display: flex;
  height: calc(100vh - 120px);
  gap: 16px;
  padding: 16px;
}

.docs-sidebar {
  width: 300px;
  flex-shrink: 0;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  display: flex;
  flex-direction: column;

  .sidebar-header {
    padding: 16px 16px 12px;
    border-bottom: 1px solid #ebeef5;

    h2 {
      margin: 0 0 12px;
      font-size: 18px;
      color: #303133;
    }
  }

  .docs-list {
    flex: 1;
  }

  .doc-item {
    padding: 10px 16px;
    cursor: pointer;
    border-bottom: 1px solid #f5f7fa;
    transition: background 0.2s;

    &:hover { background: #f5f7fa; }
    &.active { background: #ecf5ff; border-left: 3px solid #409eff; }

    .doc-title {
      font-size: 14px;
      color: #303133;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .doc-meta {
      font-size: 12px;
      color: #909399;
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
}

.docs-content {
  flex: 1;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  overflow-y: auto;
  padding: 24px 32px;

  .content-loading {
    min-height: 200px;
  }
}

.markdown-body {
  line-height: 1.7;
  color: #303133;
  font-size: 14px;

  :deep(h1) { font-size: 26px; border-bottom: 1px solid #ebeef5; padding-bottom: 8px; margin: 16px 0; }
  :deep(h2) { font-size: 22px; border-bottom: 1px solid #f0f2f5; padding-bottom: 6px; margin: 20px 0 12px; }
  :deep(h3) { font-size: 18px; margin: 16px 0 8px; }
  :deep(h4) { font-size: 16px; margin: 12px 0 6px; }
  :deep(p) { margin: 8px 0; }
  :deep(blockquote) {
    margin: 12px 0;
    padding: 8px 16px;
    border-left: 4px solid #409eff;
    background: #f5f7fa;
    color: #606266;
  }
  :deep(code) {
    background: #f5f7fa;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: Consolas, Monaco, monospace;
    font-size: 13px;
  }
  :deep(pre) {
    background: #282c34;
    color: #abb2bf;
    padding: 14px 16px;
    border-radius: 6px;
    overflow-x: auto;

    code {
      background: transparent;
      padding: 0;
      color: inherit;
    }
  }
  :deep(table) {
    border-collapse: collapse;
    margin: 12px 0;
    width: 100%;

    th, td {
      border: 1px solid #ebeef5;
      padding: 8px 12px;
      text-align: left;
    }
    th { background: #f5f7fa; font-weight: 600; }
    tr:nth-child(even) { background: #fafafa; }
  }
  :deep(a) { color: #409eff; text-decoration: none; }
  :deep(img) { max-width: 100%; }
  :deep(hr) { border: none; border-top: 1px solid #ebeef5; margin: 20px 0; }
}
</style>
