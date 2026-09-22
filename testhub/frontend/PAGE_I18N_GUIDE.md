# 页面国际化实施指南

## 📋 当前状态

### ✅ 已完成
- **Layout 组件**: 侧边栏菜单、顶部导航、面包屑、用户下拉菜单
- **翻译基础设施**: vue-i18n 配置、中英文翻译文件、语言切换功能
- **Element Plus 组件**: 跟随语言自动切换

### ⚠️ 待完成
- **页面内容**: 所有 views 目录下的页面组件还未翻译

---

## 🎯 国际化实施步骤

### 步骤 1: 在翻译文件中添加文本

**文件位置:**
- `frontend/src/locales/zh-CN.js` - 简体中文
- `frontend/src/locales/en-US.js` - 英语

**示例:** 为测试报告页面添加翻译

```javascript
// zh-CN.js
export default {
  // ... 其他翻译 ...
  report: {
    selectProject: '选择项目',
    exportReport: '导出报告',
    testPlan: '测试计划',
    passRate: '通过率',
    // ... 更多
  }
}

// en-US.js
export default {
  // ... 其他翻译 ...
  report: {
    selectProject: 'Select Project',
    exportReport: 'Export Report',
    testPlan: 'Test Plan',
    passRate: 'Pass Rate',
    // ... 更多
  }
}
```

### 步骤 2: 在页面组件中使用翻译

**方法 1: 在模板中使用 `$t()`**

```vue
<template>
  <div>
    <!-- 原来: -->
    <el-button>导出报告</el-button>

    <!-- 修改为: -->
    <el-button>{{ $t('report.exportReport') }}</el-button>
  </div>
</template>
```

**方法 2: 在 script 中使用 `t()`**

```vue
<template>
  <div>{{ buttonText }}</div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

// 在计算属性或变量中使用
const buttonText = computed(() => t('report.exportReport'))

// 在函数中使用
const showMessage = () => {
  ElMessage.success(t('common.success'))
}
</script>
```

---

## 📝 翻译规范

### 命名约定

翻译 key 使用 **camelCase** 命名：

```javascript
{
  common: {
    saveSuccess: '保存成功',  // ✅ 正确
    save_success: '保存成功',  // ❌ 错误
  }
}
```

### 组织结构

按**功能模块**组织翻译：

```javascript
export default {
  common: { /* 通用文本 */ },
  nav: { /* 导航相关 */ },
  menu: { /* 菜单项 */ },
  modules: { /* 模块名称 */ },

  // 各个页面的翻译
  report: { /* 测试报告 */ },
  project: { /* 项目管理 */ },
  testcase: { /* 测试用例 */ },
  // ... 更多模块
}
```

### 翻译优先级

1. **高优先级** (必须翻译):
   - 按钮文本 (保存、取消、删除等)
   - 表单标签
   - 表格列名
   - 提示消息
   - 错误消息

2. **中优先级**:
   - 页面标题
   - 卡片标题
   - 统计数据标签

3. **低优先级** (可选):
   - 帮助文本
   - 占位符文本

---

## 🔧 批量翻译工具脚本

### 查找所有需要翻译的中文文本

```bash
# 搜索所有包含中文的 Vue 文件
grep -r "[\u4e00-\u9fa5]" frontend/src/views/ --include="*.vue" > chinese-texts.txt

# 或者使用更精确的搜索
grep -rn ">\s*[\u4e00-\u9fa5]" frontend/src/views/ --include="*.vue"
```

### 提取按钮文本

```bash
# 查找所有按钮中的中文
grep -rn "<el-button.*>.*[\u4e00-\u9fa5].*</el-button>" frontend/src/views/
```

---

## 📋 待翻译页面清单

### AI 用例生成模块 (`views/requirement-analysis/`)
- [ ] 需求分析页面
- [ ] 生成用例记录列表
- [ ] 提示词配置
- [ ] **测试报告** (示例已创建翻译key)

### 项目管理 (`views/projects/`)
- [ ] 项目列表
- [ ] 项目详情
- [ ] 项目设置

### 测试用例 (`views/testcases/`)
- [ ] 用例列表
- [ ] 用例详情
- [ ] 用例编辑器

### 接口测试 (`views/api-testing/`)
- [ ] 接口管理
- [ ] 环境管理
- [ ] 测试套件
- [ ] 定时任务

### UI 自动化 (`views/ui-automation/`)
- [ ] 元素管理
- [ ] 脚本管理
- [ ] 测试套件
- [ ] 执行记录

### 配置中心 (`views/configuration/`)
- [ ] AI 模型配置
- [ ] UI 环境配置
- [ ] AI 智能模式配置

---

## 🎯 快速开始示例

### 示例 1: 翻译一个简单页面

假设要翻译项目列表页面:

**1. 添加翻译**

```javascript
// locales/zh-CN.js
export default {
  // ...
  project: {
    projectList: '项目列表',
    createProject: '创建项目',
    projectName: '项目名称',
    projectDesc: '项目描述',
    createdTime: '创建时间',
    actions: '操作',
  }
}

// locales/en-US.js
export default {
  // ...
  project: {
    projectList: 'Project List',
    createProject: 'Create Project',
    projectName: 'Project Name',
    projectDesc: 'Project Description',
    createdTime: 'Created Time',
    actions: 'Actions',
  }
}
```

**2. 修改页面组件**

```vue
<template>
  <div>
    <h2>{{ $t('project.projectList') }}</h2>

    <el-button type="primary" @click="createProject">
      {{ $t('project.createProject') }}
    </el-button>

    <el-table :data="projects">
      <el-table-column prop="name" :label="$t('project.projectName')" />
      <el-table-column prop="description" :label="$t('project.projectDesc')" />
      <el-table-column prop="created_at" :label="$t('project.createdTime')" />
      <el-table-column :label="$t('project.actions')">
        <!-- ... -->
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

// 在代码中使用
const createProject = () => {
  ElMessage.success(t('common.success'))
}
</script>
```

### 示例 2: 翻译带有动态文本的页面

```javascript
// 翻译文件支持插值
{
  project: {
    deleteConfirm: '确定要删除项目 {name} 吗？',
    memberCount: '共 {count} 个成员'
  }
}
```

```vue
<template>
  <div>
    <!-- 使用插值 -->
    <p>{{ $t('project.memberCount', { count: members.length }) }}</p>

    <el-button @click="handleDelete">
      {{ $t('common.delete') }}
    </el-button>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'

const { t } = useI18n()

const handleDelete = async () => {
  await ElMessageBox.confirm(
    t('project.deleteConfirm', { name: project.name }),
    t('common.warning'),
    {
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      type: 'warning'
    }
  )
}
</script>
```

---

## 🛠️ 开发工具

### VS Code 插件推荐

1. **i18n Ally** - 在编辑器中直接显示翻译，支持翻译管理
   - 安装: `ext install lokalise.i18n-ally`
   - 显示翻译 key 对应的文本
   - 快速跳转到翻译文件

2. **Vue Language Features (Volar)** - Vue 3 官方支持
   - 提供更好的模板语法高亮

### 调试技巧

**查看当前语言:**
```javascript
// 在组件中
const { locale } = useI18n()
console.log('Current locale:', locale.value)
```

**查看所有翻译:**
```javascript
// 浏览器控制台
console.log(window.__VUE_I18N__.messages)
```

**检查缺失的翻译:**
```javascript
// i18n 配置中启用警告
const i18n = createI18n({
  legacy: false,
  locale: 'zh-CN',
  fallbackLocale: 'zh-CN',
  silentFallbackWarn: false,  // 显示回退警告
  missingWarn: true,          // 显示缺失翻译警告
  messages: { ... }
})
```

---

## 📊 翻译进度追踪

建议创建一个简单的表格追踪翻译进度:

| 模块 | 页面 | 状态 | 负责人 | 备注 |
|------|------|------|--------|------|
| AI 用例生成 | 需求分析 | ⚠️ 待翻译 | - | - |
| AI 用例生成 | 测试报告 | ✅ 已完成 | Claude | 翻译 key 已创建 |
| 项目管理 | 项目列表 | ⚠️ 待翻译 | - | - |
| ... | ... | ... | ... | ... |

---

## 🚀 下一步行动

### 优先级推荐

1. **第一阶段** (高频使用页面):
   - 项目列表
   - 测试用例列表
   - 测试报告

2. **第二阶段** (核心功能):
   - AI 需求分析
   - 接口测试
   - UI 自动化

3. **第三阶段** (配置和辅助):
   - 配置中心
   - 个人设置
   - 帮助文档

### 团队协作

如果多人协作翻译:

1. **分工**: 每人负责 2-3 个模块
2. **规范**: 统一使用本文档的命名规范
3. **审查**: 翻译完成后互相审查
4. **测试**: 切换语言测试页面显示

---

## ⚠️ 注意事项

1. **不要硬编码文本**: 所有用户可见的文本都应该使用 i18n
2. **保持一致性**: 相同含义的词使用相同翻译
3. **上下文**: 提供足够的上下文信息（通过 key 命名或注释）
4. **测试**: 每个页面翻译后都要在两种语言下测试
5. **性能**: 不要在循环中调用 `t()` 函数，使用 computed

---

## 📞 需要帮助?

如果在翻译过程中遇到问题:

1. 查看 `frontend/I18N_IMPLEMENTATION.md` 了解基础配置
2. 参考已翻译的 layout 组件代码
3. 查阅 [vue-i18n 官方文档](https://vue-i18n.intlify.dev/)

---

## 📝 提交规范

翻译完成后的 git commit 格式:

```bash
git commit -m "i18n: 翻译[模块名]页面为中英双语

- 添加翻译 key 到 locales 文件
- 修改组件使用 $t() 函数
- 测试通过

Closes #issue-number"
```

---

**最后更新**: 2026-01-12
**维护者**: TestHub Team
